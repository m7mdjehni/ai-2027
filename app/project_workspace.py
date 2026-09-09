"""Shared, fail-closed filesystem boundary for generated text projects."""
import os
import re
import stat
import threading
import hashlib
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "generated_projects"
MAX_FILE_BYTES = 512_000
MAX_PROJECT_BYTES = 5_000_000
MAX_FILES = 100
TEXT_SUFFIXES = {".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".txt", ".py", ".yaml", ".yml", ".toml", ".csv", ".sql", ".xml", ".svg"}
LOCK = threading.RLock()


def capture_revision(project_id):
    """Read a bounded text revision. Callers recheck to detect concurrent edits."""
    with LOCK:
        files = {name: read_file(project_id, name) for name in list_files(project_id)}
        if sum(len(content.encode('utf-8')) for content in files.values()) > MAX_PROJECT_BYTES:
            raise ValueError('Project size limit exceeded during snapshot read')
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(content.encode('utf-8')).hexdigest().encode('ascii') + b'\n')
    return digest.hexdigest(), files


def validate_id(project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise ValueError("Invalid project ID")
    return project_id


def reject_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Links and Windows reparse points are not allowed")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError("Hard links are not allowed")


def workspace(project_id, create=False):
    validate_id(project_id)
    reject_link(ROOT)
    root = ROOT.resolve()
    target = ROOT / project_id
    reject_link(target)
    if target.resolve().parent != root:
        raise ValueError("Invalid workspace")
    if create:
        ROOT.mkdir(exist_ok=True)
        target.mkdir(exist_ok=False)
    if not target.is_dir():
        raise FileNotFoundError("Project workspace not found")
    return target


def file_path(project_id, relative):
    base = workspace(project_id)
    if not isinstance(relative, str) or len(relative) > 240:
        raise ValueError("Invalid file path")
    parts = relative.split("/")
    if len(parts) > 10:
        raise ValueError("Path is too deep")
    for part in parts:
        if (not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,79}", part)
                or part.endswith((".", " "))
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}
                or any(word in part.lower() for word in ("credential", "secret", "token", "auth", "private", "api_key"))):
            raise ValueError("Unsafe or sensitive file path")
    target = base
    for part in parts:
        target = target / part
        reject_link(target)
    if base.resolve() not in target.resolve().parents or target.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("Only supported project text files are allowed")
    return target


def list_files(project_id):
    base = workspace(project_id)
    files = []
    total = 0
    for directory, dirs, names in os.walk(base, followlinks=False):
        for name in dirs:
            reject_link(Path(directory) / name)
        for name in names:
            relative = (Path(directory) / name).relative_to(base).as_posix()
            target = file_path(project_id, relative)
            info = target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError("Unsupported or oversized file")
            total += info.st_size
            files.append(relative)
            if len(files) > MAX_FILES or total > MAX_PROJECT_BYTES:
                raise ValueError("Project size limit exceeded")
    return sorted(files)


def read_file(project_id, relative):
    target = file_path(project_id, relative)
    if not target.is_file():
        raise FileNotFoundError("File not found")
    with target.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Invalid file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise ValueError("File is too large or not text")
    return data.decode("utf-8")


def write_file(project_id, relative, content):
    if not isinstance(content, str) or "\x00" in content:
        raise ValueError("UTF-8 text content is required")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File size limit exceeded")
    with LOCK:
        target = file_path(project_id, relative)
        files = list_files(project_id)
        total = sum(file_path(project_id, f).stat().st_size for f in files)
        old_size = target.stat().st_size if target.exists() else 0
        if total - old_size + len(data) > MAX_PROJECT_BYTES or (relative not in files and len(files) >= MAX_FILES):
            raise ValueError("Project size limit exceeded")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = file_path(project_id, relative)
        descriptor, temporary = tempfile.mkstemp(prefix='.write-', dir=str(target.parent))
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            file_path(project_id, relative)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"path": relative, "bytes": len(data)}

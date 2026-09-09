"""Trusted validation policy. No command/image/limit comes from a model or request."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WebProfile:
    name: str = "static-web-v1"
    image: str = "hermes-validator-web:phase1"
    timeout_seconds: int = 20
    output_bytes: int = 65536
    max_errors: int = 100


WEB = WebProfile()


def container_command(name, snapshot, image_id):
    return ["docker", "create", "--name", name, "--label", "hermes.validation=phase1",
            "--network", "none", "--read-only", "--user", "10001:10001",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
            "--pids-limit", "32", "--ulimit", "nofile=128:128",
            "--log-driver", "none", "--workdir", "/opt/validator",
            "--mount", f"type=bind,source={snapshot},target=/project,readonly",
            "--entrypoint", "node", image_id, "/opt/validator/check_web.mjs"]

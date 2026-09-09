// Trusted parser-only checks. Never import, evaluate, spawn, or run project code.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import * as acorn from 'acorn';
import * as css from 'css-tree';
import {parse as parseHtml} from 'parse5';

export const PROFILE = 'static-web-v1';
export function revision(files) {
  const hash = crypto.createHash('sha256');
  for (const name of Object.keys(files).sort()) {
    hash.update(name, 'utf8'); hash.update('\0');
    hash.update(crypto.createHash('sha256').update(files[name], 'utf8').digest('hex'));
    hash.update('\n');
  }
  return hash.digest('hex');
}

export function validate(files) {
  const errors = [], warnings = [];
  let truncated = false;
  const add = (file, line, column, check, code, message) => {
    if (errors.length >= 100) { truncated = true; return; }
    errors.push({file, line: line || 1, column: column || 1, check, code, message});
  };
  const warning = (file, check, code) => {
    if (warnings.length < 30) warnings.push({file, check, code});
  };
  if (!Object.hasOwn(files, 'index.html')) add('index.html', 1, 1, 'entrypoint', 'missing_index', 'Required index.html is missing.');

  function asset(file, reference, line = 1) {
    if (!reference || reference.startsWith('#')) return;
    if (/^(?:https?:)?\/\//i.test(reference) || /^(?:data:|mailto:|tel:)/i.test(reference)) {
      warning(file, 'local-assets', 'external_reference_not_checked'); return;
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(reference)) {
      add(file, line, 1, 'local-assets', 'unsupported_url_scheme', 'Unsupported asset URL scheme.'); return;
    }
    let decoded;
    try { decoded = decodeURIComponent(reference.split(/[?#]/)[0]); }
    catch { add(file, line, 1, 'local-assets', 'invalid_url', 'Invalid asset URL encoding.'); return; }
    if (!decoded) return;
    const normalized = path.posix.normalize(decoded.startsWith('/') ? decoded.slice(1) : path.posix.join(path.posix.dirname(file), decoded));
    if (decoded.includes('\\') || decoded.includes('\0') || normalized === '..' || normalized.startsWith('../')) {
      add(file, line, 1, 'local-assets', 'outside_project', 'Asset reference escapes the project.'); return;
    }
    const candidate = normalized.endsWith('/') ? normalized + 'index.html' : normalized;
    if (!Object.hasOwn(files, candidate) && !Object.hasOwn(files, candidate + '/index.html'))
      add(file, line, 1, 'local-assets', 'missing_asset', 'Referenced local asset does not exist.');
  }

  function javascript(file, source, mode = 'script', baseLine = 1, baseColumn = 1) {
    try {
      const ast = acorn.parse(source, {ecmaVersion: 2024, sourceType: mode, locations: true});
      // Examine static imports as data; never resolve or execute modules.
      for (const node of ast.body) {
        if (node.source?.value && ['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(node.type)) {
          if (node.source.value.startsWith('.') || node.source.value.startsWith('/')) asset(file, node.source.value, baseLine + node.loc.start.line - 1);
          else warning(file, 'local-assets', 'bare_module_import_not_checked');
        }
      }
    } catch (error) {
      // Parser messages can quote source text/secrets. Return fixed messages only.
      add(file, baseLine + (error.loc?.line || 1) - 1,
          (error.loc?.column || 0) + 1 + ((error.loc?.line || 1) === 1 ? baseColumn - 1 : 0),
          'javascript-syntax', 'syntax_error', 'JavaScript syntax is invalid.');
    }
  }

  function stylesheet(file, source, context = 'stylesheet', baseLine = 1) {
    const report = error => add(file, baseLine + (error.line || 1) - 1, error.column || 1,
      'css-syntax', 'syntax_error', 'CSS syntax is invalid.');
    try {
      const ast = css.parse(source, {context, positions: true, onParseError: report});
      css.walk(ast, node => {
        if (node.type === 'Url') asset(file, node.value, baseLine + (node.loc?.start.line || 1) - 1);
        if (node.type === 'Atrule' && node.name.toLowerCase() === 'import' && node.prelude)
          css.walk(node.prelude, part => { if (part.type === 'String') asset(file, part.value, baseLine + (part.loc?.start.line || 1) - 1); });
      });
    } catch (error) { report(error); }
  }

  const scriptModes = new Map();
  for (const [file, source] of Object.entries(files)) {
    if (!file.toLowerCase().endsWith('.html')) continue;
    const document = parseHtml(source, {sourceCodeLocationInfo: true, onParseError: error => {
      add(file, error.startLine, error.startCol, 'html-syntax', error.code, 'HTML has a parser error.');
    }});
    const pending = [document];
    while (pending.length) {
      const node = pending.pop();
      pending.push(...(node.childNodes || []));
      if (node.content) pending.push(node.content);
      const attrs = Object.fromEntries((node.attrs || []).map(attr => [attr.name, attr.value]));
      const line = node.sourceCodeLocation?.startLine || 1;
      if (node.tagName === 'base') add(file, line, 1, 'local-assets', 'unsupported_base', 'Base URL elements are unsupported by this validation profile.');
      for (const attribute of ['src', 'poster']) if (attrs[attribute]) asset(file, attrs[attribute], line);
      if (attrs.href && ['link', 'a'].includes(node.tagName)) asset(file, attrs.href, line);
      if (attrs.srcset) warning(file, 'local-assets', 'srcset_not_checked');
      if (attrs.style) stylesheet(file, attrs.style, 'declarationList', line);
      if (Object.keys(attrs).some(name => name.startsWith('on'))) warning(file, 'javascript-syntax', 'event_attribute_not_checked');
      const text = (node.childNodes || []).filter(child => child.nodeName === '#text').map(child => child.value).join('');
      if (node.tagName === 'style') stylesheet(file, text, 'stylesheet', node.sourceCodeLocation?.startTag?.endLine || line);
      if (node.tagName === 'script') {
        const type = (attrs.type || '').toLowerCase();
        if (!['', 'module', 'text/javascript', 'application/javascript'].includes(type)) continue;
        const mode = type === 'module' ? 'module' : 'script';
        if (attrs.src && !/^(?:[a-z]+:|\/\/)/i.test(attrs.src)) {
          try {
            const relative = decodeURIComponent(attrs.src.split(/[?#]/)[0]);
            const target = path.posix.normalize(relative.startsWith('/') ? relative.slice(1) : path.posix.join(path.posix.dirname(file), relative));
            if (!target.toLowerCase().endsWith('.js')) add(file, line, 1, 'javascript-syntax', 'unsupported_script_extension', 'Local script references must use .js files.');
            if (!scriptModes.has(target)) scriptModes.set(target, new Set());
            scriptModes.get(target).add(mode);
          } catch { /* already reported by asset validation */ }
        } else if (!attrs.src) javascript(file, text, mode,
          node.sourceCodeLocation?.startTag?.endLine || line, node.sourceCodeLocation?.startTag?.endCol || 1);
      }
    }
  }
  for (const [file, source] of Object.entries(files)) {
    if (file.toLowerCase().endsWith('.css')) stylesheet(file, source);
    if (file.toLowerCase().endsWith('.js')) for (const mode of scriptModes.get(file) || ['module']) javascript(file, source, mode);
  }
  return {schema_version: 1, profile: PROFILE, revision: revision(files), passed: errors.length === 0,
    status: errors.length ? 'failed' : 'passed', checks: ['entrypoint', 'html-syntax', 'css-syntax', 'javascript-syntax', 'local-assets'],
    errors, warnings, truncated, files_checked: Object.keys(files).filter(name => /\.(html|css|js)$/.test(name)).length};
}

function loadSnapshot(root) {
  const files = {}; let total = 0;
  const pending = [''];
  while (pending.length) {
    const dir = pending.pop();
    for (const entry of fs.readdirSync(path.join(root, dir), {withFileTypes: true})) {
      const name = dir ? dir + '/' + entry.name : entry.name;
      if (name.length > 240 || name.split('/').length > 10 || entry.isSymbolicLink()) throw Error('Unsafe snapshot');
      if (entry.isDirectory()) { pending.push(name); continue; }
      const info = fs.lstatSync(path.join(root, name));
      if (!info.isFile() || info.nlink !== 1 || info.size > 512000 || Object.keys(files).length >= 100) throw Error('Snapshot limit');
      total += info.size; if (total > 5000000) throw Error('Snapshot limit');
      const buffer = fs.readFileSync(path.join(root, name));
      files[name] = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(buffer);
    }
  }
  return files;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.stdout.write(JSON.stringify(validate(loadSnapshot('/project')))); }
  catch { process.stdout.write(JSON.stringify({schema_version: 1, profile: PROFILE, passed: false,
    status: 'infrastructure_error', errors: [], message: 'Validator could not inspect the snapshot.'})); process.exitCode = 2; }
}

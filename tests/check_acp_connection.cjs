// Mirrors ACP Client 0.2.0's Windows launch; never prints raw agent output.
const fs = require('node:fs');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const settings = JSON.parse(fs.readFileSync(path.join(root, '.vscode/settings.json'), 'utf8'));
const config = settings['acp.agents']['Hermes Agent'];
const child = spawn(config.command, config.args, {
  cwd: root, env: {...process.env, ...config.env},
  shell: process.platform === 'win32', stdio: ['pipe', 'pipe', 'pipe'],
  windowsHide: true,
});
let done = false, buffered = '', stderr = '';
function finish(code, message) {
  if (done) return;
  done = true;
  clearTimeout(timer);
  console.log(message);
  if (child.exitCode === null && child.pid) {
    if (process.platform === 'win32') {
      const cleanup = spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'],
        {stdio: 'ignore', shell: false, windowsHide: true, timeout: 10000});
      if (cleanup.status !== 0) { console.log('FAIL: process cleanup could not be confirmed'); code = 1; }
    } else child.kill();
  }
  process.exitCode = code;
}
const timer = setTimeout(() => finish(1, 'FAIL: ACP handshake timed out'), 60000);
function send(id, method, params) {
  child.stdin.write(JSON.stringify({jsonrpc: '2.0', id, method, params}) + '\n');
}
child.stdin.on('error', () => {});
child.on('error', () => finish(1, 'FAIL: ACP executable could not start'));
child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-16000); });
child.stdout.on('data', chunk => {
  buffered += chunk.toString();
  if (buffered.length > 1000000) return finish(1, 'FAIL: ACP output limit exceeded');
  let end;
  while ((end = buffered.indexOf('\n')) >= 0) {
    const line = buffered.slice(0, end); buffered = buffered.slice(end + 1);
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    if (msg.id === 1 && msg.result) {
      console.log('PASS: ACP initialize');
      send(2, 'session/new', {cwd: root, mcpServers: []});
    } else if (msg.id === 2 && msg.result) {
      const models = msg.result.models;
      console.log('Models advertised:', JSON.stringify(models || null));
      finish(0, 'PASS: ACP workspace session created (file access and inference not tested)');
    } else if ((msg.id === 1 || msg.id === 2) && msg.error) {
      finish(1, 'FAIL: ACP protocol request rejected; error code ' + msg.error.code);
    }
  }
});
child.on('close', code => finish(1,
  /Device Guard|blocked by your organization/i.test(stderr)
    ? 'FAIL: Windows Device Guard blocked Hermes; exit code ' + code
    : 'FAIL: ACP closed before workspace session; exit code ' + code));
send(1, 'initialize', {protocolVersion: 1, clientCapabilities: {},
  clientInfo: {name: 'presentation-preflight', version: '1.0'}});

// Runs the actual service worker against isolated Chrome API adapters.
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const hooks = {}, timers = [], sockets = [], requests = [];
const stored = {serverUrl: 'http://127.0.0.1:8765', sessionToken: 'extension-test-token', deviceId: 'test-device'};
let currentUrl = 'https://example.org/', captureCount = 0;
const task = {id: 'test-task', mode: 'batch', status: 'collecting', image_count: 0};
class Socket {
  static OPEN = 1;
  constructor(url) { this.url = String(url); this.readyState = 1; this.sent = []; sockets.push(this); }
  send(value) { this.sent.push(JSON.parse(value)); }
  close() { this.readyState = 3; if (this.onclose) this.onclose({code: 1006}); }
}
const listener = name => ({addListener: fn => { hooks[name] = fn; }});
const context = {
  URL, WebSocket: Socket, crypto: webcrypto, FormData, AbortController, AbortSignal, Date, console,
  setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; },
  setInterval: (fn, delay) => { timers.push({fn, delay}); return timers.length; }, clearTimeout() {}, clearInterval() {},
  chrome: {
    storage: {local: {get: async keys => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map(key => [key, stored[key]])), set: async obj => Object.assign(stored, obj)}},
    tabs: {query: async () => [{url: currentUrl, windowId: 42}],
      captureVisibleTab: async (windowId, options) => { assert.equal(windowId, 42); assert.equal(options.format, 'png'); captureCount++; return 'data:image/png;base64,aGVsbG8='; }, create: async () => ({})},
    action: {setBadgeText() {}, setBadgeBackgroundColor() {}},
    alarms: {create: async () => {}, onAlarm: listener('alarm')},
    runtime: {onStartup: listener('startup'), onInstalled: listener('installed'), onMessage: listener('message')},
    commands: {onCommand: listener('command')}
  },
  fetch: async (url, options = {}) => {
    if (url.startsWith('data:')) return new Response(new Blob(['image'], {type: 'image/png'}));
    requests.push({url, options});
    assert.equal(options.headers.Authorization, 'Bearer extension-test-token');
    if (url.endsWith('/api/tasks') && options.method === 'POST') Object.assign(task, {mode: JSON.parse(options.body).mode, status: 'collecting', image_count: 0});
    if (url.endsWith('/capture')) task.status = 'capturing';
    if (url.endsWith('/images')) {
      assert.equal(options.headers['X-Capture-Id'], 'capture-1');
      assert.equal(options.body.getAll('images').length, 1);
      task.image_count++; task.status = task.mode === 'quick' ? 'processing' : 'collecting';
    }
    return Response.json(url.endsWith('/api/status') ? {active_tasks: [task]} : task);
  }
};
async function tick() { await new Promise(resolve => setImmediate(resolve)); }
const send = message => new Promise(resolve => hooks.message(message, {}, resolve));
(async () => {
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('extension/background.js', 'utf8'), context);
  await tick();
  const socket = sockets[0];
  assert.equal(socket.url, 'ws://127.0.0.1:8765/ws/agent');
  socket.onopen(); assert.equal(socket.sent[0].type, 'auth');
  assert.equal(socket.sent[0].token, stored.sessionToken);
  await socket.onmessage({data: JSON.stringify({type: 'auth.ok'})});
  assert.equal(socket.sent[1].type, 'agent.connect');
  assert.equal(socket.sent[1].device_id, 'test-device');
  await socket.onmessage({data: JSON.stringify({type: 'agent.connected'})});
  assert.equal((await send({action: 'batch_capture'})).ok, true);
  await socket.onmessage({data: JSON.stringify({type: 'capture.request', task_id: 'test-task', capture_id: 'capture-1'})});
  assert.equal(captureCount, 1);
  assert.equal((await send({action: 'get_status'})).batch_task.image_count, 1);
  currentUrl = 'chrome://settings/';
  await socket.onmessage({data: JSON.stringify({type: 'capture.request', task_id: 'test-task', capture_id: 'capture-2'})});
  assert.equal(captureCount, 1);
  assert.equal(socket.sent.at(-1).type, 'capture.error');
  assert.match((await send({action: 'get_status'})).last_error, /普通网页/);
  currentUrl = 'https://example.org/';
  hooks.command('take-screenshot'); await tick();
  assert.equal(task.mode, 'quick');
  const save = await send({action: 'save_config', server_url: 'https://example.com', app_token: 'x'});
  assert.equal(save.ok, false);
  socket.close();
  assert.equal(timers.at(-1).delay, 1000);
  for (const delay of [2000, 4000, 8000, 16000, 30000, 30000]) {
    timers.at(-1).fn(); sockets.at(-1).close(); assert.equal(timers.at(-1).delay, delay);
  }
  console.log('Extension adapter checks passed: first-message auth, snake_case protocol, current window capture, multipart upload, restricted-page error, shortcut, server validation, exponential reconnect through 30 seconds.');
})().catch(error => { console.error(error); process.exitCode = 1; });

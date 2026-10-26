// Retains the supplied agent's MV3 capture/upload, heartbeat, device and badge flow.
const HEARTBEAT_INTERVAL = 20000;
const LOG_LIMIT = 80;
let config = {};
let agentSocket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let lastPong = 0;
let activeCapture = null;
let actionBusy = false;
let authRejected = false;
let runtimeState = {connection_status: 'disconnected', last_error: '', current_task: null, batch_task: null};

async function log(event) {
  const stored = await chrome.storage.local.get('agentLogs');
  const logs = Array.isArray(stored.agentLogs) ? stored.agentLogs : [];
  logs.unshift({at: new Date().toISOString(), event});
  await chrome.storage.local.set({agentLogs: logs.slice(0, LOG_LIMIT)});
}

function updateBadge() {
  const online = runtimeState.connection_status === 'connected';
  chrome.action.setBadgeText({text: online ? 'ON' : runtimeState.connection_status === 'connecting' ? '...' : 'OFF'});
  chrome.action.setBadgeBackgroundColor({color: online ? '#16806a' : '#a44b40'});
}

function normalizeUrl(raw) {
  let url;
  try { url = new URL(raw); } catch { throw new Error('请输入有效的服务器地址。'); }
  const local = ['127.0.0.1', 'localhost'].includes(url.hostname) && url.protocol === 'http:';
  const tailnet = url.hostname.endsWith('.ts.net') && url.protocol === 'https:';
  if ((!local && !tailnet) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('请使用本机 HTTP 地址或 Tailscale HTTPS 根地址，不要带路径、参数或账号。');
  }
  return url.origin;
}

async function loadConfig() {
  config = await chrome.storage.local.get(['serverUrl', 'sessionToken', 'deviceId']);
  if (!config.deviceId) {
    config.deviceId = `extension_${crypto.randomUUID()}`;
    await chrome.storage.local.set({deviceId: config.deviceId});
  }
}

async function api(path, options = {}) {
  if (!config.serverUrl || !config.sessionToken) throw new Error('请先登录账号。');
  let response;
  try {
    response = await fetch(config.serverUrl + path, {...options,
      headers: {...options.headers, Authorization: `Bearer ${config.sessionToken}`},
      signal: options.signal || AbortSignal.timeout(40000)});
  } catch { throw new Error('无法连接后端或上传超时，请检查服务地址和 Tailscale。'); }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || '后端请求失败。');
  return result;
}

function disconnectSocket() {
  clearInterval(heartbeatTimer);
  clearTimeout(reconnectTimer);
  if (agentSocket) {
    const old = agentSocket;
    agentSocket = null;
    old.onclose = null;
    old.onmessage = null;
    old.onopen = null;
    old.close();
  }
  runtimeState.connection_status = 'disconnected';
  updateBadge();
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  if (authRejected || !config.sessionToken || !config.serverUrl) return;
  const delay = Math.min(30000, 1000 * 2 ** Math.min(reconnectAttempt++, 5));
  reconnectTimer = setTimeout(connectAgent, delay);
}

function connectAgent() {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (agentSocket || authRejected || !config.sessionToken || !config.serverUrl) return;
  const url = new URL(normalizeUrl(config.serverUrl));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/ws/agent';
  const ws = new WebSocket(url);
  agentSocket = ws;
  runtimeState.connection_status = 'connecting';
  updateBadge();
  const connectTimeout = setTimeout(() => {
    if (runtimeState.connection_status !== 'connected' && agentSocket === ws) ws.close();
  }, 10000);
  // Account login stores the bearer token as sessionToken; use the same
  // field for the Agent WebSocket handshake.
  ws.onopen = () => ws.send(JSON.stringify({type: 'auth', token: config.sessionToken}));
  ws.onmessage = async event => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === 'auth.ok') {
        ws.send(JSON.stringify({type: 'agent.connect', device_id: config.deviceId}));
      } else if (payload.type === 'agent.connected') {
        clearTimeout(connectTimeout);
        reconnectAttempt = 0;
        lastPong = Date.now();
        runtimeState.connection_status = 'connected';
        runtimeState.last_error = '';
        updateBadge();
        clearInterval(heartbeatTimer);
        heartbeatTimer = setInterval(() => {
          if (Date.now() - lastPong > 55000) { ws.close(); return; }
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: 'ping'}));
        }, HEARTBEAT_INTERVAL);
        await log('agent.connected');
      } else if (payload.type === 'pong') {
        lastPong = Date.now();
      } else if (payload.type === 'capture.request') {
        await captureAndUpload(payload.task_id, payload.capture_id, ws);
      } else if (payload.type === 'capture.cancel' && activeCapture?.id === payload.capture_id) {
        activeCapture.controller.abort();
        activeCapture = null;
      }
    } catch (error) {
      runtimeState.last_error = error.message || '连接消息处理失败。';
      await log('agent.message_failed');
    }
  };
  ws.onclose = event => {
    clearTimeout(connectTimeout);
    if (agentSocket !== ws) return;
    agentSocket = null;
    clearInterval(heartbeatTimer);
    runtimeState.connection_status = 'disconnected';
    if (event.code === 4401) {
      authRejected = true;
      runtimeState.last_error = '账号会话已失效，请重新登录。';
    }
    updateBadge();
    log('agent.disconnected').catch(() => { runtimeState.last_error = '扩展日志保存失败。'; });
    scheduleReconnect();
  };
  ws.onerror = () => { runtimeState.last_error = '后端 WebSocket 连接失败，请检查服务地址。'; };
}

async function captureAndUpload(taskId, captureId, ws) {
  activeCapture?.controller.abort();
  const capture = {id: captureId, controller: new AbortController()};
  activeCapture = capture;
  try {
    const tabs = await chrome.tabs.query({active: true, lastFocusedWindow: true});
    const tab = tabs[0];
    if (!tab || !/^https?:\/\//.test(tab.url || '')) {
      throw new Error('当前页面无法截图，请切换到普通网页；chrome:// 等内置页面不受支持。');
    }
    let dataUrl;
    try {
      dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {format: 'png'});
    } catch {
      throw new Error('Chrome 截图失败，请切换到普通网页并检查扩展截图权限。');
    }
    if (activeCapture !== capture) return;
    const blob = await (await fetch(dataUrl)).blob();
    const form = new FormData();
    form.append('images', blob, 'capture.png');
    const task = await api(`/api/tasks/${taskId}/images`, {
      method: 'POST', headers: {'X-Capture-Id': captureId}, body: form,
      signal: AbortSignal.any([capture.controller.signal, AbortSignal.timeout(40000)])});
    if (activeCapture !== capture) return;
    runtimeState.current_task = task;
    if (task.mode === 'batch') runtimeState.batch_task = task;
    runtimeState.last_error = '';
    await log('capture.completed');
  } catch (error) {
    if (activeCapture !== capture || capture.controller.signal.aborted) return;
    runtimeState.last_error = error.message;
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({type: 'capture.error', task_id: taskId, capture_id: captureId, message: '截图或上传失败'}));
    }
    await log('capture.failed');
  } finally { if (activeCapture === capture) activeCapture = null; }
}

async function refreshTasks() {
  const status = await api('/api/status');
  runtimeState.batch_task = status.active_tasks.find(task => task.mode === 'batch' && ['collecting', 'capturing'].includes(task.status)) || null;
  const current = runtimeState.current_task;
  if (current) {
    try { runtimeState.current_task = await api(`/api/tasks/${current.id}`); }
    catch { runtimeState.current_task = status.active_tasks[0] || null; }
  } else runtimeState.current_task = status.active_tasks[0] || null;
  return status;
}

async function captureTask(mode) {
  if (runtimeState.connection_status !== 'connected') throw new Error('Agent 未连接，请先保存并连接。');
  const task = await api('/api/tasks', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode, source: 'extension', reuse: mode === 'batch'})});
  runtimeState.current_task = task;
  const captured = await api(`/api/tasks/${task.id}/capture`, {method: 'POST'});
  runtimeState.current_task = captured;
  if (mode === 'batch') runtimeState.batch_task = captured;
}

async function handle(message) {
  await ready;
  if (message.action === 'get_status') {
    let serverOnline = false;
    if (config.sessionToken && config.serverUrl) {
      try { await refreshTasks(); serverOnline = true; }
      catch (error) { runtimeState.last_error = error.message; }
    }
    return {ok: true, ...runtimeState, device_id: config.deviceId, server_online: serverOnline};
  }
  if (actionBusy) throw new Error('正在处理上一项操作，请稍后再试。');
  actionBusy = true;
  try {
    if (message.action === 'save_config') {
      const serverUrl = normalizeUrl(message.server_url.trim());
      const login = await fetch(serverUrl + '/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: message.username.trim(), password: message.password})}).then(async r => { const v = await r.json(); if (!r.ok) throw new Error(v.error || '登录失败'); return v; });
      disconnectSocket();
      await chrome.storage.local.set({serverUrl, sessionToken: login.token});
      await loadConfig();
      runtimeState.current_task = null;
      runtimeState.batch_task = null;
      authRejected = false;
      reconnectAttempt = 0;
      connectAgent();
    } else if (message.action === 'reconnect') {
      disconnectSocket(); authRejected = false; reconnectAttempt = 0; connectAgent();
    } else if (message.action === 'quick') {
      await captureTask('quick');
    } else if (message.action === 'batch_capture') {
      await captureTask('batch');
    } else if (message.action === 'analyze') {
      await refreshTasks();
      if (!runtimeState.batch_task) throw new Error('当前没有多图任务，请先添加截图。');
      runtimeState.current_task = await api(`/api/tasks/${runtimeState.batch_task.id}/analyze`, {method: 'POST'});
    } else if (message.action === 'open_controller') {
      await chrome.tabs.create({url: normalizeUrl(config.serverUrl)});
    } else throw new Error('未知操作。');
    return {ok: true};
  } finally { actionBusy = false; }
}

const ready = loadConfig().then(async () => {
  await chrome.alarms.create('connection_watchdog', {periodInMinutes: 0.5});
  connectAgent();
}).catch(error => { runtimeState.last_error = error.message; });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'connection_watchdog' && !agentSocket && !reconnectTimer) ready.then(connectAgent);
});
chrome.runtime.onStartup.addListener(() => ready.then(connectAgent));
chrome.runtime.onInstalled.addListener(() => ready.then(connectAgent));
chrome.commands.onCommand.addListener(command => {
  const action = { 'take-screenshot': 'quick', 'batch-capture': 'batch_capture', analyze: 'analyze' }[command];
  if (action) handle({action}).catch(error => { runtimeState.last_error = error.message; });
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message).then(sendResponse).catch(error => {
    runtimeState.last_error = error.message;
    sendResponse({ok: false, error: error.message});
  });
  return true;
});

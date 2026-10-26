'use strict';
const $ = id => document.getElementById(id);
const labels = {collecting: '正在收集', capturing: '正在截图', processing: '正在分析', superseded: '已被新任务替代', completed: '分析完成', failed: '任务失败'};
let token = localStorage.getItem('session_token') || '';
let socket = null, retryTimer = null, heartbeat = null, reconnectAttempt = 0, lastPong = 0;
let batch = null, currentId = null, maxImages = 20, online = false, busy = false;
let refreshRunning = false, refreshAgain = false, generation = 0;
const previews = new Map();

function error(message = '') { $('error').textContent = message; }
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
async function api(path, options = {}, blob = false) {
  let response;
  try { response = await fetch('/api' + path, {...options, headers: {...options.headers, Authorization: `Bearer ${token}`}}); }
  catch { throw new Error('无法连接服务器，请检查网络后重试。'); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) disconnect(true);
    throw new Error(body.error || '请求失败，请稍后重试。');
  }
  return blob ? response.blob() : response.json();
}
const post = (path, body) => api(path, {method: 'POST', ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});

function discardPreviews() {
  for (const item of previews.values()) URL.revokeObjectURL(item.url);
  previews.clear();
  $('thumbnails').replaceChildren();
}
function disconnect(showAuth = false) {
  generation++;
  clearTimeout(retryTimer); clearInterval(heartbeat);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  if (showAuth) {
    $('auth').hidden = false; $('workspace').hidden = true;
    discardPreviews();
  }
}
function connectSocket() {
  if (!token || socket) return;
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/controller`);
  socket = ws;
  const timeout = setTimeout(() => { if (ws.readyState !== WebSocket.CLOSED && !lastPong) ws.close(); }, 10000);
  ws.onopen = () => ws.send(JSON.stringify({type: 'auth', token}));
  ws.onmessage = event => {
    let message;
    try { message = JSON.parse(event.data); } catch { error('收到无效的实时消息。'); return; }
    if (message.type === 'auth.ok') {
      clearTimeout(timeout); reconnectAttempt = 0; lastPong = Date.now();
      heartbeat = setInterval(() => {
        if (Date.now() - lastPong > 55000) { ws.close(); return; }
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: 'ping'}));
      }, 20000);
      refresh();
    } else if (message.type === 'pong') lastPong = Date.now();
    else {
      if (message.task && message.task.status !== 'superseded') {
        generation++;
        currentId = message.task_id;
        renderResult(message.task);
      }
      refresh();
    }
  };
  ws.onclose = event => {
    clearTimeout(timeout); clearInterval(heartbeat);
    if (socket !== ws) return;
    socket = null; lastPong = 0;
    if (event.code === 4401) { disconnect(true); error('访问令牌错误，请重新输入。'); return; }
    $('server_status').textContent = 'Server：实时连接中断，正在重连';
    retryTimer = setTimeout(connectSocket, Math.min(30000, 1000 * 2 ** Math.min(reconnectAttempt++, 5)));
  };
  ws.onerror = () => { $('server_status').textContent = 'Server：连接异常'; };
}

function controls() {
  const editable = batch?.status === 'collecting';
  $('new_batch').disabled = busy;
  $('quick').disabled = busy;
  $('capture').disabled = busy || !editable || !online || batch.image_count >= maxImages;
  $('upload').disabled = busy || !editable || batch.image_count >= maxImages;
  $('analyze').disabled = busy || !editable || !batch.image_count;
  $('clear_images').disabled = busy || !editable || !batch.image_count;
  $('cancel').disabled = busy || !batch || !['collecting', 'capturing', 'processing'].includes(batch.status);
  for (const button of $('thumbnails').querySelectorAll('button')) button.disabled = busy || !editable;
}

async function thumbnails(task) {
  const epoch = generation;
  if (!task || !['collecting', 'capturing'].includes(task.status)) { discardPreviews(); return; }
  const items = await api(`/tasks/${task.id}/images`);
  if (epoch !== generation) return;
  const ids = new Set(items.map(item => item.id));
  for (const [id, item] of previews) if (!ids.has(id)) { URL.revokeObjectURL(item.url); previews.delete(id); }
  for (const item of items) {
    if (!previews.has(item.id)) {
      const blob = await api(`/tasks/${task.id}/images/${item.id}`, {}, true);
      if (epoch !== generation) return;
      previews.set(item.id, {url: URL.createObjectURL(blob)});
    }
  }
  $('thumbnails').replaceChildren();
  for (const item of items) {
    const figure = node('figure', undefined, 'thumb');
    const link = node('a'); link.href = previews.get(item.id).url; link.target = '_blank'; link.rel = 'noopener';
    const image = node('img'); image.src = link.href; image.alt = `第 ${item.sequence} 张截图`; link.append(image);
    const caption = node('figcaption'); caption.append(node('span', `第 ${item.sequence} 张`));
    const remove = node('button', '删除', 'quiet');
    remove.onclick = () => action(async () => { await api(`/tasks/${task.id}/images/${item.id}`, {method: 'DELETE'}); });
    caption.append(remove); figure.append(link, caption); $('thumbnails').append(figure);
  }
  controls();
}

function renderResult(task) {
  $('result_status').textContent = labels[task.status] || task.status;
  $('current_status').textContent = labels[task.status] || task.status;
  const container = $('result'); container.replaceChildren();
  if (task.status === 'processing') container.append(node('p', '正在分析，请等待结果。新提交会立即替代当前任务。'));
  if (task.error) container.append(node('p', task.error, 'incomplete'));
  if (task.result) {
    for (const question of task.result.questions) {
      const article = node('article', undefined, 'question');
      article.append(node('h3', `第 ${question.index} 题`), node('div', `答案：${question.answer.join('、') || '无法确定'}`, 'answer'));
      container.append(article);
    }

  } else if (task.raw_result !== null && task.raw_result !== undefined) {
    container.append(node('p', '模型未返回有效的结构化 JSON，以下保留原始结果。', 'muted'), node('pre', task.raw_result || '模型返回了空文本。'));
  } else if (['collecting', 'capturing'].includes(task.status)) container.append(node('p', '图片收集完成后，结果会显示在这里。', 'muted'));
}

async function refresh() {
  if (!token || $('workspace').hidden) return;
  if (refreshRunning) { refreshAgain = true; return; }
  refreshRunning = true;
  const epoch = generation;
  try {
    const status = await api('/status');
    if (epoch !== generation) return;
    online = status.agent_online; maxImages = status.max_images;
    $('agent_status').textContent = `截图客户端：${online ? '在线' : '离线'}`;
    $('server_status').textContent = socket?.readyState === WebSocket.OPEN ? 'Server：在线' : 'Server：在线 · 实时重连中';
    batch = status.active_tasks.find(task => task.mode === 'batch' && ['collecting', 'capturing'].includes(task.status))
      || status.active_tasks.find(task => task.mode === 'batch' && task.id === currentId) || null;
    $('count').textContent = `已采集 ${batch?.image_count || 0} / ${maxImages} 张`;
    $('collection_status').textContent = batch ? (batch.error || labels[batch.status]) : '新建任务后即可添加图片。';
    if (!status.deepseek_configured) error('后端尚未配置 DeepSeek API Key 或模型，可先收集图片。');
    controls();
    if (!currentId) currentId = status.active_tasks[0]?.id || null;
    if (currentId) {
      try { const requestedId = currentId; const task = await api(`/tasks/${requestedId}`); if (epoch === generation && requestedId === currentId) renderResult(task); }
      catch (err) { if (epoch === generation) { currentId = null; error(err.message); } }
    }
    await thumbnails(batch);
  } catch (err) { error(err.message); }
  finally {
    refreshRunning = false;
    if (refreshAgain) { refreshAgain = false; refresh(); }
  }
}

async function action(work) {
  if (busy) return;
  generation++; busy = true; controls(); error();
  try { await work(); }
  catch (err) { error(err.message); }
  finally { busy = false; await refresh(); controls(); }
}
$('quick').onclick = () => action(async () => {
  const status = await api('/status');
  online = Boolean(status.agent_online);
  controls();
  if (!online) throw new Error('没有在线的截图客户端。请打开 Mac 客户端或浏览器扩展并登录连接，确认 Agent 在线后重试。');
  const task = await post('/tasks', {mode: 'quick', source: 'controller'}); currentId = task.id;
  await post(`/tasks/${task.id}/capture`);
});
$('new_batch').onclick = () => action(async () => {
  batch = await post('/tasks', {mode: 'batch', source: 'controller'}); currentId = batch.id;
});
$('capture').onclick = () => action(async () => { currentId = batch.id; await post(`/tasks/${batch.id}/capture`); });
$('upload').onclick = () => $('files').click();
$('files').onchange = () => {
  const files = Array.from($('files').files); $('files').value = '';
  if (!files.length) return;
  action(async () => {
    if (!batch || files.length + batch.image_count > maxImages) throw new Error(`每个任务最多 ${maxImages} 张图片，请减少选择。`);
    const form = new FormData(); for (const file of files) form.append('images', file);
    currentId = batch.id;
    await api(`/tasks/${batch.id}/images`, {method: 'POST', body: form});
  });
};
$('analyze').onclick = () => action(async () => {
  currentId = batch.id; const task = await post(`/tasks/${batch.id}/analyze`); discardPreviews(); renderResult(task);
});
$('clear_images').onclick = () => action(async () => { await api(`/tasks/${batch.id}/images`, {method: 'DELETE'}); });
$('cancel').onclick = () => action(async () => { currentId = batch.id; await post(`/tasks/${batch.id}/cancel`); discardPreviews(); });
$('clear_token').onclick = () => {
  token = ''; localStorage.removeItem('session_token'); disconnect(true); batch = null; currentId = null;
  $('username').value = ''; $('password').value = ''; $('result').replaceChildren(); error();
};
$('auth_form').onsubmit = async event => {
  event.preventDefault();
  try { const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('username').value.trim(), password:$('password').value})}); const data = await response.json(); if (!response.ok) throw new Error(data.error || '登录失败'); token = data.token; localStorage.setItem('session_token', token); $('password').value = ''; await begin(); }
  catch (err) { error(err.message); }
};
async function begin() {
  disconnect(); error();
  if (!token) { $('auth').hidden = false; $('workspace').hidden = true; return; }
  $('auth').hidden = true; $('workspace').hidden = false;
  await refresh(); if (!$('workspace').hidden) { connectSocket(); loadAdmin(); }
}
async function loadAdmin(){try{const me=await api('/auth/me');if(!me||me.role!=='admin')return;$('admin_panel').hidden=false;const users=await api('/admin/users');$('user_list').replaceChildren(...users.filter(u=>u.role==='user').map(u=>{const row=node('div',`${u.username} · ${u.disabled?'已停用':'正常'}`);const b=node('button','停用/启用','quiet');b.onclick=async()=>{await api(`/admin/users/${u.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({disabled:!u.disabled})});loadAdmin()};row.append(b);return row}))}catch(e){console.error(e)} }
$('user_form').onsubmit=async e=>{e.preventDefault();const username=$('new_username').value.trim();const password=$('new_password').value;if(password.length<10){error('初始密码至少需要 10 个字符。');return}try{await api('/admin/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});$('new_username').value='';$('new_password').value='';loadAdmin()}catch(e){error(e.message)}};
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(refresh, 10000);
begin();

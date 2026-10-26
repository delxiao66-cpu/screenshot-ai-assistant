const $ = id => document.getElementById(id);
const names = {collecting: '正在收集', capturing: '正在截图', processing: '正在分析', superseded: '已被新任务替代', completed: '分析完成', failed: '任务失败'};
let busy = false;
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const result = await chrome.runtime.sendMessage({action: 'get_status'});
    if (!result.ok) throw new Error(result.error);
    $('connection').textContent = `Agent：${result.connection_status === 'connected' ? '在线' : result.connection_status === 'connecting' ? '连接中' : '离线'} · 后端：${result.server_online ? '在线' : '离线'}`;
    $('device').textContent = '';
    $('task').textContent = `当前任务：${names[result.current_task?.status] || '暂无'}`;
    $('batch').textContent = `当前多图任务已收集 ${result.batch_task?.image_count || 0} 张`;
    $('error').textContent = result.last_error || result.current_task?.error || '';
    $('analyze').disabled = busy || !result.batch_task?.image_count || result.batch_task.status !== 'collecting';
  } catch (error) { $('error').textContent = error.message; }
  finally { refreshing = false; }
}

for (const id of ['save', 'reconnect', 'quick', 'batch_capture', 'analyze', 'open_controller']) {
  $(id).addEventListener('click', async () => {
    busy = true;
    document.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try {
      const result = await chrome.runtime.sendMessage({action: id === 'save' ? 'save_config' : id,
        server_url: $('serverUrl').value, username: $('username').value, password: $('password').value});
      if (!result.ok) throw new Error(result.error);
      await refresh();
    } catch (error) { $('error').textContent = error.message; }
    finally {
      busy = false;
      document.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  });
}
chrome.storage.local.get(['serverUrl']).then(stored => {
  $('serverUrl').value = stored.serverUrl || '';
  refresh();
});
setInterval(refresh, 3000);

'use strict';
const $ = id => document.getElementById(id);
const states = {running:'运行中',connecting:'连接中',reconnecting:'重连中',stopped:'已停止',eof:'播放结束',inference_error:'推理异常'};
const kinds = {target:'目标出现',video_fault:'视频故障',inference_fault:'推理故障',recovered:'恢复'};
let draft = null;
let lastEvent = 0;
let previewRetry = null;
let eventChannelsKey = '';
async function api(path, method='GET', body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {method, signal:controller.signal,
      headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  } finally { clearTimeout(timeout); }
}
function error(message, kind='action') {
  $('error').textContent = message;
  $('error').hidden = !message;
  $('error').dataset.kind = kind;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function number(value, suffix='') { return typeof value === 'number' ? value.toFixed(1) + suffix : '不可用'; }
async function poll() {
  try {
    const status = await api('/api/status');
    const enabled = status.channels.filter(c => c.enabled).length;
    $('channel-summary').textContent = `${status.max_channels} 路容量 · 已启用 ${enabled} 路`;
    $('preview-title').textContent = status.layout.video_tiles === 8 ? '八路实时分析 · 含设备摘要' : '四路实时分析';
    $('preview').alt = `${status.layout.video_tiles} 路实时检测画面`;
    const key = JSON.stringify(status.channels.map(c => [c.id,c.name]));
    if (eventChannelsKey !== key) {
      const selected = $('event-channel').value;
      const all = element('option','全部通道'); all.value = '-1';
      const options = status.channels.map(c => { const option = element('option',`通道 ${c.id+1} · ${c.name}`); option.value=c.id; return option; });
      $('event-channel').replaceChildren(all,...options); $('event-channel').value=selected || '-1';
      eventChannelsKey=key;
    }
    if ($('error').dataset.kind === 'connection') error('');
    $('connection').textContent = '设备在线'; $('connection').className = 'badge online';
    $('total-fps').textContent = number(status.channels.reduce((sum,c) => sum + c.detect_fps, 0));
    $('mosaic-fps').textContent = number(status.mosaic_fps);
    $('cpu').textContent = number(status.resources.cpu_pct, '%');
    $('memory').textContent = `内存 ${number(status.resources.memory_pct,'%')} · 进程 ${number(status.resources.rss_mb,' MB')}`;
    $('npu').textContent = (status.resources.npu_pct || []).map(value => number(value,'%')).join(' / ') || '不可用';
    $('temperature').textContent = `温度 ${number(status.resources.temperature_c,' °C')}`;
    $('storage').textContent = status.storage.error || `待写事件 ${status.storage.queued} · 丢弃事件 ${status.storage.dropped}`;
    const cards = status.channels.map(c => {
      const card = element('article', undefined, c.alarm ? 'channel alert' : 'channel');
      card.append(element('h3', c.name), element('p', `${states[c.state] || c.state}${c.alarm ? ' · 目标告警' : ''}`),
        element('p', `检测 ${number(c.detect_fps)} FPS / 解码 ${number(c.decode_fps)} FPS`),
        element('p', `${c.width} × ${c.height} · 处理耗时 ${number(c.latency_ms)} ms`),
        element('p', `完成 ${c.detected} 帧 · 覆盖 ${c.dropped} 帧`));
      if (c.error) card.append(element('p', c.error));
      const running = !['stopped','eof'].includes(c.state);
      const button = element('button', running ? '停止' : '启动');
      button.onclick = async () => {
        button.disabled = true;
        try { await api(`/api/channels/${c.id}/${running ? 'stop' : 'start'}`, 'POST'); error(''); }
        catch (e) { error(e.message); }
      };
      card.append(button);
      return card;
    });
    $('channels').replaceChildren(...cards);
  } catch (e) {
    $('connection').textContent = '连接中断'; $('connection').className = 'badge'; error(e.message, 'connection');
  } finally { setTimeout(poll, 1000); }
}
function field(label, value, type='text') {
  const node = element('label', label);
  const input = element('input'); input.type = type;
  if (type === 'checkbox') { input.checked = value; node.className = 'check'; } else input.value = value;
  node.append(input);
  return [node, input];
}
$('settings').onclick = async () => {
  try {
    draft = await api('/api/config');
    $('confidence').value = draft.confidence; $('nms-threshold').value = draft.nms;
    const forms = draft.channels.map(c => {
      const group = element('div', undefined, 'config-channel'); group.dataset.id = c.id;
      const [name, nameInput] = field(`通道 ${c.id + 1} 名称`, c.name); nameInput.dataset.key = 'name';
      const [source, sourceInput] = field('视频地址或文件路径', c.source); sourceInput.dataset.key = 'source';
      const [alarm, alarmInput] = field('告警类别，英文名称以逗号分隔', c.alarm_classes.join(',')); alarmInput.dataset.key = 'alarm_classes';
      const row = element('div',undefined,'form-row');
      const [enabled, enabledInput] = field('启用通道', c.enabled, 'checkbox'); enabledInput.dataset.key = 'enabled';
      const [loop, loopInput] = field('循环播放本地文件', c.loop, 'checkbox'); loopInput.dataset.key = 'loop';
      row.append(enabled, loop); group.append(name, source, alarm, row);
      return group;
    });
    $('config-channels').replaceChildren(...forms); $('config-error').textContent = ''; $('config-dialog').showModal();
  } catch(e) { error(e.message); }
};
$('close-config').onclick = () => $('config-dialog').close();
$('config-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    const next = structuredClone(draft);
    next.confidence = Number($('confidence').value); next.nms = Number($('nms-threshold').value);
    for (const group of $('config-channels').children) {
      const channel = next.channels.find(c => c.id === Number(group.dataset.id));
      for (const input of group.querySelectorAll('input')) {
        const key = input.dataset.key;
        channel[key] = input.type === 'checkbox' ? input.checked : key === 'alarm_classes' ? input.value.split(',').map(x => x.trim()).filter(Boolean) : input.value;
      }
    }
    await api('/api/config', 'PUT', next); $('config-dialog').close(); error('');
  } catch(e) { $('config-error').textContent = e.message; }
  finally { button.disabled = false; }
};
async function events(older=false) {
  try {
    const rows = await api(`/api/events?channel=${$('event-channel').value}&limit=30&before=${older ? lastEvent : 0}`);
    if (!older) $('events').replaceChildren();
    for (const row of rows) {
      const tr = element('tr');
      for (const text of [new Date(row.at * 1000).toLocaleString(), `通道 ${row.channel+1}`, kinds[row.kind] || row.kind, row.message]) tr.append(element('td',text));
      const cell = element('td');
      if (row.snapshot) { const link = element('a','查看截图'); link.href = row.snapshot; link.target = '_blank'; link.rel = 'noopener'; cell.append(link); }
      else cell.textContent = '—';
      tr.append(cell); $('events').append(tr);
    }
    if (rows.length) lastEvent = rows[rows.length-1].id;
    if (!$('events').children.length) { const row = element('tr'); const cell = element('td','暂无事件'); cell.colSpan=5; row.append(cell); $('events').append(row); }
    $('more-events').disabled = rows.length < 30;
  } catch(e) { error(e.message); }
}
$('refresh-events').onclick = () => events();
$('event-channel').onchange = () => events();
$('more-events').onclick = () => events(true);
$('preview').onerror = () => { clearTimeout(previewRetry); previewRetry = setTimeout(() => { $('preview').src = '/stream/mosaic.mjpg?t=' + Date.now(); }, 3000); };
poll(); events();

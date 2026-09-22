(() => {
if (window.xiaozhiAppearance) return;
let element = null;
// Gradio scopes CSS inside @media to .contain, so root layout rules must live
// in an unscoped stylesheet (the container cannot be its own descendant).
const responsive = document.createElement('style');
responsive.textContent = `@media (max-width: 960px) {
  html, body, gradio-app { height: auto !important; min-height: 100vh; overflow: visible !important; }
  .gradio-container, .gradio-container .main, .gradio-container .main.app,
  .gradio-container .main > .wrap, .gradio-container main.contain,
  .gradio-container main.contain > .column { height: auto !important; max-height: none !important; overflow: visible !important; }
}`;
document.head.append(responsive);
// gr.HTML lifecycle: initialize once, including when its settings tab is hidden.
const key = 'xiaozhi.appearance.v1';
const defaults = {version: 2, preset: 'aurora', brightness: 85, blur: 0, glass: 28, motion: true};
const gradients = {
  aurora: 'radial-gradient(ellipse at 12% 15%, #23987e 0, transparent 55%), radial-gradient(ellipse at 85% 80%, #5443ad 0, transparent 60%), linear-gradient(135deg, #183c49, #23274b)' ,
  ocean: 'radial-gradient(ellipse at 80% 20%, #117eae 0, transparent 60%), radial-gradient(ellipse at 10% 85%, #16385f 0, transparent 60%), linear-gradient(135deg, #123653, #172746)' ,
  dusk: 'radial-gradient(ellipse at 15% 20%, #c06a77 0, transparent 55%), radial-gradient(ellipse at 90% 80%, #61428f 0, transparent 60%), linear-gradient(135deg, #443046, #302443)' ,
  ink: 'linear-gradient(135deg, #151b29, #080b12)'
};
let settings = {...defaults};
try {
  const saved = JSON.parse(localStorage.getItem(key) || 'null');
  if (saved) {
    settings = {...defaults, ...saved};
    if (saved.version !== 2) settings = {...settings, version: 2, brightness: 85, blur: 0, glass: 28};
  }
} catch (_) {}
for (const [name, min, max] of [['brightness',30,100],['blur',0,16],['glass',10,85]]) {
  settings[name] = Number.isFinite(Number(settings[name])) ? Math.max(min, Math.min(max, Number(settings[name]))) : defaults[name];
}
if (!Object.hasOwn(gradients, settings.preset)) settings.preset = 'aurora';
let wall = document.getElementById('xiaozhi-wallpaper');
if (!wall) {
  wall = document.createElement('div'); wall.id = 'xiaozhi-wallpaper';
  wall.setAttribute('aria-hidden', 'true'); document.body.prepend(wall);
}
let scene = document.createElement('div'); scene.className = 'wallpaper-scene'; wall.replaceChildren(scene);
let media = null, objectURL = null, revision = 0;
const names = {aurora:'极光', ocean:'深海', dusk:'暮色', ink:'墨黑'};
const toast = document.createElement('div'); toast.id = 'appearance-toast';
toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite'); document.body.append(toast);
let message = '背景已就绪，调整即时生效。', toastTimer;
function showToast(text) {
  toast.textContent = text; toast.classList.add('visible'); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}
const status = (text, announce = false) => {
  message = text;
  if (element) element.querySelector('[data-status]').textContent = text;
  if (announce) showToast(text);
};
function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n ? n + ' B' : '';
}
function isVideo() {
  return settings.mediatype === 'video' || /\.(mp4|webm)$/i.test(settings.filename || '');
}
// 选完文件后 input 会被清空（否则再选同一个文件不会触发 change），
// 所以「选了什么」必须靠这块卡片留下来，否则用户只能靠背景变了才察觉。
function renderPicked() {
  const box = element?.querySelector('[data-picked]');
  if (!box) return;
  box.hidden = !settings.custom;
  if (!settings.custom) return;
  box.querySelector('[data-picked-kind]').textContent = isVideo() ? '视频' : '图片';
  box.querySelector('[data-picked-name]').textContent = settings.filename || '自定义背景';
  box.querySelector('[data-picked-size]').textContent = formatSize(settings.size);
}
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
const openDB = () => new Promise((resolve, reject) => {
  const request = indexedDB.open('xiaozhi-wallpaper', 1);
  request.onupgradeneeded = () => request.result.createObjectStore('media');
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
  request.onblocked = () => reject(new Error('背景存储被占用'));
});
async function database(action, value) {
  const db = await openDB();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('media', action === 'get' ? 'readonly' : 'readwrite');
      const store = tx.objectStore('media');
      const request = action === 'get' ? store.get('background') : action === 'put' ? store.put(value, 'background') : store.delete('background');
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('存储中断'));
    });
  } finally { db.close(); }
}
let writes = Promise.resolve();
function writeMedia(action, value) {
  const result = writes.catch(() => {}).then(() => database(action, value));
  writes = result; return result;
}
function playback() {
  if (!media || media.tagName !== 'VIDEO') return;
  if (settings.motion && !document.hidden && !reduced.matches) {
    media.play().catch(() => status('浏览器暂停了自动播放；可切换“播放动态背景”重试。'));
  } else media.pause();
}
function clearMedia() {
  if (media && media.tagName === 'VIDEO') { media.pause(); media.removeAttribute('src'); media.load(); }
  scene.replaceChildren(); media = null;
  if (objectURL) URL.revokeObjectURL(objectURL);
  objectURL = null;
}
function display(blob) {
  clearMedia();
  if (!blob) return;
  objectURL = URL.createObjectURL(blob);
  media = document.createElement(blob.type.startsWith('video/') ? 'video' : 'img');
  if (media.tagName === 'VIDEO') { media.muted = true; media.loop = true; media.playsInline = true; media.preload = 'auto'; }
  else { media.alt = ''; media.decoding = 'async'; }
  media.onerror = () => { ++revision; clearMedia(); settings.custom = false; settings.filename = ''; settings.size = 0; settings.mediatype = ''; apply(); writeMedia('delete').catch(() => {}); status('无法解码此文件，已恢复预设。请尝试 JPG、PNG 或 H.264 MP4。', true); };
  media.src = objectURL; scene.append(media); playback();
}
function apply(save = true) {
  const root = document.documentElement;
  root.style.setProperty('--wallpaper-brightness', settings.brightness / 100);
  root.style.setProperty('--wallpaper-blur', settings.blur + 'px');
  root.style.setProperty('--glass-opacity', settings.glass / 100);
  scene.style.backgroundImage = gradients[settings.preset];
  wall.dataset.motion = settings.motion && !reduced.matches ? 'on' : 'off';
  const badge = element?.querySelector('[data-motion-state]');
  if (badge) { badge.textContent = settings.motion ? '开' : '关'; badge.dataset.on = String(!!settings.motion); }
  if (element) {
  for (const input of element.querySelectorAll('[data-setting]')) {
    const name = input.dataset.setting;
    if (input.type === 'checkbox') input.checked = settings[name];
    else { input.value = settings[name]; element.querySelector(`[data-output="${name}"]`).textContent = settings[name] + (name === 'blur' ? ' px' : '%'); }
  }
  for (const button of element.querySelectorAll('[data-preset]')) button.setAttribute('aria-pressed', String(!media && button.dataset.preset === settings.preset));
  const preview = element.querySelector('[data-preview]');
  preview.style.backgroundImage = objectURL && media?.tagName === 'IMG' ? `url("${objectURL}")` : gradients[settings.preset];
  // 预览框也跟着漂：设置面板里当场能看出开关起了作用，不用盯着整页背景。
  preview.dataset.motion = wall.dataset.motion;
  element.querySelector('[data-current]').textContent = settings.custom ? '自定义 · ' + (settings.filename || '背景文件') : names[settings.preset] + ' · 内置背景';
  renderPicked();
  element.querySelector('[data-status]').textContent = message;
  }
  playback();
  if (save) { try { localStorage.setItem(key, JSON.stringify(settings)); } catch (_) { status('浏览器不允许保存设置，本次调整仍然有效。'); } }
}
function attach(container) {
  element = container;
  if (element.dataset.appearanceBound) { apply(false); return; }
  element.dataset.appearanceBound = 'true';
const labels = {brightness: ['背景亮度', '%'], blur: ['背景模糊', ' px'], glass: ['玻璃不透明度', '%'], motion: ['播放动态背景', '']};
function motionReport(announce) {
  const on = !!settings.motion, video = !!media && media.tagName === 'VIDEO';
  let text;
  if (!on) text = video ? '动态背景已关闭：视频已暂停，画面静止。' : '动态背景已关闭：背景停止漂移。';
  else if (reduced.matches) text = '动态背景已开启，但系统偏好「减少动态效果」生效中，画面保持静止。';
  else if (video) text = '动态背景已开启：视频正在循环播放。';
  else text = '动态背景已开启：背景开始缓慢漂移。上传视频可换成真正的动态画面。';
  status(text, announce);
}
for (const input of element.querySelectorAll('[data-setting]')) input.addEventListener('input', () => {
  const name = input.dataset.setting;
  settings[name] = input.type === 'checkbox' ? input.checked : Number(input.value);
  apply();
  // 复选开关必须有明确开/关回执；滑块拖动时不弹 Toast，避免刷屏。
  if (input.type === 'checkbox') { motionReport(true); return; }
  status(`${labels[name]?.[0] || name} 已调整为 ${settings[name]}${labels[name]?.[1] || ''}。`);
});
for (const button of element.querySelectorAll('[data-preset]')) button.addEventListener('click', async () => {
  ++revision; clearMedia(); settings.preset = button.dataset.preset; settings.custom = false; settings.filename = ''; settings.size = 0; settings.mediatype = ''; apply();
  status(`已切换为「${names[settings.preset]}」背景`, true);
  try { await writeMedia('delete'); } catch (_) { status('预设已保存；旧媒体缓存未能清理。'); }
});
element.querySelector('input[type=file]').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  const video = ['video/mp4', 'video/webm'].includes(file.type);
  const kind = video ? '视频' : '图片', size = formatSize(file.size);
  if ((!video && !['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)) || file.size > (video ? 100 : 20) * 1024 * 1024) {
    status(`未采用「${file.name}」：格式不支持或${kind}大小超限（图片 ≤ 20 MB，视频 ≤ 100 MB）。`, true); event.target.value = ''; return;
  }
  const current = ++revision;
  display(file);
  settings.custom = true; settings.filename = file.name; settings.size = file.size; settings.mediatype = video ? 'video' : 'image';
  apply(false);
  status(`已选择${kind}「${file.name}」${size ? '（' + size + '）' : ''}，正在应用…`, true);
  try {
    await writeMedia('put', file);
    if (current !== revision) return;
    apply();
    status(`已应用${kind}「${file.name}」并保存，重开页面也会恢复。`, true);
  } catch (_) { if (current === revision) status(`「${file.name}」已应用到当前页面，但保存失败；请尝试较小文件。`, true); }
  event.target.value = '';
});
element.querySelector('[data-clear-media]').addEventListener('click', async () => {
  const name = settings.filename || '自定义背景';
  ++revision; clearMedia();
  settings.custom = false; settings.filename = ''; settings.size = 0; settings.mediatype = '';
  apply();
  status(`已移除「${name}」，恢复为「${names[settings.preset]}」内置背景。`, true);
  try { await writeMedia('delete'); } catch (_) {}
});
element.querySelector('[data-reset]').addEventListener('click', async () => {
  ++revision; clearMedia(); settings = {...defaults}; apply(); status('已恢复默认外观。', true);
  try { await writeMedia('delete'); } catch (_) { status('已恢复默认外观；旧媒体缓存未能清理。'); }
});
apply(false);
}
window.xiaozhiAppearance = {attach, notify: showToast};
document.dispatchEvent(new Event('xiaozhi-appearance-ready'));
document.addEventListener('visibilitychange', playback);
reduced.addEventListener('change', () => apply(false));
apply(false);
const initialRevision = revision;
if (settings.custom) database('get').then(blob => {
  if (initialRevision !== revision) return;
  if (blob) { display(blob); apply(false); status(`已恢复${isVideo() ? '视频' : '图片'}背景「${settings.filename || '自定义背景'}」。`, true); return; }
  // 缓存没了就别再挂着文件名，否则「已选择 xxx」会指向一个不存在的背景。
  const gone = settings.filename || '上次的背景文件';
  settings.custom = false; settings.filename = ''; settings.size = 0; settings.mediatype = '';
  apply();
  status(`「${gone}」已被浏览器清理，已恢复为「${names[settings.preset]}」内置背景，请重新上传。`, true);
}).catch(() => status('无法读取背景存储，当前使用内置预设。'));
else status('外观调整即时生效，自动保存。');

})();

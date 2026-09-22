const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/appearance.js'), 'utf8');

class Element {
  constructor(tag = 'div', data = {}) {
    this.tagName = tag.toUpperCase(); this.dataset = data; this.events = {};
    this.children = []; this.attributes = {}; this.style = {setProperty(k, v) {this[k] = v;}};
    this.value = ''; this.textContent = ''; this.paused = true; this.classList = {add() {},remove() {}};
  }
  addEventListener(name, fn) { this.events[name] = fn; }
  dispatchEvent(event) { return this.events[event.type]?.(event); }
  async dispatch(name) { return this.events[name]?.({target: this}); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this[name]; }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
  prepend(child) { this.children.unshift(child); }
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; }
  load() {}
}

function harness(saved = null, storedBlob = null) {
  const controls = ['brightness', 'blur', 'glass', 'motion'].map(name => {
    const e = new Element('input', {setting: name}); e.type = name === 'motion' ? 'checkbox' : 'range'; return e;
  });
  const presets = ['aurora', 'ocean', 'dusk', 'ink'].map(preset => new Element('button', {preset}));
  const outputs = Object.fromEntries(['brightness','blur','glass'].map(k => [k, new Element('output')]));
  const preview = new Element(), current = new Element();
  const status = new Element('p'), file = new Element('input'), reset = new Element('button');
  const element = new Element();
  element.querySelectorAll = s => s === '[data-setting]' ? controls : presets;
  element.querySelector = s => s === '[data-preview]' ? preview : s === '[data-current]' ? current : s === '[data-status]' ? status : s === 'input[type=file]' ? file : s === '[data-reset]' ? reset : outputs[s.match(/"(.*?)"/)[1]];
  const document = new Element(); document.body = new Element(); document.head = new Element(); document.documentElement = new Element(); document.hidden = false;
  document.createElement = tag => new Element(tag);
  document.getElementById = id => document.body.children.find(e => e.id === id);
  const reduced = new Element(); reduced.matches = false;
  let persisted = saved, blob = storedBlob;
  const localStorage = {getItem: () => persisted, setItem: (_, v) => {persisted = v;}};
  const db = {close() {}, transaction() {
    const tx = {objectStore() {return {
      get() {return {result: blob};},
      put(value) {blob = value; return {};},
      delete() {blob = null; return {};}
    };}};
    setImmediate(() => tx.oncomplete()); return tx;
  }};
  const indexedDB = {open() {const request = {result: db}; setImmediate(() => request.onsuccess()); return request;}};
  const revoked = [];
  const context = {Event, setTimeout: () => 0, clearTimeout() {}, element, document, localStorage, indexedDB, window: {matchMedia: () => reduced},
    URL: {createObjectURL: () => 'blob:local-test', revokeObjectURL: url => revoked.push(url)}, console};
  vm.runInNewContext(source, context);
  const startupWithoutSettings = !!document.getElementById('xiaozhi-wallpaper');
  context.window.xiaozhiAppearance.attach(element);
  return {startupWithoutSettings, controls, presets, outputs, status, file, reset, document, reduced, revoked,
    saved: () => JSON.parse(persisted || '{}'), blob: () => blob,
    scene: () => document.getElementById('xiaozhi-wallpaper').children[0]};
}

test('defaults and slider changes persist immediately', async () => {
  const h = harness();
  assert.equal(h.document.documentElement.style['--glass-opacity'], .28);
  h.controls[0].value = '60'; await h.controls[0].dispatch('input');
  assert.equal(h.saved().brightness, 60);
  assert.equal(h.outputs.brightness.textContent, '60%');
});

test('corrupt stored settings fall back and out-of-range values clamp', () => {
  assert.equal(harness('{bad').controls[0].value, 85);
  const h = harness(JSON.stringify({version: 2, brightness: 999, blur: -10, glass: 'bad'}));
  assert.equal(h.controls[0].value, 100); assert.equal(h.controls[1].value, 0); assert.equal(h.controls[2].value, 28);
});

test('oversized and unsupported uploads do not replace the background', async () => {
  const h = harness();
  for (const file of [{type:'video/mp4',size:101*1024*1024},{type:'text/html',size:100}]) {
    h.file.files = [file]; await h.file.dispatch('change');
    assert.equal(h.blob(), null); assert.match(h.status.textContent, /超限/);
  }
});

test('image saves, preset removes image, reset restores defaults', async () => {
  const h = harness();
  h.file.files = [{type:'image/png', size:100}]; await h.file.dispatch('change');
  assert.equal(h.scene().children[0].tagName, 'IMG'); assert.equal(h.saved().custom, true);
  await h.presets[1].dispatch('click'); assert.equal(h.saved().preset, 'ocean');
  assert.equal(h.blob(), null); assert.equal(h.scene().children.length, 0); assert.equal(h.revoked.length, 1);
  await h.reset.dispatch('click'); assert.equal(h.saved().preset, 'aurora');
});

test('video loops silently and pauses on visibility, setting and reduced motion', async () => {
  const h = harness(); h.file.files = [{type:'video/webm',size:100}]; await h.file.dispatch('change');
  const video = h.scene().children[0];
  assert.ok(video.loop && video.muted && video.playsInline); assert.equal(video.paused, false);
  h.document.hidden = true; await h.document.dispatch('visibilitychange'); assert.equal(video.paused, true);
  h.document.hidden = false; await h.document.dispatch('visibilitychange'); assert.equal(video.paused, false);
  h.reduced.matches = true; await h.reduced.dispatch('change'); assert.equal(video.paused, true);
  h.reduced.matches = false; h.controls[3].checked = false; await h.controls[3].dispatch('input'); assert.equal(video.paused, true);
});

test('custom media restores on initialization', async () => {
  const h = harness(JSON.stringify({custom:true}), {type:'image/jpeg',size:100});
  await new Promise(resolve => setImmediate(() => setImmediate(resolve)));
  assert.equal(h.scene().children[0].tagName, 'IMG');
});


test('wallpaper exists before opening settings and old dim defaults migrate', () => {
  const h = harness(JSON.stringify({brightness:35, glass:65, blur:4, preset:'ocean'}));
  assert.ok(h.startupWithoutSettings);
  assert.equal(h.controls[0].value,85);
  assert.equal(h.controls[2].value,28);
  assert.match(h.scene().style.backgroundImage,/linear-gradient/);
});

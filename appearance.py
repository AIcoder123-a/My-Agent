"""Browser-local wallpaper settings; media never enters model context."""
from pathlib import Path

APPEARANCE_HTML = """
<section class="appearance-controls">
  <h3>让工作台更像你</h3>
  <p>选择后立即铺满工作台，自动保存到当前浏览器。</p>
  <div class="wallpaper-preview" data-preview><span data-current>正在恢复背景…</span></div>
  <label class="motion-toggle"><input type="checkbox" data-setting="motion" /> 播放动态背景 <b data-motion-state>开</b></label>
  <p class="appearance-status" role="status" aria-live="polite" data-status>正在加载外观设置…</p>
  <div class="wallpaper-presets" role="group" aria-label="背景预设">
    <button type="button" data-preset="aurora"><i></i>极光</button>
    <button type="button" data-preset="ocean"><i></i>深海</button>
    <button type="button" data-preset="dusk"><i></i>暮色</button>
    <button type="button" data-preset="ink"><i></i>墨黑</button>
  </div>
  <label class="wallpaper-upload">上传图片或视频
    <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm" />
  </label>
  <div class="wallpaper-picked" data-picked hidden>
    <span class="picked-kind" data-picked-kind>图片</span>
    <span class="picked-name" data-picked-name></span>
    <span class="picked-size" data-picked-size></span>
    <button type="button" data-clear-media>移除</button>
  </div>
  <p>图片 ≤ 20 MB；MP4 / WebM ≤ 100 MB。推荐 1920 × 1080、30 秒内视频，静音循环播放。</p>
  <label>背景亮度 <output data-output="brightness"></output>
    <input aria-label="背景亮度" type="range" data-setting="brightness" min="30" max="100" step="1" />
  </label>
  <label>背景模糊 <output data-output="blur"></output>
    <input aria-label="背景模糊" type="range" data-setting="blur" min="0" max="16" step="1" />
  </label>
  <label>玻璃不透明度 <output data-output="glass"></output>
    <input aria-label="玻璃不透明度" type="range" data-setting="glass" min="10" max="85" step="1" />
  </label>
  <button type="button" data-reset>恢复默认背景</button>
</section>
"""

APPEARANCE_CSS = """
.appearance-controls { color: #e8eaf1; line-height: 1.65; }
.appearance-controls h3 { margin: 0 0 8px; font-size: 20px; letter-spacing: -.4px; }
.appearance-controls p { color: #a8afc0; font-size: 12px; margin: 8px 0 18px; }
.appearance-controls label { display: block; margin: 16px 0; font-size: 13px; }
.appearance-controls output { float: right; color: #77ddc4; }
.appearance-controls input[type=range] { width: 100%; accent-color: #5ad9b5; margin-top: 10px; }
.appearance-controls input[type=file] { display: block; width: 100%; margin-top: 10px; font-size: 12px; }
.appearance-controls input[type=checkbox] { accent-color: #5ad9b5; }
.appearance-controls button { border: 1px solid #ffffff25; border-radius: 10px; padding: 9px 13px; color: #e8eaf1; background: #ffffff0a; cursor: pointer; }
.appearance-controls button:hover, .appearance-controls button[aria-pressed=true] { border-color: #5ad9b5; background: #5ad9b51c; }
.appearance-controls button:focus-visible, .appearance-controls input:focus-visible { outline: 2px solid #77ddc4; outline-offset: 3px; }
.wallpaper-presets { display: flex; gap: 8px; flex-wrap: wrap; }
.wallpaper-upload { border: 1px dashed #ffffff30; border-radius: 14px; padding: 16px; background: #ffffff04; }
/* 已选择文件条：选完文件后 input 会被清空，文件名也随之消失，
   没有这块就等于"选了没反应"，只能靠背景变了才察觉。 */
.wallpaper-picked { display: flex; align-items: center; gap: 8px; margin: -6px 0 14px; padding: 9px 11px; border: 1px solid #5ad9b545; border-radius: 11px; background: #5ad9b514; font-size: 12px; animation: picked-in .18s ease-out; }
.wallpaper-picked[hidden] { display: none !important; }
@keyframes picked-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
.wallpaper-picked .picked-kind { flex: 0 0 auto; padding: 1px 8px; border-radius: 999px; background: #5ad9b52e; color: #9ff0d9; font-size: 11px; }
.wallpaper-picked .picked-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #e8eaf1; }
.wallpaper-picked .picked-size { flex: 0 0 auto; color: #9aa6b8; font-size: 11px; }
.wallpaper-picked button { flex: 0 0 auto !important; padding: 3px 10px !important; font-size: 11px !important; border-radius: 8px !important; line-height: 1.5 !important; }
.wallpaper-preview { height: 112px; border-radius: 14px; background-size: cover; background-position: center; border: 1px solid #ffffff30; position: relative; overflow: hidden; }
.wallpaper-preview span { position: absolute; bottom: 0; left: 0; right: 0; padding: 18px 12px 10px; background: linear-gradient(transparent,#000b); color: white; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.appearance-controls .appearance-status { color: #a9f0dc; background: #102a2588; border: 1px solid #64dfbc30; border-radius: 10px; padding: 9px 12px; margin: 10px 0 14px; }
.wallpaper-presets { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; }
.wallpaper-presets button { padding: 6px !important; font-size: 12px !important; height: auto !important; }
.wallpaper-presets i { display: block; height: 32px; border-radius: 6px; margin-bottom: 5px; }
[data-preset=aurora] i { background: linear-gradient(125deg,#25927b,#58479b); }
[data-preset=ocean] i { background: linear-gradient(125deg,#167695,#152e5e); }
[data-preset=dusk] i { background: linear-gradient(125deg,#b26983,#594575); }
[data-preset=ink] i { background: linear-gradient(125deg,#34435c,#111827); }
.wallpaper-presets button[aria-pressed=true] { outline: 2px solid #77ddc4; outline-offset: 1px; }
/* 动态背景开关：状态徽标让"点了没生效"一眼可见 */
/* 动态背景开关：紧贴预览框下方，点了立刻能看到预览漂移 + 徽标变化 */
.motion-toggle { display: flex !important; align-items: center; gap: 9px; margin: 13px 0 2px !important; }
/* 原生复选框换成开关：默认那种深色小方块在暗色主题下"开"和"关"几乎一样，
   而这正是用户抱怨"点了不知道有没有生效"的根源之一。 */
.motion-toggle input[type=checkbox] {
  appearance: none !important; -webkit-appearance: none !important;
  position: relative !important; flex: 0 0 auto !important;
  width: 34px !important; height: 19px !important; margin: 0 !important;
  border-radius: 999px !important; border: 1px solid #ffffff2e !important;
  background: #ffffff1a !important;
  cursor: pointer !important; transition: background .18s, border-color .18s;
}
.motion-toggle input[type=checkbox]::after {
  content: "" !important; position: absolute !important; top: 2px !important; left: 2px !important;
  width: 13px !important; height: 13px !important; border-radius: 50% !important;
  background: #c3ccda !important; transition: transform .18s, background .18s;
}
/* 这两条必须带 !important：Gradio 主题自己给 checkbox 的 background 加了 !important，
   不加就永远看不到"开"的绿色——正是用户说的"点了不知道有没有生效"。 */
.motion-toggle input[type=checkbox]:checked { background: #5ad9b5 !important; border-color: #5ad9b5 !important; }
.motion-toggle input[type=checkbox]:checked::after { transform: translateX(15px) !important; background: #062a22 !important; }
.motion-toggle input[type=checkbox]:focus-visible { outline: 2px solid #77ddc4 !important; outline-offset: 3px !important; }
.motion-toggle b { margin-left: auto; min-width: 30px; text-align: center; font-size: 11px; font-weight: 650; border-radius: 999px; padding: 2px 9px; border: 1px solid #ffffff30; color: #b9c3d2; background: #ffffff08; transition: background .16s, color .16s, border-color .16s; }
.motion-toggle b[data-on=true] { color: #052b23; background: #5ad9b5; border-color: #5ad9b5; box-shadow: 0 0 12px #5ad9b555; }
/* 预览框跟着漂移：开关有没有生效，在设置面板里就能立刻看出来 */
.wallpaper-preview { transition: background-position .7s ease; background-position: center; }
.wallpaper-preview[data-motion=on] { animation: xiaozhi-pan 18s ease-in-out infinite alternate; }
@keyframes xiaozhi-pan { from { background-position: 34% 56%; } to { background-position: 66% 40%; } }
"""

APPEARANCE_JS = (Path(__file__).parent / 'assets' / 'appearance.js').read_text(encoding='utf-8')

GLASS_CSS = """
#xiaozhi-wallpaper { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; background: #070b13; }
#xiaozhi-wallpaper .wallpaper-scene { position: absolute; inset: -20px; background-size: cover; background-position: center; filter: blur(var(--wallpaper-blur, 0px)) brightness(var(--wallpaper-brightness, .85)); }
#xiaozhi-wallpaper img, #xiaozhi-wallpaper video { width: 100%; height: 100%; object-fit: cover; }
/* 动态背景开启时，预设渐变也做缓慢漂移——
   否则"播放动态背景"在没有视频时点了看不出任何变化。 */
#xiaozhi-wallpaper .wallpaper-scene { transition: transform .8s ease; will-change: transform; }
#xiaozhi-wallpaper[data-motion=on] .wallpaper-scene { animation: xiaozhi-drift 26s ease-in-out infinite alternate; }
@keyframes xiaozhi-drift {
  from { transform: scale(1.04) translate3d(0, 0, 0); }
  to { transform: scale(1.15) translate3d(-2.4%, -1.8%, 0); }
}
body { background: #070b13 !important; }
.gradio-container { position: relative; z-index: 1; background: transparent !important; }
#topbar, #left-panel, #right-panel, #center-panel { background: rgba(12,17,27,var(--glass-opacity,.28)) !important; backdrop-filter: blur(3px) saturate(115%); -webkit-backdrop-filter: blur(3px) saturate(115%); border: 1px solid rgba(222,238,255,.16) !important; box-shadow: 0 14px 40px #00000020, inset 0 1px 0 #ffffff0c !important; }
#left-panel, #right-panel { background: rgba(12,17,27,calc(var(--glass-opacity,.28) + .18)) !important; }
.gradio-container::after, .gradio-container::before { display: none !important; }
#app-shell { gap: 10px !important; padding: 10px !important; box-sizing: border-box; }
#left-panel { border-radius: 18px; }
#topbar { align-items: center !important; padding: 8px 20px !important; flex-basis: 70px !important; }
#topbar > .column { gap: 2px !important; }
#context-badge { text-align: right; }
#app-shell { height: calc(100vh - 70px) !important; max-height: calc(100vh - 70px) !important; }
#appearance-toast { position: fixed; left: 50%; bottom: 28px; transform: translate(-50%, 12px); z-index: 10000; max-width: min(520px,90vw); padding: 12px 20px; border: 1px solid #80e0c260; border-radius: 14px; color: #e9fff8; background: #102822f2; box-shadow: 0 10px 40px #0005; font: 14px/1.6 system-ui; opacity: 0; transition: opacity .18s, transform .18s; pointer-events: none; }
#appearance-toast.visible { opacity: 1; transform: translate(-50%,0); }
/* Measurement buttons must match live tab widths, or Gradio hides useful tabs. */
#right-panel .tab-container button { font-size: 11px !important; padding: 0 5px !important; min-width: 0 !important; flex: 0 0 auto !important; letter-spacing: 0 !important; }
#right-panel .tab-container:not(.visually-hidden) { gap: 1px !important; justify-content: space-between; }
#right-panel .tabitem[style*="display: none"] { display: none !important; }
#right-panel [role=tabpanel] { animation: panel-enter .16s ease-out; }
@keyframes panel-enter { from { opacity: .5; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
#welcome h2, #welcome p { text-shadow: 0 2px 12px #000b; }
#welcome .welcome-card { background: rgba(15,23,37,.65) !important; }
#welcome .welcome-card:active { transform: scale(.98); }
#brand-title p, #welcome p, #trace-hint p, #composer-hint { color: #c8d0dc !important; }
.inspector-heading, .conversation-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #e5ecf5; }
.inspector-heading { padding: 2px 2px 8px; }
.inspector-heading span { font-size: 15px; font-weight: 650; letter-spacing: .04em; }
.inspector-heading small, .conversation-heading small { color: #abb9ca; font-size: 11px; font-weight: 400; }
.conversation-heading { padding: 19px 26px; border-bottom: 1px solid #ffffff0c; font-size: 13px; }
#left-panel { width: 224px !important; min-width: 224px !important; max-width: 224px !important; flex: 0 0 224px !important; padding: 16px 12px !important; gap: 9px !important; }
#left-panel .section-label { margin-top: 12px; color: #b7c2d2; }
#clear-display-button { background: transparent !important; box-shadow: none !important; border-color: transparent !important; color: #bac6d7 !important; }
#clear-display-button:hover { background: #ffffff0a !important; }
#right-panel { padding: 18px 16px !important; gap: 12px !important; }
#inspector-tabs > .tab-wrapper .tab-container:not(.visually-hidden) button { font-size: 13px !important; flex: 1 1 auto !important; height: 34px !important; padding: 0 10px !important; }
#inspector-tabs > .tab-wrapper .tab-container.visually-hidden button { font-size: 13px !important; padding: 0 10px !important; }
#welcome .welcome-title { font-size: clamp(26px,2.5vw,36px) !important; line-height: 1.3; font-weight: 600; letter-spacing: -.045em; }
#welcome .welcome-sub { font-size: 14px; line-height: 1.85; max-width: 460px; }
#welcome .welcome-mark { width: 46px; height: 46px; border-radius: 16px; background: linear-gradient(145deg,#86e7c5,#1aa88b); box-shadow: 0 8px 30px #2ec9a52e; }
#welcome .welcome-mark::before { content: none; font-size: 25px; color: #083f35; }
#welcome .welcome-mark::after { content: "✦" !important; color: #083f35; }
#welcome .welcome-grid { gap: 12px; margin-top: 28px; }
#welcome .welcome-card { border-color: #ffffff24 !important; box-shadow: 0 8px 20px #00000014 !important; padding: 17px !important; }
#welcome .welcome-card:hover { background: rgba(30,46,61,.82) !important; border-color: #86e7c570 !important; transform: translateY(-2px); }
#composer-shell { padding: 12px !important; margin-bottom: 4px !important; border-color: #ffffff30 !important; background: rgba(18,28,43,.6) !important; box-shadow: 0 12px 36px #0003 !important; }
#message-composer textarea { background: transparent !important; box-shadow: none !important; color: #f1f5fb !important; }
#message-composer textarea::placeholder { color: #abb8ca !important; }
#trace-table:has(tbody:empty) { opacity: .6; }
button:disabled { cursor: not-allowed !important; }
#chatbot .message-row .md.chatbot.prose { background: rgba(12,22,35,.7) !important; border: 1px solid #ffffff16; border-radius: 14px; padding: 14px 18px !important; }
#topbar { border-radius: 0 0 18px 18px; }
#center-panel { border-radius: 20px; }
#right-panel { border-radius: 18px; }
#chatbot, #chat-stage { background: transparent !important; }
#chatbot .message { line-height: 1.75; }
button:focus-visible { outline: 2px solid #74e2c3 !important; outline-offset: 3px; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; transition-duration: .01ms !important; } }
@media (max-width: 960px) {
  #topbar { flex: 0 0 auto !important; height: auto !important; min-height: 100px !important; flex-direction: column !important; gap: 8px !important; align-items: stretch !important; }
  #topbar > .column { min-width: 0 !important; width: 100% !important; }
  #runtime-status, #context-badge { text-align: left !important; }
  #welcome { position: relative !important; inset: auto !important; transform: none !important; padding: 28px 20px !important; }
  #welcome .welcome-title { font-size: 26px !important; }
  #composer-hint-row { flex-wrap: wrap !important; padding: 8px 16px 14px !important; }
  .conversation-heading { padding: 16px 20px; }
  .conversation-heading small { display: none; }
  .gradio-container { height: auto !important; min-height: 100vh !important; overflow: visible !important; }
  .gradio-container > .main, .gradio-container .main.app, .gradio-container .main > .wrap,
  .gradio-container main.contain, .gradio-container main.contain > .column { height: auto !important; max-height: none !important; overflow: visible !important; }
  #app-shell { flex-wrap: wrap !important; height: auto !important; max-height: none !important; overflow: visible !important; gap: 12px !important; }
  #left-panel, #center-panel, #right-panel { display: flex !important; min-width: 0 !important; width: 100% !important; max-width: none !important; flex-basis: 100% !important; height: auto !important; max-height: none !important; }
  #center-panel { order: 0; min-height: 75vh !important; }
  #left-panel { order: 1; max-height: 360px !important; overflow: auto !important; }
  #right-panel { order: 2; min-height: 480px !important; }
  #chat-stage { min-height: 280px !important; }
}
"""

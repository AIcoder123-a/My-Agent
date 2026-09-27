"""前端静态资产：CSS 与 JS。

这些是纯字符串常量，没有任何业务逻辑。
单独成模块后，gui.py 里只剩界面组装。
"""

import json

from pathlib import Path

from appearance import APPEARANCE_JS, GLASS_CSS

PROJECT_ROOT = Path(__file__).resolve().parent

ASSETS_DIR = PROJECT_ROOT / "assets"

# ============================================================
# CSS
# ============================================================

# 主样式表放在 assets/app.css。
# 原先它是 1800 行字面量，横在 GUI 模块的中间。

APP_CSS = (
    ASSETS_DIR / "app.css"
).read_text(
    encoding="utf-8",
)

CSS = APP_CSS

# ============================================================
# UI
# ============================================================

# ============================================================
# 右栏 Tab 修正
#
# 必须追加在 GLASS_CSS 之后：GLASS_CSS 用 !important 且在其之前，
# 写在主 CSS 里会被它整段压掉。
# ============================================================

RIGHT_PANEL_FIX_CSS = """
/* ------------------------------------------------------------
   关键修复：禁止面板换列
   Gradio 的 .column 自带 flex-wrap: wrap，而三栏面板是
   flex-direction: column + 固定高度 + overflow-x: hidden。
   于是当某个 Tab 的内容比面板高时，溢出部分会被"换行"到
   右侧新起的一列 → 整块被 overflow-x 裁掉 → 面板一片空白。
   表现：右栏只有第一个 Tab 有内容，切到其余 Tab 全白。

   强制 nowrap，让内容改走纵向滚动。
   ------------------------------------------------------------ */

#left-panel,
#center-panel,
#right-panel {
    flex-wrap: nowrap !important;
}

/* 面板子项禁止被压缩。
   面板是固定高度的 flex 列容器，内容一超高，
   flex-shrink 会把子项压扁；而 Gradio 的块内部是
   position:absolute（靠 --start-* 变量定位），
   压扁后内层不会跟着缩，直接和相邻块叠在一起
   （表现：标题被 Tab 栏压住）。
   正确行为是内容溢出 → 交给 overflow-y 滚动。 */
#left-panel > *,
#right-panel > * {
    flex-shrink: 0 !important;
}

/* 面板内的 Tab 容器不该被压缩或撑宽 */
#right-panel > .tabs,
#settings-shell > .tabs {
    flex: 0 0 auto !important;
    min-width: 0 !important;
    max-width: 100% !important;
}

/* Gradio 用一组隐藏的"测量按钮"判断 Tab 是否放得下；
   测量按钮与真实按钮宽度不一致时，它会判定放不下
   并把整排 Tab 折叠成下拉框（视觉上就是"Tab 不见了"）。
   这里把右栏所有 Tab（含设置页里嵌套的那层）
   统一压到同一尺寸，让测量值与真实值一致。 */
#right-panel .tab-container button,
#right-panel .tabs .tab-container button {
    font-size: 12px !important;
    padding: 0 8px !important;
    min-width: 0 !important;
    flex: 0 1 auto !important;
}

#right-panel .tab-container:not(.visually-hidden),
#right-panel .tabs .tab-container:not(.visually-hidden) {
    gap: 2px !important;
    flex-wrap: nowrap !important;
    overflow: visible !important;
}

#right-panel .tabitem { min-height: 0; }
"""

# ============================================================
# 输入框「+」菜单 / 拖拽上传
#
# 放在 GLASS_CSS 之后追加：GLASS_CSS 全量 !important，
# 写在主 CSS 里会被整段压掉。
# ============================================================

COMPOSER_MENU_HTML = r"""
<div class="composer-menu" data-menu hidden>
  <button type="button" data-action="upload">
    <span class="cm-icon">＋</span>
    <span class="cm-text">上传附件<small>导入工作区，路径写入输入框</small></span>
  </button>
  <button type="button" data-action="folder">
    <span class="cm-icon">▤</span>
    <span class="cm-text">导入本地文件夹<small>填写绝对路径后导入</small></span>
  </button>
  <div class="cm-folder" data-folder hidden>
    <input type="text" placeholder="D:\projects\demo" data-folder-input />
    <button type="button" data-action="folder-go">导入</button>
  </div>
  <div class="cm-sep"></div>
  <button type="button" data-action="open">
    <span class="cm-icon">▢</span>
    <span class="cm-text">打开工作区目录</span>
  </button>
  <button type="button" data-action="export">
    <span class="cm-icon">↧</span>
    <span class="cm-text">导出当前对话</span>
  </button>
  <button type="button" data-action="clear">
    <span class="cm-icon">⌫</span>
    <span class="cm-text">清空当前显示</span>
  </button>
  <button type="button" data-action="plugins">
    <span class="cm-icon">⬡</span>
    <span class="cm-text">插件管理</span>
  </button>
</div>
"""

COMPOSER_EXTRA_CSS = """
/* 由「+」「×」这类纯符号按钮触发的隐藏组件，
   不能用 visible=False（Gradio 会直接把它移出 DOM，
   JS 就拿不到它了），只能靠 CSS 藏。 */
.xiaozhi-hidden { display: none !important; }

#composer-shell { position: relative; }

/* 「+」按钮：与发送按钮同高，贴着输入框左下角 */
#composer-plus {
    flex: 0 0 auto !important;
    width: 38px !important;
    min-width: 38px !important;
    height: 38px !important;
    min-height: 38px !important;
    padding: 0 !important;
    align-self: flex-end !important;
    border-radius: var(--r-md) !important;
}
#composer-plus {
    color: var(--tx-2) !important;
    border: 1px solid var(--hair) !important;
    background: var(--glass) !important;
    box-shadow: none !important;
    transition: border-color .16s ease, color .16s ease,
                background .16s ease, transform .16s ease;
}
#composer-plus:hover,
#composer-plus.menu-open {
    border-color: var(--accent-bd) !important;
    background: var(--accent-soft) !important;
    color: var(--accent-hi) !important;
}
#composer-plus.menu-open { transform: rotate(45deg); }
#composer-plus button,
#composer-plus > button {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    padding: 0 !important;
    font-size: 20px !important;
    line-height: 1 !important;
    justify-content: center !important;
    border-radius: var(--r-md) !important;
}

/* 菜单本体：锚在 composer-shell 上，向上弹出 */
.composer-menu {
    position: absolute;
    left: 46px;
    bottom: 62px;
    z-index: 60;
    width: 272px;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    border: 1px solid var(--bd-2);
    border-radius: var(--r-lg);
    background: rgba(14, 20, 31, .92);
    backdrop-filter: blur(20px) saturate(175%);
    -webkit-backdrop-filter: blur(20px) saturate(175%);
    box-shadow: 0 20px 54px rgba(0, 0, 0, .55);
    animation: cm-pop .14s ease-out;
}
@keyframes cm-pop {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}
.composer-menu[hidden],
.composer-menu .cm-folder[hidden] { display: none !important; }

.composer-menu button {
    display: flex !important;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 9px 10px;
    border: none !important;
    background: transparent !important;
    color: var(--tx-2) !important;
    font-size: 13px !important;
    font-family: var(--sans) !important;
    text-align: left !important;
    cursor: pointer;
    border-radius: var(--r-sm) !important;
    box-shadow: none !important;
    min-height: 0 !important;
    height: auto !important;
    justify-content: flex-start !important;
}
.composer-menu button:hover {
    background: var(--panel-2) !important;
    color: var(--tx) !important;
}
.composer-menu .cm-icon {
    flex: 0 0 auto;
    width: 23px;
    height: 23px;
    display: grid;
    place-items: center;
    border-radius: 7px;
    border: 1px solid var(--hair);
    background: var(--glass-2);
    font-size: 12px;
}
.composer-menu .cm-text { display: flex; flex-direction: column; gap: 2px; }
.composer-menu .cm-text small { color: var(--faint); font-size: 11px; line-height: 1.4; }
.composer-menu .cm-sep { height: 1px; background: var(--hair); margin: 4px 6px; }

.composer-menu .cm-folder {
    display: flex;
    gap: 6px;
    padding: 6px 8px 8px;
}
.composer-menu .cm-folder input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 6px 9px;
    border: 1px solid var(--bd) !important;
    border-radius: var(--r-sm);
    background: var(--panel) !important;
    color: var(--tx) !important;
    font-family: var(--mono);
    font-size: 12px;
}
.composer-menu .cm-folder input:focus {
    border-color: var(--accent-bd) !important;
    box-shadow: 0 0 0 3px var(--accent-soft) !important;
}
.composer-menu .cm-folder button {
    width: auto !important;
    flex: 0 0 auto;
    padding: 6px 13px !important;
    border: 1px solid var(--accent-bd) !important;
    background: var(--accent-soft) !important;
    color: var(--accent-hi) !important;
    font-size: 12px !important;
}

/* 拖拽上传覆盖层 */
#xiaozhi-dropzone {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: none;
    align-items: center;
    justify-content: center;
    background: rgba(6, 10, 17, .52);
    backdrop-filter: blur(7px);
    -webkit-backdrop-filter: blur(7px);
}
#xiaozhi-dropzone.visible { display: flex; }
#xiaozhi-dropzone .dz-card {
    border: 2px dashed rgba(23, 189, 147, .8);
    border-radius: 22px;
    padding: 36px 52px;
    text-align: center;
    background: rgba(11, 32, 27, .78);
    box-shadow: 0 20px 60px rgba(0, 0, 0, .5);
    pointer-events: none;
}
#xiaozhi-dropzone .dz-card b {
    display: block;
    font-size: 18px;
    font-weight: 600;
    color: #eafff8;
    letter-spacing: -.01em;
}
#xiaozhi-dropzone .dz-card span {
    display: block;
    margin-top: 8px;
    font-size: 12.5px;
    color: #a9c9bf;
}

/* 上传控件本身不露面：入口是「+」菜单和拖拽。
   用离屏而不是 display:none——个别浏览器会拒绝
   对 display:none 的 file input 触发选择框。 */
#composer-upload {
    position: fixed !important;
    left: -9999px !important;
    top: 0 !important;
    width: 1px !important;
    height: 1px !important;
    min-width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

/* ------------------------------------------------------------
   左栏：分组、工作区卡片、插件卡片
   ------------------------------------------------------------ */

/* 分组标签之上加一条发丝线，让左栏读起来是"几段"而不是一片 */
#left-panel .section-label.rule {
    border-top: 1px solid var(--hair);
    margin-top: 14px;
}

/* 手风琴标题（如"重命名 / 删除会话"）要保持中性色：
   Gradio 主题给内层 span 上了链接蓝，看着像可跳转的文字。 */
#left-panel .gr-accordion .label-wrap span:not(.icon) {
    color: var(--tx-2) !important;
    font-weight: 550 !important;
}
#left-panel .gr-accordion .label-wrap:hover span:not(.icon) {
    color: var(--tx) !important;
}

/* 次要动作并排：等宽两列，居中文字 */
#left-panel .side-row {
    gap: 6px !important;
    align-items: stretch !important;
    flex-wrap: nowrap !important;
}
#left-panel .side-row > * {
    flex: 1 1 0 !important;
    min-width: 0 !important;
}
#left-panel .side-row button {
    justify-content: center !important;
    text-align: center !important;
    padding: 0 8px !important;
}

/* 工作区卡片：短名 + 文件数，下面一行完整路径（超长两行截断）
   左栏模型状态卡复用同一套结构，所以选择器并列写。 */
#workspace-path .side-path,
#model-status .side-path {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
}
#workspace-path .side-path-name,
#model-status .side-path-name {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--tx-2);
}
#workspace-path .side-path-count,
#model-status .side-path-count {
    font-size: 11.5px;
    color: var(--muted);
}
/* 未配置接口：名字用警示色，让人一眼看出这里有问题 */
#model-status .side-path-name.warn {
    font-family: inherit;
    color: var(--err);
    font-weight: 600;
}
#model-status .side-path-count {
    white-space: nowrap;
}
/* 完整路径只占一行：超出用省略号收尾，完整值挂在 title 上。
   之前用 break-all 换行，会在 "workspa / ce" 中间硬断，很别扭。 */
#workspace-path .side-path-full,
#model-status .side-path-full {
    margin-top: 5px;
    font-family: var(--mono);
    font-size: 10.5px;
    line-height: 1.5;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: default;
}

#left-panel .side-stat,
#plugin-summary .side-stat {
    font-size: 11.5px !important;
    line-height: 1.6;
    color: var(--muted) !important;
}
#left-panel .side-stat b,
#plugin-summary .side-stat b {
    color: var(--accent-hi);
    font-weight: 650;
}

/* ------------------------------------------------------------
   右栏 5 个 Tab（进度 / 文件 / 能力 / 插件 / 设置）
   按钮内边距收紧，保证一排放得下、又不触发折叠成下拉框
   ------------------------------------------------------------ */

#inspector-tabs > .tab-wrapper .tab-container:not(.visually-hidden) button {
    font-size: 12.5px !important;
    padding: 0 6px !important;
    height: 32px !important;
    min-width: 0 !important;
}

/* 卸载是低频破坏性操作，安静一点：
   平时只是描边红字，悬停才实心，和「停止」按钮保持同一套语言。 */
#plugin-uninstall-button {
    background: transparent !important;
    border: 1px solid var(--hair) !important;
    color: var(--muted) !important;
    box-shadow: none !important;
}
#plugin-uninstall-button:hover {
    background: rgba(239, 106, 120, .10) !important;
    border-color: rgba(239, 106, 120, .45) !important;
    color: var(--err) !important;
}

#plugin-intro p, #plugin-detail p, #plugin-status p { margin: 0 !important; }
#plugin-detail h3 { margin: 0 0 8px !important; font-size: 14px !important; }
#plugin-detail ul { padding-left: 18px !important; margin: 4px 0 !important; }
#plugin-table { font-size: 12px !important; }
"""

CSS += GLASS_CSS
CSS += RIGHT_PANEL_FIX_CSS
CSS += COMPOSER_EXTRA_CSS

# ============================================================
# Composer Keyboard UX
# ============================================================

KEYBOARD_JS = r"""
() => {
    if (window.__xiaozhiComposerKeyboardInstalled) {
        return;
    }

    window.__xiaozhiComposerKeyboardInstalled = true;

    document.addEventListener("click", (event) => {
        const button = event.target.closest("button.action-feedback");
        if (button && !button.disabled) {
            window.xiaozhiAppearance?.notify("已收到操作：" + button.textContent.trim());
        }
    }, true);

    // ---------------------------------------------------------
    // 在线字体（非阻塞；加载失败自动回退系统字体栈）
    // ---------------------------------------------------------

    (function () {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "https://fonts.googleapis.com/css2"
            + "?family=Plus+Jakarta+Sans:wght@400;500;600;700"
            + "&family=JetBrains+Mono:wght@400;500;600"
            + "&display=swap";
        // print  media 技巧：下载期间不阻塞渲染，加载完成后再生效。
        // 断网 / 国内网络下首屏直接使用系统字体栈，不再等待。
        link.media = "print";
        link.onload = function () { link.media = "all"; };
        document.head.appendChild(link);
    })();

    // ---------------------------------------------------------
    // 空状态：有消息就隐藏欢迎区
    // ---------------------------------------------------------

    function syncWelcome() {
        var chatbot = document.querySelector("#chatbot");
        var welcome = document.querySelector("#welcome");
        if (!chatbot || !welcome) { return; }

        var count = chatbot.querySelectorAll(
            ".message"
        ).length;

        welcome.style.display = count > 0 ? "none" : "";
    }

    // 流式输出时 DOM 变动很频繁，用 rAF 合并。
    var scheduled = false;

    function scheduleSync() {
        if (scheduled) { return; }
        scheduled = true;
        requestAnimationFrame(function () {
            scheduled = false;
            syncWelcome();
        });
    }

    new MutationObserver(scheduleSync).observe(
        document.body,
        {
            childList: true,
            subtree: true
        }
    );

    syncWelcome();
    window.addEventListener("load", syncWelcome);

    // ---------------------------------------------------------
    // 欢迎卡片：点击后写回输入框
    // ---------------------------------------------------------

    document.addEventListener(
        "click",
        (event) => {
            var card = event.target.closest(
                ".welcome-card"
            );

            if (!card) { return; }

            var prompt = card.getAttribute(
                "data-prompt"
            ) || "";

            var textarea = document.querySelector(
                "#message-composer textarea"
            );

            if (!textarea) { return; }

            textarea.value = prompt;
            textarea.dispatchEvent(
                new Event("input", { bubbles: true })
            );
            textarea.focus();
            window.xiaozhiAppearance?.notify("任务已填入输入框，按 Enter 或点击发送开始。");
        },
        true
    );

    document.addEventListener(
        "keydown",
        (event) => {
            const target = event.target;

            if (!(target instanceof HTMLTextAreaElement)) {
                return;
            }

            const composer = target.closest(
                "#message-composer"
            );

            if (!composer) {
                return;
            }

            // 中文/日文等输入法组合输入期间，不拦截 Enter。
            if (
                event.isComposing
                || event.keyCode === 229
            ) {
                return;
            }

            if (event.key !== "Enter") {
                return;
            }

            // Shift+Enter：由我们显式插入换行。
            // Gradio/Textbox 自己可能会拦截默认 Enter 行为，
            // 所以不能只依赖浏览器默认换行。
            if (event.shiftKey) {
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();

                const start = target.selectionStart ?? target.value.length;
                const end = target.selectionEnd ?? target.value.length;

                target.setRangeText(
                    "\n",
                    start,
                    end,
                    "end"
                );

                // 通知 Gradio 前端状态：textarea 内容已变化。
                target.dispatchEvent(
                    new InputEvent(
                        "input",
                        {
                            bubbles: true,
                            inputType: "insertLineBreak",
                            data: "\n"
                        }
                    )
                );

                return;
            }

            // 普通 Enter：阻止 textarea 换行并发送。
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();

            const button = document.querySelector(
                "#send-task-button button"
            ) || document.querySelector(
                "#send-task-button"
            );

            if (button) {
                button.click();
            }
        },
        true
    );
}
"""

# ------------------------------------------------------------
# 切 Tab 时把右栏滚动位置归零
#
# 右栏是 overflow-y:auto，在"能力"页滚到底后切到"进度"，
# 面板仍停在原来的滚动位置，看上去内容"跑没了"。
# ------------------------------------------------------------

TAB_SCROLL_JS = """
(function () {
    var reset = function (node) {
        while (node && node !== document.body) {
            if (node.scrollHeight > node.clientHeight + 4) {
                node.scrollTop = 0;
            }
            node = node.parentElement;
        }
    };

    document.addEventListener("click", function (event) {
        var btn = event.target.closest("button");
        if (!btn) { return; }
        if (btn.getAttribute("role") !== "tab"
            && !btn.closest(".tab-container")) { return; }

        var host = btn.closest("#right-panel")
            || btn.closest("#settings-panel");
        if (!host) { return; }

        setTimeout(function () { reset(host); }, 0);
    }, true);
})();
"""

# ------------------------------------------------------------
# 「+」菜单 + 拖拽上传 + Tab 跳转
# ------------------------------------------------------------

COMPOSER_MENU_JS = """
(function () {
    var gradioClick = function (selector) {
        var host = document.querySelector(selector);
        if (!host) { return false; }
        var btn = host.tagName === 'BUTTON' ? host : host.querySelector('button');
        if (!btn) { return false; }
        btn.click();
        return true;
    };

    // Gradio 6 有时把 elem_id 放在组件根节点，有时放在里面的
    // textarea/input 上——两种都要能写进去。
    var resolveField = function (selector) {
        var host = document.querySelector(selector);
        if (!host) { return null; }
        if (host.matches('textarea, input')) { return host; }
        return host.querySelector('textarea') || host.querySelector('input');
    };

    var setText = function (selector, value) {
        var field = resolveField(selector);
        if (!field) { return false; }
        field.value = value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    };

    var notify = function (text) {
        if (window.xiaozhiAppearance && window.xiaozhiAppearance.notify) {
            window.xiaozhiAppearance.notify(text);
        }
    };

    // 供按钮/菜单直接调用：按名字切到右栏某个 Tab
    window.xiaozhiOpenTab = function (name) {
        window.xiaozhiShell?.open();
        var host = document.querySelector('#right-panel');
        if (!host) { return false; }
        var tries = 0;
        function selectTab() {
            var buttons = host.querySelectorAll('button[role="tab"]');
            for (var i = 0; i < buttons.length; i++) {
                if ((buttons[i].textContent || '').trim() === name) {
                    buttons[i].click();
                    return;
                }
            }
            if (++tries < 12) { setTimeout(selectTab, 80); }
            else { notify('面板未能打开，请重新点击工作台。'); }
        }
        requestAnimationFrame(selectTab);
        return true;
    };

    // 直达「设置 → 模型」：先切右栏顶层 Tab，
    // 再轮询点开设置里嵌套的那层子 Tab（Gradio 是懒渲染的）。
    window.xiaozhiOpenModelSettings = function () {
        if (!window.xiaozhiOpenTab('设置')) { return false; }
        var tries = 0;
        var timer = setInterval(function () {
            tries += 1;
            var panel = document.querySelector('#settings-panel');
            var done = false;
            if (panel) {
                var subs = panel.querySelectorAll('.tab-container:not(.visually-hidden) button');
                for (var i = 0; i < subs.length; i++) {
                    if ((subs[i].textContent || '').trim() === '模型') {
                        subs[i].click();
                        done = true;
                        break;
                    }
                }
            }
            if (done || tries > 25) { clearInterval(timer); }
        }, 120);
        return true;
    };

    // ---------------------------------------------------------
    // 拖拽上传：整窗接管，松手即导入
    // ---------------------------------------------------------

    var overlay = document.createElement('div');
    overlay.id = 'xiaozhi-dropzone';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<div class="dz-card"><b>松手即导入工作区</b>'
        + '<span>路径会自动写进输入框，可直接让小智读取</span></div>';
    document.body.appendChild(overlay);

    var depth = 0;

    var hasFiles = function (event) {
        if (!event.dataTransfer) { return false; }
        var types = event.dataTransfer.types || [];
        return Array.prototype.indexOf.call(types, 'Files') !== -1;
    };

    var hideOverlay = function () {
        depth = 0;
        overlay.classList.remove('visible');
    };

    window.addEventListener('dragenter', function (event) {
        if (!hasFiles(event)) { return; }
        event.preventDefault();
        depth += 1;
        overlay.classList.add('visible');
    });

    window.addEventListener('dragover', function (event) {
        if (!hasFiles(event)) { return; }
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        overlay.classList.add('visible');
    });

    window.addEventListener('dragleave', function (event) {
        if (!hasFiles(event)) { return; }
        depth = Math.max(0, depth - 1);
        if (depth === 0) { hideOverlay(); }
    });

    window.addEventListener('drop', function (event) {
        if (!hasFiles(event)) { return; }
        // 不阻止的话浏览器会直接打开这个文件，页面被替换掉。
        event.preventDefault();
        hideOverlay();

        var files = event.dataTransfer.files;
        if (!files || !files.length) { return; }

        // 桌面拖进来的文件夹：浏览器只给一个空壳，拿不到真实路径，
        // 硬传上去会得到一个 0 字节的假文件，不如直接告诉用户走哪条路。
        var items = event.dataTransfer.items
            ? Array.prototype.slice.call(event.dataTransfer.items)
            : [];
        var droppedFolder = false;
        for (var index = 0; index < items.length; index += 1) {
            var entry = items[index].webkitGetAsEntry ? items[index].webkitGetAsEntry() : null;
            if (entry && entry.isDirectory) { droppedFolder = true; break; }
        }
        if (droppedFolder) {
            notify('拖入的是文件夹。浏览器读不到文件夹的绝对路径，请用输入框左侧的 ＋ → 导入本地文件夹。');
            return;
        }

        var input = document.querySelector('#composer-upload input[type=file]');
        if (!input) { notify('上传控件还没就绪，请刷新页面后重试。'); return; }

        try {
            input.files = files;
        } catch (error) {
            notify('这个浏览器不允许直接放入文件，请点输入框左侧的 ＋ 选择文件。');
            return;
        }

        input.dispatchEvent(new Event('change', { bubbles: true }));
        notify('已收到 ' + files.length + ' 个文件，正在导入工作区…');
    });

    // ---------------------------------------------------------
    // 「+」菜单
    // ---------------------------------------------------------

    var syncPlus = function (open) {
        var plus = document.querySelector('#composer-plus');
        if (!plus) { return; }
        plus.classList.toggle('menu-open', open);
        plus.setAttribute('aria-expanded', String(open));
    };

    var closeMenu = function () {
        var menu = document.querySelector('.composer-menu');
        if (menu) { menu.hidden = true; }
        syncPlus(false);
    };

    document.addEventListener('click', function (event) {
        var menu = document.querySelector('.composer-menu');
        if (!menu) { return; }

        if (event.target.closest('#composer-plus')) {
            event.preventDefault();
            menu.hidden = !menu.hidden;
            // 按钮自己也给反馈：展开时变强调色并转 45°（＋ → ×）
            syncPlus(!menu.hidden);
            if (!menu.hidden) {
                var folder = menu.querySelector('[data-folder]');
                if (folder) { folder.hidden = true; }
            }
            return;
        }

        var item = event.target.closest('.composer-menu [data-action]');
        if (item) {
            var action = item.dataset.action;

            if (action === 'upload') {
                var input = document.querySelector('#composer-upload input[type=file]');
                closeMenu();
                if (input) { input.click(); }
                else { notify('上传控件还没就绪，请刷新页面后重试。'); }
                return;
            }

            if (action === 'folder') {
                var row = menu.querySelector('[data-folder]');
                if (!row) { return; }
                row.hidden = !row.hidden;
                if (!row.hidden) { row.querySelector('input').focus(); }
                return;
            }

            if (action === 'folder-go') {
                var value = (menu.querySelector('[data-folder-input]').value || '').trim();
                if (!value) { notify('请先填写文件夹的绝对路径。'); return; }
                closeMenu();
                setText('#folder-quick-path', value);
                gradioClick('#quick-folder-btn');
                return;
            }

            if (action === 'clear') {
                closeMenu();
                gradioClick('#clear-display-button');
                return;
            }

            if (action === 'export') {
                closeMenu();
                gradioClick('#export-button');
                return;
            }

            if (action === 'open') {
                closeMenu();
                gradioClick('#open-workspace-button');
                return;
            }

            if (action === 'plugins') {
                closeMenu();
                if (!window.xiaozhiOpenTab('插件')) { notify('没有找到插件页，请在右栏手动切换。'); }
                return;
            }
            return;
        }

        // 点空白处收起
        if (!menu.hidden && !event.target.closest('#composer-menu')) {
            closeMenu();
        }
    }, true);

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { closeMenu(); }
    });
})();
"""

WORKBENCH_JS = (Path(__file__).parent / "assets" / "workbench.js").read_text(encoding="utf-8")
WORKBENCH_CSS = (Path(__file__).parent / "assets" / "workbench.css").read_text(encoding="utf-8")
WORKBENCH_JS = "{const style=document.createElement('style');style.textContent=" + json.dumps(WORKBENCH_CSS) + ";document.head.append(style);}" + WORKBENCH_JS
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + APPEARANCE_JS + "\n" + WORKBENCH_JS + "\n", 1)
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + TAB_SCROLL_JS + "\n", 1)
KEYBOARD_JS = KEYBOARD_JS.replace("() => {", "() => {\n" + COMPOSER_MENU_JS + "\n", 1)

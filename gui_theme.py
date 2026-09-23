"""Gradio 主题与配色变量。"""

import gradio as gr

THEME_VARS = {
    # 页面：最底层保持实色，玻璃要"糊"的是它上面的光晕层
    "body_background_fill": "#07070b",
    "body_text_color": "#f2f2f7",
    "body_text_color_subdued": "#8a8a99",
    # 面层：一律半透明，否则组件会挡住背景光晕，玻璃感直接消失
    "background_fill_primary": "rgba(255,255,255,.055)",
    "background_fill_secondary": "rgba(255,255,255,.032)",
    "block_background_fill": "rgba(255,255,255,.055)",
    "block_border_color": "rgba(255,255,255,.10)",
    "block_label_background_fill": "rgba(255,255,255,.05)",
    "block_label_text_color": "#8a8a99",
    "panel_background_fill": "rgba(255,255,255,.055)",
    "panel_border_color": "rgba(255,255,255,.09)",
    "panel_border_width": "1px",
    "border_color_primary": "rgba(255,255,255,.10)",
    "border_color_accent": "rgba(16,163,127,.45)",
    # 输入
    "input_background_fill": "rgba(255,255,255,.045)",
    "input_background_fill_focus": "rgba(255,255,255,.075)",
    "input_border_color": "rgba(255,255,255,.10)",
    "input_border_color_focus": "rgba(16,163,127,.45)",
    "input_placeholder_color": "#65657a",
    "input_shadow": "none",
    "input_shadow_focus": "none",
    # 按钮
    "button_secondary_background_fill": "rgba(255,255,255,.055)",
    "button_secondary_text_color": "#f2f2f7",
    "button_secondary_border_color": "rgba(255,255,255,.10)",
    "button_primary_background_fill": "#10a37f",
    "button_primary_background_fill_hover": "#17bd93",
    "button_primary_text_color": "#03150f",
    "button_primary_border_color": "rgba(255,255,255,.20)",
    # 表格
    "table_border_color": "rgba(255,255,255,.07)",
    "table_even_background_fill": "rgba(255,255,255,.022)",
    "table_odd_background_fill": "rgba(255,255,255,.045)",
    "table_text_color": "#b8b8c6",
    "table_row_focus": "rgba(255,255,255,.075)",
    # 其它
    "code_background_fill": "rgba(8,10,16,.55)",
    "link_text_color": "#17bd93",
    "link_text_color_hover": "#1fcb9e",
    "checkbox_background_color": "rgba(255,255,255,.06)",
    "checkbox_label_background_fill": "rgba(255,255,255,.05)",
    "checkbox_label_text_color": "#f2f2f7",
    "slider_color": "#10a37f",
    "loader_color": "#10a37f",
    "color_accent": "#10a37f",
    "color_accent_soft": "rgba(16,163,127,.14)",
    "stat_background_fill": "rgba(255,255,255,.05)",
    "error_background_fill": "rgba(239,106,120,.12)",
    "error_border_color": "rgba(239,106,120,.38)",
    "error_text_color": "#ef6a78",
    "shadow_drop": "0 8px 28px rgba(0,0,0,.36)",
    "shadow_drop_lg": "0 18px 50px rgba(0,0,0,.42)",
    "shadow_inset": "inset 0 1px 0 rgba(255,255,255,.10)",
}

def build_theme() -> gr.themes.Base:
    """
    显式构造暗色主题。

    Gradio 的 ``Base()`` 默认是浅色，只有浏览器偏好为暗色时才会切到暗色。
    这里把浅色与暗色两套变量写成同一组暗色值，
    保证任何系统环境下渲染结果一致，
    也避免未被我自定义 CSS 覆盖的组件（手风琴、状态遮罩、
    表格、下拉面板等）出现突兀的白底。
    """

    base = gr.themes.Base()
    available = set(vars(base))

    pairs = {}

    for key, value in THEME_VARS.items():

        # 不是每个变量都有 _dark 变体（例如 color_accent），
        # 必须按主题实际拥有的键来写，否则 set() 会直接报错。
        if key in available:
            pairs[key] = value

        dark_key = key + "_dark"

        if dark_key in available:
            pairs[dark_key] = value

    return base.set(**pairs)

theme = build_theme()

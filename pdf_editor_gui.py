# -*- coding: utf-8 -*-
"""
pdf_editor_gui —— PDFTextEditor 图形界面（Tkinter）

性能要点：预览只渲染"当前可见区域 + 缓冲边"（clip 渲染），不整页渲染；
滚动/缩放后对未覆盖区域做防抖重绘，因此高倍缩放也不卡。

交互：
· 鼠标滚轮 = 上下滚动；Ctrl+滚轮 = 缩放（以鼠标位置为锚点）
· 勾选「对比预览」自动缩放到合适大小；选中文字并输入替换内容会自动开启对比
· 表单里的草稿即时参与预览：填完「替换」不用先加清单就能看到效果（窗格是预览，点
  「添加到清单」才真正写进规则）
· 帮助为悬浮窗口；各面板之间分隔条可拖动调整宽高
依赖：PyMuPDF、fontTools、numpy（见 requirements.txt）
"""
from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import pymupdf as fitz
import numpy as np

import pdf_edit_core as core

APP_TITLE = "PDFTextEditor"

# 字体：界面统一中文字体；标题用粗体着重
UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
UI_FONT_TITLE = ("Microsoft YaHei UI", 10, "bold")     # 分组标题：比正文大一档
UI_FONT_SMALL = ("Microsoft YaHei UI", 8)              # 灰色微提示
ZOOM_MIN, ZOOM_MAX = 0.2, 5.0
TILE_MARGIN = 0.5          # 缓冲边 = 视口尺寸的 50%
RENDER_DEBOUNCE_MS = 40
DRAFT_DEBOUNCE_MS = 350    # 表单草稿变了之后，等这么久再重建预览（别每敲一键就重建整份文档）
RIGHT_PANE_MIN = 400       # 右侧操作栏的最小宽度（保证表单/清单不被压扁）
GLYPH_CHIP_MAX = 2         # 「替换」行右侧最多放几条字形提醒（多了会把输入框挤没，其余进日志）

# 伪加粗描边（bold_stroke）：默认关（纯填充最接近原文）；开启时的默认粗细与标定候选档位。
# 实测：描边 0.03 时墨量是原文的 145%（看着明显变粗），0.005 时约 96%（基本吻合）。
DEFAULT_STROKE = 0.005
STROKE_CANDIDATES = (0.0, 0.005, 0.008, 0.01, 0.015, 0.02, 0.03, 0.04)

# ---------------- 主题（浅色 / 深色） ----------------
# Windows 下 ttk 默认用 vista 主题，很多颜色改不动，所以统一切到 clam，
# 由下面的调色板完全接管配色；tk 原生控件（画布 / 日志 / 分隔条）另行上色。
THEMES = {
    "light": dict(
        label="浅色",
        bg="#f0f0f0", fg="#1a1a1a",           # 面板底色 / 文字
        bar_bg="#e3e3e3",                     # 工具栏底色（与面板区分）
        field="#ffffff",                      # 输入框、列表底
        border="#c9c9c9", btn="#e8e8e8", btn_hover="#dcdcdc",
        sel_bg="#cfe4ff", sel_fg="#1a1a1a",   # 选中项
        head="#d6d6d6",                       # 表头（比行底深一档，才像表头）
        sash="#c4c4c4",                       # 分隔条
        canvas="#e4e6e8",                     # 预览画布底色（页面四周）
        accent="#0066cc", ok="#00aa66", warn="#cc6600", pill_fg="#777777",
        hint="#777777",                            # 提示文字：与日志「最近的消息」同一种灰
        primary="#0E6B4F", primary_fg="#ffffff",   # 主按钮：与图标同色（深绿）
        muted="#777777", mark="#e53935", disabled="#a0a0a0",
    ),
    "dark": dict(
        label="深色",
        bg="#2b2b2b", fg="#e6e6e6",
        bar_bg="#333333",
        field="#3a3a3a",
        border="#4d4d4d", btn="#3a3a3a", btn_hover="#4a4a4a",
        sel_bg="#3f5b78", sel_fg="#ffffff",
        head="#454545",
        sash="#4a4a4a",
        canvas="#1c1c1c",
        accent="#4fc3f7", ok="#4ade80", warn="#fbbf24", pill_fg="#9a9a9a",
        hint="#9a9a9a",
        primary="#0E6B4F", primary_fg="#ffffff",
        muted="#9a9a9a", mark="#ff5252", disabled="#6f6f6f",
    ),
}


def settings_path() -> str:
    """轻量设置文件（主题等）。与字体缓存同目录：便携版在程序目录 data\\，否则在用户缓存目录。"""
    return os.path.join(core.user_cache_dir(), "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# 单行提示：按当前操作阶段只显示一条（序号交给「tips」胶囊，文案里不再带序号）
HINT_OPEN = "先「打开 PDF」"
HINT_PICK = "单击左侧预览里的文字，或按住左键框选一行"
HINT_TYPE = "在「替换」里填新文字"
HINT_ADD = "右侧是即时预览（尚未加入清单），点「添加到清单」生效"
HINT_MORE = "继续点文字加下一条，或点「另存为…」导出"
# 高级组标题：收起/展开时只换前面的 ▸ / ▾
ADV_LABEL = "高级：左边框位置 / 间隙 / 字宽 / 范围 / 描边"


def make_pill(parent, text, canvas_bg, fill, text_color, font):
    """画一个圆角胶囊徽标，返回 (canvas, 形状 id, 文字 id)，便于换主题时重新上色。"""
    from tkinter import font as tkfont
    f = tkfont.Font(font=font)
    w = f.measure(text) + 16
    h = f.metrics("linespace") + 2
    cv = tk.Canvas(parent, width=w, height=h, highlightthickness=0, bd=0, bg=canvas_bg)
    r = h / 2.0
    pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h,
           r, h, 0, h, 0, h - r, 0, r, 0, 0]
    shape = cv.create_polygon(pts, smooth=True, fill=fill)
    tid = cv.create_text(w / 2, h / 2, text=text, fill=text_color, font=font)
    return cv, shape, tid

HELP_TEXT = f"""PDFTextEditor · 使用说明

────────────────────────────
【四步上手】
1. 点「打开 PDF」，选择要改的 PDF（必须是电子文本 PDF，文字可选中的）。
2. 在左侧「原图」里点一下要修改的那段文字，它会高亮，并自动带出字体、字号、左边框。
3. 在右侧「替换」里输入新文字；需要时调整字体 / 字号 / 对齐 / 范围；
   然后点「添加到清单」。可以重复 2~3 步加多条。
4. 点「另存为…」导出新 PDF。

提示：选中文字并开始输入替换内容后，会**自动开启对比预览**并缩放到合适大小，
      左侧并排显示「原图」和「改后」，两边缩放、滚动同步。

────────────────────────────
【缩放与滚动】
· 鼠标滚轮           = 上下滚动
· Ctrl + 鼠标滚轮    = 缩放（以鼠标所在位置为中心）
· Shift + 鼠标滚轮   = 左右滚动
· 拖动「缩放」滑块 / 百分比输入框回车 / ＋ － 按钮
· 快捷键：Ctrl+0 复位 100%，Ctrl+= 放大，Ctrl+- 缩小
· 按住鼠标中键拖动可平移；也可用滚动条

────────────────────────────
【调整界面布局】
各面板之间的分隔条都能用鼠标拖动：
· 左（PDF 预览）↔ 右（操作面板）：拖宽度
· 「原图 ↔ 改后预览」：拖宽度
· 右侧「编辑 ↔ 修改清单」：拖高度
· 底部「日志」栏：拖高度（整窗通栏，左右占满）
（每次启动使用默认布局，不做记忆。）

把 PDF 路径作为参数传给程序可直接打开：
    python pdf_editor_gui.py "D:\\某文件.pdf"
打包成 exe 后也可以把 PDF 拖到 exe 上打开。

────────────────────────────
【界面主题】
工具栏右侧「设置 ▾」→「深色主题」点一下即可切换（勾选 = 深色），选择会被记住，下次启动沿用。
（深色模式下预览画布、日志栏、修改清单、以及 Windows 标题栏都会一起变暗。）
导入 / 导出 config、帮助也在这个「设置」菜单里。

────────────────────────────
【各控件说明】
· 原文        ：从预览里点选出来的，只读。
· 替换      ：要改成的新文字。
· 字体/字号   ：默认按原片段的字体/字号自动填好，一般不用改。
· 对齐        ：
    - 保持原位  ：新文字沿用原来的位置（默认）。
    - 左对齐留白：让新文字在单元格里左对齐，并留出「间隙(字宽)」的空白。
· 左边框x     ：左对齐时的参照线（点选片段会自动填入）。
· 范围        ：
    - 所有相同文本：该文字在文档里出现几次就全改（例如上下两份相同的表）。
    - 仅选中这一处：只改你点的这一处。
· 添加/更新   ：把当前设置加进「修改清单」；同一处的重复添加会覆盖。
· 描边(高级)   ：默认关（新字是纯填充，最接近原文）。只有原文件的"加粗"是用描边
                 模拟的时候，才在「高级」里勾「伪加粗」并填宽度（默认 0.005）。
· 自动标定描边 ：在「设置 ▾」里。会拿文档里的真实文字当样本，自动算出该用多粗的描边
                 （算出来是 0 就表示原文不是伪加粗，不用描边）。
· 导入/导出 config：与命令行版共用同一份配置文件，便于复用与批量处理。

────────────────────────────
【常见问题】
Q：为什么改完字变粗或变细？
A：先看「高级」里的描边：默认是不描边的。若原文的加粗是用描边模拟的，勾上「伪加粗」
   并填个合适的宽度；也可以点「设置 ▾ → 自动标定描边」让它自动算。

Q：为什么导出的文件变小/变大了？
A：程序会对内嵌字体做子集化处理，体积通常和原文件接近；这是正常的。

Q：点不到文字 / 选中不了？
A：该 PDF 可能是扫描件（没有文字层），本工具不适用。

Q：改错了想撤销？
A：在「修改清单」里选中那条点「删除选中」，或点「清空」重新来。

Q：会不会把原文件改坏？
A：不会。程序始终从原文件重新生成，只有点「另存为…」时才写出新文件。

Q：为什么替换后的字换了字体，或者变成方框？
A：你输入的文字里可能有当前字体没有的字形（例如把英文片段改成中文）。程序会**逐字**
   改用包含该字形的字体（默认宋体优先），并在「替换」行右侧与日志里说明回退到了哪个
   字体，所以不会画成方框。想固定字体，就在「字体」下拉里换一个覆盖这些字的字体。
"""


def resource_path(rel: str) -> str:
    """兼容源码运行与 PyInstaller 打包（_MEIPASS）的资源路径。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def fmt_num(v, nd: int = 2) -> str:
    """数字转文本：去掉多余的 0（10.45 -> "10.45"，10.0 -> "10"）。字号/描边都用它。"""
    s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


def rule_key(rule: dict):
    """规则的唯一键：原文 + 页码 + 位置（与「添加到清单」的去重口径一致）。"""
    return (rule.get("old"), rule.get("page"), tuple(rule.get("bbox") or ()) or None)


class PdfEditorApp(tk.Tk):
    def __init__(self, initial=None):
        super().__init__()
        self.title(APP_TITLE)
        try:
            self.iconbitmap(resource_path(os.path.join("assets", "icon.ico")))
        except Exception:
            pass
        self.geometry("1320x860")
        self.minsize(1000, 640)

        self.src_path = None
        self.orig = None
        self._after_doc = None
        self._preview_sig = None          # 预览文档对应的规则快照（变了才重建）
        self.page_no = 0
        self.zoom = 1.2
        self.rules = []
        self.cfg_globals = {}             # 导入 config 时的全局默认（字体/字号/颜色…）
        self.bold_stroke = 0.0            # 伪加粗描边：默认关（「高级」里可开）
        self._span_cache = {}
        self._tiles = {}
        self._img_items = {}
        self._render_job = None
        self._draft_job = None            # 草稿预览的防抖定时器
        self._sel_bbox = None
        self._updating = False
        self.show_after = tk.BooleanVar(value=False)
        self.font_choices = core.all_font_choices()   # 系统字体索引 + 内置候选（自动定位）
        self._font_info = None                        # 当前片段识别出的字体信息
        self._font_toast = None                       # 「缺少字体」浮窗
        self._sel_size = 10.0                         # 当前选中片段的字号
        self._sel_rgb = None                          # 当前选中片段的原文颜色 (r,g,b)
        self._color_override = None                   # 用户自选颜色；None = 跟随原文
        self._picking = False                         # 取色器：正等着在页面上点一下
        self._drag = None                             # 左键框选状态（按下-拖动-松开）
        self._sel_from_range = False                  # 当前选中是不是框选出来的
        self._sel_span_count = 1                      # 框选到了几个片段（>1 才需要提醒）
        self._sel_fonts = []                          # 当前选区涉及几种字体（框选可能多种）
        self._glyph_log_sig = None                    # 字形提醒的日志去重

        # 主题：默认浅色；上次的选择会记住（settings.json 与字体缓存同目录）
        saved_theme = load_settings().get("theme")
        self._theme = saved_theme if saved_theme in THEMES else "light"
        self._pal = THEMES[self._theme]
        self._icons = {}                 # 图标 PhotoImage 引用
        self._icon_widgets = []          # [(控件, 固定配色或 None, 图标名)]

        self._apply_styles()
        self._build_ui()
        self._update_hint()
        self._log('就绪. 请先点"打开 PDF". 第一次用请看"帮助".')
        self.after(80, self._on_configure)
        self.after(200, self._apply_minsizes)
        self.after(150, self._apply_titlebar)      # 窗口映射后再套一次标题栏颜色
        if initial and os.path.exists(initial):
            self.load_pdf(initial)

    # ================= UI =================
    def _apply_styles(self):
        """各级标题着重显示：分组标题、表头、面板标签用粗体；界面统一中文字体。"""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")   # clam 才能完全自定义配色（含深色）
        except tk.TclError:
            pass
        style.configure(".", font=UI_FONT)
        style.configure("TLabelframe.Label", font=UI_FONT_TITLE)
        style.configure("Treeview.Heading", font=UI_FONT_BOLD)
        self._style_ttk()

    def _style_ttk(self):
        """按当前调色板配置所有 ttk 样式。"""
        p = self._pal
        st = ttk.Style(self)
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"], font=UI_FONT_SMALL)
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["border"],
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"], font=UI_FONT_TITLE)
        st.configure("TSeparator", background=p["border"])

        st.configure("TButton", background=p["btn"], foreground=p["fg"],
                     bordercolor=p["border"], lightcolor=p["btn"], darkcolor=p["btn"],
                     focuscolor=p["sel_bg"], padding=(10, 4))
        st.map("TButton",
               background=[("pressed", p["sel_bg"]), ("active", p["btn_hover"]), ("disabled", p["btn"])],
               foreground=[("disabled", p["disabled"])])

        # 主按钮：图标同款深绿实心（只有最关键的动作用）
        st.configure("Accent.TButton", background=p["primary"], foreground=p["primary_fg"],
                     bordercolor=p["primary"], lightcolor=p["primary"], darkcolor=p["primary"],
                     focuscolor=p["primary"], padding=(12, 4))
        st.map("Accent.TButton",
               background=[("pressed", p["sel_bg"]), ("active", p["primary"]),
                           ("disabled", p["btn"])],
               foreground=[("disabled", p["disabled"])],
               bordercolor=[("disabled", p["border"])],
               lightcolor=[("disabled", p["btn"])],
               darkcolor=[("disabled", p["btn"])])
        # 次按钮：无边框"幽灵"样式
        st.configure("Ghost.TButton", background=p["bg"], foreground=p["fg"],
                     bordercolor=p["bg"], lightcolor=p["bg"], darkcolor=p["bg"],
                     focuscolor=p["bg"], padding=(10, 4))
        st.map("Ghost.TButton",
               background=[("pressed", p["btn"]), ("active", p["btn_hover"]),
                           ("disabled", p["bg"])],
               foreground=[("disabled", p["disabled"])])

        for name in ("TMenubutton", "Toolbar.TMenubutton"):
            st.configure(name, background=p["btn"], foreground=p["fg"],
                         arrowcolor=p["fg"], bordercolor=p["border"],
                         lightcolor=p["btn"], darkcolor=p["btn"], padding=(8, 3))
            st.map(name, background=[("active", p["btn_hover"])],
                   foreground=[("disabled", p["disabled"])])

        # 工具栏专用样式：底色与面板区分，控件背景跟着走
        st.configure("Toolbar.TFrame", background=p["bar_bg"])
        st.configure("Toolbar.TLabel", background=p["bar_bg"], foreground=p["fg"])
        st.configure("Toolbar.TCheckbutton", background=p["bar_bg"], foreground=p["fg"],
                     focuscolor=p["bar_bg"])
        st.map("Toolbar.TCheckbutton", background=[("active", p["bar_bg"])],
               foreground=[("disabled", p["disabled"])])
        for sty in ("TCheckbutton", "TRadiobutton"):
            st.configure(sty, background=p["bg"], foreground=p["fg"], focuscolor=p["bg"])
            st.map(sty, background=[("active", p["bg"])],
                   foreground=[("disabled", p["disabled"])])

        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                     bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                     insertcolor=p["fg"], padding=(4, 3))
        st.map("TEntry", fieldbackground=[("disabled", p["bg"])],
               foreground=[("disabled", p["disabled"])])

        st.configure("TCombobox", fieldbackground=p["field"], background=p["btn"],
                     foreground=p["fg"], bordercolor=p["border"], arrowcolor=p["fg"],
                     lightcolor=p["border"], darkcolor=p["border"], padding=(4, 2))
        st.map("TCombobox",
               fieldbackground=[("readonly", p["field"]), ("disabled", p["bg"])],
               foreground=[("readonly", p["fg"]), ("disabled", p["disabled"])],
               background=[("active", p["btn_hover"])])

        st.configure("TScrollbar", background=p["btn"], troughcolor=p["bg"],
                     bordercolor=p["bg"], arrowcolor=p["fg"],
                     lightcolor=p["btn"], darkcolor=p["btn"])
        st.map("TScrollbar", background=[("active", p["btn_hover"]), ("pressed", p["sel_bg"])])

        st.configure("TScale", background=p["bg"], troughcolor=p["field"],
                     bordercolor=p["border"], lightcolor=p["primary"], darkcolor=p["primary"])
        st.map("TScale", background=[("active", p["bg"])])

        st.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                     foreground=p["fg"], bordercolor=p["border"],
                     lightcolor=p["border"], darkcolor=p["border"], rowheight=24)
        st.map("Treeview", background=[("selected", p["sel_bg"])],
               foreground=[("selected", p["sel_fg"])])
        st.configure("Treeview.Heading", background=p["head"], foreground=p["fg"],
                     bordercolor=p["border"], lightcolor=p["head"], darkcolor=p["head"],
                     relief="flat")
        st.map("Treeview.Heading", background=[("active", p["btn_hover"])])

        # 下拉列表是 Tk 原生 listbox，只能用 option_add 上色
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["sel_bg"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["sel_fg"])
        self.option_add("*TCombobox*Listbox.font", UI_FONT)

    def _style_widgets(self):
        """给 tk 原生控件（分隔条 / 画布 / 日志 / 彩色标签）上色。"""
        p = self._pal
        self.configure(bg=p["bg"])
        for pane in (self.content, self.outer, self.paned, self.right_paned):
            pane.configure(background=p["sash"])
        for cv in (self.canvas, self.canvas_after):
            cv.configure(background=p["canvas"])
        self.log.configure(background=p["field"], foreground=p["fg"],
                           insertbackground=p["fg"], selectbackground=p["sel_bg"],
                           selectforeground=p["sel_fg"], highlightthickness=0, bd=0)
        self.lbl_hint.configure(foreground=p["hint"])
        self._show_empty_card()
        self._load_icons()
        # tips 胶囊跟着换色
        self.pill.configure(bg=p["bg"])
        self.pill.itemconfigure(self._pill_shape, fill=p["primary"])
        self.pill.itemconfigure(self._pill_text, fill=p["pill_fg"])
        help_txt = getattr(self, "_help_txt", None)
        if help_txt is not None and help_txt.winfo_exists():
            help_txt.configure(background=p["field"], foreground=p["fg"],
                               insertbackground=p["fg"])
            self._help_win.configure(background=p["bg"])
        # tk 原生菜单（「设置」下拉）
        for m in getattr(self, "_menus", ()):
            m.configure(background=p["field"], foreground=p["fg"],
                        activebackground=p["sel_bg"], activeforeground=p["sel_fg"],
                        selectcolor=p["field"], disabledforeground=p["disabled"],
                        bd=0, relief="flat")
        # Windows 标题栏跟随深浅色
        self._apply_titlebar()

    def _build_more_menu(self, parent):
        """把不常用的功能收进工具栏右侧的「设置 ▾」下拉，避免窄窗口被挤掉。"""
        self.mb_more = ttk.Menubutton(parent, text="设置 ▾", style="Toolbar.TMenubutton")
        menu = tk.Menu(self.mb_more, tearoff=0)
        # 只有浅/深两套，直接点一下切换（勾选状态 = 当前是否深色）
        self.v_dark = tk.BooleanVar(value=(self._theme == "dark"))
        menu.add_checkbutton(label="深色主题", variable=self.v_dark,
                             command=self._on_toggle_dark)
        menu.add_separator()
        menu.add_command(label="自动标定描边", command=self.auto_calibrate)
        menu.add_command(label="打开字体文件夹", command=self._open_font_folder)
        menu.add_separator()
        menu.add_command(label="导入 config…", command=self.import_config)
        menu.add_command(label="导出 config…", command=self.export_config)
        menu.add_separator()
        menu.add_command(label="帮助", command=self.show_help)
        self.mb_more.configure(menu=menu)
        self.mb_more.pack(side="right")
        self._menus = (menu,)

    def _load_icons(self):
        """按当前主题贴线条图标：浅色底用深灰线、深色底用浅灰线；绿色主按钮固定用白色那套。"""
        folder = "dark" if self._theme == "dark" else "light"
        for widget, fixed, name in getattr(self, "_icon_widgets", ()):
            sub = fixed or folder
            try:
                img = tk.PhotoImage(file=resource_path(
                    os.path.join("assets", "icons", sub, name + ".png")))
            except Exception:
                continue
            widget.configure(image=img, compound="left")
            self._icons[(sub, name)] = img      # 持有引用，防止被回收

    def _on_toggle_dark(self):
        self.set_theme("dark" if self.v_dark.get() else "light")

    def set_theme(self, key):
        """切换主题（菜单与自检共用）。"""
        if key not in THEMES or key == self._theme:
            return
        self._theme = key
        self._pal = THEMES[key]
        self.v_dark.set(key == "dark")
        self._style_ttk()
        self._style_widgets()
        data = load_settings()
        data["theme"] = key
        save_settings(data)
        self._log("主题已切换为「%s」（下次启动沿用）" % self._pal["label"])

    def _apply_titlebar(self):
        """深色模式下让 Windows 标题栏也变暗（Win10 20H1+ / Win11）。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)   # GA_ROOT
            if not hwnd:
                return
            value = ctypes.c_int(1 if self._theme == "dark" else 0)
            for attr in (20, 19):     # 20 = Win10 20H1+ / Win11，19 = 更早版本
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            # 触发一次非客户区重画（0x0001|0x0002|0x0004|0x0010|0x0020）
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        except Exception:
            pass

    def _build_ui(self):
        bar = ttk.Frame(self, padding=(8, 6), style="Toolbar.TFrame")
        bar.pack(side="top", fill="x")
        # 工具栏靠自身底色与内容区区分，不再画分界线
        # ── 右端：设置 ▾（先占位，窄窗口时优先保证它可见）──
        self._build_more_menu(bar)
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=8)
        # ── 文件组：打开 / 另存为 ──
        self.btn_open = ttk.Button(bar, text="打开", command=self.open_pdf)
        self.btn_open.pack(side="left")
        self.btn_save = ttk.Button(bar, text="另存为…", style="Accent.TButton",
                                   command=self.save_as)
        self.btn_save.pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        # ── 页面组：翻页 ──
        ttk.Button(bar, text="◀", width=3, command=lambda: self.change_page(-1)).pack(side="left")
        self.lbl_page = ttk.Label(bar, text="0/0", width=4, anchor="center",
                                  style="Toolbar.TLabel")
        self.lbl_page.pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self.change_page(1)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        # ── 视图组：对比预览 / 适应窗口 ──
        self.cb_compare = ttk.Checkbutton(bar, text="对比预览", variable=self.show_after,
                                          style="Toolbar.TCheckbutton",
                                          command=self._on_toggle_after)
        self.cb_compare.pack(side="left")
        self.btn_fit = ttk.Button(bar, text="适应窗口", command=self.autofit)
        self.btn_fit.pack(side="left", padx=(8, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        # ── 缩放组（原独立一行的缩放条并进工具栏） ──
        ttk.Label(bar, text="缩放", style="Toolbar.TLabel").pack(side="left")
        self.zoom_var = tk.DoubleVar(value=self.zoom * 100)   # 百分比（与 set_zoom/_on_slider 口径一致）
        self.scale = ttk.Scale(bar, from_=ZOOM_MIN * 100, to=ZOOM_MAX * 100,
                               orient="horizontal", length=80, variable=self.zoom_var,
                               command=self._on_slider)
        self.scale.pack(side="left", padx=(6, 4))
        self.e_zoom = ttk.Entry(bar, width=4)
        self.e_zoom.insert(0, f"{self.zoom * 100:.0f}")
        self.e_zoom.pack(side="left")
        ttk.Label(bar, text="%", style="Toolbar.TLabel").pack(side="left", padx=(2, 6))
        self.e_zoom.bind("<Return>", lambda e: self._apply_zoom_entry())
        self.e_zoom.bind("<FocusOut>", lambda e: self._apply_zoom_entry())
        ttk.Button(bar, text="－", width=3,
                   command=lambda: self.set_zoom(self.zoom / 1.25)).pack(side="left")
        ttk.Button(bar, text="＋", width=3,
                   command=lambda: self.set_zoom(self.zoom * 1.25)).pack(side="left")

        # 线条图标：跟随主题；绿色主按钮固定用白色那套
        self._icon_widgets = [(self.btn_open, None, "open"),
                              (self.btn_save, "accent", "save"),
                              (self.cb_compare, None, "compare"),
                              (self.btn_fit, None, "fit"),
                              (self.mb_more, None, "settings")]

        content = tk.PanedWindow(self, orient="vertical", sashwidth=6, sashrelief="raised",
                                 background=self._pal["sash"], bd=0, opaqueresize=False)
        self.content = content
        content.pack(fill="both", expand=True)

        outer = tk.PanedWindow(content, orient="horizontal", sashwidth=6, sashrelief="raised",
                               background=self._pal["sash"], bd=0, opaqueresize=False)
        self.outer = outer
        content.add(outer, stretch="always", minsize=320)

        # ---- 左：缩放条 + 画布 ----
        left = ttk.Frame(outer)
        outer.add(left, stretch="always", minsize=320)

        # 画布上方一行：「tips」胶囊 + 当前阶段提示（替代原来的「原图 / 改后」标签）
        hdr = ttk.Frame(left)
        hdr.pack(side="top", fill="x", padx=8, pady=(6, 4))
        self.pill, self._pill_shape, self._pill_text = make_pill(
            hdr, "tips", self._pal["bg"], self._pal["primary"], self._pal["pill_fg"], UI_FONT)
        self.pill.pack(side="left")
        self.lbl_hint = ttk.Label(hdr, text="", foreground=self._pal["hint"])
        self.lbl_hint.pack(side="left", padx=(6, 0))
        hdr.bind("<Configure>", lambda e: self.lbl_hint.configure(wraplength=max(200, e.width - 60)))

        body = ttk.Frame(left)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.paned = tk.PanedWindow(body, orient="horizontal", sashwidth=6, sashrelief="raised",
                                    background=self._pal["sash"], bd=0, opaqueresize=False)
        self.paned.grid(row=0, column=0, sticky="nsew")

        f_before = ttk.Frame(self.paned)
        self.canvas = tk.Canvas(f_before, background=self._pal["canvas"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.paned.add(f_before, stretch="always", minsize=180)

        self.f_after = ttk.Frame(self.paned)
        self.canvas_after = tk.Canvas(self.f_after, background=self._pal["canvas"], highlightthickness=0)
        self.canvas_after.pack(fill="both", expand=True)

        self.vbar = ttk.Scrollbar(body, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(body, orient="horizontal", command=self._xview)
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        for c in (self.canvas, self.canvas_after):
            c.configure(yscrollcommand=self._yscroll_set, xscrollcommand=self._xscroll_set)
            c.bind("<MouseWheel>", self._on_wheel_scroll)
            c.bind("<Shift-MouseWheel>", self._on_wheel_hscroll)
            c.bind("<Control-MouseWheel>", self._on_wheel_zoom)
            c.bind("<ButtonPress-2>", lambda e, cc=c: self._pan_start(e, cc))
            c.bind("<B2-Motion>", lambda e, cc=c: self._pan_move(e, cc))
            c.bind("<Configure>", lambda e: self._schedule_render())
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.bind("<Control-Key-0>", lambda e: self.set_zoom(1.0))
        self.bind("<Control-plus>", lambda e: self.set_zoom(self.zoom * 1.25))
        self.bind("<Control-equal>", lambda e: self.set_zoom(self.zoom * 1.25))
        self.bind("<Control-minus>", lambda e: self.set_zoom(self.zoom / 1.25))

        # ---- 右：竖向可拖（编辑 | 修改清单 | 日志） ----
        right = ttk.Frame(outer, padding=8)
        self.right_frame = right
        outer.add(right, stretch="never", minsize=330, width=440)

        self.right_paned = tk.PanedWindow(right, orient="vertical", sashwidth=6, sashrelief="raised",
                                          background=self._pal["sash"], bd=0, opaqueresize=False)
        self.right_paned.pack(fill="both", expand=True)

        pane1 = ttk.Frame(self.right_paned)
        self._pane_edit = pane1
        self._build_edit_form(pane1)
        # 初始高度交给 _apply_minsizes 按表单自然高度定，避免下方留死区
        self.right_paned.add(pane1, stretch="never", minsize=210)

        pane2 = ttk.Frame(self.right_paned)
        self._build_rules_pane(pane2)
        self.right_paned.add(pane2, stretch="always", minsize=110)

        # 日志：整窗底部通栏，可上下拖高度
        logpane = ttk.Frame(content, padding=(8, 4))
        self._build_log_pane(logpane)
        content.add(logpane, stretch="never", minsize=185, height=185)

        self._style_widgets()

    def _build_edit_form(self, parent):
        edit = ttk.LabelFrame(parent, text="选中片段", padding=8)
        edit.pack(fill="x")

        ttk.Label(edit, text="原文（可改）").grid(row=0, column=0, sticky="w", pady=5)
        # 可手改：文字层抽错了（Type3 / 子集字体缺 ToUnicode 时常见乱码）由用户校正。
        # 位置不依赖这里的文字：scope=single/range 都是按 页码 + bbox 定位的。
        self.e_old = ttk.Entry(edit, width=30)
        self.e_old.grid(row=0, column=1, columnspan=2, sticky="we", pady=5)
        self.e_old.bind("<Return>", lambda e: self.add_rule())

        ttk.Label(edit, text="替换", font=UI_FONT_BOLD).grid(row=1, column=0, sticky="w", pady=5)
        self.e_new = ttk.Entry(edit, width=24)
        self.e_new.grid(row=1, column=1, sticky="we", pady=5)
        self.e_new.bind("<Return>", lambda e: self.add_rule())
        self.e_new.bind("<KeyRelease>", lambda e: self._on_new_text())
        # 字形提醒就贴在输入框右边：选区混合字体 / 替换文字缺字形（会逐字回退）
        self.lbl_glyph = ttk.Label(edit, text="", style="Muted.TLabel")
        self.lbl_glyph.grid(row=1, column=2, sticky="e", padx=(6, 0))

        ttk.Label(edit, text="字体").grid(row=2, column=0, sticky="w", pady=4)
        fontbox = ttk.Frame(edit)
        fontbox.grid(row=2, column=1, columnspan=2, sticky="we", pady=4)
        self.cb_font = ttk.Combobox(fontbox, width=18, state="readonly",
                                    values=[label for _p, label, _f in self.font_choices])
        self.cb_font.current(0)
        self.cb_font.bind("<<ComboboxSelected>>", self._on_draft_change)
        self.cb_font.pack(side="left")
        ttk.Button(fontbox, text="…", width=3, command=self._browse_font).pack(side="left", padx=(4, 0))
        self.e_size = ttk.Entry(fontbox, width=5)
        self.e_size.insert(0, "10")
        self.e_size.bind("<KeyRelease>", self._on_draft_change)
        self.e_size.pack(side="right", padx=(4, 0))
        ttk.Label(fontbox, text="字号").pack(side="right")

        # 识别结果：平时只有一行小字；缺字体时才多出一个"占满整行"的字体文件夹按钮
        self.inf = ttk.Frame(edit)
        self.inf.grid(row=3, column=0, columnspan=3, sticky="we", pady=(0, 2))
        self.lbl_fontinfo = ttk.Label(self.inf, text="", style="Muted.TLabel",
                                      wraplength=300, justify="left")
        self.lbl_fontinfo.pack(fill="x")
        self.btn_fontdir = ttk.Button(self.inf, text="字体文件夹（把字体放这里）",
                                      command=self._open_font_folder)
        self.inf.grid_remove()               # 没内容时整行不占位置

        ttk.Label(edit, text="颜色").grid(row=4, column=0, sticky="w", pady=5)
        cf = ttk.Frame(edit)
        cf.grid(row=4, column=1, columnspan=2, sticky="w", pady=5)
        self.sw_color = tk.Label(cf, text="   ", width=2, relief="solid", bd=1,
                                 cursor="hand2", bg="#000000")
        self.sw_color.pack(side="left")
        self.sw_color.bind("<Button-1>", lambda e: self._pick_color_dialog())
        ttk.Button(cf, text="取色器", width=7, style="Ghost.TButton",
                   command=self._start_eyedropper).pack(side="left", padx=(6, 0))
        ttk.Button(cf, text="原文", width=5, style="Ghost.TButton",
                   command=self._use_original_color).pack(side="left", padx=(4, 0))
        self.lbl_color = ttk.Label(cf, text="", style="Muted.TLabel")
        self.lbl_color.pack(side="left", padx=(8, 0))

        ttk.Label(edit, text="对齐").grid(row=5, column=0, sticky="w", pady=5)
        self.v_align = tk.StringVar(value="match")
        self.cb_align = ttk.Combobox(edit, width=14, state="readonly",
                                     values=["保持原位", "左对齐留白"])
        self.cb_align.current(0)
        self.cb_align.grid(row=5, column=1, sticky="w", pady=5)
        self.cb_align.bind("<<ComboboxSelected>>", self._on_align_change)

        # ── 高级项：默认收起，点一行小字展开 ──
        self._adv_open = False
        self.lbl_adv = ttk.Label(edit, text=f"▸ {ADV_LABEL}",
                                 style="Muted.TLabel", cursor="hand2")
        self.lbl_adv.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.lbl_adv.bind("<Button-1>", self._toggle_advanced)

        self.adv = ttk.Frame(edit)
        self.adv.grid(row=7, column=0, columnspan=3, sticky="we")
        ttk.Label(self.adv, text="左边框位置").grid(row=0, column=0, sticky="w", pady=4)
        af2 = ttk.Frame(self.adv)
        af2.grid(row=0, column=1, sticky="w", pady=4)
        self.e_border = ttk.Entry(af2, width=8)
        self.e_border.bind("<KeyRelease>", self._on_draft_change)
        self.e_border.pack(side="left")
        ttk.Label(af2, text="间隙").pack(side="left", padx=(10, 4))
        self.cb_gap = ttk.Combobox(af2, width=5, values=["1/4", "1/3", "1/2", "1"], state="readonly")
        self.cb_gap.current(1)
        self.cb_gap.bind("<<ComboboxSelected>>", self._on_draft_change)
        self.cb_gap.pack(side="left")
        ttk.Label(af2, text="字宽").pack(side="left", padx=(4, 0))

        ttk.Label(self.adv, text="范围").grid(row=1, column=0, sticky="w", pady=4)
        self.v_scope = tk.StringVar(value="all")
        sf = ttk.Frame(self.adv)
        sf.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Radiobutton(sf, text="所有相同文本", value="all", variable=self.v_scope,
                        command=self._on_scope_change).pack(side="left")
        ttk.Radiobutton(sf, text="仅选中这一处", value="single", variable=self.v_scope,
                        command=self._on_scope_change).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(sf, text="框选的这一段", value="range", variable=self.v_scope,
                        command=self._on_scope_change).pack(side="left", padx=(8, 0))

        # 描边（伪加粗）：默认关。原文是"伪加粗"（用描边假装粗体）时才需要；
        # 加多了会让替换后的字明显比原文粗（实测 0.03 时墨量是原文的 145%）。
        ttk.Label(self.adv, text="描边").grid(row=2, column=0, sticky="w", pady=4)
        self.v_stroke = tk.BooleanVar(value=False)
        stf = ttk.Frame(self.adv)
        stf.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Checkbutton(stf, text="伪加粗", variable=self.v_stroke,
                        command=self._on_draft_change).pack(side="left")
        self.e_stroke = ttk.Entry(stf, width=6)
        self.e_stroke.insert(0, fmt_num(DEFAULT_STROKE, 3))     # 描边要 3 位小数：0.005
        self.e_stroke.bind("<KeyRelease>", self._on_draft_change)
        self.e_stroke.pack(side="left", padx=(8, 4))
        ttk.Label(stf, text="(0 = 不描边)", style="Muted.TLabel").pack(side="left")
        self.adv.grid_remove()               # 默认收起

        btns = ttk.Frame(edit)
        btns.grid(row=8, column=0, columnspan=3, sticky="we", pady=(10, 0))
        btns.columnconfigure(0, weight=1)       # 主按钮占满，次按钮靠右
        self.btn_add = ttk.Button(btns, text="添加到清单", style="Accent.TButton",
                                  command=self.add_rule)
        self.btn_add.grid(row=0, column=0, sticky="we")
        ttk.Button(btns, text="取消选择", style="Ghost.TButton",
                   command=self.clear_selection).grid(row=0, column=1, padx=(6, 0))
        edit.columnconfigure(1, weight=1)
        self._update_color_widgets()

    def _toggle_advanced(self, _event=None):
        """展开/收起「高级」那一组低频项。"""
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.grid()
            self.lbl_adv.configure(text=f"▾ {ADV_LABEL}")
        else:
            self.adv.grid_remove()
            self.lbl_adv.configure(text=f"▸ {ADV_LABEL}")
        # 表单高度变了：让右栏分隔条重新贴到表单底边
        self.update_idletasks()
        self._apply_minsizes()

    def _on_align_change(self, _event=None):
        """下拉的显示文案 ↔ 内部取值（match / left）。"""
        self.v_align.set("match" if self.cb_align.current() == 0 else "left")
        self._on_draft_change()

    def _set_e_old(self, text):
        """写入「原文」。选片段时由程序填，用户也可以直接改成正确文字。"""
        self.e_old.delete(0, "end")
        if text:
            self.e_old.insert(0, text)

    def _build_rules_pane(self, parent):
        self.rules_frame = ttk.LabelFrame(parent, text="修改清单", padding=8)
        self.rules_frame.pack(fill="both", expand=True)
        head = ttk.Frame(self.rules_frame)
        head.pack(fill="x")
        ttk.Label(head, text="选中一条可删除", style="Muted.TLabel").pack(side="left")
        ttk.Button(head, text="清空", style="Ghost.TButton",
                   command=self.clear_rules).pack(side="right")
        ttk.Button(head, text="删除选中", style="Ghost.TButton",
                   command=self.del_rule).pack(side="right", padx=4)
        wrap = ttk.Frame(self.rules_frame)
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        sb = ttk.Scrollbar(wrap, orient="vertical")
        self.tree = ttk.Treeview(wrap, columns=("t",), show="", height=6, yscrollcommand=sb.set)
        sb.configure(command=self.tree.yview)
        self.tree.column("t", anchor="w")
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def _build_log_pane(self, parent):
        logf = ttk.LabelFrame(parent, text="日志", padding=6)
        logf.pack(fill="both", expand=True)
        head = ttk.Frame(logf)
        head.pack(fill="x")
        ttk.Label(head, text="最近的消息", style="Muted.TLabel").pack(side="left")
        ttk.Button(head, text="清空", style="Ghost.TButton",
                   command=self._clear_log).pack(side="right")
        body = ttk.Frame(logf)
        body.pack(fill="both", expand=True, pady=(4, 0))
        sb = ttk.Scrollbar(body, orient="vertical")
        self.log = tk.Text(body, height=7, width=28, wrap="word", yscrollcommand=sb.set)
        sb.configure(command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _clear_log(self):
        self.log.delete("1.0", "end")

    def _place_sash(self, h):
        """把右栏分隔条放到指定高度（容错：极端尺寸下 Tk 可能拒绝）。"""
        try:
            self.right_paned.sash_place(0, 0, h)
        except tk.TclError:
            pass

    def _apply_minsizes(self):
        """按"完整显示所需的最小尺寸"设置 minsize，避免拖到遮挡/裁切。"""
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        # 右栏最小宽度：取编辑表单请求宽度 + 边距；窄窗口时按窗宽比例封顶，避免内部约束打架
        try:
            need_w = max(self._pane_edit.winfo_reqwidth(),
                         self.right_paned.winfo_reqwidth()) + 16
            need_w = max(need_w, RIGHT_PANE_MIN)                       # 右栏保证最小宽度
            need_w = min(need_w, max(RIGHT_PANE_MIN, int(self.winfo_width() * 0.40)))
            self.outer.paneconfigure(self.right_frame, minsize=need_w)
            # 初始宽度若小于最小宽度，直接撑到最小宽度，避免一上来就被裁切
            if self.right_frame.winfo_width() < need_w:
                self.outer.paneconfigure(self.right_frame, width=need_w)
        except tk.TclError:
            pass
        # 右栏两块：编辑表单不裁切；清单留基本高度
        panes = self.right_paned.panes()
        if len(panes) >= 2:
            try:
                form_h = self._pane_edit.winfo_reqheight() + 8
                avail = self.right_paned.winfo_height()
                if avail and form_h + 110 > avail:
                    form_h = max(150, avail - 110)
                self.right_paned.paneconfigure(panes[0], minsize=form_h, height=form_h)
                # 分隔条落到表单自然底边：立刻做一次，并在 idle 里再补一次
                # （几何管理器可能在这一帧之后才应用 grid()/grid_remove() 的变化）
                self.right_paned.sash_place(0, 0, form_h)
                self.after_idle(lambda h=form_h: self._place_sash(h))
            except tk.TclError:
                pass

    def show_help(self):
        """帮助：悬浮窗口（置顶于主窗口）。"""
        if getattr(self, "_help_win", None) and self._help_win.winfo_exists():
            self._help_win.lift()
            self._help_win.focus_set()
            return
        win = tk.Toplevel(self)
        self._help_win = win
        self._help_txt = None
        win.title("帮助 · " + APP_TITLE)
        win.geometry("560x640")
        win.transient(self)          # 悬浮于主窗口之上
        win.configure(background=self._pal["bg"])
        txt = tk.Text(win, wrap="word", padx=10, pady=8,
                      background=self._pal["field"], foreground=self._pal["fg"],
                      insertbackground=self._pal["fg"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")
        self._help_txt = txt

    # ================= 通用辅助 =================
    def _log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _set_hint(self, msg):
        self.lbl_hint.config(text=msg)

    def _update_hint(self):
        """按当前操作阶段只显示一条提示。"""
        if self.orig is None:
            msg = HINT_OPEN
        elif self._sel_bbox is None:
            msg = HINT_MORE if self.rules else HINT_PICK
        elif not self.e_new.get().strip():
            msg = HINT_TYPE
        else:
            msg = HINT_ADD
        self._set_hint(msg)

    def _selected_font(self):
        """下拉框当前选中的 (字体文件, 字面号)；显示"需要安装"提示时返回 (None, 0)。"""
        idx = self.cb_font.current()
        if idx < 0 or idx >= len(self.font_choices):
            return None, 0
        p, _label, face = self.font_choices[idx]
        return p, int(face)

    def _selected_font_path(self):
        return self._selected_font()[0]

    def _select_font(self, path, face=0):
        """把下拉框定位到指定字体（列表里没有就补进去）。返回是否命中已有项。"""
        want = (os.path.normcase(path), int(face))
        for i, (p, _l, f) in enumerate(self.font_choices):
            if (os.path.normcase(p), int(f)) == want:
                self.cb_font.current(i)
                return True
        self.font_choices.append((path, f"自定义 {os.path.basename(path)}", int(face)))
        self.cb_font.configure(values=[l for _p, l, _f in self.font_choices])
        self.cb_font.current(len(self.font_choices) - 1)
        return False

    def _reload_font_choices(self):
        """重新扫描字体目录并刷新下拉列表（尽量保持当前选择）。"""
        cur = self._selected_font()
        self.font_choices = core.all_font_choices()
        self.cb_font.configure(values=[l for _p, l, _f in self.font_choices])
        if cur[0] and not self._select_font(*cur):
            self.cb_font.current(0)

    def _open_font_folder(self):
        """打开"放字体"的文件夹。

        注意：不碰 Windows 字体文件夹（C:\\Windows\\Fonts 需要管理员权限，改它不合适），
        字体放我们自己的目录就行，不用安装。
        """
        d = core.user_font_dir()
        self._reload_font_choices()
        self._log(f"字体文件夹: {d}（把字体放进去后，重新点选文字就会识别）")
        try:
            os.startfile(d)                     # noqa: S606  (Windows)
        except Exception:
            messagebox.showinfo(APP_TITLE, f"字体文件夹（把字体放这里，不用安装）：\n{d}")

    def _show_missing_font_toast(self, info):
        """缺字体：弹一个不阻塞的浮窗，说明"要装什么字体、放哪里"，并给"先用替身"的出口。"""
        old = self._font_toast
        if old is not None and old.winfo_exists():
            old.destroy()
        d = core.user_font_dir()
        sug = info.get("suggest") or {}
        sug_name = sug.get("family") or "宋体 SimSun"
        t = tk.Toplevel(self)
        self._font_toast = t
        t.title("缺少字体")
        t.transient(self)
        t.attributes("-topmost", True)
        t.resizable(False, False)
        frm = ttk.Frame(t, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"⚠ 缺少字体：{info.get('raw')}",
                  font=UI_FONT_BOLD, foreground=self._pal["warn"]).pack(anchor="w")
        ttk.Label(frm, justify="left", wraplength=380,
                  text=("这一段用的字体系统里没有。直接替换会和原文长得不一样，所以先不动它。\n\n"
                        "① 下载这个字体（.ttf / .otf），放进这个文件夹：\n"
                        f"     {d}\n"
                        "     （不用装进 Windows 字体文件夹 —— 那个需要管理员权限）\n\n"
                        "② 放好之后回到窗口，重新点一下这段文字即可识别。"
                        )).pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="打开字体文件夹", style="Accent.TButton",
                   command=self._open_font_folder).pack(side="left")
        ttk.Button(btns, text=f"先用「{sug_name}」代替", style="Ghost.TButton",
                   command=lambda: self._use_substitute(info)).pack(side="left", padx=6)
        ttk.Button(btns, text="知道了", style="Ghost.TButton",
                   command=t.destroy).pack(side="right")
        t.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() - t.winfo_reqwidth() - 24
        y = self.winfo_rooty() + 64
        t.geometry(f"+{max(0, x)}+{max(0, y)}")
        t.after(30000, lambda: t.destroy() if t.winfo_exists() else None)

    def _use_substitute(self, info):
        """用户明确选择"先用替身代替"（不会自动发生）。"""
        sug = info.get("suggest") or {}
        path = sug.get("path") or core.DEFAULT_FONT
        self._select_font(path, sug.get("face", 0))
        self._font_info = dict(info, source="substitute-used",
                               family=sug.get("family") or os.path.basename(path))
        self._show_font_info(self._font_info, self._sel_size)
        self._log(f"已用 {self._font_info['family']} 代替缺失字体 {info.get('raw')}")
        self._on_draft_change()

    def _set_font_row(self, text, color=None, show_button=False):
        """「识别结果」这一行：没内容就整行收起；缺字体时才显示占满整行的按钮。"""
        if not text:
            self.btn_fontdir.pack_forget()
            self.inf.grid_remove()
            return
        self.lbl_fontinfo.configure(text=text, foreground=color or self._pal["muted"])
        self.inf.grid()
        if show_button:
            self.btn_fontdir.pack(fill="x", pady=(4, 0))       # 整行占满
        else:
            self.btn_fontdir.pack_forget()
        self.update_idletasks()
        self._apply_minsizes()               # 表单高度变了，分隔条要重新贴

    def _show_font_info(self, info, size):
        """把"识别到了什么字体/字号"写在界面上；缺字体就明确提示 + 给出字体文件夹入口。"""
        src = (info or {}).get("source")
        if src == "type3":
            # Type3 不是"你缺字体"：字形是 PDF 自带的绘制程序，永远下载不到对应字体文件。
            # 所以这里不报警、不给字体文件夹按钮，只说明"已按某个中文字体近似"。
            self._set_font_row(
                f"内嵌字形（{info.get('raw')}）：常见于网页/MD 转 PDF · "
                f"系统里没有对应字体文件，已用 {info.get('family')} 近似 · {fmt_num(size)}pt",
                self._pal["muted"], show_button=False)
            return
        if src == "missing":
            self._set_font_row(
                f"⚠ 缺少字体 {info.get('raw')}：把字体文件放进下面的文件夹，"
                f"或点浮窗里的「先用 … 代替」",
                self._pal["warn"], show_button=True)
            return
        how = {"exact": "精确匹配", "family": "族名匹配", "substitute": "替身",
               "alias": "别名匹配", "manual": "手动选择",
               "substitute-used": "你选择的代替"}.get(src, "")
        txt = (f"已识别：{info.get('family')}"
               + (f"（{how}）" if how else "")
               + f" · {fmt_num(size)}pt"
               + (" · 粗体" if info.get("bold") else "")
               + (" · 斜体" if info.get("italic") else "")
               + (f" · 原文体 {info.get('raw')}" if src == "substitute-used" else ""))
        self._set_font_row(txt, self._pal["muted"], show_button=False)

    def _browse_font(self):
        """选择一个自定义字体文件（.ttf/.ttc/.otf），追加进下拉列表。"""
        path = filedialog.askopenfilename(
            title="选择字体文件",
            filetypes=[("字体文件", "*.ttf *.ttc *.otf"), ("所有文件", "*.*")])
        if not path:
            return
        if not self._select_font(path, 0):
            self._log(f"已加入自定义字体: {path}")
        self._on_draft_change()

    def _gap_value(self):
        s = self.cb_gap.get()
        try:
            if "/" in s:
                a, b = s.split("/")
                return float(a) / float(b)
            return float(s)
        except Exception:
            return 1 / 3

    def _stroke_value(self):
        """「高级」里的描边：没勾选 = 0（不描边），勾了才用填的数值。"""
        if not self.v_stroke.get():
            return 0.0
        try:
            return max(0.0, float(self.e_stroke.get() or DEFAULT_STROKE))
        except ValueError:
            return 0.0

    def _sync_stroke_widgets(self, value=None):
        """把描边值（默认取 self.bold_stroke）反映到「高级」的控件上。"""
        v = self.bold_stroke if value is None else float(value)
        self.v_stroke.set(v > 0)
        self.e_stroke.delete(0, "end")
        self.e_stroke.insert(0, fmt_num(v if v > 0 else DEFAULT_STROKE, 3))
        self._on_draft_change()

    # ---------- 颜色：默认跟随原文；可调色板选色 / 页面上取色 ----------
    def _eff_color(self):
        """当前生效的文字颜色：自选 > 原文颜色 > 黑。"""
        if self._color_override is not None:
            return tuple(self._color_override)
        if self._sel_rgb is not None:
            return tuple(self._sel_rgb)
        return (0, 0, 0)

    def _update_color_widgets(self):
        rgb = self._eff_color()
        hexs = "#%02x%02x%02x" % rgb
        try:
            self.sw_color.configure(bg=hexs)
        except tk.TclError:
            pass
        src = ("自定义" if self._color_override is not None
               else ("跟随原文" if self._sel_rgb is not None else "默认黑"))
        self.lbl_color.configure(text=f"{hexs} · {src}")

    def _pick_color_dialog(self):
        """点色块 -> 调色板选颜色。"""
        rgb, _hex = colorchooser.askcolor(color="#%02x%02x%02x" % self._eff_color(),
                                          title="选择文字颜色")
        if rgb:
            self._color_override = tuple(int(round(v)) for v in rgb)
            self._update_color_widgets()
            self._log("颜色改为 #%02x%02x%02x（自定义）" % self._color_override)
            self._on_draft_change()

    def _use_original_color(self):
        """恢复"跟随原文颜色"。"""
        self._color_override = None
        self._update_color_widgets()
        self._log("颜色恢复为跟随原文： #%02x%02x%02x" % self._eff_color())
        self._on_draft_change()

    def _start_eyedropper(self):
        """取色器：进入取色状态，随后在页面上点一下即取该处颜色。"""
        if self.orig is None:
            messagebox.showinfo(APP_TITLE, "请先打开 PDF.")
            return
        self._picking = True
        try:
            self.canvas.configure(cursor="crosshair")
        except tk.TclError:
            pass
        self._set_hint("取色器：在左侧页面上点一下要取的颜色")
        self._log("取色器：在左侧页面上点一下")

    def _sample_color_at(self, x, y):
        """页面上取色：取这一小片里"最深的像素"（文字总比底色深）。"""
        try:
            pm = self._page().get_pixmap(matrix=fitz.Matrix(8, 8),
                                         clip=fitz.Rect(x - 2, y - 2, x + 2, y + 2),
                                         alpha=False)
        except Exception:
            return None
        s, n = pm.samples, pm.n
        if n < 3:
            return None
        best, best_lum = None, 1e9
        for i in range(0, len(s) - n + 1, n):
            r, g, b = s[i], s[i + 1], s[i + 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < best_lum:
                best, best_lum = (r, g, b), lum
        return best

    def _update_add_state(self):
        state = "normal" if (self.e_old.get().strip() and self.e_new.get().strip()) else "disabled"
        self.btn_add.config(state=state)

    # ================= 打开 / 页码 =================
    def open_pdf(self):
        path = filedialog.askopenfilename(title="选择 PDF", filetypes=[("PDF", "*.pdf"), ("所有文件", "*.*")])
        if path:
            self.load_pdf(path)

    def load_pdf(self, path):
        try:
            self.orig = fitz.open(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"打开失败: {e}")
            return
        self.src_path = path
        self.page_no = 0
        self.rules = []
        self.cfg_globals = {}
        self._after_doc = None
        self._preview_sig = None
        self._span_cache.clear()
        self._tiles.clear()
        self._sel_bbox = None
        self._set_font_row("")               # 换文档了：识别行收起
        self._spans_of_page(self.page_no)      # 预热当前页片段缓存
        self._page_diag(self.page_no)
        self._refresh_rules()
        self.title(f"{APP_TITLE} — {os.path.basename(path)}")
        self._log(f"已打开: {path} ({self.orig.page_count} 页)")
        self.canvas.delete("empty")           # 有文档了，撤掉引导卡
        self._update_hint()
        self.update_idletasks()
        self.autofit()

    def change_page(self, d):
        if self.orig is None:
            return
        np_ = min(max(self.page_no + d, 0), self.orig.page_count - 1)
        if np_ != self.page_no:
            self.page_no = np_
            self._sel_bbox = None
            self.canvas.delete("sel")
            self._tiles.clear()
            self._render_all()
            self.lbl_page.config(text=f"{self.page_no + 1}/{self.orig.page_count}")
            self._page_diag(self.page_no)

    # ================= 渲染（只画可见区域） =================
    def _span_dicts(self, pno):
        """当前页片段（带字体名/字号/粗斜/颜色），按页缓存。"""
        if pno not in self._span_cache:
            spans = []
            for _p, _page, span, text in core.iter_spans(self.orig, pno):
                if not text.strip():
                    continue
                spans.append({"rect": fitz.Rect(span["bbox"]), "text": text,
                              "font": span.get("font", ""), "size": span.get("size", 10),
                              "flags": int(span.get("flags", 0)),
                              "color": int(span.get("color", 0))})
            self._span_cache[pno] = spans
        return self._span_cache[pno]

    def _page_diag(self, pno):
        """本页文字层概况写进日志：让用户明白为什么"点不中"或者"只能选一个字"。

        - 片段数 0：没有文字层（图片扫描，或文字已被转成矢量轮廓）—— 点选不可用
        - Type3 片段：字形由 PDF 自带（网页/MD 转 PDF 常见），这类文件常常一个字一个片段
        - 无文字映射的字形：原文会显示成乱码，可在「原文」里手改
        """
        spans = self._span_dicts(pno)
        n = len(spans)
        if n == 0:
            self._log("本页没有文字层（可能是图片扫描，或文字已被转成矢量）：点选不可用")
            return
        t3 = sum(1 for d in spans if str(d["font"]).lower().startswith("type3"))
        bad = 0
        try:
            for sp in self.orig[pno].get_texttrace():
                for ch in sp.get("chars", ()):
                    if ch[0] in (0, 0xFFFD) or ch[0] < 0:
                        bad += 1
        except Exception:
            pass
        msg = f"本页可选取片段 {n} 个"
        if t3:
            msg += (f"（其中 {t3} 个是 Type3 内嵌字形，常见于网页/MD 转 PDF，"
                    f"粒度可能细到单个字 —— 可按住左键框选一行）")
        if bad:
            msg += f"；{bad} 个字形没有文字映射，原文可能是乱码（可在「原文」里手改）"
        self._log(msg)

    def _spans_of_page(self, pno):
        """当前页可点选的文字片段 [(rect, text, font, size)]，按页缓存。"""
        return [(d["rect"], d["text"], d["font"], d["size"]) for d in self._span_dicts(pno)]

    def _span_at(self, pt):
        """点中的片段（含粗斜/颜色等完整信息）。"""
        for d in self._span_dicts(self.page_no):
            if d["rect"].contains(pt):
                return d
        return None

    def _set_scrollregion(self):
        if self.orig is None:
            return
        page = self.orig[self.page_no]
        sr = (0, 0, int(page.rect.width * self.zoom), int(page.rect.height * self.zoom))
        last = getattr(self, "_last_sr", None)
        if sr != last:
            for c in self._canvases():
                c.configure(scrollregion=sr)
            self._last_sr = sr

    def _visible_pts(self, canvas):
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        x0 = canvas.canvasx(0)
        y0 = canvas.canvasy(0)
        return fitz.Rect(x0 / self.zoom, y0 / self.zoom,
                         (x0 + w) / self.zoom, (y0 + h) / self.zoom), w, h

    def _make_photo(self, pm):
        try:
            return tk.PhotoImage(data=pm.tobytes("ppm"))
        except tk.TclError:
            return tk.PhotoImage(data=pm.tobytes("png"))

    def _render_canvas(self, canvas, doc):
        page = doc[self.page_no]
        pw, ph = page.rect.width, page.rect.height
        vis, w, h = self._visible_pts(canvas)
        if w < 50 or h < 50:
            clip = fitz.Rect(0, 0, pw, ph)      # 尺寸还没算出来时整页兜底
        else:
            mx = vis.width * TILE_MARGIN
            my = vis.height * TILE_MARGIN
            clip = fitz.Rect(max(0.0, vis.x0 - mx), max(0.0, vis.y0 - my),
                             min(pw, vis.x1 + mx), min(ph, vis.y1 + my))
        pm = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), clip=clip)
        img = self._make_photo(pm)
        x, y = clip.x0 * self.zoom, clip.y0 * self.zoom
        item = self._img_items.get(canvas)
        if item is not None:
            try:
                canvas.itemconfigure(item, image=img)
                canvas.coords(item, x, y)
            except tk.TclError:
                item = None
        if item is None:
            item = canvas.create_image(x, y, anchor="nw", image=img, tags="page")
            self._img_items[canvas] = item
        canvas.tag_lower(item)          # 图始终在最底层，避免盖住选区高亮
        canvas.delete("stale")
        canvas.image = img
        self._tiles[canvas] = (self.zoom, clip)

    def _covered(self, canvas):
        t = self._tiles.get(canvas)
        if not t:
            return False
        z, clip = t
        if abs(z - self.zoom) > 1e-6:
            return False
        vis, w, h = self._visible_pts(canvas)
        if w < 50 or h < 50:
            return False
        return (vis.x0 >= clip.x0 - 1 and vis.y0 >= clip.y0 - 1
                and vis.x1 <= clip.x1 + 1 and vis.y1 <= clip.y1 + 1)

    def _ensure_after_doc(self):
        """重建「对比」文档：清单 + 表单里的草稿一起参与；规则变了才重建。"""
        if self.orig is None:
            self._after_doc, self._preview_sig = None, None
            return
        rules = self._preview_rules()
        if not rules:
            self._after_doc, self._preview_sig = None, None
            return
        sig = json.dumps(rules, sort_keys=True, ensure_ascii=False, default=str)
        if self._after_doc is None or sig != self._preview_sig:
            try:
                self._after_doc = self._build_working(rules)
                self._preview_sig = sig
            except Exception as e:
                self._log(f"生成预览失败: {e}")
                self._after_doc, self._preview_sig = None, None

    def _render_all(self):
        if self.orig is None:
            return
        self._set_scrollregion()
        self._render_canvas(self.canvas, self.orig)
        if self.show_after.get():
            self._ensure_after_doc()
            if self._after_doc is not None:
                self._render_canvas(self.canvas_after, self._after_doc)
            else:
                self.canvas_after.delete("all")
                self._tiles.pop(self.canvas_after, None)
                self._img_items.pop(self.canvas_after, None)
        if self._sel_bbox is not None:
            self._highlight(self._sel_bbox)

    def _show_empty_card(self, _event=None):
        """没打开文档时，在预览区中央显示引导卡。"""
        if self.orig is not None:
            return
        cv = self.canvas
        cv.delete("empty")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 220 or h < 170:
            return
        p = self._pal
        cx, cy = w / 2, h / 2
        cv.create_rectangle(cx - 180, cy - 72, cx + 180, cy + 72,
                            fill=p["field"], outline=p["border"], tags="empty")
        cv.create_text(cx, cy - 26, text="📄", font=("Segoe UI Emoji", 24), tags="empty")
        cv.create_text(cx, cy + 14, text="把 PDF 拖到这里，或点左上角「打开」",
                       font=UI_FONT, fill=p["fg"], tags="empty")
        cv.create_text(cx, cy + 40, text="支持可选中文字的电子文本 PDF（不支持扫描件）",
                       font=UI_FONT_SMALL, fill=p["muted"], tags="empty")

    def _schedule_render(self):
        if self.orig is None:                 # 还没打开文档：显示引导卡就够了
            self._show_empty_card()
            return
        if self._render_job is not None:
            return
        self._render_job = self.after(RENDER_DEBOUNCE_MS, self._do_scheduled_render)

    def _do_scheduled_render(self):
        self._render_job = None
        self._render_all()

    def _on_configure(self):
        if self.orig is not None:
            self._schedule_render()

    # ================= 缩放 / 滚动 =================
    def _page(self):
        return self.orig[self.page_no]

    def autofit(self):
        if self.orig is None:
            return
        self.update_idletasks()
        page = self._page()
        n = len(self.paned.panes()) or 1
        avail_w = self.paned.winfo_width() / n - 40
        avail_h = self.paned.winfo_height() - 40
        if avail_w < 60 or avail_h < 60:
            return
        z = min(avail_w / page.rect.width, avail_h / page.rect.height)
        z = min(max(z, ZOOM_MIN), ZOOM_MAX)
        self._tiles.clear()
        self.set_zoom(z, pivot=None, force=True)

    def set_zoom(self, new, pivot=None, force=False):
        new = min(max(float(new), ZOOM_MIN), ZOOM_MAX)
        if not force and abs(new - self.zoom) < 1e-4:
            return
        old = self.zoom
        if pivot is None:
            sx = max(1, self.canvas.winfo_width()) / 2
            sy = max(1, self.canvas.winfo_height()) / 2
            pivot = (sx, sy, self.canvas.canvasx(sx), self.canvas.canvasy(sy))
        sx, sy, cx, cy = pivot
        fx, fy = cx / old, cy / old
        self.zoom = new
        self._updating = True
        self.zoom_var.set(new * 100)
        self.e_zoom.delete(0, "end")
        self.e_zoom.insert(0, f"{new * 100:.0f}")
        self._updating = False
        # 先更新 scrollregion 并定位，再按新视口渲染，避免出现空白
        self._last_sr = None
        self._set_scrollregion()
        page = self._page()
        cw = page.rect.width * new or 1
        ch = page.rect.height * new or 1
        self.canvas.xview_moveto(max(0.0, fx * new - sx) / cw)
        self.canvas.yview_moveto(max(0.0, fy * new - sy) / ch)
        # 校正一次（消除取整/浮点误差），确保锚点严格停在鼠标处
        try:
            self.update_idletasks()
            dxx = self.canvas.canvasx(sx) - fx * new
            if abs(dxx) > 0.4:
                self.canvas.xview_moveto(max(0.0, self.canvas.canvasx(0) - dxx) / cw)
            dyy = self.canvas.canvasy(sy) - fy * new
            if abs(dyy) > 0.4:
                self.canvas.yview_moveto(max(0.0, self.canvas.canvasy(0) - dyy) / ch)
        except tk.TclError:
            pass
        self._sync_from(self.canvas)
        self._tiles.clear()
        self._render_all()

    def _on_slider(self, _v):
        if self._updating:
            return
        self.set_zoom(self.zoom_var.get() / 100.0)

    def _apply_zoom_entry(self):
        if self._updating:
            return
        s = self.e_zoom.get().strip().rstrip("%")
        try:
            self.set_zoom(float(s) / 100.0)
        except ValueError:
            self.e_zoom.delete(0, "end")
            self.e_zoom.insert(0, f"{self.zoom * 100:.0f}")

    def _on_wheel_scroll(self, event):
        delta = -3 if event.delta > 0 else 3
        self._yview("scroll", delta, "units")
        return "break"

    def _on_wheel_hscroll(self, event):
        delta = -3 if event.delta > 0 else 3
        self._xview("scroll", delta, "units")
        return "break"

    def _on_wheel_zoom(self, event):
        canvas = event.widget
        pivot = (event.x, event.y, canvas.canvasx(event.x), canvas.canvasy(event.y))
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self.set_zoom(self.zoom * factor, pivot=pivot)
        return "break"

    def _yscroll_set(self, lo, hi):
        self.vbar.set(lo, hi)

    def _xscroll_set(self, lo, hi):
        self.hbar.set(lo, hi)

    def _canvases(self):
        if self.show_after.get():
            return (self.canvas, self.canvas_after)
        return (self.canvas,)

    def _yview(self, *args):
        for c in self._canvases():
            c.yview(*args)
        self._after_scroll()

    def _xview(self, *args):
        for c in self._canvases():
            c.xview(*args)
        self._after_scroll()

    def _after_scroll(self):
        for c in self._canvases():
            if not self._covered(c):
                self._schedule_render()
                return

    def _sync_from(self, src):
        xv, yv = src.xview(), src.yview()
        for c in self._canvases():
            if c is not src:
                c.xview_moveto(xv[0])
                c.yview_moveto(yv[0])
        self.vbar.set(*yv)
        self.hbar.set(*xv)

    def _pan_start(self, event, canvas):
        canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event, canvas):
        canvas.scan_dragto(event.x, event.y, gain=1)
        self._sync_from(canvas)
        self._after_scroll()

    def _on_toggle_after(self):
        self._toggle_after(autofit=True)

    def _toggle_after(self, autofit=False):
        try:
            panes = [str(p) for p in self.paned.panes()]
            if self.show_after.get():
                if str(self.f_after) not in panes:
                    self.paned.add(self.f_after, stretch="always", minsize=180)
            else:
                if str(self.f_after) in panes:
                    self.paned.forget(self.f_after)
                self._tiles.pop(self.canvas_after, None)
                self._img_items.pop(self.canvas_after, None)
                self.canvas_after.delete("all")
        except tk.TclError:
            pass
        self._tiles.clear()
        self._last_sr = None
        if autofit:
            self.autofit()
        else:
            self._render_all()
        self._sync_from(self.canvas)

    def _enable_after(self):
        if not self.show_after.get():
            self.show_after.set(True)
            self._toggle_after(autofit=True)

    # ================= 画布交互 =================
    DRAG_MIN = 3          # 拖动不足这么多像素就算单击，不算框选

    def _evt_pt(self, event):
        """事件坐标 -> 页面坐标。"""
        return (self.canvas.canvasx(event.x) / self.zoom,
                self.canvas.canvasy(event.y) / self.zoom)

    def on_canvas_press(self, event):
        """左键按下：先记锚点，等松开时再决定是「单击」还是「框选」。"""
        if self.orig is None:
            return
        x, y = self._evt_pt(event)
        self._drag = {"x0": x, "y0": y, "x1": x, "y1": y,
                      "moved": False, "pick": bool(self._picking), "item": None}

    def on_canvas_drag(self, event):
        """拖动中：画选框。Tk 没有透明度，用网纹填充 + 虚线边框代替。"""
        d = self._drag
        if not d or d["pick"] or self.orig is None:
            return
        x, y = self._evt_pt(event)
        d["x1"], d["y1"] = x, y
        if (abs(x - d["x0"]) * self.zoom < self.DRAG_MIN
                and abs(y - d["y0"]) * self.zoom < self.DRAG_MIN):
            return
        d["moved"] = True
        box = (d["x0"] * self.zoom, d["y0"] * self.zoom, x * self.zoom, y * self.zoom)
        if d["item"] is None:
            d["item"] = self.canvas.create_rectangle(
                *box, outline=self._pal["mark"], dash=(4, 3),
                fill=self._pal["mark"], stipple="gray25", tags="rubber")
        else:
            self.canvas.coords(d["item"], *box)

    def on_canvas_release(self, event):
        """松开左键：没拖动 = 原来的单击（含取色器）；拖动了 = 框选。"""
        d, self._drag = self._drag, None
        if not d or self.orig is None:
            return
        if d["item"] is not None:
            self.canvas.delete(d["item"])
        if not d["moved"] or d["pick"]:
            self.on_canvas_click(event)
            return
        x, y = self._evt_pt(event)
        self._range_select(fitz.Rect(min(d["x0"], x), min(d["y0"], y),
                                     max(d["x0"], x), max(d["y0"], y)),
                           anchor=fitz.Point(d["x0"], d["y0"]))

    def on_canvas_click(self, event):
        if self.orig is None:
            return
        x = self.canvas.canvasx(event.x) / self.zoom
        y = self.canvas.canvasy(event.y) / self.zoom
        if self._picking:                     # 取色器：这一下是来取色的
            self._picking = False
            try:
                self.canvas.configure(cursor="")
            except tk.TclError:
                pass
            rgb = self._sample_color_at(x, y)
            if rgb:
                self._color_override = tuple(rgb)
                self._update_color_widgets()
                self._log("取色器取到 #%02x%02x%02x" % rgb)
                self._on_draft_change()
            self._update_hint()
            return
        d = self._span_at(fitz.Point(x, y))
        if not d:
            return
        if self.v_scope.get() == "range":     # 这一次是单击，不是框选
            self.v_scope.set("single")
        self._apply_span_choice(d)

    def _range_select(self, rect, anchor=None):
        """框选：取「锚点那一行」内与矩形相交的片段，合并成一条 range 规则。

        为什么先只支持一行：跨行的片段并成一条规则后只有一条基线，新文字会全挤在
        第一行上（跨行重排是下一步）。限一行之后，Type3 那种"一个字一个片段"的
        文件就能一次圈到一整句。
        """
        anchor = anchor or fitz.Point(rect.x0, rect.y0)
        probe = fitz.Rect(rect)
        # 几乎水平的拖动矩形高度接近 0（无效矩形），intersects 会判成不相交 —— 撑开一点
        if probe.y1 - probe.y0 < 0.5:
            probe.y0 -= 0.5
            probe.y1 += 0.5
        if probe.x1 - probe.x0 < 0.5:
            probe.x0 -= 0.5
            probe.x1 += 0.5
        # 判据与 pdf_edit_core 的 range 匹配**共用同一个函数**：
        # 两边口径一致，"界面高亮选了哪些"就等于"规则实际会改哪些"。
        hit = [d for d in self._span_dicts(self.page_no)
               if core.center_in_rect(probe, d["rect"])]
        if not hit:
            self._log("框选范围内没有文字（本页可能没有文字层，点选也不可用）")
            self._update_hint()
            return
        # 锚点所在行：优先"圈住了锚点"的片段，否则取纵向最近的那一段
        base = next((d for d in hit if d["rect"].contains(anchor)), None)
        if base is None:
            base = min(hit, key=lambda d: min(abs(d["rect"].y0 - anchor.y),
                                              abs(d["rect"].y1 - anchor.y)))
        y0, y1 = base["rect"].y0 - 1.0, base["rect"].y1 + 1.0
        mid = lambda d: (d["rect"].y0 + d["rect"].y1) / 2        # noqa: E731
        sel = sorted((d for d in hit if y0 <= mid(d) <= y1), key=lambda d: d["rect"].x0)
        if not sel:
            return
        if len(sel) < len(hit):
            self._log("框选跨了多行：暂时只取锚点那一行（跨行替换还没做）")
        # 样式取"出现最多"的那一种，避免个别混排字把整段带偏
        cnt = {}
        for d in sel:
            k = (d["font"], round(float(d["size"]), 1), int(d["flags"]), int(d["color"]))
            cnt[k] = cnt.get(k, 0) + 1
        key = max(cnt, key=lambda k: cnt[k])
        fonts = sorted({d["font"] for d in sel if d["font"]})
        union = fitz.Rect(sel[0]["rect"])
        for d in sel[1:]:
            union |= d["rect"]
        merged = {"rect": union, "text": self._join_selected(sel),
                  "font": key[0], "size": key[1], "flags": key[2], "color": key[3]}
        self.v_scope.set("range")             # 框选 = 只改这一段
        self._apply_span_choice(merged, count=len(sel), from_range=True, fonts=fonts)
        if len(cnt) > 1:
            self._log(f"提示：选区里有 {len(cnt)} 种样式，已统一按 {key[0]} {fmt_num(key[1])}pt 处理")
        if len(fonts) > 1:
            self._log(f"注意：本选区含 {len(fonts)} 种字体（{'、'.join(fonts)}）："
                      f"新文字按出现最多的那一种起排，缺字形的字会逐字回退到覆盖它的字体")

    @staticmethod
    def _join_selected(sel):
        """拼接选中的片段文本；片段之间有空隙就补一个空格。

        PDF 里词间空格通常不是字符，而是字形之间的空隙 —— 不补的话
        "Hello world" 会粘成 "Helloworld"（中文一般没空隙，不受影响）。
        """
        out, prev = "", None
        for d in sel:
            if prev is not None:
                gap = d["rect"].x0 - prev["rect"].x1
                if gap > 0.25 * max(float(d["size"]), 1.0) and not out.endswith(" "):
                    out += " "
            out += d["text"]
            prev = d
        return out

    def _apply_span_choice(self, d, count=1, from_range=False, fonts=None):
        """把一个片段（或框选合并后的结果）填进表单。fonts = 本次选区涉及的所有字体名。"""
        rect, text, font, size = d["rect"], d["text"], d["font"], d["size"]
        self._sel_from_range = bool(from_range)
        self._sel_span_count = int(count)
        self._sel_fonts = list(fonts or ([font] if font else []))
        self._sel_bbox = rect
        self._set_e_old(text)
        self.e_size.delete(0, "end")
        self.e_size.insert(0, fmt_num(size))     # 保精度：10.45 就是 10.45，不取整
        # 颜色默认跟随原文（灰字/彩字不会被悄悄改成黑色）
        self._sel_rgb = core.int_to_rgb(d["color"])
        self._color_override = None
        self._update_color_widgets()
        # 字体自动跟随：按字体名在系统字体里定位（含粗/斜）；系统里没有就不顶替，弹浮窗
        info = core.resolve_font(font, bold=bool(d["flags"] & 16), italic=bool(d["flags"] & 2))
        self._font_info = info
        self._sel_size = size
        if info["source"] == "missing":
            self.cb_font.set(f"⚠ 需要安装：{info['raw']}")     # 明确标出缺什么，不静默用宋体
            self._show_missing_font_toast(info)
        else:
            self._select_font(info["path"], info["face"])
        self._show_font_info(info, size)
        self.e_border.delete(0, "end")
        bx = self._nearest_left_border(rect)
        if bx is not None:
            self.e_border.insert(0, f"{bx:.2f}")
        self._highlight(rect)
        self._update_add_state()
        self.e_new.focus_set()
        self._update_hint()
        head = f"已框选 {count} 个片段" if count > 1 else "选中"
        self._log(f'{head}: {text!r} → 字体 {font or "?"} / {info["family"]}, {fmt_num(size)}pt')
        if self.e_new.get().strip():
            self._enable_after()
        self._update_glyph_note()
        self._schedule_draft_preview()

    def _on_new_text(self):
        self._update_add_state()
        self._update_hint()
        if self.e_old.get().strip() and self.e_new.get().strip():
            self._enable_after()
        self._update_glyph_note()          # 打字即更新「替换」行标注（日志走防抖那次）
        self._schedule_draft_preview()

    def _update_glyph_note(self, log=False):
        """「替换」行右侧的小标注（log=True 时同时写日志）。

        两类提醒：① 替换文字里有当前字体没有的字形 —— 会自动**逐字**回退到包含该字形的
        字体；② 选区里不止一种字体（框选常见）。详细内容（"哪几个字 → 落到哪个字体"）
        在打字停顿后写进日志；标注只留最要紧的两条，多的用「…」收尾，免得把输入框挤没。
        预览与导出走同一套 core 逻辑，所以标注即所见。
        """
        chips, detail = [], []                  # chips: (严重度, 短标注)；越靠前越该让人动手
        new = self.e_new.get().strip()
        fp, ff = self._selected_font()
        if self.orig is not None and new and fp:
            # 判定只跟 (字体, 字面, 新文字) 有关：同一串就别每敲一键重算一遍
            key = (os.path.normcase(fp), int(ff), new)
            if getattr(self, "_glyph_plan_key", None) != key:
                fb, nof = {}, []
                for ch, path, face, status in core.char_font_plan(new, fp, ff):
                    if status == "fallback":
                        fb.setdefault(core.font_label(path, face), []).append(ch)
                    elif status == "none":
                        nof.append(ch)
                self._glyph_plan_key, self._glyph_plan_cache = key, (fb, nof)
            fallback, no_font = self._glyph_plan_cache
            if no_font:                             # 连候选字体都没有：只能人工处理，最要紧
                chips.append((0, f"⚠ {len(no_font)} 字无字体"))
                detail.append("这些字连候选字体里都没有字形，可能显示成方框：%s" % "".join(no_font))
            if fallback:                            # 已自动逐字回退：说清落到哪个字体
                cnt = sum(len(v) for v in fallback.values())
                chips.append((1, f"⚠ {cnt} 字回退"))
                detail.append("替换文字里有 %d 个字当前字体没有，已回退：%s"
                              % (cnt, "；".join("%s → %s" % ("".join(v), k)
                                                for k, v in fallback.items())))
        fonts = list(getattr(self, "_sel_fonts", ()) or ())
        if len(fonts) > 1:                          # 选区混了多种字体：上下文提醒
            chips.append((2, f"⚠ {len(fonts)} 种字体"))
            detail.append("本选区含 %d 种字体：%s（新文字按出现最多的那一种起排，"
                          "缺字形的字会逐字回退到覆盖它的字体）" % (len(fonts), "、".join(fonts)))
        chips.sort(key=lambda kv: kv[0])            # 稳定排序（同级保持原顺序）
        shown = [t for _k, t in chips[:GLYPH_CHIP_MAX]]     # 「替换」行很窄：多的进日志
        if len(chips) > GLYPH_CHIP_MAX:
            shown.append("…")
        tip = " · ".join(shown)
        try:
            self.lbl_glyph.configure(text=tip,
                                     foreground=self._pal["warn"] if tip else self._pal["muted"])
        except tk.TclError:
            pass
        sig = " | ".join(detail)
        # 只有真正写了日志才记签名：打字时的无日志调用不能"吃掉"这次提醒
        if log and sig and sig != self._glyph_log_sig:
            for line in detail:
                self._log(line)
            self._glyph_log_sig = sig

    def _on_scope_change(self, _event=None):
        """「范围」变了：框选合并出来的原文按「所有相同文本」匹配不到东西，先提醒一句。"""
        if self.v_scope.get() == "all" and self._sel_from_range and self._sel_span_count > 1:
            self._log(f"注意：这条原文是框选合并的 {self._sel_span_count} 个片段，"
                      f"按「所有相同文本」几乎匹配不到（要只改这一处请选「框选的这一段」）")
        self._on_draft_change()

    def _on_draft_change(self, _event=None):
        """字体 / 字号 / 对齐 / 间隙 / 范围等影响绘制的项变了：刷新即时预览。"""
        if self.orig is None:
            return
        # 之前是"缺少字体"，但用户自己在下拉里选了字体 -> 就按他选的来
        if (self._font_info or {}).get("source") == "missing" and self.cb_font.current() >= 0:
            fp, _ff = self._selected_font()
            self._font_info = dict(self._font_info, source="manual",
                                   family=os.path.basename(fp or ""))
            self._show_font_info(self._font_info, self._sel_size)
        if self._draft_rule() is not None:
            self._enable_after()
        self._update_glyph_note()
        self._schedule_draft_preview()

    def _schedule_draft_preview(self):
        """草稿变了：稍等一下再重建预览，别每敲一键就重建整份文档。"""
        if self.orig is None:
            return
        if self._draft_job is not None:
            self.after_cancel(self._draft_job)
        self._draft_job = self.after(DRAFT_DEBOUNCE_MS, self._do_draft_preview)

    def _do_draft_preview(self):
        self._draft_job = None
        self._render_all()
        self._update_glyph_note(log=True)   # 打字停顿后再写日志，避免每敲一键刷屏

    def _nearest_left_border(self, rect):
        page = self._page()
        best = None
        for d in page.get_drawings():
            for it in d.get("items", []):
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.x - p2.x) < 0.5:
                        y0, y1 = sorted((p1.y, p2.y))
                        if y0 - 1 <= rect.y0 and y1 + 1 >= rect.y1 and rect.x0 - 80 < p1.x <= rect.x0 + 0.5:
                            best = p1.x if best is None else max(best, p1.x)
                elif it[0] == "re":
                    r = it[1]
                    if r.height > 4 and rect.x0 - 80 < r.x1 <= rect.x0 + 0.5 and r.y0 - 1 <= rect.y0 and r.y1 + 1 >= rect.y1:
                        best = r.x1 if best is None else max(best, r.x1)
        return best

    def _highlight(self, rect):
        self.canvas.delete("sel")
        x0, y0, x1, y1 = (rect.x0 * self.zoom, rect.y0 * self.zoom,
                          rect.x1 * self.zoom, rect.y1 * self.zoom)
        self.canvas.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                                     outline=self._pal["mark"], width=2, tags="sel")

    def clear_selection(self):
        """取消选择：清空 原文/替换 两栏，去掉左侧高亮。"""
        self._clear_fields()

    def _clear_fields(self):
        """清空「原文」「替换」，取消左侧高亮，并回到当前阶段提示。"""
        self._set_e_old("")
        self.e_new.delete(0, "end")
        self._sel_bbox = None
        self._sel_rgb = None
        self._sel_from_range = False
        self._sel_span_count = 1
        self._sel_fonts = []
        self._glyph_log_sig = None
        self._color_override = None
        self.canvas.delete("sel")
        self._update_add_state()
        self._update_hint()
        self._update_color_widgets()
        self._set_font_row("")               # 没有选中片段了：识别行收起
        self._update_glyph_note()
        self._schedule_draft_preview()      # 草稿没了：预览回到「只按清单」

    # ================= 规则 =================
    def _form_rule(self):
        """按表单当前内容组装一条规则（不校验、不去重）。"""
        try:
            size = float(self.e_size.get() or 10)
        except ValueError:
            size = 10.0
        fp, ff = self._selected_font()
        rule = {"old": self.e_old.get().strip(),
                "new": self.e_new.get().strip(),
                "font": fp or "",
                "font_face": ff,
                "font_size": size,
                "color": list(self._eff_color()),
                "bold_stroke": self._stroke_value()}
        if self.v_align.get() == "left":
            rule["align"] = "left"
            try:
                rule["left_border_x"] = float(self.e_border.get())
            except Exception:
                rule["left_border_x"] = None            # 还没定位到左边框
            rule["left_gap"] = self._gap_value() * size
        if self.v_scope.get() in ("single", "range"):
            rule["scope"] = self.v_scope.get()
            rule["page"] = self.page_no
            if self._sel_bbox is not None:
                rule["bbox"] = [round(v, 2) for v in (self._sel_bbox.x0, self._sel_bbox.y0,
                                                      self._sel_bbox.x1, self._sel_bbox.y1)]
        return rule

    def _draft_rule(self):
        """表单草稿（只用于预览）；内容不全或参数没备齐时返回 None。"""
        rule = self._form_rule()
        if not rule["old"] or not rule["new"]:
            return None
        if rule.get("align") == "left" and rule.get("left_border_x") is None:
            # 左对齐但还没算出边框位置：预览先按「保持原位」，免得画到莫名其妙的地方
            for k in ("align", "left_border_x", "left_gap"):
                rule.pop(k, None)
        return rule

    def _preview_rules(self):
        """预览规则 = 清单 + 草稿；原文/页/位置都相同的，草稿顶掉清单里那条。

        不这样做的话同一条会被匹配两次：删两遍、字也叠着画两遍。
        """
        rules = [dict(r) for r in self.rules]
        draft = self._draft_rule()
        if draft is None:
            return rules
        key = rule_key(draft)
        for i, r in enumerate(rules):
            if rule_key(r) == key:
                rules[i] = draft
                break
        else:
            rules.append(draft)
        return rules

    def add_rule(self):
        rule = self._form_rule()
        if not rule["old"]:
            messagebox.showwarning(APP_TITLE, "请先在左侧预览里点选要修改的文字.")
            return
        if not rule["new"]:
            messagebox.showwarning(APP_TITLE, '请填写"替换"内容.')
            return
        if rule.get("scope") == "range" and not rule.get("bbox"):
            # 框选的范围没了（典型：框选后翻页/换页，选区被清掉但表单还留着）
            messagebox.showwarning(
                APP_TITLE,
                "框选的范围已经失效（比如翻过页）。\n\n"
                "请在左侧预览里重新框选一次，或把「范围」改成「所有相同文本」。")
            return
        if rule.get("scope", "all") == "all" and self._sel_from_range and self._sel_span_count > 1:
            # 框选合并的原文（多个片段拼起来）几乎不可能等于 PDF 里某一段真实文字，
            # 按「所有相同文本」加进去是"白加一条"（静默无效果），所以拦下来。
            messagebox.showwarning(
                APP_TITLE,
                f"这条原文是把 {self._sel_span_count} 个片段拼起来的（框选合并），"
                f"按「所有相同文本」几乎匹配不到任何地方，加了不会有任何效果。\n\n"
                "· 要只改框选的这一处：把「范围」选回「框选的这一段」\n"
                "· 要全文替换：先把这里改成某一处真实存在的片段文字（单击选中它再添加）")
            return
        if not rule["font"]:
            raw = (self._font_info or {}).get("raw") or "未知"
            messagebox.showwarning(
                APP_TITLE,
                f"这段文字用的字体（{raw}）系统里没有，先不替你决定用哪个字体。\n\n"
                f"· 把字体文件放进「字体文件夹」（不用安装），或\n"
                f"· 点浮窗里的「先用 … 代替」，或\n"
                f"· 自己在下拉里挑一个字体\n"
                f"之后再点「添加到清单」。")
            return
        if rule.get("align") == "left" and rule.get("left_border_x") is None:
            messagebox.showwarning(APP_TITLE, '左对齐需要"左边框x" (点选片段会自动填入).')
            return
        key = rule_key(rule)
        replaced = False
        for i, r in enumerate(self.rules):
            if rule_key(r) == key:
                self.rules[i] = rule
                replaced = True
                break
        if not replaced:
            self.rules.append(rule)
        self._refresh_rules()
        self._enable_after()
        self._render_all()
        self._log(("已更新" if replaced else "已添加") + f"规则: {rule['old']!r} -> {rule['new']!r}")
        self._clear_fields()

    def del_rule(self):
        for iid in self.tree.selection():
            r = self.rules.pop(int(iid))
            self._log(f"已删除: {r['old']!r}")
            break
        self._refresh_rules()
        self._render_all()
        self._clear_fields()

    def clear_rules(self):
        self.rules = []
        self._refresh_rules()
        self._render_all()
        self._log("清单已清空")
        self._clear_fields()

    def _refresh_rules(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            scope = {"single": "  ·仅此处", "range": "  ·框选段"}.get(r.get("scope"), "")
            self.tree.insert("", "end", iid=str(i),
                             values=(f"{r['old']}  →  {r['new']}{scope}",))
        self.rules_frame.configure(text=f"修改清单  ({len(self.rules)})")
        self._update_add_state()
        self._update_hint()

    # ================= 文档构建 =================
    def _build_working(self, rules=None):
        """构建"改后"文档。省略 rules 时用修改清单（导出用）；传参时按那份构建（预览用）。"""
        if self.orig is None:
            raise RuntimeError("未打开 PDF")
        rules = self.rules if rules is None else rules
        doc = fitz.open(self.src_path)
        cfg = dict(self.cfg_globals)      # CLI 风格 config 的全局字体/字号/颜色靠这里补齐
        cfg["replacements"] = rules
        targets = core.collect_targets(doc, rules, cfg)
        core.apply_replacements(doc, cfg, targets)
        return doc

    def auto_calibrate(self):
        if self.orig is None:
            messagebox.showinfo(APP_TITLE, "请先打开 PDF.")
            return
        ruled = {r["old"] for r in self.rules}
        cand = None
        for d in self._span_dicts(self.page_no):
            t = d["text"]
            if t.strip() and t not in ruled and (cand is None or len(t) > len(cand["text"])):
                cand = d
        if not cand:
            messagebox.showinfo(APP_TITLE, "没有可用于标定的文字.")
            return
        try:
            bw = self._calibrate(cand["rect"], cand["text"], cand["font"], cand["size"],
                                 core.int_to_rgb(cand["color"]))
        except Exception as e:
            self._log(f"标定失败: {e}")
            return
        self.bold_stroke = bw
        for r in self.rules:
            r["bold_stroke"] = bw
        self._sync_stroke_widgets(bw)
        self._render_all()
        self._log(f"自动标定完成: 描边 = {fmt_num(bw, 3)} (用 {cand['text']!r} 校准)"
                  + ("，判定原文不是伪加粗" if bw <= 0 else ""))

    def _calibrate(self, rect, text, font, size, color=(0, 0, 0)):
        info = core.resolve_font(font)
        font_file = core.ensure_ttf(info["path"], info["face"])
        rgb = tuple(v / 255.0 for v in core.norm_rgb(color))
        zoom = 6
        clip = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2)

        def gray(doc):
            pm = doc[self.page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, 0].astype(int)

        ref = gray(self.orig)
        origins = None
        for _p, _page, span, t in core.iter_spans(self.orig, self.page_no):
            if t == text and abs(fitz.Rect(span["bbox"]).x0 - rect.x0) < 0.01:
                origins = [tuple(c["origin"]) for c in span["chars"]]
                break
        if origins is None:
            return self.bold_stroke
        best = (1e9, self.bold_stroke)
        for bw in STROKE_CANDIDATES:        # 含 0：原文不是伪加粗时就能选"不描边"
            doc = fitz.open(self.src_path)
            pg = doc[self.page_no]
            r = fitz.Rect(rect)
            r.x0 -= core.DEFAULT_PAD_X
            r.x1 += core.DEFAULT_PAD_X
            r.y0 -= core.DEFAULT_PAD_Y
            r.y1 += core.DEFAULT_PAD_Y
            pg.add_redact_annot(r, fill=None)
            pg.apply_redactions(**core.PDF_REDACT)
            for ch, (x, y) in zip(text, origins):
                pg.insert_text(fitz.Point(x, y), ch, fontsize=size,
                               fontname=core.font_resource_name(info["path"], info["face"]),
                               fontfile=font_file, color=rgb, fill=rgb,
                               render_mode=2, border_width=bw)
            got = gray(doc)
            if got.shape != ref.shape:
                continue
            mean = float(np.abs(ref - got).mean())
            if mean < best[0]:
                best = (mean, bw)
        return best[1]

    def save_as(self):
        if self.orig is None:
            messagebox.showinfo(APP_TITLE, "请先打开 PDF.")
            return
        if not self.rules:
            messagebox.showinfo(APP_TITLE, "还没有任何修改规则.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile=os.path.splitext(os.path.basename(self.src_path))[0] + "_修改后.pdf")
        if not out:
            return
        try:
            work = self._build_working()
            core.finalize(work, out, self.orig.metadata)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"保存失败: {e}")
            return
        self._log(f"已保存: {out}")
        self._set_hint(f"✓ 已导出：{os.path.basename(out)}（可继续改，或再点「另存为…」）")
        messagebox.showinfo(APP_TITLE, f"已保存:\n{out}")

    # ================= config =================
    def export_config(self):
        if not self.rules:
            messagebox.showinfo(APP_TITLE, "清单为空.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")], initialfile="rules.json")
        if not path:
            return
        cfg = {
            "src": self.src_path,
            "out": os.path.splitext(self.src_path)[0] + "_edited.pdf" if self.src_path else "output.pdf",
            "font": self._selected_font_path(),
            "font_face": self._selected_font()[1],
            "font_name": "simsun",
            "font_size": self._form_rule()["font_size"],
            "color": list(self._eff_color()),
            "bold_stroke": self._stroke_value(),
            "replacements": self.rules,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self._log(f"已导出 config: {path}")

    # config 里"对整份文档生效"的默认项：导入时必须留下。CLI 风格的 config
    # （规则不带 font/font_size/color，靠全局项）否则会静默回落到内置默认。
    CONFIG_GLOBAL_KEYS = ("font", "font_face", "font_name", "font_size",
                          "color", "bold_stroke", "pad_x", "pad_y")

    def _apply_config(self, cfg, base_dir=None):
        """把一份 config（GUI 导出或 CLI 手写）装进界面：规则 / 描边 / 全局默认。"""
        self.rules = [r for r in cfg.get("replacements", [])
                      if isinstance(r, dict) and r.get("old")]
        self.bold_stroke = float(cfg.get("bold_stroke") or 0.0)
        self._sync_stroke_widgets()
        if not self.src_path and cfg.get("src"):
            src = cfg["src"] if os.path.isabs(cfg["src"]) else os.path.join(base_dir or "", cfg["src"])
            if os.path.exists(src):
                self.load_pdf(src)
        # 放在 load_pdf 之后：load_pdf 会重置全局默认（换文档就不该沿用旧 config 的）
        self.cfg_globals = {k: cfg[k] for k in self.CONFIG_GLOBAL_KEYS if k in cfg}
        self._refresh_rules()
        self._render_all()

    def import_config(self):
        path = filedialog.askopenfilename(title="选择 config.json",
                                          filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"读取失败: {e}")
            return
        self._apply_config(cfg, os.path.dirname(path))
        self._log(f"已导入 {len(self.rules)} 条规则: {path}")


def main():
    enable_dpi_awareness()
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    app = PdfEditorApp(initial=initial)
    app.mainloop()


if __name__ == "__main__":
    main()

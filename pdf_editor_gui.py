# -*- coding: utf-8 -*-
"""
pdf_editor_gui —— PDFTextEditor 图形界面（Tkinter）

性能要点：预览只渲染"当前可见区域 + 缓冲边"（clip 渲染），不整页渲染；
滚动/缩放后对未覆盖区域做防抖重绘，因此高倍缩放也不卡。

交互：
· 鼠标滚轮 = 上下滚动；Ctrl+滚轮 = 缩放（以鼠标位置为锚点）
· 勾选「对比预览」自动缩放到合适大小；选中文字并输入替换内容会自动开启对比
· 帮助为悬浮窗口；各面板之间分隔条可拖动调整宽高
依赖：PyMuPDF、fontTools、numpy（见 requirements.txt）
"""
from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz
import numpy as np

import pdf_edit_core as core

APP_TITLE = "PDFTextEditor"

# 字体：界面统一中文字体；标题用粗体着重
UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
ZOOM_MIN, ZOOM_MAX = 0.2, 5.0
TILE_MARGIN = 0.5          # 缓冲边 = 视口尺寸的 50%
RENDER_DEBOUNCE_MS = 40

STEPS = ("① 点左侧预览里的文字\n"
         "② 填\"替换为\"\n"
         "③ 点\"添加到清单\"\n"
         "④ 点\"另存为\"导出")

HELP_TEXT = f"""PDFTextEditor · 使用说明

────────────────────────────
【四步上手】
1. 点「打开 PDF」，选择要改的 PDF（必须是电子文本 PDF，文字可选中的）。
2. 在左侧「原图」里点一下要修改的那段文字，它会高亮，并自动带出字体、字号、左边框。
3. 在右侧「替换为」里输入新文字；需要时调整字体 / 字号 / 对齐 / 范围；
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
【各控件说明】
· 原文        ：从预览里点选出来的，只读。
· 替换为      ：要改成的新文字。
· 字体/字号   ：默认按原片段的字体/字号自动填好，一般不用改。
· 对齐        ：
    - 保持原位  ：新文字沿用原来的位置（默认）。
    - 左对齐留白：让新文字在单元格里左对齐，并留出「间隙(字宽)」的空白。
· 左边框x     ：左对齐时的参照线（点选片段会自动填入）。
· 范围        ：
    - 所有相同文本：该文字在文档里出现几次就全改（例如上下两份相同的表）。
    - 仅选中这一处：只改你点的这一处。
· 添加/更新   ：把当前设置加进「修改清单」；同一处的重复添加会覆盖。
· 自动标定加粗：改完之后如果字看起来偏粗/偏细，点一下自动校准描边宽度。
· 导入/导出 config：与命令行版共用同一份配置文件，便于复用与批量处理。

────────────────────────────
【常见问题】
Q：为什么改完字变粗或变细？
A：原文件的"加粗"常是用描边模拟的。点「自动标定加粗」重新校准即可。

Q：为什么导出的文件变小/变大了？
A：程序会对内嵌字体做子集化处理，体积通常和原文件接近；这是正常的。

Q：点不到文字 / 选中不了？
A：该 PDF 可能是扫描件（没有文字层），本工具不适用。

Q：改错了想撤销？
A：在「修改清单」里选中那条点「删除选中」，或点「清空」重新来。

Q：会不会把原文件改坏？
A：不会。程序始终从原文件重新生成，只有点「另存为…」时才写出新文件。
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
        self._after_dirty = True
        self.page_no = 0
        self.zoom = 1.2
        self.rules = []
        self.bold_stroke = 0.03
        self.page_spans = []
        self._span_cache = {}
        self._tiles = {}
        self._img_items = {}
        self._render_job = None
        self._sel_bbox = None
        self._updating = False
        self._pan = None
        self.show_after = tk.BooleanVar(value=False)
        self.font_choices = core.list_available_fonts()   # 本机可用字体（覆盖面广）

        self._apply_styles()
        self._build_ui()
        self._set_hint(STEPS)
        self._log('就绪. 请先点"打开 PDF". 第一次用请看"帮助".')
        self.after(80, self._on_configure)
        self.after(200, self._apply_minsizes)
        if initial and os.path.exists(initial):
            self.load_pdf(initial)

    # ================= UI =================
    def _apply_styles(self):
        """各级标题着重显示：分组标题、表头、面板标签用粗体；界面统一中文字体。"""
        style = ttk.Style(self)
        style.configure(".", font=UI_FONT)
        style.configure("TLabelframe.Label", font=UI_FONT_BOLD)
        style.configure("Treeview.Heading", font=UI_FONT_BOLD)

    def _build_ui(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="打开 PDF", command=self.open_pdf).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="◀", width=3, command=lambda: self.change_page(-1)).pack(side="left")
        self.lbl_page = ttk.Label(bar, text="0/0", width=7, anchor="center")
        self.lbl_page.pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self.change_page(1)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="自动标定加粗", command=self.auto_calibrate).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="导入 config", command=self.import_config).pack(side="left")
        ttk.Button(bar, text="导出 config", command=self.export_config).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Checkbutton(bar, text="对比预览", variable=self.show_after,
                        command=self._on_toggle_after).pack(side="left")
        ttk.Button(bar, text="适应窗口", command=self.autofit).pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="另存为…", command=self.save_as).pack(side="left")
        ttk.Button(bar, text="帮助", command=self.show_help).pack(side="right")

        content = tk.PanedWindow(self, orient="vertical", sashwidth=6, sashrelief="raised",
                                 background="#d0d0d0", bd=0, opaqueresize=False)
        self.content = content
        content.pack(fill="both", expand=True)

        outer = tk.PanedWindow(content, orient="horizontal", sashwidth=6, sashrelief="raised",
                               background="#d0d0d0", bd=0, opaqueresize=False)
        self.outer = outer
        content.add(outer, stretch="always", minsize=320)

        # ---- 左：缩放条 + 画布 ----
        left = ttk.Frame(outer)
        outer.add(left, stretch="always", minsize=420)

        zbar = ttk.Frame(left, padding=(6, 4))
        zbar.pack(side="top", fill="x")
        ttk.Label(zbar, text="缩放").pack(side="left")
        self.zoom_var = tk.DoubleVar(value=self.zoom)
        self.scale = ttk.Scale(zbar, from_=ZOOM_MIN * 100, to=ZOOM_MAX * 100,
                               orient="horizontal", length=180, variable=self.zoom_var,
                               command=self._on_slider)
        self.scale.pack(side="left", padx=(4, 6))
        self.e_zoom = ttk.Entry(zbar, width=6)
        self.e_zoom.pack(side="left")
        ttk.Label(zbar, text="%").pack(side="left", padx=(2, 6))
        self.e_zoom.bind("<Return>", lambda e: self._apply_zoom_entry())
        self.e_zoom.bind("<FocusOut>", lambda e: self._apply_zoom_entry())
        ttk.Button(zbar, text="－", width=3, command=lambda: self.set_zoom(self.zoom / 1.25)).pack(side="left")
        ttk.Button(zbar, text="＋", width=3, command=lambda: self.set_zoom(self.zoom * 1.25)).pack(side="left")
        ttk.Label(zbar, text="滚轮滚动 · Ctrl+滚轮缩放 · Shift+滚轮横向",
                  foreground="#777").pack(side="left", padx=10)

        body = ttk.Frame(left)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.paned = tk.PanedWindow(body, orient="horizontal", sashwidth=6, sashrelief="raised",
                                    background="#d0d0d0", bd=0, opaqueresize=False)
        self.paned.grid(row=0, column=0, sticky="nsew")

        f_before = ttk.Frame(self.paned)
        ttk.Label(f_before, text="原图 · 点这里选文字", foreground="#0a6", font=UI_FONT_BOLD).pack(side="top", anchor="w", padx=4)
        self.canvas = tk.Canvas(f_before, background="#3b3b3b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.paned.add(f_before, stretch="always", minsize=280)

        self.f_after = ttk.Frame(self.paned)
        ttk.Label(self.f_after, text="改后 · 只读预览", foreground="#c60", font=UI_FONT_BOLD).pack(side="top", anchor="w", padx=4)
        self.canvas_after = tk.Canvas(self.f_after, background="#3b3b3b", highlightthickness=0)
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
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.bind("<Control-Key-0>", lambda e: self.set_zoom(1.0))
        self.bind("<Control-plus>", lambda e: self.set_zoom(self.zoom * 1.25))
        self.bind("<Control-equal>", lambda e: self.set_zoom(self.zoom * 1.25))
        self.bind("<Control-minus>", lambda e: self.set_zoom(self.zoom / 1.25))

        # ---- 右：竖向可拖（编辑 | 修改清单 | 日志） ----
        right = ttk.Frame(outer, padding=6)
        self.right_frame = right
        outer.add(right, stretch="never", minsize=330, width=440)

        self.right_paned = tk.PanedWindow(right, orient="vertical", sashwidth=6, sashrelief="raised",
                                          background="#d0d0d0", bd=0, opaqueresize=False)
        self.right_paned.pack(fill="both", expand=True)

        pane1 = ttk.Frame(self.right_paned)
        self._pane_edit = pane1
        self._build_edit_form(pane1)
        self.right_paned.add(pane1, stretch="never", minsize=210, height=380)

        pane2 = ttk.Frame(self.right_paned)
        self._build_rules_pane(pane2)
        self.right_paned.add(pane2, stretch="always", minsize=110)

        # 日志：整窗底部通栏，可上下拖高度
        logpane = ttk.Frame(content, padding=(6, 2))
        self._build_log_pane(logpane)
        content.add(logpane, stretch="never", minsize=70, height=150)

    def _build_edit_form(self, parent):
        self.lbl_hint = ttk.Label(parent, text="", foreground="#06c", wraplength=380, justify="left")
        self.lbl_hint.pack(fill="x", pady=(0, 6))

        edit = ttk.LabelFrame(parent, text="编辑选中片段", padding=6)
        edit.pack(fill="x")

        ttk.Label(edit, text="原文").grid(row=0, column=0, sticky="w", pady=2)
        self.e_old = ttk.Entry(edit, width=34)
        self.e_old.grid(row=0, column=1, columnspan=2, sticky="we", pady=2)

        ttk.Label(edit, text="替换为").grid(row=1, column=0, sticky="w", pady=2)
        self.e_new = ttk.Entry(edit, width=34)
        self.e_new.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        self.e_new.bind("<Return>", lambda e: self.add_rule())
        self.e_new.bind("<KeyRelease>", lambda e: self._on_new_text())

        ttk.Label(edit, text="字体").grid(row=2, column=0, sticky="w", pady=2)
        fontbox = ttk.Frame(edit)
        fontbox.grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        self.cb_font = ttk.Combobox(fontbox, width=18, state="readonly",
                                    values=[label for _, label in self.font_choices])
        self.cb_font.current(0)
        self.cb_font.pack(side="left")
        ttk.Button(fontbox, text="…", width=3, command=self._browse_font).pack(side="left", padx=(4, 0))
        self.e_size = ttk.Entry(fontbox, width=5)
        self.e_size.insert(0, "10")
        self.e_size.pack(side="right", padx=(4, 0))
        ttk.Label(fontbox, text="字号").pack(side="right")

        ttk.Label(edit, text="对齐").grid(row=3, column=0, sticky="w", pady=2)
        self.v_align = tk.StringVar(value="match")
        af = ttk.Frame(edit)
        af.grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(af, text="保持原位", value="match", variable=self.v_align).pack(side="left")
        ttk.Radiobutton(af, text="左对齐留白", value="left", variable=self.v_align).pack(side="left", padx=(8, 0))

        ttk.Label(edit, text="留白").grid(row=4, column=0, sticky="w", pady=2)
        af2 = ttk.Frame(edit)
        af2.grid(row=4, column=1, columnspan=2, sticky="w")
        ttk.Label(af2, text="左边框x").pack(side="left")
        self.e_border = ttk.Entry(af2, width=8)
        self.e_border.pack(side="left", padx=(2, 10))
        ttk.Label(af2, text="间隙").pack(side="left")
        self.cb_gap = ttk.Combobox(af2, width=5, values=["1/4", "1/3", "1/2", "1"], state="readonly")
        self.cb_gap.current(1)
        self.cb_gap.pack(side="left")
        ttk.Label(af2, text="字宽").pack(side="left", padx=(2, 0))

        ttk.Label(edit, text="范围").grid(row=5, column=0, sticky="w", pady=2)
        self.v_scope = tk.StringVar(value="all")
        sf = ttk.Frame(edit)
        sf.grid(row=5, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(sf, text="所有相同文本", value="all", variable=self.v_scope).pack(side="left")
        ttk.Radiobutton(sf, text="仅选中这一处", value="single", variable=self.v_scope).pack(side="left", padx=(8, 0))

        btns = ttk.Frame(edit)
        btns.grid(row=6, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self.btn_add = ttk.Button(btns, text="添加到清单", command=self.add_rule)
        self.btn_add.pack(side="left")
        ttk.Button(btns, text="取消选择", command=self.clear_selection).pack(side="left", padx=4)
        edit.columnconfigure(1, weight=1)

    def _build_rules_pane(self, parent):
        lst = ttk.LabelFrame(parent, text="修改清单", padding=6)
        lst.pack(fill="both", expand=True)
        wrap = ttk.Frame(lst)
        wrap.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical")
        self.tree = ttk.Treeview(wrap, columns=("old", "new", "scope"), show="headings",
                                 height=6, yscrollcommand=sb.set)
        sb.configure(command=self.tree.yview)
        for c, w, t in (("old", 120, "原文"), ("new", 140, "替换为"), ("scope", 56, "范围")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        lb = ttk.Frame(lst)
        lb.pack(fill="x", pady=(4, 0))
        ttk.Button(lb, text="删除选中", command=self.del_rule).pack(side="left")
        ttk.Button(lb, text="清空", command=self.clear_rules).pack(side="left", padx=4)
        ttk.Button(lb, text="另存为…", command=self.save_as).pack(side="right")

    def _build_log_pane(self, parent):
        logf = ttk.LabelFrame(parent, text="日志", padding=4)
        logf.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(logf, orient="vertical")
        self.log = tk.Text(logf, height=4, width=28, wrap="word", yscrollcommand=sb.set)
        sb.configure(command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _apply_minsizes(self):
        """按"完整显示所需的最小尺寸"设置 minsize，避免拖到遮挡/裁切。"""
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        # 右栏最小宽度：取编辑表单请求宽度 + 边距
        try:
            need_w = max(self._pane_edit.winfo_reqwidth(),
                         self.right_paned.winfo_reqwidth()) + 30
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
                self.right_paned.paneconfigure(panes[0], minsize=form_h)
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
        win.title("帮助 · " + APP_TITLE)
        win.geometry("560x640")
        win.transient(self)          # 悬浮于主窗口之上
        txt = tk.Text(win, wrap="word", padx=10, pady=8)
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")

    # ================= 通用辅助 =================
    def _log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _set_hint(self, msg):
        self.lbl_hint.config(text=msg)

    def _selected_font_path(self):
        idx = self.cb_font.current()
        return self.font_choices[idx if idx >= 0 else 0][0]

    def _browse_font(self):
        """选择一个自定义字体文件（.ttf/.ttc/.otf），追加进下拉列表。"""
        path = filedialog.askopenfilename(
            title="选择字体文件",
            filetypes=[("字体文件", "*.ttf *.ttc *.otf"), ("所有文件", "*.*")])
        if not path:
            return
        for i, (p, _l) in enumerate(self.font_choices):
            if os.path.normcase(p) == os.path.normcase(path):
                self.cb_font.current(i)
                return
        label = f"自定义 {os.path.basename(path)}"
        self.font_choices.append((path, label))
        self.cb_font.configure(values=[l for _, l in self.font_choices])
        self.cb_font.current(len(self.font_choices) - 1)
        self._log(f"已加入自定义字体: {path}")

    def _gap_value(self):
        s = self.cb_gap.get()
        try:
            if "/" in s:
                a, b = s.split("/")
                return float(a) / float(b)
            return float(s)
        except Exception:
            return 1 / 3

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
        self._after_dirty = True
        self._after_doc = None
        self._span_cache.clear()
        self._tiles.clear()
        self._sel_bbox = None
        self.page_spans = self._spans_of_page(self.page_no)
        self._refresh_rules()
        self.title(f"{APP_TITLE} — {os.path.basename(path)}")
        self._log(f"已打开: {path} ({self.orig.page_count} 页)")
        self._set_hint(STEPS)
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

    # ================= 渲染（只画可见区域） =================
    def _spans_of_page(self, pno):
        if pno not in self._span_cache:
            spans = []
            for p, page, span, text in core.iter_spans(self.orig):
                if p != pno or not text.strip():
                    continue
                spans.append((fitz.Rect(span["bbox"]), text, span.get("font", ""), span.get("size", 10)))
            self._span_cache[pno] = spans
        if pno == self.page_no:
            self.page_spans = self._span_cache[pno]
        return self._span_cache[pno]

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
        w = canvas.winfo_width(); h = canvas.winfo_height()
        x0 = canvas.canvasx(0); y0 = canvas.canvasy(0)
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
        if self.orig is None or not self.rules:
            self._after_doc = None
            return
        if self._after_dirty or self._after_doc is None:
            try:
                self._after_doc = self._build_working()
                self._after_dirty = False
            except Exception as e:
                self._log(f"生成预览失败: {e}")
                self._after_doc = None

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

    def _schedule_render(self):
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
        self._pan = (event.x, event.y)
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
                    self.paned.add(self.f_after, stretch="always", minsize=280)
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
    def on_canvas_click(self, event):
        if self.orig is None:
            return
        x = self.canvas.canvasx(event.x) / self.zoom
        y = self.canvas.canvasy(event.y) / self.zoom
        pt = fitz.Point(x, y)
        hit = None
        for rect, text, font, size in self._spans_of_page(self.page_no):
            if rect.contains(pt):
                hit = (rect, text, font, size)
                break
        if not hit:
            return
        rect, text, font, size = hit
        self._sel_bbox = rect
        self.e_old.delete(0, "end"); self.e_old.insert(0, text)
        self.e_size.delete(0, "end"); self.e_size.insert(0, str(int(round(size))))
        sysfont = core.find_system_font(font)
        for i, (p, _l) in enumerate(self.font_choices):
            if os.path.normcase(p) == os.path.normcase(sysfont):
                self.cb_font.current(i)
                break
        self.e_border.delete(0, "end")
        bx = self._nearest_left_border(rect)
        if bx is not None:
            self.e_border.insert(0, f"{bx:.2f}")
        self._highlight(rect)
        self._update_add_state()
        self.e_new.focus_set()
        self._set_hint(f'已选中"{text}", 填"替换为"后按回车')
        self._log(f'选中: {text!r} (字体 {font or "?"}, {size:.1f}pt)')
        if self.e_new.get().strip():
            self._enable_after()

    def _on_new_text(self):
        self._update_add_state()
        if self.e_old.get().strip() and self.e_new.get().strip():
            self._enable_after()

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
        self.canvas.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#e53935", width=2, tags="sel")

    def clear_selection(self):
        """取消选择：清空 原文/替换为 两栏，去掉左侧高亮。"""
        self._clear_fields()

    def _clear_fields(self):
        """清空「原文」「替换为」，取消左侧高亮，并回到四步引导。"""
        self.e_old.delete(0, "end")
        self.e_new.delete(0, "end")
        self._sel_bbox = None
        self.canvas.delete("sel")
        self._update_add_state()
        self._set_hint(STEPS)

    # ================= 规则 =================
    def add_rule(self):
        old = self.e_old.get().strip()
        new = self.e_new.get().strip()
        if not old:
            messagebox.showwarning(APP_TITLE, "请先在左侧预览里点选要修改的文字.")
            return
        if not new:
            messagebox.showwarning(APP_TITLE, '请填写"替换为"内容.')
            return
        rule = {"old": old, "new": new,
                "font": self._selected_font_path(),
                "font_size": float(self.e_size.get() or 10),
                "bold_stroke": self.bold_stroke}
        if self.v_align.get() == "left":
            rule["align"] = "left"
            try:
                rule["left_border_x"] = float(self.e_border.get())
            except Exception:
                messagebox.showwarning(APP_TITLE, '左对齐需要"左边框x" (点选片段会自动填入).')
                return
            rule["left_gap"] = self._gap_value() * rule["font_size"]
        if self.v_scope.get() == "single":
            rule["scope"] = "single"
            rule["page"] = self.page_no
            if self._sel_bbox is not None:
                rule["bbox"] = [round(v, 2) for v in (self._sel_bbox.x0, self._sel_bbox.y0,
                                                      self._sel_bbox.x1, self._sel_bbox.y1)]
        key = (rule["old"], rule.get("page"), tuple(rule.get("bbox", ())) or None)
        replaced = False
        for i, r in enumerate(self.rules):
            rkey = (r["old"], r.get("page"), tuple(r.get("bbox", ())) or None)
            if rkey == key:
                self.rules[i] = rule
                replaced = True
                break
        if not replaced:
            self.rules.append(rule)
        self._after_dirty = True
        self._refresh_rules()
        self._enable_after()
        self._render_all()
        self._log(("已更新" if replaced else "已添加") + f"规则: {old!r} -> {new!r}")
        self._clear_fields()

    def del_rule(self):
        for iid in self.tree.selection():
            r = self.rules.pop(int(iid))
            self._log(f"已删除: {r['old']!r}")
            break
        self._after_dirty = True
        self._refresh_rules()
        self._render_all()
        self._clear_fields()

    def clear_rules(self):
        self.rules = []
        self._after_dirty = True
        self._refresh_rules()
        self._render_all()
        self._log("清单已清空")
        self._clear_fields()

    def _refresh_rules(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            scope = "仅此处" if r.get("scope") == "single" else "全部"
            self.tree.insert("", "end", iid=str(i), values=(r["old"], r["new"], scope))
        self._update_add_state()

    # ================= 文档构建 =================
    def _build_working(self):
        if self.orig is None:
            raise RuntimeError("未打开 PDF")
        doc = fitz.open(self.src_path)
        cfg = {"replacements": self.rules}
        targets = core.collect_targets(doc, self.rules, cfg)
        core.apply_replacements(doc, cfg, targets)
        return doc

    def auto_calibrate(self):
        if self.orig is None:
            messagebox.showinfo(APP_TITLE, "请先打开 PDF.")
            return
        ruled = {r["old"] for r in self.rules}
        cand = None
        for rect, text, font, size in self._spans_of_page(self.page_no):
            if text.strip() and text not in ruled and (cand is None or len(text) > len(cand[1])):
                cand = (rect, text, font, size)
        if not cand:
            messagebox.showinfo(APP_TITLE, "没有可用于标定的文字.")
            return
        rect, text, font, size = cand
        try:
            bw = self._calibrate(rect, text, font, size)
        except Exception as e:
            self._log(f"标定失败: {e}")
            return
        self.bold_stroke = bw
        for r in self.rules:
            r["bold_stroke"] = bw
        self._after_dirty = True
        self._render_all()
        self._log(f'自动标定完成: bold_stroke = {bw} (用 {text!r} 校准)')

    def _calibrate(self, rect, text, font, size):
        font_file = core.ensure_ttf(core.find_system_font(font))
        zoom = 6
        clip = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2)

        def gray(doc):
            pm = doc[self.page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, 0].astype(int)

        ref = gray(self.orig)
        origins = None
        for p, page, span, t in core.iter_spans(self.orig):
            if p == self.page_no and t == text and abs(fitz.Rect(span["bbox"]).x0 - rect.x0) < 0.01:
                origins = [tuple(c["origin"]) for c in span["chars"]]
                break
        if origins is None:
            return self.bold_stroke
        best = (1e9, self.bold_stroke)
        for bw in (0.02, 0.025, 0.03, 0.035, 0.04):
            doc = fitz.open(self.src_path)
            pg = doc[self.page_no]
            r = fitz.Rect(rect); r.x0 -= .7; r.x1 += .7; r.y0 -= 1.2; r.y1 += 1.2
            pg.add_redact_annot(r, fill=None)
            pg.apply_redactions(**core.PDF_REDACT)
            for ch, (x, y) in zip(text, origins):
                pg.insert_text(fitz.Point(x, y), ch, fontsize=size, fontname="simsun",
                               fontfile=font_file, color=(0, 0, 0), fill=(0, 0, 0),
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
            "font_name": "simsun",
            "font_size": float(self.e_size.get() or 10),
            "bold_stroke": self.bold_stroke,
            "replacements": self.rules,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self._log(f"已导出 config: {path}")

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
        self.rules = [r for r in cfg.get("replacements", []) if isinstance(r, dict) and r.get("old")]
        if cfg.get("bold_stroke"):
            self.bold_stroke = float(cfg["bold_stroke"])
        if not self.src_path and cfg.get("src"):
            src = cfg["src"] if os.path.isabs(cfg["src"]) else os.path.join(os.path.dirname(path), cfg["src"])
            if os.path.exists(src):
                self.load_pdf(src)
        self._after_dirty = True
        self._refresh_rules()
        self._render_all()
        self._log(f"已导入 {len(self.rules)} 条规则: {path}")


def main():
    enable_dpi_awareness()
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    app = PdfEditorApp(initial=initial)
    app.mainloop()


if __name__ == "__main__":
    main()

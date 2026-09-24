# -*- coding: utf-8 -*-
"""
pdf_editor_gui —— PDF 文字修改器（Tkinter 图形界面）

操作：
  打开 PDF -> 在预览里点要改的文字 -> 填"替换为" -> 添加到清单 -> 预览对比 -> 另存为
规则可导入/导出成与 CLI 共用的 config.json（方案 B）。
依赖：PyMuPDF、fontTools、Pillow、numpy（见 requirements.txt）
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

APP_TITLE = "PDF 文字修改器"
FONT_CHOICES = [(p, label) for p, label in core.FONT_CHOICES]


def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


class PdfEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")

        self.src_path = None
        self.orig = None          # 原始文档（用于片段索引）
        self.working = None       # 应用规则后的文档
        self.page_no = 0
        self.zoom = 1.6
        self.rules = []           # 规则列表
        self.bold_stroke = 0.03
        self.page_spans = []      # 当前页 [(Rect, text, font, size)]
        self._img = None
        self._sel_bbox = None

        self._build_ui()
        self._log("就绪。请先「打开 PDF」。")

    # ---------- UI ----------
    def _build_ui(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="打开 PDF", command=self.open_pdf).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="◀", width=3, command=lambda: self.change_page(-1)).pack(side="left")
        self.lbl_page = ttk.Label(bar, text="0/0", width=8, anchor="center")
        self.lbl_page.pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self.change_page(1)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="－", width=3, command=lambda: self.do_zoom(1/1.25)).pack(side="left")
        self.lbl_zoom = ttk.Label(bar, text="160%", width=6, anchor="center")
        self.lbl_zoom.pack(side="left")
        ttk.Button(bar, text="＋", width=3, command=lambda: self.do_zoom(1.25)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="自动标定加粗", command=self.auto_calibrate).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="导入 config", command=self.import_config).pack(side="left")
        ttk.Button(bar, text="导出 config", command=self.export_config).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="预览对比", command=self.preview_compare).pack(side="left")
        ttk.Button(bar, text="另存为…", command=self.save_as).pack(side="left")

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：画布
        left = ttk.Frame(paned)
        self.canvas = tk.Canvas(left, background="#3b3b3b", highlightthickness=0)
        vs = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(left, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        paned.add(left, weight=3)

        # 右：面板
        right = ttk.Frame(paned, padding=6)
        paned.add(right, weight=2)

        edit = ttk.LabelFrame(right, text="编辑选中片段", padding=6)
        edit.pack(fill="x")
        ttk.Label(edit, text="原文").grid(row=0, column=0, sticky="w", pady=2)
        self.e_old = ttk.Entry(edit, width=34)
        self.e_old.grid(row=0, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Label(edit, text="替换为").grid(row=1, column=0, sticky="w", pady=2)
        self.e_new = ttk.Entry(edit, width=34)
        self.e_new.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)

        ttk.Label(edit, text="字体").grid(row=2, column=0, sticky="w", pady=2)
        self.cb_font = ttk.Combobox(edit, width=20, state="readonly",
                                    values=[f"{label}" for _, label in FONT_CHOICES])
        self.cb_font.current(0)
        self.cb_font.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(edit, text="字号").grid(row=2, column=2, sticky="e")
        self.e_size = ttk.Entry(edit, width=6)
        self.e_size.insert(0, "10")
        self.e_size.grid(row=2, column=2, sticky="e", pady=2)
        self.e_size.grid_configure(padx=(0, 40))

        ttk.Label(edit, text="对齐").grid(row=3, column=0, sticky="w", pady=2)
        self.v_align = tk.StringVar(value="match")
        ttk.Radiobutton(edit, text="保持原位", value="match", variable=self.v_align).grid(row=3, column=1, sticky="w")
        self.v_gap = tk.StringVar(value="1/3")
        gapf = ttk.Frame(edit)
        gapf.grid(row=4, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(gapf, text="左对齐留白", value="left", variable=self.v_align).pack(side="left")
        ttk.Label(gapf, text="间隙(字宽)").pack(side="left", padx=(8, 2))
        self.cb_gap = ttk.Combobox(gapf, width=6, values=["1/4", "1/3", "1/2", "1"], state="readonly")
        self.cb_gap.current(1)
        self.cb_gap.pack(side="left")
        ttk.Label(gapf, text="左边框x").pack(side="left", padx=(8, 2))
        self.e_border = ttk.Entry(gapf, width=8)
        self.e_border.pack(side="left")

        ttk.Label(edit, text="范围").grid(row=5, column=0, sticky="w", pady=2)
        self.v_scope = tk.StringVar(value="all")
        sf = ttk.Frame(edit)
        sf.grid(row=5, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(sf, text="所有相同文本", value="all", variable=self.v_scope).pack(side="left")
        ttk.Radiobutton(sf, text="仅选中这一处", value="single", variable=self.v_scope).pack(side="left", padx=(8, 0))

        btns = ttk.Frame(edit)
        btns.grid(row=6, column=0, columnspan=3, sticky="we", pady=(6, 0))
        ttk.Button(btns, text="添加到清单", command=self.add_rule).pack(side="left")
        ttk.Button(btns, text="取消选择", command=self.clear_selection).pack(side="left", padx=4)
        edit.columnconfigure(1, weight=1)

        lst = ttk.LabelFrame(right, text="修改清单", padding=6)
        lst.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("old", "new", "scope")
        self.tree = ttk.Treeview(lst, columns=cols, show="headings", height=10)
        for c, w, t in (("old", 130, "原文"), ("new", 150, "替换为"), ("scope", 60, "范围")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        lb = ttk.Frame(lst)
        lb.pack(fill="x", pady=(4, 0))
        ttk.Button(lb, text="删除选中", command=self.del_rule).pack(side="left")
        ttk.Button(lb, text="清空", command=self.clear_rules).pack(side="left", padx=4)

        logf = ttk.LabelFrame(right, text="日志", padding=4)
        logf.pack(fill="both", expand=False, pady=(8, 0))
        self.log = tk.Text(logf, height=7, wrap="word")
        self.log.pack(fill="both", expand=True)

    # ---------- 工具 ----------
    def _log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _selected_font_path(self):
        idx = self.cb_font.current()
        if idx < 0:
            idx = 0
        return FONT_CHOICES[idx][0]

    def _gap_value(self):
        s = self.cb_gap.get()
        try:
            if "/" in s:
                a, b = s.split("/")
                return float(a) / float(b)
            return float(s)
        except Exception:
            return 1 / 3

    # ---------- 打开/渲染 ----------
    def open_pdf(self):
        path = filedialog.askopenfilename(title="选择 PDF", filetypes=[("PDF", "*.pdf"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.orig = fitz.open(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"打开失败：{e}")
            return
        self.src_path = path
        self.page_no = 0
        self.rules = []
        self._refresh_rules()
        self.title(f"{APP_TITLE} — {os.path.basename(path)}")
        self._log(f"已打开：{path}（{self.orig.page_count} 页）")
        self.render()

    def _build_span_index(self, pno):
        spans = []
        for p, page, span, text in core.iter_spans(self.orig):
            if p != pno:
                continue
            if not text.strip():
                continue
            spans.append((fitz.Rect(span["bbox"]), text, span.get("font", ""), span.get("size", 10)))
        self.page_spans = spans

    def render(self):
        if self.orig is None:
            return
        self._build_span_index(self.page_no)
        page = self.orig[self.page_no]
        pm = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom))
        self._img = tk.PhotoImage(data=pm.tobytes("png"))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)
        self.canvas.configure(scrollregion=(0, 0, pm.width, pm.height))
        self.canvas.create_rectangle(2, 2, pm.width - 2, pm.height - 2, outline="#666")
        self.lbl_page.config(text=f"{self.page_no + 1}/{self.orig.page_count}")
        self.lbl_zoom.config(text=f"{int(self.zoom * 100)}%")
        self._sel_bbox = None

    def change_page(self, d):
        if self.orig is None:
            return
        np_ = min(max(self.page_no + d, 0), self.orig.page_count - 1)
        if np_ != self.page_no:
            self.page_no = np_
            self.render()

    def do_zoom(self, f):
        self.zoom = min(max(self.zoom * f, 0.3), 6.0)
        self.render()

    # ---------- 画布交互 ----------
    def on_canvas_click(self, event):
        if self.orig is None:
            return
        x = self.canvas.canvasx(event.x) / self.zoom
        y = self.canvas.canvasy(event.y) / self.zoom
        pt = fitz.Point(x, y)
        hit = None
        for rect, text, font, size in self.page_spans:
            if rect.contains(pt):
                hit = (rect, text, font, size)
                break
        if not hit:
            return
        rect, text, font, size = hit
        self._sel_bbox = rect
        self.e_old.delete(0, "end"); self.e_old.insert(0, text)
        self.e_size.delete(0, "end"); self.e_size.insert(0, str(int(round(size))))
        # 自动匹配字体
        sysfont = core.find_system_font(font)
        for i, (p, _l) in enumerate(FONT_CHOICES):
            if os.path.normcase(p) == os.path.normcase(sysfont):
                self.cb_font.current(i)
                break
        self.e_border.delete(0, "end")
        bx = self._nearest_left_border(rect)
        if bx is not None:
            self.e_border.insert(0, f"{bx:.2f}")
        self._highlight(rect)
        self._log(f"选中：{text!r}（字体 {font or '?'}，{size:.1f}pt）")

    def _nearest_left_border(self, rect):
        page = self.orig[self.page_no]
        best = None
        for d in page.get_drawings():
            for it in d.get("items", []):
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.x - p2.x) < 0.5:
                        y0, y1 = sorted((p1.y, p2.y))
                        if y0 - 1 <= rect.y0 and y1 + 1 >= rect.y1 and rect.x0 - 80 < p1.x <= rect.x0 + 0.5:
                            if best is None or p1.x > best:
                                best = p1.x
                elif it[0] == "re":
                    r = it[1]
                    if r.height > 4 and rect.x0 - 80 < r.x1 <= rect.x0 + 0.5 and r.y0 - 1 <= rect.y0 and r.y1 + 1 >= rect.y1:
                        if best is None or r.x1 > best:
                            best = r.x1
        return best

    def _highlight(self, rect):
        self.canvas.delete("sel")
        x0, y0, x1, y1 = (rect.x0 * self.zoom, rect.y0 * self.zoom,
                          rect.x1 * self.zoom, rect.y1 * self.zoom)
        self.canvas.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#e53935", width=2, tags="sel")

    def clear_selection(self):
        self.canvas.delete("sel")
        self._sel_bbox = None

    # ---------- 规则 ----------
    def add_rule(self):
        old = self.e_old.get().strip()
        new = self.e_new.get().strip()
        if not old:
            messagebox.showwarning(APP_TITLE, "请先在预览里点选要修改的文字。")
            return
        if not new:
            messagebox.showwarning(APP_TITLE, "请填写「替换为」内容。")
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
                messagebox.showwarning(APP_TITLE, "左对齐需要「左边框x」（点选片段会自动填入）。")
                return
            rule["left_gap"] = self._gap_value() * rule["font_size"]
        if self.v_scope.get() == "single":
            rule["scope"] = "single"
            rule["page"] = self.page_no
            if self._sel_bbox is not None:
                rule["bbox"] = [round(v, 2) for v in (self._sel_bbox.x0, self._sel_bbox.y0,
                                                      self._sel_bbox.x1, self._sel_bbox.y1)]
        # 同位置/同原文覆盖
        key = (rule["old"], rule.get("page"), tuple(rule.get("bbox", ())) or None)
        for i, r in enumerate(self.rules):
            rkey = (r["old"], r.get("page"), tuple(r.get("bbox", ())) or None)
            if rkey == key:
                self.rules[i] = rule
                self._refresh_rules()
                self._log(f"已更新规则：{old!r} -> {new!r}")
                return
        self.rules.append(rule)
        self._refresh_rules()
        self._log(f"已添加规则：{old!r} -> {new!r}")

    def del_rule(self):
        for iid in self.tree.selection():
            idx = int(iid)
            r = self.rules.pop(idx)
            self._log(f"已删除：{r['old']!r}")
            break
        self._refresh_rules()

    def clear_rules(self):
        self.rules = []
        self._refresh_rules()
        self._log("清单已清空")

    def _refresh_rules(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            scope = "仅此处" if r.get("scope") == "single" else "全部"
            self.tree.insert("", "end", iid=str(i), values=(r["old"], r["new"], scope))

    # ---------- 文档构建 ----------
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
            messagebox.showinfo(APP_TITLE, "请先打开 PDF。")
            return
        # 找一段未被修改的最长文字做标定
        ruled = {r["old"] for r in self.rules}
        cand = None
        for rect, text, font, size in self.page_spans:
            if text.strip() and text not in ruled and len(text) > len((cand or ("",))[1]):
                cand = (rect, text, font, size)
        if not cand:
            messagebox.showinfo(APP_TITLE, "没有可用于标定的文字。")
            return
        rect, text, font, size = cand
        try:
            bw = self._calibrate(rect, text, size)
        except Exception as e:
            self._log(f"标定失败：{e}")
            return
        self.bold_stroke = bw
        self._log(f"自动标定完成：bold_stroke = {bw}（用 {text!r} 校准）")

    def _calibrate(self, rect, text, size):
        font_file = core.ensure_ttf(core.find_system_font(
            next((f for r, t, f, s in self.page_spans if t == text), "")),)
        zoom = 6
        clip = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2)

        def gray(doc):
            pm = doc[self.page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, 0].astype(int)

        ref = gray(self.orig)
        candidates = [0.02, 0.025, 0.03, 0.035, 0.04]
        best = (1e9, self.bold_stroke)
        origins = None
        for p, page, span, t in core.iter_spans(self.orig):
            if p == self.page_no and t == text and fitz.Rect(span["bbox"]).x0 == rect.x0:
                origins = [tuple(c["origin"]) for c in span["chars"]]
                break
        if origins is None:
            return self.bold_stroke
        for bw in candidates:
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

    def preview_compare(self):
        if self.orig is None or not self.rules:
            messagebox.showinfo(APP_TITLE, "先打开 PDF 并至少添加一条规则。")
            return
        try:
            work = self._build_working()
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"生成失败：{e}")
            return
        win = tk.Toplevel(self)
        win.title("预览对比：左=原图  右=改后")
        win.geometry("1100x760")
        z = 1.0
        p1 = self.orig[self.page_no].get_pixmap(matrix=fitz.Matrix(z, z))
        p2 = work[self.page_no].get_pixmap(matrix=fitz.Matrix(z, z))
        i1 = tk.PhotoImage(data=p1.tobytes("png"))
        i2 = tk.PhotoImage(data=p2.tobytes("png"))
        f = ttk.Frame(win)
        f.pack(fill="both", expand=True)
        c1 = tk.Canvas(f, background="#3b3b3b"); c1.pack(side="left", fill="both", expand=True)
        c2 = tk.Canvas(f, background="#3b3b3b"); c2.pack(side="right", fill="both", expand=True)
        c1.create_image(0, 0, anchor="nw", image=i1); c1.configure(scrollregion=(0, 0, p1.width, p1.height))
        c2.create_image(0, 0, anchor="nw", image=i2); c2.configure(scrollregion=(0, 0, p2.width, p2.height))
        c1.image = i1; c2.image = i2
        self._log("已打开预览对比窗口。")

    def save_as(self):
        if self.orig is None:
            return
        if not self.rules:
            messagebox.showinfo(APP_TITLE, "还没有任何修改规则。")
            return
        out = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                           initialfile=os.path.splitext(os.path.basename(self.src_path))[0] + "_修改后.pdf")
        if not out:
            return
        try:
            work = self._build_working()
            core.finalize(work, out, self.orig.metadata)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"保存失败：{e}")
            return
        self._log(f"已保存：{out}")
        messagebox.showinfo(APP_TITLE, f"已保存：\n{out}")

    # ---------- config 导入/导出 ----------
    def export_config(self):
        if not self.rules:
            messagebox.showinfo(APP_TITLE, "清单为空。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")],
                                            initialfile="rules.json")
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
        self._log(f"已导出 config：{path}")

    def import_config(self):
        path = filedialog.askopenfilename(title="选择 config.json", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"读取失败：{e}")
            return
        self.rules = [r for r in cfg.get("replacements", []) if isinstance(r, dict) and r.get("old")]
        if cfg.get("bold_stroke"):
            self.bold_stroke = float(cfg["bold_stroke"])
        if not self.src_path and cfg.get("src"):
            src = cfg["src"] if os.path.isabs(cfg["src"]) else os.path.join(os.path.dirname(path), cfg["src"])
            if os.path.exists(src):
                self.src_path = src
                self.orig = fitz.open(src)
                self.page_no = 0
                self.render()
        self._refresh_rules()
        self._log(f"已导入 {len(self.rules)} 条规则：{path}")


def main():
    enable_dpi_awareness()
    app = PdfEditorApp()
    app.mainloop()


if __name__ == "__main__":
    main()

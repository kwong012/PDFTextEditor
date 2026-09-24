# -*- coding: utf-8 -*-
"""
pdf_edit_core —— PDF 原位文字替换核心逻辑（CLI 与 GUI 共用）

原理回顾
========
1. 逐字符定位：用 rawdict 取每个字符的基点(origin)，避免整段重排累积误差。
2. 删旧字：redaction + fill=None（不填白块，否则残留白色矩形，部分阅读器显示成边框）。
3. 重绘：同款系统字体 + render_mode=2 + 极细描边，复刻原文件的"伪加粗"。
4. 收尾：subset_fonts() 控体积；set_metadata() 保留原元数据。
"""
from __future__ import annotations

import os
import fitz

DEFAULT_FONT = r"C:\Windows\Fonts\simsun.ttc"
FONT_CHOICES = [
    (r"C:\Windows\Fonts\simsun.ttc", "宋体 SimSun"),
    (r"C:\Windows\Fonts\simhei.ttf", "黑体 SimHei"),
    (r"C:\Windows\Fonts\simkai.ttf", "楷体 KaiTi"),
    (r"C:\Windows\Fonts\simfang.ttf", "仿宋 FangSong"),
]
# PDF 字体名 -> 系统字体文件
FONT_ALIASES = {
    "simsun": DEFAULT_FONT, "宋体": DEFAULT_FONT, "nsimsun": DEFAULT_FONT,
    "simhei": r"C:\Windows\Fonts\simhei.ttf", "黑体": r"C:\Windows\Fonts\simhei.ttf",
    "simkai": r"C:\Windows\Fonts\simkai.ttf", "楷体": r"C:\Windows\Fonts\simkai.ttf",
    "kaiti": r"C:\Windows\Fonts\simkai.ttf",
    "simfang": r"C:\Windows\Fonts\simfang.ttf", "仿宋": r"C:\Windows\Fonts\simfang.ttf",
    "fangsong": r"C:\Windows\Fonts\simfang.ttf",
    "msyh": r"C:\Windows\Fonts\msyh.ttc", "微软雅黑": r"C:\Windows\Fonts\msyh.ttc",
}
PDF_REDACT = dict(
    images=fitz.PDF_REDACT_IMAGE_NONE,
    graphics=fitz.PDF_REDACT_LINE_ART_NONE,
    text=fitz.PDF_REDACT_TEXT_REMOVE,
)


def user_cache_dir(app: str = "PDFTextEditor") -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, app)
    os.makedirs(d, exist_ok=True)
    return d


def find_system_font(pdf_font_name: str | None) -> str:
    """据 PDF 内字体名猜系统字体文件，找不到就退回宋体。"""
    n = (pdf_font_name or "").lower().replace(" ", "").replace(",", "")
    for key, path in FONT_ALIASES.items():
        if key in n:
            return path
    return DEFAULT_FONT


def ensure_ttf(font_path: str, cache_dir: str | None = None) -> str:
    """PyMuPDF 对 .ttc 支持不稳；若为 ttc，用 fontTools 取第 0 号字面另存 .ttf。"""
    if not font_path or not font_path.lower().endswith(".ttc"):
        return font_path
    if not os.path.exists(font_path):
        return font_path
    cache_dir = cache_dir or user_cache_dir()
    out = os.path.join(cache_dir, os.path.basename(font_path) + ".0.ttf")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(font_path):
        from fontTools.ttLib import TTCollection
        os.makedirs(cache_dir, exist_ok=True)
        TTCollection(font_path).fonts[0].save(out)
    return out


def resolve_settings(rule: dict, cfg: dict) -> dict:
    """把全局配置 + 单条规则合并成最终绘制参数。"""
    size = float(rule.get("font_size", cfg.get("font_size", 10)))
    return {
        "font_file": rule.get("font") or cfg.get("font") or DEFAULT_FONT,
        "font_name": rule.get("font_name") or cfg.get("font_name", "simsun"),
        "font_size": size,
        "bold_stroke": float(rule.get("bold_stroke", cfg.get("bold_stroke", 0.03))),
        "pad_x": float(rule.get("pad_x", cfg.get("pad_x", 0.7))),
        "pad_y": float(rule.get("pad_y", cfg.get("pad_y", 1.2))),
    }


def _bbox_close(a: fitz.Rect, b, tol: float = 1.5) -> bool:
    b = fitz.Rect(b)
    return (abs(a.x0 - b.x0) < tol and abs(a.y0 - b.y0) < tol
            and abs(a.x1 - b.x1) < tol and abs(a.y1 - b.y1) < tol)


def iter_spans(doc):
    """遍历所有页面，yield (page, span_dict, text)。"""
    for pno in range(doc.page_count):
        page = doc[pno]
        for block in page.get_text("rawdict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = "".join(c["c"] for c in span["chars"])
                    yield pno, page, span, text


def collect_targets(doc, repls, cfg=None):
    """按规则匹配片段，返回 [(page, bbox, origins, rule)]。

    rule["scope"]=="single" 时用 rule["page"] + rule["bbox"] 精确定位单处。
    """
    cfg = cfg or {}
    found = []
    for pno, page, span, text in iter_spans(doc):
        bbox = fitz.Rect(span["bbox"])
        for rule in repls:
            if text != rule.get("old"):
                continue
            if rule.get("scope", "all") == "single":
                if rule.get("page") is not None and int(rule["page"]) != pno:
                    continue
                if rule.get("bbox") and not _bbox_close(bbox, rule["bbox"]):
                    continue
            origins = [tuple(c["origin"]) for c in span["chars"]]
            found.append((page, bbox, origins, rule, span))
    return found


def compute_positions(origins, new_text, rule, font_size):
    """决定每个新字符画在哪。

    align='left' 时从 left_border_x + left_gap 起左对齐（给单元格留边框间距）；
    否则长度相同逐字沿用原基点，变长则按原首字字距续排。
    """
    xs = [o[0] for o in origins]
    y = origins[0][1]
    n = len(new_text)
    step = (xs[-1] - xs[0]) / (len(xs) - 1) if len(xs) > 1 else float(font_size)
    if rule.get("align") == "left":
        x0 = float(rule["left_border_x"]) + float(rule.get("left_gap", 0.0))
        return [(x0 + i * step, y) for i in range(n)]
    if n == len(origins):
        return list(origins)
    return [(xs[0] + i * step, y) for i in range(n)]


def apply_replacements(doc, cfg, targets=None):
    """执行删旧 + 重绘，就地修改 doc。返回实际处理的片段数。"""
    cfg = cfg or {}
    repls = cfg.get("replacements", [])
    if targets is None:
        targets = collect_targets(doc, repls, cfg)

    # 1) 删旧字（不填白块）
    pages_touched = set()
    for page, bbox, origins, rule, span in targets:
        st = resolve_settings(rule, cfg)
        r = fitz.Rect(bbox)
        r.x0 -= st["pad_x"]; r.x1 += st["pad_x"]
        r.y0 -= st["pad_y"]; r.y1 += st["pad_y"]
        page.add_redact_annot(r, fill=None)
        pages_touched.add(page.number)
    for pno in pages_touched:
        doc[pno].apply_redactions(**PDF_REDACT)

    # 2) 逐字重绘
    cache = {}
    for page, bbox, origins, rule, span in targets:
        st = resolve_settings(rule, cfg)
        ttf = cache.setdefault(st["font_file"], ensure_ttf(st["font_file"]))
        pos = compute_positions(origins, rule["new"], rule, st["font_size"])
        for ch, (x, y) in zip(rule["new"], pos):
            page.insert_text(
                fitz.Point(x, y), ch,
                fontsize=st["font_size"], fontname=st["font_name"], fontfile=ttf,
                color=(0, 0, 0), fill=(0, 0, 0),
                render_mode=2, border_width=st["bold_stroke"],
            )
    return len(targets)


def finalize(doc, out_path, original_metadata=None):
    """子集化字体 + 保留元数据 + 保存。"""
    doc.subset_fonts(verbose=False)
    if original_metadata:
        doc.set_metadata(original_metadata)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path, garbage=4, deflate=True, clean=True)
    return out_path


def process(src, out, cfg, dry_run=False, log=print):
    """一步到位：开文件 -> 替换 -> 保存。返回匹配到的片段数。"""
    doc = fitz.open(src)
    meta = fitz.open(src).metadata
    repls = cfg.get("replacements", [])
    targets = collect_targets(doc, repls, cfg)
    log(f"匹配到 {len(targets)} 处")
    for page, bbox, origins, rule, span in targets:
        log(f"  p{page.number} {rule.get('old')!r} -> {rule.get('new')!r} "
            f"bbox={[round(v, 1) for v in bbox]}")
    if dry_run:
        return len(targets)
    apply_replacements(doc, cfg, targets)
    finalize(doc, out, meta)
    log(f"已保存: {out}  ({os.path.getsize(out)} bytes)")
    return len(targets)

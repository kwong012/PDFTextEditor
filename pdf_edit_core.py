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

# 候选字体（覆盖面尽量广；运行时用 list_available_fonts() 过滤出实际存在的）
CJK_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\simsun.ttc",   "宋体 SimSun"),
    (r"C:\Windows\Fonts\simhei.ttf",   "黑体 SimHei"),
    (r"C:\Windows\Fonts\simkai.ttf",   "楷体 KaiTi"),
    (r"C:\Windows\Fonts\simfang.ttf",  "仿宋 FangSong"),
    (r"C:\Windows\Fonts\msyh.ttc",     "微软雅黑 Microsoft YaHei"),
    (r"C:\Windows\Fonts\msyhbd.ttc",   "微软雅黑 粗 YaHei Bold"),
    (r"C:\Windows\Fonts\msyhl.ttc",    "微软雅黑 细 YaHei Light"),
    (r"C:\Windows\Fonts\msjh.ttc",     "微软正黑体 JhengHei"),
    (r"C:\Windows\Fonts\msjhbd.ttc",   "微软正黑体 粗 JhengHei Bold"),
    (r"C:\Windows\Fonts\Deng.ttf",     "等线 DengXian"),
    (r"C:\Windows\Fonts\Dengb.ttf",    "等线 粗 DengXian Bold"),
    (r"C:\Windows\Fonts\Dengl.ttf",    "等线 细 DengXian Light"),
    (r"C:\Windows\Fonts\simyou.ttf",   "幼圆 YouYuan"),
    (r"C:\Windows\Fonts\SIMLI.TTF",    "隶书 LiSu"),
    (r"C:\Windows\Fonts\STSONG.TTF",   "华文宋体 STSong"),
    (r"C:\Windows\Fonts\STZHONGS.TTF", "华文中宋 STZhongsong"),
    (r"C:\Windows\Fonts\STKAITI.TTF",  "华文楷体 STKaiti"),
    (r"C:\Windows\Fonts\STFANGSO.TTF", "华文仿宋 STFangsong"),
    (r"C:\Windows\Fonts\STXIHEI.TTF",  "华文细黑 STXihei"),
    (r"C:\Windows\Fonts\STXINGKA.TTF", "华文行楷 STXingkai"),
    (r"C:\Windows\Fonts\STXINWEI.TTF", "华文新魏 STXinwei"),
    (r"C:\Windows\Fonts\STLITI.TTF",   "华文隶书 STLiti"),
    (r"C:\Windows\Fonts\STHUPO.TTF",   "华文琥珀 STHupo"),
    (r"C:\Windows\Fonts\STCAIYUN.TTF", "华文彩云 STCaiyun"),
    (r"C:\Windows\Fonts\FZSTK.TTF",    "方正舒体 FZShuTi"),
    (r"C:\Windows\Fonts\FZYTK.TTF",    "方正姚体 FZYaoti"),
    (r"C:\Windows\Fonts\SimsunExtG.ttf", "宋体-扩展 SimSun-ExtG"),
]


# 本机实际可用的候选字体（运行时过滤）
def list_available_fonts():
    """返回本机实际存在的候选字体 [(路径, 名称)]；宋体兜底。"""
    out = [(p, label) for p, label in CJK_FONT_CANDIDATES if os.path.exists(p)]
    if not any(os.path.normcase(p) == os.path.normcase(DEFAULT_FONT) for p, _ in out):
        out.insert(0, (DEFAULT_FONT, "宋体 SimSun"))
    return out


# PDF 内字体名（小写、去空格与逗号）-> 系统字体文件
FONT_ALIASES = {
    # 宋体 / 黑体 / 楷体 / 仿宋
    "simsun": r"C:\Windows\Fonts\simsun.ttc", "宋体": r"C:\Windows\Fonts\simsun.ttc",
    "nsimsun": r"C:\Windows\Fonts\simsun.ttc", "newsun": r"C:\Windows\Fonts\simsun.ttc",
    "simsun-extb": r"C:\Windows\Fonts\simsunb.ttf",
    "simsunextg": r"C:\Windows\Fonts\SimsunExtG.ttf",
    "simhei": r"C:\Windows\Fonts\simhei.ttf", "黑体": r"C:\Windows\Fonts\simhei.ttf",
    "simkai": r"C:\Windows\Fonts\simkai.ttf", "楷体": r"C:\Windows\Fonts\simkai.ttf",
    "kaiti": r"C:\Windows\Fonts\simkai.ttf", "楷": r"C:\Windows\Fonts\simkai.ttf",
    "simfang": r"C:\Windows\Fonts\simfang.ttf", "仿宋": r"C:\Windows\Fonts\simfang.ttf",
    "fangsong": r"C:\Windows\Fonts\simfang.ttf",
    # 雅黑 / 正黑 / 等线
    "msyh": r"C:\Windows\Fonts\msyh.ttc", "微软雅黑": r"C:\Windows\Fonts\msyh.ttc",
    "microsoftyahei": r"C:\Windows\Fonts\msyh.ttc", "yahei": r"C:\Windows\Fonts\msyh.ttc",
    "msjh": r"C:\Windows\Fonts\msjh.ttc", "微软正黑": r"C:\Windows\Fonts\msjh.ttc",
    "microsoftjhenghei": r"C:\Windows\Fonts\msjh.ttc", "jhenghei": r"C:\Windows\Fonts\msjh.ttc",
    "dengxian": r"C:\Windows\Fonts\Deng.ttf", "等线": r"C:\Windows\Fonts\Deng.ttf",
    # 其它中文
    "youyuan": r"C:\Windows\Fonts\simyou.ttf", "幼圆": r"C:\Windows\Fonts\simyou.ttf",
    "lisu": r"C:\Windows\Fonts\SIMLI.TTF", "隶书": r"C:\Windows\Fonts\SIMLI.TTF",
    "stsong": r"C:\Windows\Fonts\STSONG.TTF", "华文宋体": r"C:\Windows\Fonts\STSONG.TTF",
    "stzhongsong": r"C:\Windows\Fonts\STZHONGS.TTF", "华文中宋": r"C:\Windows\Fonts\STZHONGS.TTF",
    "stkaiti": r"C:\Windows\Fonts\STKAITI.TTF", "华文楷体": r"C:\Windows\Fonts\STKAITI.TTF",
    "stfangsong": r"C:\Windows\Fonts\STFANGSO.TTF", "华文仿宋": r"C:\Windows\Fonts\STFANGSO.TTF",
    "stxihei": r"C:\Windows\Fonts\STXIHEI.TTF", "华文细黑": r"C:\Windows\Fonts\STXIHEI.TTF",
    "stxingkai": r"C:\Windows\Fonts\STXINGKA.TTF", "华文行楷": r"C:\Windows\Fonts\STXINGKA.TTF",
    "stxinwei": r"C:\Windows\Fonts\STXINWEI.TTF", "华文新魏": r"C:\Windows\Fonts\STXINWEI.TTF",
    "stliti": r"C:\Windows\Fonts\STLITI.TTF", "华文隶书": r"C:\Windows\Fonts\STLITI.TTF",
    "sthupo": r"C:\Windows\Fonts\STHUPO.TTF", "华文琥珀": r"C:\Windows\Fonts\STHUPO.TTF",
    "stcaiyun": r"C:\Windows\Fonts\STCAIYUN.TTF", "华文彩云": r"C:\Windows\Fonts\STCAIYUN.TTF",
    "fzshuti": r"C:\Windows\Fonts\FZSTK.TTF", "方正舒体": r"C:\Windows\Fonts\FZSTK.TTF",
    "fzyaoti": r"C:\Windows\Fonts\FZYTK.TTF", "方正姚体": r"C:\Windows\Fonts\FZYTK.TTF",
    # 常见西文（含中英混排）
    "arial": r"C:\Windows\Fonts\arial.ttf", "timesnewroman": r"C:\Windows\Fonts\times.ttf",
    "times": r"C:\Windows\Fonts\times.ttf", "calibri": r"C:\Windows\Fonts\calibri.ttf",
    "couriernew": r"C:\Windows\Fonts\cour.ttf", "cour": r"C:\Windows\Fonts\cour.ttf",
    "tahoma": r"C:\Windows\Fonts\tahoma.ttf", "verdana": r"C:\Windows\Fonts\verdana.ttf",
    "segoeui": r"C:\Windows\Fonts\segoeui.ttf", "georgia": r"C:\Windows\Fonts\georgia.ttf",
    "cambria": r"C:\Windows\Fonts\cambria.ttc", "consola": r"C:\Windows\Fonts\consola.ttf",
}

# 删旧字用的 redaction 参数：不动图片与线条（否则会破坏表格线）
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


def _norm_font_name(s: str | None) -> str:
    s = (s or "").lower()
    for ch in (" ", ",", "-", "_", "\t"):
        s = s.replace(ch, "")
    return s


_FONT_ALIASES_NORM = None


def find_system_font(pdf_font_name: str | None) -> str:
    """据 PDF 内字体名猜系统字体文件（按最长匹配键优先），找不到退回宋体。"""
    global _FONT_ALIASES_NORM
    if _FONT_ALIASES_NORM is None:
        _FONT_ALIASES_NORM = {_norm_font_name(k): v for k, v in FONT_ALIASES.items()}
    n = _norm_font_name(pdf_font_name)
    best_key = None
    for key in _FONT_ALIASES_NORM:
        if key and key in n and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key:
        return _FONT_ALIASES_NORM[best_key]
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


def iter_spans(doc, page_no=None):
    """遍历文本片段，yield (pno, page, span_dict, text)。

    page_no 给定时只扫该页：预览与标定只需要当前页，避免整篇扫描。
    """
    pages = range(doc.page_count) if page_no is None else (page_no,)
    for pno in pages:
        page = doc[pno]
        for block in page.get_text("rawdict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = "".join(c["c"] for c in span["chars"])
                    yield pno, page, span, text


def collect_targets(doc, repls, cfg=None):
    """按规则匹配片段，返回 [(page, bbox, origins, rule, span)]。

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

    # 每条目标的绘制参数只解析一次，删旧与重绘两趟共用
    plans = [(page, bbox, origins, rule, resolve_settings(rule, cfg))
             for page, bbox, origins, rule, _span in targets]

    # 1) 删旧字（不填白块）
    pages_touched = set()
    for page, bbox, _origins, _rule, st in plans:
        r = fitz.Rect(bbox)
        r.x0 -= st["pad_x"]
        r.x1 += st["pad_x"]
        r.y0 -= st["pad_y"]
        r.y1 += st["pad_y"]
        page.add_redact_annot(r, fill=None)
        pages_touched.add(page.number)
    for pno in pages_touched:
        doc[pno].apply_redactions(**PDF_REDACT)

    # 2) 逐字重绘
    cache = {}
    for page, _bbox, origins, rule, st in plans:
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

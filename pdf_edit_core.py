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

import json
import os
import re
import sys

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
    """字体缓存目录。

    便携版：可执行文件同级放一个 portable.flag，缓存就写进程序自己的
    文件夹（<程序目录>\\data），保证整份软件不往文件夹外写任何东西；
    否则按常规写到 %LOCALAPPDATA%\\<app>。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(exe_dir, "portable.flag")):
            d = os.path.join(exe_dir, "data")
            os.makedirs(d, exist_ok=True)
            return d
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


def _alias_file(pdf_font_name: str | None) -> str | None:
    """按手工映射表找系统字体文件（按最长匹配键优先）；没有匹配返回 None。"""
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
    return None


def find_system_font(pdf_font_name: str | None) -> str:
    """据 PDF 内字体名猜系统字体文件（按最长匹配键优先），找不到退回宋体。"""
    return _alias_file(pdf_font_name) or DEFAULT_FONT


def ensure_ttf(font_path: str, face: int = 0, cache_dir: str | None = None) -> str:
    """PyMuPDF 对 .ttc/.otc 支持不稳；若为集合字体，用 fontTools 取第 face 号字面另存 .ttf。"""
    if not font_path:
        return font_path
    if os.path.splitext(font_path)[1].lower() not in (".ttc", ".otc"):
        return font_path
    if not os.path.exists(font_path):
        return font_path
    cache_dir = cache_dir or user_cache_dir()
    face = int(face or 0)
    out = os.path.join(cache_dir, f"{os.path.basename(font_path)}.{face}.ttf")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(font_path):
        from fontTools.ttLib import TTCollection
        os.makedirs(cache_dir, exist_ok=True)
        faces = TTCollection(font_path).fonts
        if not faces:
            return font_path
        faces[min(face, len(faces) - 1)].save(out)
    return out


# ==================== 系统字体索引（自动定位 Windows 字体文件夹） ====================
# 目标：PDF 里写的是字体名（SimSun / MicrosoftYaHei-Bold / Heiti ...），
# 这里把字体目录整个扫一遍，读出每个字体文件（含 .ttc 的每个字面）的真实族名/样式名，
# 建一个"名字 -> 文件 + 字面号"的索引，之后按名字精确定位，而不是靠一张手工映射表。
FONT_EXTS = (".ttf", ".ttc", ".otf", ".otc")


def font_dirs() -> list:
    """要扫描的字体目录：系统字体 + 用户安装的字体 + 程序自己的 fonts（放下载的字体）。"""
    out = []
    win = os.environ.get("WINDIR") or r"C:\Windows"
    out.append(os.path.join(win, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    out.append(user_font_dir())
    return [d for d in out if os.path.isdir(d)]


def user_font_dir(create: bool = True) -> str:
    """用户可以往里放字体文件的文件夹（识别不到时提示放这儿）。"""
    d = os.path.join(user_cache_dir(), "fonts")
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _name_rec(nm, nid: int) -> str:
    """取 name 表里的某条记录（优先 Windows/Unicode，其次 Mac）。"""
    try:
        rec = nm.getName(nid, 3, 1) or nm.getName(nid, 1, 0)
        return rec.toUnicode().strip() if rec else ""
    except Exception:
        return ""


def _face_info(font, path: str, face: int) -> dict:
    nm = font["name"]
    fam = _name_rec(nm, 16) or _name_rec(nm, 1)
    sub = _name_rec(nm, 17) or _name_rec(nm, 2)
    full = _name_rec(nm, 4)
    ps = _name_rec(nm, 6)
    bold = italic = False
    try:
        fs = font["OS/2"].fsSelection
        bold, italic = bool(fs & 0x20), bool(fs & 0x01)
    except Exception:
        pass
    try:
        ms = font["head"].macStyle
        bold, italic = bold or bool(ms & 0x01), italic or bool(ms & 0x02)
    except Exception:
        pass
    probe = f"{sub} {full} {ps}".lower()
    if not bold:
        bold = any(k in probe for k in ("bold", "heavy", "semibold", "demibold", "black"))
    if not italic:
        italic = any(k in probe for k in ("italic", "oblique", "slanted"))
    return {"path": path, "face": face, "family": fam or ps or full,
            "sub": sub, "full": full or fam, "ps": ps, "bold": bold, "italic": italic}


def _scan_font_file(path: str) -> list:
    out = []
    try:
        from fontTools.ttLib import TTCollection
        if os.path.splitext(path)[1].lower() in (".ttc", ".otc"):
            col = TTCollection(path, lazy=True)
            for i, f in enumerate(col.fonts):
                try:
                    out.append(_face_info(f, path, i))
                except Exception:
                    continue
            col.close()
        else:
            from fontTools.ttLib import TTFont
            f = TTFont(path, lazy=True, fontNumber=0)
            out.append(_face_info(f, path, 0))
            f.close()
    except Exception:
        pass
    return out


def _font_sig() -> str:
    """字体目录签名（文件名+大小+时间），变了才重建索引。"""
    items = []
    for d in font_dirs():
        try:
            for fn in sorted(os.listdir(d)):
                if os.path.splitext(fn)[1].lower() not in FONT_EXTS:
                    continue
                p = os.path.join(d, fn)
                try:
                    st = os.stat(p)
                    items.append(f"{p}|{int(st.st_size)}|{int(st.st_mtime)}")
                except OSError:
                    continue
        except OSError:
            continue
    return str(hash(";".join(items)))


_INDEX_CACHE = {"sig": None, "fonts": None, "maps": None}


def font_index(force: bool = False) -> list:
    """全部字体的索引（带 JSON 缓存 + 目录签名校验）。"""
    if not force and _INDEX_CACHE["fonts"] is not None:
        return _INDEX_CACHE["fonts"]
    sig = _font_sig()
    cache = os.path.join(user_cache_dir(), "font_index.json")
    fonts = None
    if not force and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("sig") == sig:
                fonts = data.get("fonts") or []
        except Exception:
            fonts = None
    if fonts is None:
        fonts = []
        for d in font_dirs():
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in names:
                if os.path.splitext(fn)[1].lower() not in FONT_EXTS:
                    continue
                fonts.extend(_scan_font_file(os.path.join(d, fn)))
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"sig": sig, "fonts": fonts}, fh, ensure_ascii=False)
        except Exception:
            pass
    _INDEX_CACHE.update(sig=sig, fonts=fonts, maps=None)
    return fonts


_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _norm2(s: str) -> str:
    """归一化字体名：小写、去掉空格与常见分隔符。"""
    s = (s or "").lower()
    for ch in (" ", "-", "_", ",", ".", "\t", "'", '"', "(", ")", "+"):
        s = s.replace(ch, "")
    return s


def _style_of(name: str) -> tuple:
    """从字体名里拆出 (去样式的名字, 粗体, 斜体)。"""
    n = _norm2(name)
    bold = italic = False
    for kw in ("bolditalic", "boldoblique"):
        if kw in n:
            bold = italic = True
            n = n.replace(kw, "")
    for kw in ("semibold", "demibold", "bold", "heavy", "black", "extrabold"):
        if kw in n:
            bold = True
            n = n.replace(kw, "")
    for kw in ("italic", "oblique", "slanted"):
        if kw in n:
            italic = True
            n = n.replace(kw, "")
    for kw, b, i in (("bold", True, False), ("italic", False, True)):
        pass
    return n, bold, italic


def _font_maps():
    if _INDEX_CACHE["maps"] is None:
        by_ps, by_full, by_fam = {}, {}, {}
        for f in font_index():
            for key, mp in ((_norm2(f.get("ps")), by_ps),
                            (_norm2(f.get("full")), by_full),
                            (_norm2(f.get("family")), by_fam)):
                if key:
                    mp.setdefault(key, []).append(f)
        _INDEX_CACHE["maps"] = (by_ps, by_full, by_fam)
    return _INDEX_CACHE["maps"]


def _pick(cands: list, bold: bool, italic: bool):
    """从同名候选里挑最匹配粗/斜的那一个。"""
    if not cands:
        return None
    best, best_score = None, -9
    for f in cands:
        score = 0
        if bool(f.get("bold")) == bool(bold):
            score += 2
        else:
            score -= 1
        if bool(f.get("italic")) == bool(italic):
            score += 1
        if score > best_score:
            best, best_score = f, score
    return best


# 换平台就要找"替身"的常见字体名（macOS / Adobe 名）-> 系统里更可能存在的族名
FONT_SUBSTITUTES = {
    "heiti": "simhei", "heitisc": "simhei", "stheiti": "simhei", "heitilight": "simhei",
    "pingfangsc": "microsoftyahei", "pingfangtc": "microsoftjhenghei",
    "pingfanghk": "microsoftjhenghei",
    "songtisc": "simsun", "songtitc": "simsun", "stsong": "stsong",
    "stsongsc": "stsong", "stsongtc": "stsong", "kaitisc": "kaiti",
    "yuanti": "youyuan", "yuanticsc": "youyuan",
    "hiraginosansgb": "microsoftyahei", "notosanscjksc": "microsoftyahei",
    "notosanssc": "microsoftyahei", "sourcehansanssc": "microsoftyahei",
    "sourcehansanscn": "microsoftyahei", "notoserifcjksc": "simsun",
    "helvetica": "arial", "helveticaneue": "arial", "arialmt": "arial",
    "timesnewromanpsmt": "timesnewroman", "timesroman": "timesnewroman",
    "couriernewpsmt": "couriernew", "couriermt": "couriernew",
    "zapfdingbats": "wingdings", "symbol": "arial", "calibri": "calibri",
}


def _substitute_name(name: str) -> str | None:
    """按替身表（最长键优先）给出一个更可能存在的族名；没有则 None。"""
    n = _norm2(name)
    best = None
    for key in FONT_SUBSTITUTES:
        if key and key in n and (best is None or len(key) > len(best)):
            best = key
    return FONT_SUBSTITUTES[best] if best else None


def _guess_substitute(raw: str) -> str:
    """字体缺失时，按名字猜一个"看起来最接近"的族名（只是建议，界面会问用户）。"""
    n = _norm2(raw)
    for key, fam in (("yahei", "microsoftyahei"), ("雅黑", "microsoftyahei"),
                     ("hei", "simhei"), ("黑", "simhei"), ("sans", "simhei"),
                     ("gothic", "simhei"), ("fang", "fangsong"), ("仿", "fangsong"),
                     ("kai", "kaiti"), ("楷", "kaiti"),
                     ("song", "simsun"), ("宋", "simsun"), ("ming", "simsun"),
                     ("mincho", "simsun"), ("serif", "simsun")):
        if key in n:
            return fam
    return "simsun"


def _lookup_index(name: str, bold: bool, italic: bool):
    by_ps, by_full, by_fam = _font_maps()
    pool = []
    for mp, tag in ((by_ps, "exact"), (by_full, "exact"), (by_fam, "family")):
        for f in mp.get(name, []):
            pool.append((f, tag))
    if not pool:
        return None, ""
    cand = _pick([f for f, _t in pool], bold, italic)
    tags = [t for f, t in pool if f is cand]
    return cand, ("exact" if "exact" in tags else "family")


def resolve_font(pdf_name: str, bold: bool = False, italic: bool = False) -> dict:
    """把 PDF 里的字体名解析成可用的系统字体文件。

    顺序：系统字体索引精确匹配 -> 族名匹配 -> 平台替身表 -> 手工别名表。
    都找不到时 source="missing"：不静默顶替，把 raw 字体名报给界面，由用户决定
    （装字体 / 先用 suggest 里的替身）。
    返回 {path, face, family, source, raw, suggest, note}。
    """
    raw = (pdf_name or "").strip()
    clean = _SUBSET_PREFIX.sub("", raw)          # 去掉子集前缀 ABCDEF+
    n, n_bold, n_italic = _style_of(clean)
    bold = bool(bold or n_bold)
    italic = bool(italic or n_italic)
    if clean.lower().startswith("type3"):        # Type3：每个字是独立矢量程序，无从得知字体
        n = ""

    cand, src = None, ""
    if n:
        cand, src = _lookup_index(n, bold, italic)
    if cand is None:
        sub = _substitute_name(clean)            # 平台替身（Heiti->SimHei, Helvetica->Arial）
        if sub:
            cand, _tag = _lookup_index(sub, bold, italic)
            if cand:
                src = "substitute"
    if cand is None:
        alias = _alias_file(clean)               # 手工别名表（中文名覆盖最稳）
        if alias:
            hit = None
            for f in font_index():
                if os.path.normcase(f["path"]) == os.path.normcase(alias):
                    hit = f
                    break
            if hit is not None:
                cand, src = _pick([hit], bold, italic), "alias"
            elif os.path.exists(alias):
                cand = {"path": alias, "face": 0, "family": os.path.basename(alias),
                        "bold": bold, "italic": italic}
                src = "alias"

    if cand is None:
        # 没找到：给一个建议替身，但标记为 missing —— 界面必须让用户明确选择
        sug, _tag = _lookup_index(_guess_substitute(clean or raw), bold, italic)
        if sug is None:
            sug = {"path": DEFAULT_FONT, "face": 0, "family": "宋体 SimSun",
                   "bold": bold, "italic": italic}
        return {"path": sug["path"], "face": int(sug.get("face") or 0),
                "family": sug.get("family") or os.path.basename(sug["path"]),
                "bold": bool(sug.get("bold")), "italic": bool(sug.get("italic")),
                "source": "missing", "raw": raw or "未知",
                "suggest": {"path": sug["path"], "face": int(sug.get("face") or 0),
                            "family": sug.get("family") or os.path.basename(sug["path"])},
                "note": f"缺少字体：{raw or '未知'}"}
    return {"path": cand["path"], "face": int(cand.get("face") or 0),
            "family": cand.get("family") or os.path.basename(cand["path"]),
            "bold": bool(cand.get("bold")), "italic": bool(cand.get("italic")),
            "source": src, "raw": raw, "suggest": None, "note": ""}


def all_font_choices() -> list:
    """下拉列表用：(文件路径, 显示名, 字面号)。内置候排放前面，其后按名字排序。"""
    out, seen = [], set()

    def add(path, label, face=0):
        key = (os.path.normcase(path), int(face))
        if key in seen:
            return
        seen.add(key)
        out.append((path, label, int(face)))

    for p, label in list_available_fonts():
        add(p, label, 0)
    for f in sorted(font_index(), key=lambda x: (_norm2(x.get("family")), x.get("face", 0))):
        fam = f.get("family") or os.path.basename(f["path"])
        sub = f.get("sub") or ""
        label = fam + (f" · {sub}" if sub and _norm2(sub) not in ("regular", _norm2(fam)) else "")
        if (f.get("face") or 0) > 0:
            label += f" #{f['face']}"
        add(f["path"], label, f.get("face") or 0)
    return out


def resolve_settings(rule: dict, cfg: dict) -> dict:
    """把全局配置 + 单条规则合并成最终绘制参数。"""
    size = float(rule.get("font_size", cfg.get("font_size", 10)))
    return {
        "font_file": rule.get("font") or cfg.get("font") or DEFAULT_FONT,
        "font_face": int(rule.get("font_face") or cfg.get("font_face") or 0),
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
        key = (st["font_file"], st["font_face"])
        ttf = cache.setdefault(key, ensure_ttf(st["font_file"], st["font_face"]))
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

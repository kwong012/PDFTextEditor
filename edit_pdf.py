# -*- coding: utf-8 -*-
"""
edit_pdf —— 命令行版：按 config.json 原位替换 PDF 文字

用法:
    python edit_pdf.py --config config.json
    python edit_pdf.py --config config.json --dry-run
配置见 config.example.json
"""
import argparse
import json
import os
import sys

import pymupdf as fitz

import pdf_edit_core as core


def _abspath(base_dir, p):
    return p if os.path.isabs(p) else os.path.join(base_dir, p)


def _glyph_note(rule, cfg):
    """新文字里缺字形（会逐字回退到别的字体）时的说明；没有则返回空串。

    与界面「替换」行/日志用的是同一套 core 判定，所以命令行看到的去向和界面一致。
    """
    st = core.resolve_settings(rule, cfg)
    new = rule.get("new") or ""
    primary = (st["font_file"], st["font_face"])
    plan = core.char_font_plan(new, primary[0], primary[1],
                               core.font_fallback_chain(primary, st.get("font_fallback")))
    fallback, none = {}, []
    for ch, path, face, status in plan:
        if status == "fallback":
            fallback.setdefault(core.font_label(path, face), []).append(ch)
        elif status == "none":
            none.append(ch)
    parts = []
    if fallback:
        parts.append("缺字形 %d 个，回退：%s"
                     % (sum(len(v) for v in fallback.values()),
                        "；".join("%s → %s" % ("".join(v), k) for k, v in fallback.items())))
    if none:
        parts.append("有 %d 个字连候选字体里都没有，可能显示成方框：%s" % (len(none), "".join(none)))
    return "；".join(parts)


def main():
    ap = argparse.ArgumentParser(description="PDF 原位文字替换（保持外观一致）")
    ap.add_argument("--config", required=True, help="JSON 配置文件")
    ap.add_argument("--dry-run", action="store_true", help="只列出匹配到的片段，不写文件")
    args = ap.parse_args()

    cfg_path = os.path.abspath(args.config)
    base = os.path.dirname(cfg_path)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    src = _abspath(base, cfg["src"])
    out = _abspath(base, cfg.get("out") or (os.path.splitext(src)[0] + "_edited.pdf"))
    repls = cfg.get("replacements", [])

    doc = fitz.open(src)
    meta = doc.metadata
    targets = core.collect_targets(doc, repls, cfg)

    print(f"匹配到 {len(targets)} 处：")
    for page, bbox, _origins, rule, _span in targets:
        print(f"  p{page.number} {rule.get('old')!r} -> {rule.get('new')!r} "
              f"bbox={[round(v, 1) for v in bbox]}")

    matched_olds = {id(t[3]) for t in targets}
    for rule in repls:
        if id(rule) not in matched_olds:
            print(f"  [警告] 未找到片段: {rule.get('old')!r}", file=sys.stderr)

    # 缺字形的字会被逐字回退：先把去向说清楚（dry-run 时尤其有用）
    for rule in {id(r): r for _p, _b, _o, r, _s in targets}.values():
        note = _glyph_note(rule, cfg)
        if note:
            print(f"  [提示] {rule.get('old')!r} -> {rule.get('new')!r}：{note}")

    if args.dry_run:
        return 0 if targets else 1

    core.apply_replacements(doc, cfg, targets)
    core.finalize(doc, out, meta)
    print(f"已保存: {out}  ({os.path.getsize(out)} bytes)")
    return 0 if targets else 1


if __name__ == "__main__":
    raise SystemExit(main())

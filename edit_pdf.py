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

import fitz

import pdf_edit_core as core


def _abspath(base_dir, p):
    return p if os.path.isabs(p) else os.path.join(base_dir, p)


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
    meta = fitz.open(src).metadata
    targets = core.collect_targets(doc, repls, cfg)

    print(f"匹配到 {len(targets)} 处：")
    for page, bbox, origins, rule, span in targets:
        print(f"  p{page.number} {rule.get('old')!r} -> {rule.get('new')!r} "
              f"bbox={[round(v, 1) for v in bbox]}")

    matched_olds = {id(t[3]) for t in targets}
    for rule in repls:
        if id(rule) not in matched_olds:
            print(f"  [警告] 未找到片段: {rule.get('old')!r}", file=sys.stderr)

    if args.dry_run:
        return 0 if targets else 1

    core.apply_replacements(doc, cfg, targets)
    core.finalize(doc, out, meta)
    print(f"已保存: {out}  ({os.path.getsize(out)} bytes)")
    return 0 if targets else 1


if __name__ == "__main__":
    raise SystemExit(main())

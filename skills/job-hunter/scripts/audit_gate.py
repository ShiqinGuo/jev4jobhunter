#!/usr/bin/env python3
"""Portable mechanical text checks. Passing is not authorization to send."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


def check_text(text: str, *, min_len: int = 1, max_len: int = 800,
               allowed_links: list[str] | None = None) -> dict:
    if min_len < 1 or max_len < min_len:
        raise ValueError("invalid-length-range")
    hits = []
    if not text.strip():
        hits.append("empty-text")
    if not min_len <= len(text) <= max_len:
        hits.append("length-out-of-range")
    for marker in ("as an ai", "人工智能语言模型", "traceback (most recent call last)", chr(96) * 3):
        if marker in text.casefold():
            hits.append("template-or-debug-output")
            break
    if re.search(r"\{\{[^{}\n]+\}\}|\[(?:姓名|公司名|职位名|待补充|TODO|NAME|COMPANY)\]", text, re.I):
        hits.append("unfilled-placeholder")
    if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in text):
        hits.append("control-character")
    approved = set(allowed_links or [])
    for link in re.findall(r"https?://[^\s<>\"']+", text):
        link = link.rstrip(".,;!?，。；！？)）]】")
        if link not in approved:
            hits.append("unapproved-link")
            break
    return {"pass": not hits, "hits": hits, "len": len(text)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--level", choices=["L1", "L2", "L3"], default="L2",
                        help="Compatibility label; does not grant permission.")
    parser.add_argument("--thread", default="")
    parser.add_argument("--min-len", type=int, default=1)
    parser.add_argument("--max-len", type=int, default=800)
    parser.add_argument("--allow-link", action="append", default=[])
    args = parser.parse_args()
    try:
        text = args.text_file.read_text(encoding="utf-8-sig") if args.text_file else args.text
        result = check_text(text, min_len=args.min_len, max_len=args.max_len,
                            allowed_links=args.allow_link)
        result.update(level=args.level, thread=args.thread)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["pass"] else 1
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"pass": False, "error": str(error)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

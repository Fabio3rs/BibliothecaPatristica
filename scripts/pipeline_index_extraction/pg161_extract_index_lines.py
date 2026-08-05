#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/pg161_extract_index_lines.py <ocr_page.txt> [...]
# Prints the textual content of OCR blocks for quick inspection of PG161 index pages.

from __future__ import annotations

import re
import sys
from pathlib import Path


BLOCK_RE = re.compile(r"<bloco[^>]*>\s*(.*?)\s*</bloco>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    text = TAG_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: pg161_extract_index_lines.py <ocr_page.txt> [...]", file=sys.stderr)
        return 1
    for arg in argv[1:]:
        path = Path(arg)
        print(f"FILE {path.name}")
        content = path.read_text(encoding="utf-8")
        for idx, match in enumerate(BLOCK_RE.finditer(content), start=1):
            cleaned = clean(match.group(1))
            if cleaned:
                print(f"[{idx}] {cleaned}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

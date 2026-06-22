#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

PATTERNS = [
    re.compile(r'\bELENCHUS\b', re.I),
    re.compile(r'\bAUCTORUM\s+ET\s+OPERUM\b', re.I),
    re.compile(r'\bINDEX\s+CAPITUM\b', re.I),
    re.compile(r'\bPROLEGOMENA\b', re.I),
    re.compile(r'\bORDO\s+RERUM\b', re.I),
    re.compile(r'\bINDEX\s+ANALYTICUS\b', re.I),
    re.compile(r'\bINDEX\s+RERUM\s+ET\s+VERBORUM\b', re.I),
    re.compile(r'\bINDEX\s+GR[ÆAE]CITATIS\b', re.I),
]


@dataclass
class Hit:
    file: str
    page: int | None
    line: int
    text: str


def page_from_path(path: Path) -> int | None:
    m = re.search(r'-(\d+)\.txt$', path.name)
    return int(m.group(1)) if m else None


def scan_file(path: Path, max_lines: int | None = None) -> list[Hit]:
    hits: list[Hit] = []
    with path.open('r', encoding='utf-8', errors='replace') as fh:
        for line_no, raw in enumerate(fh, start=1):
            if max_lines is not None and line_no > max_lines:
                break
            line = raw.rstrip('\n')
            if not line.strip():
                continue
            if any(p.search(line) for p in PATTERNS):
                hits.append(Hit(str(path), page_from_path(path), line_no, line.strip()))
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description='Scan one PG/PL volume for index headings.')
    ap.add_argument('--volume', required=True, help='Volume code, e.g. PG001 or PL099')
    ap.add_argument('--root', type=Path, default=Path('teste'), help='Root directory that contains PG*/PL* folders')
    ap.add_argument('--sample', type=int, default=8, help='Number of files to show from the front and tail')
    ap.add_argument('--max-lines', type=int, default=80, help='Max lines to inspect per sampled file')
    ap.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    args = ap.parse_args()

    text_root = args.root / args.volume / 'text'
    files = sorted(text_root.glob('*.txt'))
    if not files:
        raise SystemExit(f'No OCR text files found in {text_root}')

    front = files[: args.sample]
    tail = files[-args.sample :] if args.sample > 0 else []
    seen = set(front)
    sample_files = front + [p for p in tail if p not in seen]

    all_hits = []
    for path in files:
        all_hits.extend(scan_file(path))

    if args.json:
        print(json.dumps({
            'volume': args.volume,
            'text_root': str(text_root),
            'file_count': len(files),
            'sample_files': [str(p) for p in sample_files],
            'all_hits': [hit.__dict__ for hit in all_hits],
        }, ensure_ascii=False, indent=2))
        return

    print(f'VOLUME {args.volume}')
    print(f'ROOT   {text_root}')
    print(f'FILES  {len(files)}')
    print()

    print('== Sample hits ==')
    for path in sample_files:
        page = page_from_path(path)
        print(f'[{page if page is not None else "?"}] {path.name}')
        with path.open('r', encoding='utf-8', errors='replace') as fh:
            for line_no, raw in enumerate(fh, start=1):
                if line_no > args.max_lines:
                    break
                line = raw.rstrip('\n')
                if not line.strip():
                    continue
                if any(p.search(line) for p in PATTERNS):
                    print(f'  L{line_no}: {line.strip()}')
    print()

    print('== All heading hits ==')
    for hit in all_hits:
        print(f'[{hit.page if hit.page is not None else "?"}] {Path(hit.file).name}:L{hit.line} {hit.text}')


if __name__ == '__main__':
    main()

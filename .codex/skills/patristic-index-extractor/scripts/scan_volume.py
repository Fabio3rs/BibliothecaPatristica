#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from patristica_pipeline.common import page_sort_key, parse_volume_info


PATTERN_GROUPS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "common": [
        ("volume_title", re.compile(r"\bTOMUS\b", re.I)),
    ],
    "PGPL": [
        ("volume_front", re.compile(r"\bELENCHUS\b", re.I)),
        ("volume_front", re.compile(r"\bAUCTORUM\s+ET\s+OPERUM\b", re.I)),
        ("work_front", re.compile(r"\bINDEX\s+CAPITUM\b", re.I)),
        ("work_front", re.compile(r"\bPROLEGOMENA\b", re.I)),
        ("volume_end", re.compile(r"\bORDO\s+RERUM\b", re.I)),
        ("volume_end", re.compile(r"\bINDEX\s+ANALYTICUS\b", re.I)),
        ("volume_end", re.compile(r"\bINDEX\s+RERUM\s+ET\s+VERBORUM\b", re.I)),
        ("volume_end", re.compile(r"\bINDEX\s+GR[ÆAE]CITATIS\b", re.I)),
    ],
    "PO": [
        ("volume_table", re.compile(r"^\s*TABLE\s+DES\s+MATI[ÈE]RES\b", re.I)),
        ("fascicle_inventory", re.compile(r"^\s*(?:FASC\.\s*[IVXLC0-9]+\s*[—-]|PATR\.\s*OR\..*\bFASC\.\s*[IVXLC0-9]+\b)", re.I)),
        ("work_front_matter", re.compile(r"^\s*AVERTISSEMENT\.?\s*$", re.I)),
        ("work_front_matter", re.compile(r"^\s*INTRODUCTION\.?\s*$", re.I)),
        ("work_front_matter", re.compile(r"^\s*PR[ÉE]FACE\.?\s*$", re.I)),
        ("work_front_matter", re.compile(r"^\s*PROLOGUE\.?\s*$", re.I)),
        ("work_internal_table", re.compile(r"^\s*TABLE\s+DES\s+MATI[ÈE]RES\s+CONTENUES\s+DANS\s+CE\s+LIVRE\b", re.I)),
        ("work_index_nominal", re.compile(r"^\s*TABLE\s+DES\s+NOMS\s+PROPRES\b", re.I)),
        ("work_index_nominal", re.compile(r"^\s*INDEX\s+DES\s+NOMS\s+PROPRES\b", re.I)),
        ("work_index_nominal", re.compile(r"^\s*TABLE\s+ALPHAB[ÉE]TIQUE\s+DES\s+NOMS\s+PROPRES\b", re.I)),
        ("work_index_scripture", re.compile(r"^\s*INDEX\s+DES\s+CITATIONS\s+DES\s+[ÉE]CRITURES\b", re.I)),
        ("work_index_scripture", re.compile(r"^\s*INDEX\s+DES\s+PASSAGES\s+DE\s+LA\s+SAINTE\s+[ÉE]CRITURE\b", re.I)),
        ("work_index_alphabetical", re.compile(r"^\s*TABLE\s+ALPHAB[ÉE]TIQUE(?:\s+DES\s+MATI[ÈE]RES)?\.?\s*$", re.I)),
        ("work_index_analytic", re.compile(r"^\s*TABLE\s+ANALYTIQUE\s+DES\s+MATI[ÈE]RES\b", re.I)),
        ("editorial_closure", re.compile(r"^\s*ADDENDA(?:\s+AND\s+CORRIGENDA)?\.?\s*$", re.I)),
        ("editorial_closure", re.compile(r"^\s*CORRIGENDA\.?\s*$", re.I)),
    ],
}


@dataclass
class Hit:
    file: str
    file_seq: int | None
    page: int | None
    line: int
    text: str
    kind: str


def patterns_for_collection(collection: str) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = list(PATTERN_GROUPS["common"])
    if collection == "PO":
        patterns.extend(PATTERN_GROUPS["PO"])
    else:
        patterns.extend(PATTERN_GROUPS["PGPL"])
    return patterns


def file_seq_from_path(path: Path) -> int | None:
    m = re.search(r'-(\d+)\.txt$', path.name)
    return int(m.group(1)) if m else None


def fold_ligatures(text: str) -> str:
    return (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )


def scan_file(path: Path, patterns: Iterable[tuple[str, re.Pattern[str]]], max_lines: int | None = None) -> list[Hit]:
    hits: list[Hit] = []
    with path.open('r', encoding='utf-8', errors='replace') as fh:
        for line_no, raw in enumerate(fh, start=1):
            if max_lines is not None and line_no > max_lines:
                break
            line = raw.rstrip('\n')
            if not line.strip():
                continue
            folded_line = fold_ligatures(line)
            for kind, pattern in patterns:
                if pattern.search(line) or pattern.search(folded_line):
                    file_seq = file_seq_from_path(path)
                    hits.append(Hit(str(path), file_seq, None, line_no, line.strip(), kind))
                    break
    return hits


def summarize_hits(hits: list[Hit]) -> dict[str, list[dict[str, object]]]:
    summary: dict[str, list[dict[str, object]]] = {}
    seen: set[tuple[str, int | None, str]] = set()
    for hit in hits:
        key = (hit.kind, hit.file_seq, hit.text)
        if key in seen:
            continue
        seen.add(key)
        summary.setdefault(hit.kind, []).append(
            {
                "file_seq": hit.file_seq,
                "page": hit.page,
                "file": hit.file,
                "line": hit.line,
                "text": hit.text,
            }
        )
    return summary


def detect_retrospective_tables(hits: list[Hit], collection: str) -> list[dict[str, object]]:
    if collection != "PO":
        return []
    retrospective: list[dict[str, object]] = []
    for hit in hits:
        upper = hit.text.upper()
        if hit.kind == "volume_table" and ("TOMES" in upper or re.search(r"\bTOME\s+[IVXLC0-9]+\b", upper)):
            retrospective.append(
                {
                    "file_seq": hit.file_seq,
                    "page": hit.page,
                    "file": hit.file,
                    "line": hit.line,
                    "text": hit.text,
                }
            )
    return retrospective


def main() -> None:
    ap = argparse.ArgumentParser(description='Scan one PG/PL/PO volume for index headings.')
    ap.add_argument('--volume', required=True, help='Volume code, e.g. PG001, PL099, or PO025')
    ap.add_argument('--root', type=Path, default=Path('teste'), help='Root directory that contains PG*/PL*/PO* folders')
    ap.add_argument('--sample', type=int, default=8, help='Number of files to show from the front and tail')
    ap.add_argument('--max-lines', type=int, default=80, help='Max lines to inspect per sampled file')
    ap.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    args = ap.parse_args()

    text_root = args.root / args.volume / 'text'
    info = parse_volume_info(args.root / args.volume)
    collection = info.series if info is not None else args.volume[:2].upper()
    patterns = patterns_for_collection(collection)
    files = sorted(text_root.glob('*.txt'), key=page_sort_key)
    if not files:
        raise SystemExit(f'No OCR text files found in {text_root}')

    front = files[: args.sample]
    tail = files[-args.sample :] if args.sample > 0 else []
    seen = set(front)
    sample_files = front + [p for p in tail if p not in seen]

    all_hits = []
    for path in files:
        all_hits.extend(scan_file(path, patterns))
    summary = summarize_hits(all_hits)
    retrospective_tables = detect_retrospective_tables(all_hits, collection)

    if args.json:
        print(json.dumps({
            'volume': args.volume,
            'collection': collection,
            'text_root': str(text_root),
            'file_count': len(files),
            'sample_files': [str(p) for p in sample_files],
            'hit_summary': summary,
            'retrospective_tables': retrospective_tables,
            'all_hits': [hit.__dict__ for hit in all_hits],
        }, ensure_ascii=False, indent=2))
        return

    print(f'VOLUME {args.volume}')
    print(f'COLL   {collection}')
    print(f'ROOT   {text_root}')
    print(f'FILES  {len(files)}')
    print()

    print('== Sample hits ==')
    for path in sample_files:
        file_seq = file_seq_from_path(path)
        print(f'[OCR file {file_seq if file_seq is not None else "?"}] {path.name}')
        with path.open('r', encoding='utf-8', errors='replace') as fh:
            for line_no, raw in enumerate(fh, start=1):
                if line_no > args.max_lines:
                    break
                line = raw.rstrip('\n')
                if not line.strip():
                    continue
                folded_line = fold_ligatures(line)
                for kind, pattern in patterns:
                    if pattern.search(line) or pattern.search(folded_line):
                        print(f'  L{line_no} [{kind}]: {line.strip()}')
                        break
    print()

    print('== All heading hits ==')
    for hit in all_hits:
        print(f'[OCR file {hit.file_seq if hit.file_seq is not None else "?"}] {Path(hit.file).name}:L{hit.line} [{hit.kind}] {hit.text}')


if __name__ == '__main__':
    main()

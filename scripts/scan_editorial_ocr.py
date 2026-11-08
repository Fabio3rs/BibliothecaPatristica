#!/usr/bin/env python3
"""Read-only exploratory scan of OCR pages in teste/{volume}/text/*.txt.

Finds documented alternate-edition numbering, index citation patterns, suspicious
split digits, explicit Latin/Greek terms and possible inline page-number sequences.
All results are investigative leads, NOT verified editorial mappings.

Examples:
  python scripts/scan_editorial_ocr.py --root teste --out data/editorial_scan.jsonl
  python scripts/scan_editorial_ocr.py --root teste --volume PG040 --markers --out data/pg040_markers.jsonl
  python scripts/scan_editorial_ocr.py --root teste --volume PG110 --min-page 660 --max-page 666 --out /tmp/pg110_index.jsonl
  python scripts/scan_editorial_ocr.py --root teste --volume PG113 --term Rastoiza --term Rhamble --term Roma --out /tmp/pg113_terms.jsonl
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import heapq
import json
from pathlib import Path
import re
import sys

DEFAULT_VOLUMES = ("PG110", "PL010", "PG040", "PG113", "PG148", "PG087.01")
FILE_NUMBER = re.compile(r"-(\d+)\.txt$", re.I)
BLOCK = re.compile(r"<bloco\b(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S | re.I)
ATTR = re.compile(r'\b([a-z_]+)="([^"]*)"', re.I)
INDEX_HEADER = re.compile(r"\b(?:INDEX|INDICES|INDICE[ΣS]?|GLOSSAR\w*|ONOMASTIC\w*)\b", re.I)
EDITION_NOTE = re.compile(
    r"petropol\w*|veron\w*|antver\w*|antuer\w*|"
    r"(?:edit(?:ion\w*|\.)\s*.{0,65}?(?:pagin\w*|numer\w*))|"
    r"(?:numer\w*\s*.{0,65}?(?:edition\w*|pagin\w*))",
    re.I | re.S,
)
REF_PATTERNS = {
    "leo_page_line": re.compile(r"(?<!\w)Leo\s+(\d{1,4})\s*,\s*(\d{1,2})(?!\d)", re.I),
    "roman_tome_page": re.compile(r"(?<!\w)(?:VIII|VII|VI|IV|III|II|IX|V|X|I)\s*,\s*(\d{2,4})(?!\d)", re.I),
    "number_page_line": re.compile(r"(?<!\w)(\d{2,4})\s*,\s*(\d{1,2})(?!\d)"),
    # OCR '121 9' can be 1219; this is a weak clue, never a repair rule.
    "split_number_candidate": re.compile(r"(?<!\d)(\d{2,3})[ \t]+([0-9])(?!\d)"),
}
BOLD = re.compile(r"\*\*(\d{2,4})\*\*")
# Number placed between running words, as in '... δὲ 114 πε-'.
INLINE = re.compile(r"(?<![\w*])(\d{2,4})(?!\d)[ \t]+(?=[^\W\d_])", re.UNICODE)


def page_number(path: Path) -> int | None:
    match = FILE_NUMBER.search(path.name)
    return int(match[1]) if match else None


def excerpt(value: str, at: int, length: int, radius: int = 100) -> str:
    return " ".join(value[max(0, at - radius): at + length + radius]
                    .replace("[br/]", " ").split())


def block_parts(raw: str):
    for m in BLOCK.finditer(raw):
        attrs = dict(ATTR.findall(m["attrs"]))
        yield {
            "kind": attrs.get("tipo", ""),
            "script": attrs.get("script", ""),
            "body": m["body"],
            "start_offset": m.start("body"),
        }


def file_line(raw: str, absolute_offset: int) -> int:
    return raw.count("\n", 0, absolute_offset) + 1


def compact_match(raw, block, match):
    body = block["body"]
    return {
        "file_line": file_line(raw, block["start_offset"] + match.start()),
        "block_type": block["kind"],
        "script": block["script"],
        "match": match.group(0),
        "context": excerpt(body, match.start(), len(match.group(0))),
    }


def scan_page(path: Path, volume: str, terms: list[str], args):
    data = path.read_bytes()
    raw = data.decode("utf-8", errors="replace")
    blocks = list(block_parts(raw))
    header = " ".join(b["body"] for b in blocks if b["kind"] == "cabecalho")
    is_index = bool(INDEX_HEADER.search(header))
    base = {"volume": volume, "physical_page": page_number(path),
            "file": str(path), "ocr_sha256": hashlib.sha256(data).hexdigest()}
    events = []
    markers = []
    term_patterns = [(term, re.compile(re.escape(term), re.I)) for term in terms]

    for block in blocks:
        kind, body = block["kind"], block["body"]
        if kind in ("cabecalho", "nota", "texto_principal", "aparato_critico"):
            for m in list(EDITION_NOTE.finditer(body))[:args.max_per_page]:
                events.append({**base, "kind": "editorial_note_hint", **compact_match(raw, block, m)})
        if kind in ("cabecalho", "nota", "texto_principal", "aparato_critico"):
            for term, regex in term_patterns:
                for m in list(regex.finditer(body))[:args.max_per_page]:
                    events.append({**base, "kind": "term_hit", "term": term,
                                   **compact_match(raw, block, m)})

    primary = [b for b in blocks if b["kind"] == "texto_principal"]
    if is_index:
        counts = Counter()
        examples = {}
        for name, pattern in REF_PATTERNS.items():
            hits = []
            for block in primary:
                for m in pattern.finditer(block["body"]):
                    counts[name] += 1
                    if len(hits) < args.max_per_page:
                        hits.append(compact_match(raw, block, m))
            examples[name] = hits
        if counts:
            events.append({**base, "kind": "index_reference_patterns", "header":
                           " ".join(header.split())[:180], "counts": dict(counts), "examples": examples})

    if args.markers and not is_index:
        for block in primary:
            for label, pattern in (("bold", BOLD), ("inline", INLINE)):
                for m in pattern.finditer(block["body"]):
                    number = int(m[1])
                    if not args.min_marker <= number <= args.max_marker:
                        continue
                    markers.append({"number": number, "style": label,
                                    **compact_match(raw, block, m)})
        # Avoid recording the same numeral twice when the bold marker matches inline.
        unique = {}
        for m in markers:
            unique[(m["number"], m["file_line"], m["style"])] = m
        markers = list(unique.values())
        if markers:
            events.append({**base, "kind": "possible_inline_markers", "header":
                           " ".join(header.split())[:180], "total": len(markers),
                           "samples": markers[:args.max_per_page]})
    return events, markers, base


def marker_links(volume: str, page_candidates, max_pairs: int):
    """Cross-file monotonic clues, not automatically accepted anchors."""
    heap = []
    total = 0
    numbers = sorted(page_candidates)
    for i, physical in enumerate(numbers):
        here = page_candidates[physical]
        if len(here) > 12:  # crowded with footnotes/inline numbers: weak candidates
            continue
        for nxt in numbers[i + 1:]:
            gap = nxt - physical
            if gap > 7:
                break
            if gap < 1 or len(page_candidates[nxt]) > 12:
                continue
            for a in here:
                for b in page_candidates[nxt]:
                    dn = b["number"] - a["number"]
                    if not 1 <= dn <= gap + 2:
                        continue
                    if abs(dn - gap) > 2:
                        continue
                    priority = 4 * (a["style"] == "bold") + 4 * (b["style"] == "bold")
                    priority += max(0, 3 - abs(dn - gap))
                    priority += 2 if gap == 1 else 0
                    total += 1
                    event = {"kind": "possible_number_progression", "volume": volume,
                                  "physical_a": physical, "physical_b": nxt,
                                  "number_a": a["number"], "number_b": b["number"],
                                  "file_a": a["file"], "file_b": b["file"],
                                  "line_a": a["file_line"], "line_b": b["file_line"],
                                  "context_a": a["context"], "context_b": b["context"],
                                  "score_for_review_order": priority}
                    if max_pairs:
                        rank = (priority, -physical, -a["number"], -nxt, -b["number"], total)
                        if len(heap) < max_pairs:
                            heapq.heappush(heap, (rank, event))
                        elif rank > heap[0][0]:
                            heapq.heapreplace(heap, (rank, event))
    pairs = [event for _, event in heap]
    pairs.sort(key=lambda x: (-x["score_for_review_order"], x["physical_a"], x["number_a"]))
    return pairs, total


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=Path("teste"))
    p.add_argument("--volume", action="append", default=[], help="Repeatable, e.g. --volume PG110 --volume PL010")
    p.add_argument("--all", action="store_true", help="Scan all existing volume directories")
    p.add_argument("--min-page", type=int, default=0)
    p.add_argument("--max-page", type=int, default=999999)
    p.add_argument("--term", action="append", default=[], help="Literal search, repeatable")
    p.add_argument("--terms-file", type=Path, help="UTF-8 file with one literal search term per line")
    p.add_argument("--markers", action="store_true", help="Include weak internal-number candidates and page-to-page links")
    p.add_argument("--min-marker", type=int, default=50)
    p.add_argument("--max-marker", type=int, default=1500)
    p.add_argument("--max-pairs", type=int, default=150, help="Maximum progression examples per volume")
    p.add_argument("--max-per-page", type=int, default=5, help="Maximum evidence excerpts per category per page")
    p.add_argument("--out", type=Path, required=True, help="New JSONL file; never overwritten")
    args = p.parse_args()
    if args.min_page > args.max_page or args.min_marker > args.max_marker or args.max_per_page < 1 or args.max_pairs < 0:
        p.error("Invalid page/marker range or maximum")
    terms = list(dict.fromkeys(args.term))
    if args.terms_file:
        terms += [s.strip() for s in args.terms_file.read_text(encoding="utf-8").splitlines()
                  if s.strip() and not s.lstrip().startswith("#")]
    terms = list(dict.fromkeys(s for s in terms if s))
    if args.all:
        volumes = [d.name for d in args.root.iterdir() if d.is_dir() and (d / "text").is_dir()]
    else:
        volumes = args.volume or list(DEFAULT_VOLUMES)
    volumes = sorted(dict.fromkeys(volumes))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.out.with_name(args.out.name + ".summary.json")
    if summary_path.exists():
        p.error(f"summary output already exists: {summary_path}")
    summary = {"root": str(args.root), "volumes": volumes, "pages_scanned": 0,
               "events_by_kind": Counter(), "events_by_volume": defaultdict(Counter),
               "terms": terms, "warnings": [],
               "note": "Investigative clues only. No DB mutations, no verified mapping."}
    with args.out.open("x", encoding="utf-8") as output:
        for volume in volumes:
            root = args.root / volume / "text"
            if not root.is_dir():
                summary["warnings"].append(f"missing: {root}")
                continue
            pages = sorted(((page_number(f), f) for f in root.glob("*.txt") if page_number(f) is not None),
                           key=lambda it: (it[0], str(it[1])))
            page_candidates = defaultdict(list)
            for physical, path in pages:
                if not args.min_page <= physical <= args.max_page:
                    continue
                events, markers, base = scan_page(path, volume, terms, args)
                summary["pages_scanned"] += 1
                for event in events:
                    output.write(json.dumps(event, ensure_ascii=False) + "\n")
                    summary["events_by_kind"][event["kind"]] += 1
                    summary["events_by_volume"][volume][event["kind"]] += 1
                for m in markers:
                    page_candidates[physical].append({**m, "file": str(path)})
            if args.markers:
                links, total = marker_links(volume, page_candidates, args.max_pairs)
                for event in links:
                    output.write(json.dumps(event, ensure_ascii=False) + "\n")
                    summary["events_by_kind"][event["kind"]] += 1
                    summary["events_by_volume"][volume][event["kind"]] += 1
                summary.setdefault("possible_number_progression_counts", {})[volume] = total
            print(f"{volume}: scanned {sum(args.min_page <= n <= args.max_page for n,_ in pages)} pages", file=sys.stderr)
    summary["events_by_kind"] = dict(summary["events_by_kind"])
    summary["events_by_volume"] = {k: dict(v) for k, v in summary["events_by_volume"].items()}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pages_scanned": summary["pages_scanned"], "events": summary["events_by_kind"],
                      "log": str(args.out), "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

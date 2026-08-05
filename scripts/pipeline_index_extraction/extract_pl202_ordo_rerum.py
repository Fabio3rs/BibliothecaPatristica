#!/usr/bin/env python3
"""Extract PL202 closing ORDO RERUM contents lines and build a helper request.

Run:
  python scripts/pipeline_index_extraction/extract_pl202_ordo_rerum.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL202/text \
    --parsed-out /homessddata/Projects/pdfocr/data/intermediate_payloads/PL202/parsed_entries.json \
    --helper-request-out /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL202_helper_request.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


START_MARKER = "JOANNES BELETHUS THEOLOGUS PARISIENSIS."
NOISE_LINES = {
    "Digitized by Google",
    "Patrol. CCII.",
    "ORDO RERUM",
    "ORDO RERUM ?",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "QUÆ IN HOC TOMO CONTINENTUR.",
}

PAGE_ONLY_RE = re.compile(r"^\d+(?:-\d+)?$")
PAGE_AT_END_RE = re.compile(r"^(?P<text>.*?)(?:\s+)(?P<page>\d+(?:-\d+)?)(?:\.)?$")
CAPISH_RE = re.compile(r"^[A-Z0-9ÆŒÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÇ\s\.,;:\-\(\)\?\/\'’]+$")


def iter_text_lines(source_root: Path, file_paths: list[Path] | None = None):
    paths = file_paths if file_paths is not None else sorted(source_root.glob("*.txt"))
    for path in paths:
        txt = path.read_text(encoding="utf-8")
        in_block = False
        block_type = None
        for raw in txt.splitlines():
            m = re.search(r'<bloco tipo="([^"]+)"', raw)
            if m:
                block_type = m.group(1)
                in_block = True
                continue
            if in_block and raw.strip() == "</bloco>":
                in_block = False
                block_type = None
                continue
            if in_block and block_type == "texto_principal":
                s = raw.strip()
                if s:
                    yield path, s


def parse_contents(source_root: Path, file_paths: list[Path] | None = None):
    collect = False
    buf: list[str] = []
    buf_file: str | None = None
    entries: list[dict] = []

    def flush_buffer():
        nonlocal buf, buf_file
        if buf:
            entries.append({
                "source_file": buf_file,
                "entry_raw": " ".join(buf).strip(),
            })
            buf = []
            buf_file = None

    for path, line in iter_text_lines(source_root, file_paths=file_paths):
        if not collect:
            if line == START_MARKER:
                collect = True
                entries.append({
                    "source_file": str(path),
                    "entry_raw": line,
                })
            continue

        if line in NOISE_LINES:
            continue

        if PAGE_ONLY_RE.match(line):
            if buf:
                buf.append(line)
                flush_buffer()
            continue

        m = PAGE_AT_END_RE.match(line)
        if m:
            text = m.group("text").rstrip()
            page = m.group("page")
            if buf:
                buf.append(text)
                buf.append(page)
                flush_buffer()
            else:
                entries.append({
                    "source_file": str(path),
                    "entry_raw": f"{text} {page}",
                })
            continue

        if CAPISH_RE.match(line) and "—" not in line and ":" not in line and "," not in line and ";" not in line:
            flush_buffer()
            entries.append({
                "source_file": str(path),
                "entry_raw": line,
            })
            continue

        if not buf:
            buf_file = str(path)
        buf.append(line)

    flush_buffer()
    return entries


def build_helper_request(volume_id: str, source_root: Path, parsed_entries: list[dict]):
    helper_entries = []
    for idx, item in enumerate(parsed_entries, start=1):
        raw = item["entry_raw"]
        m = PAGE_AT_END_RE.match(raw)
        if not m:
            continue
        page = m.group("page")
        if not page.isdigit() and "-" not in page:
            continue
        lemma = m.group("text").rstrip()
        query_names = [lemma]
        if "—" in lemma:
            query_names.append(lemma.split("—", 1)[0].strip())
        helper_entries.append({
            "entry_id": f"{volume_id.lower()}_ordo_{idx:04d}",
            "lemma_raw": lemma,
            "query_names": query_names,
            "page_hints": [page],
            "page_hint_ints": [int(page.split("-", 1)[0])],
            "context_raw": raw,
        })
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--volume-id", default="PL202")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--parsed-out", required=True)
    parser.add_argument("--helper-request-out", required=True)
    parser.add_argument("--files", nargs="*")
    args = parser.parse_args()

    source_root = Path(args.source_root)
    file_paths = [Path(p) for p in args.files] if args.files else None
    parsed = parse_contents(source_root, file_paths=file_paths)
    parsed_path = Path(args.parsed_out)
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    parsed_path.write_text(json.dumps({
        "volume_id": args.volume_id,
        "source_root": str(source_root),
        "entries": parsed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    helper_request = build_helper_request(args.volume_id, source_root, parsed)
    helper_path = Path(args.helper_request_out)
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

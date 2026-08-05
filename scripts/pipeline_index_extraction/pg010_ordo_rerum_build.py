#!/usr/bin/env python3
"""Usage: build the PG010 closing ORDO RERUM payload from OCR tail pages.

Run from the repository root:
  python scripts/pipeline_index_extraction/pg010_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG010/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG010_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG010 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG010_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PG010"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus X"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_START_FILE = "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-823.txt"
SECTION_END_FILE = "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-828.txt"
SECTION_START_PAGE = 1611
SECTION_END_PAGE = 1620

PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*\.?$")
PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
WS_RE = re.compile(r"\s+")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        return 0
    return int(match.group(1))


def parse_page_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        block_type = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if block_type not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        if not body:
            continue
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if line and line != "Digitized by Google":
                lines.append(line)
    return lines


def extract_tail_lines(source_root: Path) -> list[tuple[str, str]]:
    files = sorted(source_root.glob("*.txt"), key=page_seq)
    seen_tail = False
    out: list[tuple[str, str]] = []
    for path in files:
        lines = parse_page_lines(path)
        if not seen_tail:
            if any("ORDO RERUM" in line for line in lines):
                seen_tail = True
            else:
                continue
        if seen_tail:
            for line in lines:
                out.append((path.as_posix(), line))
            if any(line == "FINIS TOMI DECIMI." for line in lines):
                break
    return out


def split_compound_line(line: str) -> list[str]:
    if " — § " not in line:
        return [line]
    parts = re.split(r"\s—\s(?=§\s)", line)
    return [normalize(part) for part in parts if normalize(part)]


def parse_entry(segment: str) -> tuple[str, int | None, str | None]:
    value = normalize(segment)
    match = PAGE_RE.search(value)
    if not match:
        return value, None, None
    page_raw = match.group(1)
    lemma = normalize(value[: match.start()]).rstrip(" ,;:.—-")
    return lemma, int(page_raw), page_raw


def make_entry_kind(lemma_raw: str, page_int: int | None) -> str:
    if page_int is None:
        return "heading_group"
    if lemma_raw.startswith("§ "):
        return "sublemma"
    if lemma_raw.startswith("CAP.") or lemma_raw.startswith("CAPUT "):
        return "heading_group"
    if lemma_raw.startswith("PARS ") or lemma_raw.startswith("OPERUM ") or lemma_raw.startswith("APPENDIX") or lemma_raw.startswith("DE DOCTRINA"):
        return "heading_group"
    return "lemma"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG010 ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    helper_output = read_json(args.helper_output_json, {})
    tail_lines = extract_tail_lines(args.source_root)

    section_raw_json = {
        "helper_status": helper_output.get("entries", [{}])[0].get("status") if helper_output.get("entries") else None,
        "helper_entry_id": helper_output.get("entries", [{}])[0].get("entry_id") if helper_output.get("entries") else None,
        "helper_best_candidate": helper_output.get("entries", [{}])[0].get("best_candidate"),
        "section_kind_reason": (
            "Closing ORDO RERUM / QUÆ IN HOC TOMO CONTINENTUR block at the end of the volume; "
            "editorial closure rather than alphabetical index proper."
        ),
        "source_files_considered": sorted({path for path, _ in tail_lines}),
    }

    nodes = [
        {
            "node_key": f"{SECTION_KEY}:node:001",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": SECTION_HEADING_RAW,
            "label_norm": SECTION_HEADING_NORM,
            "label_sort": SECTION_HEADING_NORM,
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {
                "helper_best_candidate": helper_output.get("entries", [{}])[0].get("best_candidate") if helper_output.get("entries") else None
            },
        }
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    seen_entries: set[tuple[str, str, int | None]] = set()
    entry_order = 0

    for source_file, raw_line in tail_lines:
        if raw_line in {
            "ORDO RERUM",
            "QUÆ IN HOC TOMO CONTINENTUR.",
            "1611 ORDO RERUM 1612",
            "1613 QUÆ IN HOC TOMO CONTINENTUR. 1614",
            "1615 ORDO RERUM 1616",
            "1619 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 1620",
        }:
            continue
        if raw_line == "FINIS TOMI DECIMI.":
            entry_order += 1
            entries.append(
                {
                    "entry_key": f"{VOLUME_ID}:entry:{entry_order:06d}",
                    "section_key": SECTION_KEY,
                    "parent_node_key": f"{SECTION_KEY}:node:001",
                    "entry_order": entry_order,
                    "entry_kind": "heading_group",
                    "lemma_raw": raw_line,
                    "lemma_display": raw_line,
                    "lemma_norm": "finis tomi decimi",
                    "lemma_sort": "finis tomi decimi",
                    "entry_raw": raw_line,
                    "context_raw": raw_line,
                    "heading_letter": None,
                    "inferred_printed_page": None,
                    "section_start_file": SECTION_START_FILE,
                    "editorial_anchor_file": source_file,
                    "target_file_best": None,
                    "confidence": 0.96,
                    "raw_json": {
                        "source_file": source_file,
                        "note": "Volume closure marker."
                    },
                }
            )
            continue

        for segment in split_compound_line(raw_line):
            lemma_raw, page_int, page_raw = parse_entry(segment)
            if not lemma_raw:
                continue
            key = (source_file, lemma_raw, page_int)
            if key in seen_entries:
                continue
            seen_entries.add(key)
            entry_order += 1
            entry_kind = make_entry_kind(lemma_raw, page_int)
            inferred_printed_page = page_int
            entries.append(
                {
                    "entry_key": f"{VOLUME_ID}:entry:{entry_order:06d}",
                    "section_key": SECTION_KEY,
                    "parent_node_key": f"{SECTION_KEY}:node:001",
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": segment,
                    "context_raw": segment,
                    "heading_letter": None,
                    "inferred_printed_page": inferred_printed_page,
                    "section_start_file": SECTION_START_FILE,
                    "editorial_anchor_file": source_file,
                    "target_file_best": None,
                    "confidence": 0.9 if page_int is not None else 0.82,
                    "raw_json": {
                        "source_file": source_file,
                        "page_ref_raw": page_raw,
                        "section_kind": "ordo_rerum",
                    },
                }
            )
            if page_int is not None:
                refs.append(
                    {
                        "entry_key": f"{VOLUME_ID}:entry:{entry_order:06d}",
                        "ref_order": 1,
                        "ref_kind": "editorial_page",
                        "ref_raw": page_raw,
                        "page_ref_raw": page_raw,
                        "page_ref_int": page_int,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": None,
                        "target_file_probability": None,
                        "section_start_file": SECTION_START_FILE,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.9,
                        "raw_json": {
                            "source_file": source_file,
                            "page_ref_source": "toc_line",
                        },
                    }
                )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Closing ORDO RERUM block extracted from the tail pages only.",
        },
        "sections": [
            {
                "section_key": SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": SECTION_HEADING_RAW,
                "heading_norm": SECTION_HEADING_NORM,
                "heading_letter": None,
                "page_start": SECTION_START_PAGE,
                "page_end": SECTION_END_PAGE,
                "file_start": SECTION_START_FILE,
                "file_end": SECTION_END_FILE,
                "confidence": 0.98,
                "raw_json": section_raw_json,
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "partial_recovery",
            "entries_status_reason": (
                "Recovered the closing ORDO RERUM line items and their editorial page references from the tail OCR, "
                "but left `target_file_best` unresolved because the closing index is editorial and the volume-level "
                "content anchors are not stable enough to resolve every line automatically."
            ),
            "evidence_files": [
                "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-823.txt",
                "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-824.txt",
                "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-826.txt",
                "/homessddata/Projects/pdfocr/teste/PG010/text/ce57ca85-8678-4d29-9965-a955f466d92c-828.txt",
            ],
        },
        "notes": [
            "The final block is a closing ORDO RERUM / ELENCHUS-style contents list, not an alphabetical lemma index.",
            "The helper resolved the opening tail page to file 824; file 828 also matches the same heading but with a later inferred page.",
            "OCR literals were preserved, including inherited dashes and irregular page-number order in the source.",
        ],
    }

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

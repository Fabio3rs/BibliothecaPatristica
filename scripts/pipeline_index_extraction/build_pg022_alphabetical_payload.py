#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg022_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG022/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG022_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG022_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG022 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG022_alphabetical_indices.json

Build the PG022 alphabetical payload from the OCR tail. The script keeps the
final INDEX ANALYTICUS and ORDO RERUM blocks separate, preserves OCR literals,
and only merges obvious split OCR lines.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG022"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus XXII"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG022/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG022_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG022_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG022_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG022"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_ANALYTIC = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "heading_norm": "index analyticus",
    "heading_letter": None,
    "page_start": None,
    "page_end": None,
    "file_start": str(DEFAULT_SOURCE_ROOT / "e6bcf7f0-84e7-455e-98e3-dab47110e1d7-652.txt"),
    "file_end": str(DEFAULT_SOURCE_ROOT / "e6bcf7f0-84e7-455e-98e3-dab47110e1d7-658.txt"),
    "confidence": 0.99,
    "raw_json": {"section_kind_reason": "Analytical subject index with alphabetic letter-group headings."},
}

SECTION_ORDO = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": None,
    "page_end": None,
    "file_start": str(DEFAULT_SOURCE_ROOT / "e6bcf7f0-84e7-455e-98e3-dab47110e1d7-659.txt"),
    "file_end": str(DEFAULT_SOURCE_ROOT / "e6bcf7f0-84e7-455e-98e3-dab47110e1d7-662.txt"),
    "confidence": 0.98,
    "raw_json": {"section_kind_reason": "Closing contents table distinct from the alphabetical index."},
}


LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_END_RE = re.compile(r"\b\d{1,4}(?:,\s*\d{1,4})*(?:\s*-\s*\d{1,4})?\.\s*$")
PAGE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SPLIT_RE = re.compile(r"(?<=\d\.)\s+(?=[A-ZÆŒIVXLCDM])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        block_type = (match.group(1) or "").strip().lower()
        if block_type != "texto_principal":
            continue
        content = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def page_numbers(text: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for match in PAGE_NUM_RE.finditer(text):
        page = int(match.group(1))
        if page not in seen:
            seen.add(page)
            values.append(page)
    return values


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    m = PAGE_NUM_RE.search(text)
    if m:
        text = text[: m.start()].rstrip(" ,;:.-")
    return text or None


def chunk_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            chunks.append(normalize(" ".join(buffer)) or "")
            buffer = []

    for line in lines:
        if LETTER_RE.fullmatch(line):
            flush()
            chunks.append(line)
            continue
        if line.startswith("INDEX ANALYTICUS") or line.startswith("ORDO RERUM"):
            flush()
            continue
        buffer.append(line)
        if PAGE_END_RE.search(line):
            flush()
    flush()
    return [chunk for chunk in chunks if chunk]


def split_entries(chunk: str) -> list[str]:
    return [part.strip() for part in SPLIT_RE.split(chunk) if part.strip()] or [chunk]


def helper_request_payload() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(DEFAULT_SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": f"{VOLUME_ID}:probe:001",
                "lemma_raw": "Christus cur impiis inevitabiles pœnas descripsit",
                "query_names": [
                    "Christus cur impiis inevitabiles pœnas descripsit",
                    "pœnas inevitabiles impiis descripsit",
                ],
                "page_hints": ["106", "107"],
                "page_hint_ints": [106, 107],
                "context_raw": "Pœnas inevitabiles cur Christus impiis descripsit, justis vero æternam a Deo vitam promitti docuerit, 106, 107.",
            },
            {
                "entry_id": f"{VOLUME_ID}:probe:002",
                "lemma_raw": "Legis novæ in Evangelio Christi sanctionem fore testimonio prophetico comprobatur",
                "query_names": [
                    "Legis novæ in Evangelio Christi sanctionem fore testimonio prophetico comprobatur",
                    "Legis novæ in Evangelio Christi sanctionem",
                ],
                "page_hints": ["443"],
                "page_hint_ints": [443],
                "context_raw": "Legis novæ in Evangelio Christi sanctionem fore testimonio prophetico comprobatur, 443.",
            },
            {
                "entry_id": f"{VOLUME_ID}:probe:003",
                "lemma_raw": "Unguento odoratissimo ungi præter pontifices, prophetas et reges Mosaicæ legis instituto, nemini fas erat",
                "query_names": [
                    "Unguento odoratissimo ungi præter pontifices, prophetas et reges Mosaicæ legis instituto, nemini fas erat",
                    "nemini fas erat",
                ],
                "page_hints": ["176"],
                "page_hint_ints": [176],
                "context_raw": "Unguento odoratissimo ungi præter pontifices, prophetas et reges Mosaicæ legis instituto, nemini fas erat, 176.",
            },
        ],
    }


def run_helper(request_path: Path, output_path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
            "--input",
            str(request_path),
            "--output",
            str(output_path),
            "--pretty",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"helper failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(output_path, {})


def parse_section_analytic(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = {file_seq(p): p for p in discover_files(source_root)}
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    current_letter = None

    for seq in range(652, 659):
        path = files[seq]
        lines = extract_lines(path)
        if seq == 652:
            start = 0
            for i, line in enumerate(lines):
                if LETTER_RE.fullmatch(line) and line == "A":
                    start = i
                    break
            lines = lines[start:]
        for chunk in chunk_lines(lines):
            if LETTER_RE.fullmatch(chunk):
                current_letter = chunk
                node_order += 1
                nodes.append(
                    {
                        "node_key": f"{VOLUME_ID}:node:analytic:{node_order:03d}",
                        "section_key": SECTION_ANALYTIC["section_key"],
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": chunk,
                        "label_norm": chunk.lower(),
                        "label_sort": chunk.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": str(path)},
                    }
                )
                continue
            for entry_raw in split_entries(chunk):
                pages = page_numbers(entry_raw)
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_ANALYTIC["section_key"],
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "lemma",
                    "lemma_raw": lemma_from_entry(entry_raw),
                    "lemma_display": lemma_from_entry(entry_raw),
                    "lemma_norm": sort_norm(lemma_from_entry(entry_raw)),
                    "lemma_sort": sort_norm(lemma_from_entry(entry_raw)),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": current_letter,
                    "inferred_printed_page": pages[0] if pages else None,
                    "section_start_file": SECTION_ANALYTIC["file_start"],
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.92 if len(pages) <= 2 else 0.84,
                    "raw_json": {
                        "source_file": str(path),
                        "pages": pages,
                    },
                }
                entries.append(entry)
                for ref_order, page in enumerate(pages, start=1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": str(page),
                            "page_ref_raw": str(page),
                            "page_ref_int": page,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": str(path),
                            "target_file_probability": 0.95,
                            "section_start_file": SECTION_ANALYTIC["file_start"],
                            "editorial_anchor_file": str(path),
                            "confidence": 0.9,
                            "raw_json": {"source_file": str(path)},
                        }
                    )
    return entries, refs, nodes


def parse_section_ordo(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files = {file_seq(p): p for p in discover_files(source_root)}
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0

    for seq in range(659, 663):
        path = files[seq]
        lines = extract_lines(path)
        if seq == 659:
            start = 0
            for i, line in enumerate(lines):
                if line.startswith("ORDO RERUM"):
                    start = i + 1
                    break
            lines = lines[start:]
        for line in lines:
            if line.startswith("Digitized") or line.startswith("FINIS TOMI") or line.startswith("Imprimerie"):
                continue
            if not PAGE_NUM_RE.search(line) and not line.upper().startswith(
                ("LIBER ", "CAP.", "PROŒMIUM", "FRAGMENTA", "CANONES", "EUSEBII", "DE ", "QUÆSTIONES", "SUPPLEMENTA", "ECLOGÆ")
            ):
                continue
            for entry_raw in split_entries(line):
                if normalize(entry_raw) in {"ca. . 1291", "ca."}:
                    continue
                pages = page_numbers(entry_raw)
                entry_order += 1
                entry_key = f"{VOLUME_ID}:ordo:{entry_order:06d}"
                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_ORDO["section_key"],
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group",
                    "lemma_raw": lemma_from_entry(entry_raw),
                    "lemma_display": lemma_from_entry(entry_raw),
                    "lemma_norm": sort_norm(lemma_from_entry(entry_raw)),
                    "lemma_sort": sort_norm(lemma_from_entry(entry_raw)),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": None,
                    "inferred_printed_page": pages[0] if pages else None,
                    "section_start_file": SECTION_ORDO["file_start"],
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.89,
                    "raw_json": {
                        "source_file": str(path),
                        "pages": pages,
                        "section_kind": "ordo_rerum",
                    },
                }
                entries.append(entry)
                for ref_order, page in enumerate(pages, start=1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": str(page),
                            "page_ref_raw": str(page),
                            "page_ref_int": page,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": str(path),
                            "target_file_probability": 0.95,
                            "section_start_file": SECTION_ORDO["file_start"],
                            "editorial_anchor_file": str(path),
                            "confidence": 0.88,
                            "raw_json": {"source_file": str(path)},
                        }
                    )
    return entries, refs


def build_payload(source_root: Path, helper_output: dict[str, Any]) -> dict[str, Any]:
    analytic_entries, analytic_refs, nodes = parse_section_analytic(source_root)
    ordo_entries, ordo_refs = parse_section_ordo(source_root)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "PG022 final OCR tail index and contents table.",
        },
        "sections": [SECTION_ANALYTIC, SECTION_ORDO],
        "nodes": nodes,
        "entries": analytic_entries + ordo_entries,
        "refs": analytic_refs + ordo_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the analytical subject index and the closing ORDO RERUM contents table from the OCR tail, preserving split OCR lines conservatively.",
            "evidence_files": [
                str(source_root / f"e6bcf7f0-84e7-455e-98e3-dab47110e1d7-{seq}.txt")
                for seq in range(652, 663)
            ],
        },
        "notes": [
            {
                "note_type": "sectioning",
                "text": "INDEX ANALYTICUS is serialized as the main alphabetical analytical section; ORDO RERUM is serialized separately as the closing contents table.",
            },
            {
                "note_type": "helper",
                "text": f"Helper probe output entries: {len(helper_output.get('entries', []) or [])}.",
            },
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    helper_request = helper_request_payload()
    write_json(args.helper_request_json, helper_request)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve the PG022 OCR tail and write the final alphabetical payload.",
            "completed": [
                "sections identified",
                "helper probes prepared",
            ],
            "pending": [
                "run helper",
                "build final payload",
                "write output file",
            ],
            "blocked": [],
            "notes": ["Use only files within the PG022 source_root."],
        },
    )
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(args.source_root, helper_output)
    write_json(args.output_file, payload)
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "source_root": str(args.source_root),
            "helper_request_json": str(args.helper_request_json),
            "helper_output_json": str(args.helper_output_json),
            "output_file": str(args.output_file),
        },
    )


if __name__ == "__main__":
    main()

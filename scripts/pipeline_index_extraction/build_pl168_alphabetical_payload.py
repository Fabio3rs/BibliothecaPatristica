#!/usr/bin/env python3
"""Usage: build the PL168 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl168_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL168/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL168_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL168_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL168 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL168_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL168"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 168"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL168/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL168_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL168_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL168_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL168"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_1_FILES = list(range(810, 825))
SECTION_2_FILES = [826]

SECTION_1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

SECTION_1_HEADING_RAW = "INDEX RERUM ET VERBORUM."
SECTION_2_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

NOISE_LINE_RE = re.compile(
    r"^(?:Digitized by Google|PATROL\.\s*CLXVIII\.?|INDEX RERUM ET VERBORUM\.?|INDEX RERUM\.?|ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR\.?|[A-Z])$",
    re.IGNORECASE,
)
REVOCATUR_RE = re.compile(r"^Revocatur lector\b", re.IGNORECASE)
SINGLE_LETTER_RE = re.compile(r"^[A-Z]$")
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
REF_SPLIT_RE = re.compile(r"(?<=\d\.)\s+(?=[A-ZÆŒ])")
REF_SPLIT_RE_2 = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_lines(path: Path, *, keep_noise: bool = False) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if not keep_noise and NOISE_LINE_RE.fullmatch(line):
            continue
        if not keep_noise and SINGLE_LETTER_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in PAGE_TOKEN_RE.finditer(header):
            token = match.group(1)
            if token.startswith("0"):
                continue
            page = int(token)
            page_map.setdefault(page, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def unique_preserve_order(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = normalize(value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def split_chunks(text: str) -> list[str]:
    value = normalize(text) or ""
    if not value:
        return []
    parts = [value]
    for splitter in (REF_SPLIT_RE, REF_SPLIT_RE_2):
        new_parts: list[str] = []
        for part in parts:
            new_parts.extend([frag.strip() for frag in splitter.split(part) if frag.strip()])
        parts = new_parts
    return parts


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        # Ignore thousand separators like "96,000".
        tail = text[match.end() : match.end() + 5]
        if tail.startswith(",") and re.match(r",\d{3}\b", tail):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_chunk(chunk: str) -> str | None:
    text = normalize(chunk) or ""
    if not text:
        return None
    first_ref = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
    if first_ref:
        head = text[: first_ref.start()].rstrip(" ,;:.")
    else:
        head = text
    if "." in head:
        prefix = head.split(".", 1)[0].strip(" ,;:.")
        if prefix and len(prefix) <= 40 and not re.search(r",", prefix):
            return prefix
    head = head.strip(" ,;:.")
    return head or None


def entry_kind(section_kind: str, chunk: str, has_refs: bool) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    text = chunk.strip()
    if not has_refs and re.search(r"\b(?:Vide|vid\.?|V\.|cf\.?|voir|id\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    # Small fallback search when a header OCR page number is missing.
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_text_files(source_root):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        if needle.search(parsed.get("header_text") or ""):
            return str(path)
    return None


def parse_volume(files: list[Path], page_map: dict[int, str], source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections = [
        {
            "section_key": SECTION_1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_1_HEADING_RAW,
            "heading_norm": sort_norm(SECTION_1_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1635,
            "page_end": 1664,
            "file_start": str(next(path for path in files if file_seq(path) == SECTION_1_FILES[0])),
            "file_end": str(next(path for path in files if file_seq(path) == SECTION_1_FILES[-1])),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Subject index headed INDEX RERUM ET VERBORUM; alphabetical subject entries with multiple page locators.",
                "evidence_files": [
                    str(next(path for path in files if file_seq(path) == SECTION_1_FILES[0])),
                    str(next(path for path in files if file_seq(path) == SECTION_1_FILES[-1])),
                ],
            },
        },
        {
            "section_key": SECTION_2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_2_HEADING_RAW,
            "heading_norm": sort_norm(SECTION_2_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1667,
            "page_end": 1668,
            "file_start": str(next(path for path in files if file_seq(path) == 826)),
            "file_end": str(next(path for path in files if file_seq(path) == 826)),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table at the end of the volume.",
                "evidence_files": [str(next(path for path in files if file_seq(path) == 826))],
            },
        },
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    helper_seed: list[dict[str, Any]] = []

    def emit_entry(section_key: str, section_kind: str, source_file: str, raw_text: str) -> None:
        nonlocal entry_order
        text = normalize(raw_text) or ""
        if not text:
            return
        if text in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
            return
        page_hints = extract_page_hints(text)
        has_refs = bool(page_hints)
        kind = entry_kind(section_kind, text, has_refs)
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        lemma_raw = lemma_from_chunk(text) if kind != "heading_group" else normalize(text)
        target_file_best = target_for_page(page_hints[0], page_map, source_root) if page_hints else source_file
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": sections[0]["file_start"] if section_key == SECTION_1_KEY else sections[1]["file_start"],
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.92 if has_refs else 0.7,
            "raw_json": {
                "source_file": source_file,
                "page_hints": page_hints,
                "section_kind": section_kind,
            },
        }
        entries.append(entry)

        if has_refs:
            helper_seed.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or text,
                    "query_names": unique_preserve_order(
                        [
                            lemma_raw,
                            text,
                            (lemma_raw or text).split(",", 1)[0],
                        ]
                    ),
                    "page_hints": [str(page) for page in page_hints[:3]],
                    "page_hint_ints": page_hints[:3],
                    "context_raw": text,
                }
            )

        for ref_order, page in enumerate(page_hints, start=1):
            target_file = target_for_page(page, page_map, source_root)
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
                    "target_file": target_file,
                    "target_file_probability": 0.99 if target_file else None,
                    "section_start_file": sections[0]["file_start"] if section_key == SECTION_1_KEY else sections[1]["file_start"],
                    "editorial_anchor_file": source_file,
                    "confidence": 0.95 if target_file else 0.72,
                    "raw_json": {
                        "source_file": source_file,
                        "locator_method": "header_page_map" if target_file else "unresolved",
                    },
                }
            )

    # Section 1: subject index tail.
    for path in files:
        seq = file_seq(path)
        if seq not in SECTION_1_FILES:
            continue
        # Page 825 is a false-positive continuation from the running text, not index material.
        if seq == 825:
            continue
        joined = []
        page_lines = extract_lines(path, keep_noise=True)
        if seq == 810 and len(page_lines) >= 2:
            page_lines = page_lines[2:]
        for line in page_lines:
            if NOISE_LINE_RE.fullmatch(line) or SINGLE_LETTER_RE.fullmatch(line):
                continue
            if REVOCATUR_RE.search(line):
                continue
            joined.append(line)
        page_text = re.sub(r"(?<=\w)-\s+", "", " ".join(joined))
        page_text = re.sub(r"\s+", " ", page_text).strip()
        if not page_text:
            continue
        for chunk in split_chunks(page_text):
            chunk = normalize(chunk) or ""
            if not chunk or NOISE_LINE_RE.fullmatch(chunk):
                continue
            # Split the remaining chunk once more on strong citation boundaries.
            sub_chunks = [chunk]
            for splitter in (REF_SPLIT_RE, REF_SPLIT_RE_2):
                new_sub_chunks: list[str] = []
                for sub in sub_chunks:
                    new_sub_chunks.extend([frag.strip() for frag in splitter.split(sub) if frag.strip()])
                sub_chunks = new_sub_chunks
            for sub in sub_chunks:
                emit_entry(SECTION_1_KEY, "analytic_subject", str(path), sub)

    # Section 2: closing contents table.
    for path in files:
        seq = file_seq(path)
        if seq not in SECTION_2_FILES:
            continue
        joined = []
        page_lines = extract_lines(path, keep_noise=True)
        for line in page_lines:
            if NOISE_LINE_RE.fullmatch(line) or SINGLE_LETTER_RE.fullmatch(line):
                continue
            joined.append(line)
        page_text = re.sub(r"(?<=\w)-\s+", "", " ".join(joined))
        page_text = re.sub(r"\s+", " ", page_text).strip()
        for chunk in split_chunks(page_text):
            chunk = normalize(chunk) or ""
            if not chunk or NOISE_LINE_RE.fullmatch(chunk):
                continue
            emit_entry(SECTION_2_KEY, "ordo_rerum", str(path), chunk)

    return sections, entries, refs, helper_seed


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": helper.get("best_candidate"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in helper.get("candidates", [])[:5]
            ],
        }
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best["file"]
            raw_json["helper_best_file"] = best["file"]


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_text_files(source_root)
    page_map = build_page_map(files)
    sections, entries, refs, helper_seed = parse_volume(files, page_map, source_root)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 3, "adjacency_window": 2},
        "entries": helper_seed[:6],
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}
    attach_helper(entries, helper_output)

    # Recompute refs target_file_best for any helper-overridden entry.
    entry_map = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        entry = entry_map.get(ref["entry_key"])
        if not entry:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        if helper.get("best_candidate", {}).get("file"):
            ref["target_file"] = helper["best_candidate"]["file"]
            ref["target_file_probability"] = helper["best_candidate"].get("probability")

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the tail analytical subject index headed INDEX RERUM ET VERBORUM and the closing ORDO RERUM contents table from the OCR tail.",
        "evidence_files": [
            str(next(path for path in files if file_seq(path) == SECTION_1_FILES[0])),
            str(next(path for path in files if file_seq(path) == SECTION_1_FILES[-1])),
            str(next(path for path in files if file_seq(path) == 826)),
        ],
    }
    notes = [
        "Section 1 is the subject index; section 2 is the closing ORDO RERUM table of contents.",
        "File 825 is a false-positive running-text page with an index header in the OCR and was not serialized as index material.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PL168 alphabetical payload and keep OCR file/page numbering separate.",
            "completed": [
                "identified the subject index tail",
                "identified the closing ORDO RERUM table",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep page refs literal and resolve target files against OCR headers only.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL168 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

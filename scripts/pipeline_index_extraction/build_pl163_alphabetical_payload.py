#!/usr/bin/env python3
"""Usage: build the PL163 closing ORDO RERUM payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl163_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL163/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL163_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL163_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL163 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL163_alphabetical_indices.json
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


VOLUME_ID = "PL163"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 163"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"

SECTION_HEADING_RAW = "ORDO· RERUM QVÆ IN HOC TOMO CONTINENTUR·"
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"

SECTION_FILE_START = 744
SECTION_FILE_END = 760
SECTION_PAGE_START = 1185
SECTION_PAGE_END = 1512

SECTION_HEADING_RE = re.compile(
    r"^ORDO[·.]?\s+RERUM(?:\s+QV[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR[·.]?)?$",
    re.IGNORECASE,
)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*\[lb/\])?\s*$")
ROMAN_START_RE = re.compile(r"^(?:[IVXLCDM]+(?:\s+(?:bis|ter|quater))?\.?)(?:\s+|$)", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
DIGITIZED_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
FINIS_RE = re.compile(r"^FINIS TOMI CENTESIMI SEXAGESIMI TERTII\.$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def sort_key(text: str | None) -> str | None:
    value = normalize_ws(text)
    return value.lower() if value else None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def clean_line(raw: str) -> str:
    line = raw.strip()
    if not line or line.startswith("<") or line.startswith("</"):
        return ""
    line = line.replace("[lb/]", " ")
    line = normalize_ws(line)
    if FINIS_RE.match(line):
        return ""
    return line


def is_section_heading(line: str) -> bool:
    return bool(SECTION_HEADING_RE.match(line))


def has_page_ref(line: str) -> bool:
    return bool(PAGE_RE.search(line))


def is_heading_like(line: str) -> bool:
    if not line or has_page_ref(line):
        return False
    if is_section_heading(line) or DIGITIZED_RE.match(line):
        return False
    if "—" in line:
        return False
    if ROMAN_START_RE.match(line):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
    if line.endswith(".") and (upper_ratio >= 0.7 or (line[0].isupper() and len(line.split()) <= 8)):
        return True
    if line.endswith(":") and upper_ratio >= 0.7:
        return True
    return False


def strip_page_ref(text: str) -> tuple[str, int | None]:
    match = PAGE_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(" .;:"), int(match.group(1))


def make_query_names(lemma_raw: str) -> list[str]:
    text = normalize_ws(lemma_raw)
    candidates: list[str] = []
    if text:
        candidates.append(text)
    if " — " in text:
        candidates.append(normalize_ws(text.split(" — ", 1)[1]))
    if ". — " in text:
        candidates.append(normalize_ws(text.split(". — ", 1)[0] + "."))
    if "," in text:
        candidates.append(normalize_ws(text.split(",", 1)[0]))
    if "  " in text:
        candidates.append(normalize_ws(text.replace("  ", " ")))
    unique: list[str] = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique[:4]


def build_segments(source_root: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    files = [path for path in discover_files(source_root) if SECTION_FILE_START <= file_seq(path) <= SECTION_FILE_END]
    entries: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = [str(path) for path in files]

    started = False
    pending_heading: list[str] = []
    current_lines: list[str] = []
    current_file: Path | None = None
    entry_order = 0

    def emit_entry(lines: list[str], source_file: Path) -> None:
        nonlocal entry_order
        raw = normalize_ws(" ".join(lines))
        if not raw:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        lemma_raw, page_ref = strip_page_ref(raw)
        is_cross_reference = page_ref is None and bool(re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.)\b", raw, re.IGNORECASE))
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": "cross_reference" if is_cross_reference else "heading_group",
            "lemma_raw": None if is_cross_reference else (lemma_raw or raw),
            "lemma_display": None if is_cross_reference else (lemma_raw or raw),
            "lemma_norm": None if is_cross_reference else (normalize_ws(lemma_raw or raw).lower() if (lemma_raw or raw) else None),
            "lemma_sort": None if is_cross_reference else sort_key(lemma_raw or raw),
            "entry_raw": raw,
            "context_raw": raw,
            "heading_letter": None,
            "inferred_printed_page": page_ref,
            "section_start_file": str(files[0]) if files else str(source_file),
            "editorial_anchor_file": str(source_file),
            "target_file_best": str(source_file) if page_ref is None else None,
            "confidence": 0.82 if page_ref is not None else 0.74,
            "raw_json": {
                "source_file": str(source_file),
                "section_kind": "ordo_rerum",
                "entry_kind_reason": "bare cross-reference without a material locator" if is_cross_reference else "page-less editorial heading inside the closing ORDO RERUM table",
            },
        }
        if page_ref is not None:
            helper_entries.append(
                {
                    "entry_id": f"{VOLUME_ID.lower()}_{entry_order:04d}",
                    "lemma_raw": lemma_raw or raw,
                    "query_names": make_query_names(lemma_raw or raw),
                    "page_hints": [str(page_ref)],
                    "page_hint_ints": [page_ref],
                    "context_raw": raw,
                }
            )
        entries.append(entry)

    for path in files:
        for raw_line in read_text(path).splitlines():
            line = clean_line(raw_line)
            if not line or DIGITIZED_RE.match(line):
                continue
            if not started:
                if is_section_heading(line):
                    started = True
                continue
            if is_section_heading(line):
                continue
            if not entries and not current_lines and not pending_heading and line.upper() == "QUE IN HOC TOMO CONTINENTUR.":
                continue

            if is_heading_like(line):
                if current_lines:
                    emit_entry(current_lines, current_file or path)
                    current_lines = []
                    current_file = None
                pending_heading.append(line)
                continue

            if pending_heading:
                emit_entry(pending_heading, path)
                pending_heading = []

            if not current_lines:
                current_file = path
            current_lines.append(line)
            if has_page_ref(line):
                emit_entry(current_lines, current_file or path)
                current_lines = []
                current_file = None

    if pending_heading:
        emit_entry(pending_heading, files[-1] if files else source_root)
    if current_lines:
        emit_entry(current_lines, current_file or (files[-1] if files else source_root))

    return entries, helper_entries, evidence_files


def build_helper_request(source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json) or {}


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {entry.get("entry_id"): entry for entry in helper_output.get("entries", []) if entry.get("entry_id")}


def compact_helper_entry(helper_item: dict[str, Any]) -> dict[str, Any]:
    top_candidates: list[dict[str, Any]] = []
    for candidate in helper_item.get("top_candidates", [])[:5]:
        top_candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "evidence_kinds": candidate.get("evidence_kinds"),
            }
        )
    best = helper_item.get("best_candidate") or {}
    return {
        "status": helper_item.get("status"),
        "candidate_role": helper_item.get("candidate_role"),
        "reason_summary": helper_item.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "reason_summary": best.get("reason_summary"),
        },
        "top_candidates": top_candidates,
    }


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    entries, helper_entries, evidence_files = build_segments(source_root)
    section_files = [path for path in discover_files(source_root) if SECTION_FILE_START <= file_seq(path) <= SECTION_FILE_END]
    helper_request = build_helper_request(source_root, helper_entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_map = helper_index(helper_output)

    refs: list[dict[str, Any]] = []
    for entry in entries:
        if entry["inferred_printed_page"] is None:
            continue
        helper_id = f"{VOLUME_ID.lower()}_{entry['entry_order']:04d}"
        helper_item = helper_map.get(helper_id) or {}
        compact = compact_helper_entry(helper_item) if helper_item else {}
        best = helper_item.get("best_candidate") or {}
        target_file = best.get("file") or entry["editorial_anchor_file"]
        target_prob = best.get("probability")
        entry["target_file_best"] = target_file
        entry["confidence"] = target_prob if isinstance(target_prob, (int, float)) else entry["confidence"]
        entry["raw_json"]["helper"] = compact
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(entry["inferred_printed_page"]),
                "page_ref_raw": str(entry["inferred_printed_page"]),
                "page_ref_int": entry["inferred_printed_page"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": target_prob,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": entry["confidence"],
                "raw_json": {
                    "helper": compact,
                    "source_file": entry["editorial_anchor_file"],
                    "section_kind": "ordo_rerum",
                },
            }
        )

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": SECTION_PAGE_START,
        "page_end": SECTION_PAGE_END,
        "file_start": str(section_files[0]) if section_files else str(source_root),
        "file_end": str(section_files[-1]) if section_files else str(source_root),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "final_volume_ordo_rerum_closure",
            "evidence_files": evidence_files,
            "page_start_reason": "The first OCR spread in the section lacks a visible printed page number; the section is anchored conservatively from the first contents spread and the next visible header sequence.",
            "page_end_reason": "The closing spread shows the final visible printed page header in the section sequence before the OCR tail turns into non-textual back matter.",
            "helper_status": helper_output.get("status"),
        },
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Closing ORDO RERUM contents table with OCR page-header drift and several wrapped lines.",
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from files 744-760, including page-less editorial headings and page-bearing contents lines. The helper was used to anchor the cited pages to target OCR files.",
        "evidence_files": evidence_files,
    }

    notes = [
        "This volume ends with a closing ORDO RERUM table rather than a lexical alphabetical index.",
        "The OCR tail contains page-header drift and a few entries split across wrapped lines; OCR literals were preserved.",
        "Page-less editorial headings inside the contents table were kept as `heading_group` entries.",
    ]

    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "source_root": str(source_root),
        "helper_request_json": str(helper_request_json),
        "helper_output_json": str(helper_output_json),
        "output_file": str(output_file),
    }

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Validate the PL163 ORDO RERUM payload and preserve OCR literals.",
        "completed": [
            "closing ORDO RERUM block identified",
            "helper request built from page-bearing contents lines",
            "helper executed for cited page anchoring",
            "final payload assembled",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Keep OCR file suffixes separate from editorial page numbers.",
            "Do not normalize ambiguous page headers away.",
        ],
    }

    fragments = {
        "volume.json": volume,
        "sections.json": [section],
        "nodes.json": [],
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": [],
        "coverage.json": coverage,
        "notes.json": notes,
        "manifest.json": manifest,
        "todo.json": todo,
    }
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": [section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL163 closing ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    build_payload(
        args.source_root,
        args.helper_request_json,
        args.helper_output_json,
        args.intermediate_dir,
        args.output_file,
    )


if __name__ == "__main__":
    main()

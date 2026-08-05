#!/usr/bin/env python3
"""Usage: build the PL107 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl107_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL107/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL107_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL107_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL107 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL107_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL107"
COLLECTION = "PL"
VOLUME_LABEL = "PL107"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "Ordo rerum quae in hoc tomo continentur."

BLOCK_NOISE_RE = re.compile(r"^(?:Digitized by Google|THIS VOLUME DOES NOT CIRCULATE OUTSIDE THE LIBRARY\.)$", re.IGNORECASE)
HEADER_ONLY_RE = re.compile(r"^\d{1,4}$")
SECTION_HEADER_RE = re.compile(r"^ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?$", re.IGNORECASE)
SECTION_START_RE = re.compile(r"^(?:ORDO RERUM\s+)?QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.$", re.IGNORECASE)
CONTINUATION_STOP_RE = re.compile(r"^(?:Pr[æa]fatio|Pro[œo]mium|LIBER PRIMUS\.)", re.IGNORECASE)
PAGE_REF_RE = re.compile(
    r"(?<!\d)(?:(?P<col>col\.?|col)\s*)?(?P<start>\d{1,4})(?:\s*(?:[-–—]|à)\s*(?P<end>\d{1,4}))?(?=[\s\.,;:\)\]]|$)",
    re.IGNORECASE,
)
HEADING_LIKE_RE = re.compile(r"^[A-ZÆŒ0-9 .,'’()\-/:;]+$")
UPPERISH_RE = re.compile(r"^[A-ZÆŒ][A-ZÆŒ0-9 .,'’()\-/:;]+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def norm_sort(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = normalize(raw)
        if not text:
            continue
        if HEADER_ONLY_RE.fullmatch(text):
            continue
        if BLOCK_NOISE_RE.fullmatch(text):
            continue
        if text in {"A", "B", "C", "D"}:
            continue
        lines.append(text)
    return lines


def extract_header_numbers(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = parsed.get("header_text") or ""
    numbers: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        value = int(match.group(1))
        if value >= 1:
            numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for number in extract_header_numbers(path)[:3]:
            page_map.setdefault(number, str(path))
    return page_map


def lookup_target(page_ref: int | None, page_map: dict[int, str]) -> tuple[str | None, str]:
    if page_ref is None:
        return None, "none"
    if page_ref in page_map:
        return page_map[page_ref], "exact"
    for delta in (1, -1, 2, -2, 3, -3):
        candidate = page_ref + delta
        if candidate in page_map:
            return page_map[candidate], f"fuzzy_{delta:+d}"
    return None, "missing"


def looks_like_heading(text: str) -> bool:
    value = normalize(text)
    if not value:
        return False
    return bool(HEADING_LIKE_RE.fullmatch(value)) and len(value) <= 120


def strip_page_ref(text: str) -> tuple[str, int | None, str | None, str | None]:
    value = normalize(text)
    match = None
    for match in PAGE_REF_RE.finditer(value):
        pass
    if match is None:
        return value, None, None, None
    start = int(match.group("start"))
    end = match.group("end")
    col = match.group("col")
    ref_raw = match.group(0).strip().rstrip(".,;:")
    lemma = normalize(value[: match.start()]).rstrip(" ,;:.")
    if end is not None:
        ref_raw = f"{ref_raw}"
    page_ref_col = normalize(col) if col else None
    return lemma, start, ref_raw, page_ref_col


def simplify_query_name(text: str) -> str:
    value = normalize(text)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" ,;:.")
    return value


def parse_section_entries(
    files: list[Path], page_map: dict[int, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], str, str]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    section_start_path = str(files[0])
    section_end_path = str(files[-1])

    entry_order = 0
    preamble_lines: list[str] = []
    current_parts: list[str] = []
    seen_first_ref = False
    in_section = False
    awaiting_toc_start = False

    def emit_entry(parts: list[str], source_file: Path) -> None:
        nonlocal entry_order
        if not parts:
            return
        entry_raw = normalize(" ".join(parts))
        if not entry_raw:
            return
        lemma_raw, page_ref_int, ref_raw, page_ref_col = strip_page_ref(entry_raw)
        entry_kind = "heading_group"
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        target_file_best, target_source = lookup_target(page_ref_int, page_map)
        if target_file_best is None:
            target_file_best = str(source_file)
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw or None,
            "lemma_display": lemma_raw or None,
            "lemma_norm": norm_sort(lemma_raw),
            "lemma_sort": norm_sort(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": None,
            "inferred_printed_page": page_ref_int,
            "section_start_file": section_start_path,
            "editorial_anchor_file": str(source_file),
            "target_file_best": target_file_best,
            "confidence": 0.97 if page_ref_int is not None else 0.88,
            "raw_json": {
                "source_file": str(source_file),
                "section_kind": "ordo_rerum",
                "target_resolution": target_source,
                "page_ref_raw": ref_raw,
                "page_ref_col": page_ref_col,
            },
        }
        entries.append(entry)
        if page_ref_int is not None:
            ref_order = 1
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw or str(page_ref_int),
                    "page_ref_raw": ref_raw or str(page_ref_int),
                    "page_ref_int": page_ref_int,
                    "page_ref_col": page_ref_col,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file_best,
                    "target_file_probability": 1.0 if target_source == "exact" else 0.8,
                    "section_start_file": section_start_path,
                    "editorial_anchor_file": str(source_file),
                    "confidence": 0.97 if target_source == "exact" else 0.85,
                    "raw_json": {
                        "source_file": str(source_file),
                        "section_kind": "ordo_rerum",
                        "target_resolution": target_source,
                    },
                }
            )
        helper_entries.append(
            {
                "entry_id": entry_key.replace(":entry:", "_"),
                "lemma_raw": lemma_raw or entry_raw,
                "query_names": [simplify_query_name(lemma_raw or entry_raw)],
                "page_hints": [str(page_ref_int)] if page_ref_int is not None else [],
                "page_hint_ints": [page_ref_int] if page_ref_int is not None else [],
                "context_raw": entry_raw,
            }
        )

    for path in files:
        lines = clean_lines(path)
        for line in lines:
            if not in_section:
                if SECTION_START_RE.fullmatch(line):
                    in_section = True
                    awaiting_toc_start = True
                continue
            if awaiting_toc_start:
                if not (looks_like_heading(line) or PAGE_REF_RE.search(line)):
                    continue
                awaiting_toc_start = False
            if CONTINUATION_STOP_RE.match(line):
                if current_parts:
                    emit_entry(current_parts, path)
                    current_parts = []
                evidence_files = list(dict.fromkeys(evidence_files))
                return entries, refs, helper_entries, evidence_files, section_start_path, section_end_path
            if SECTION_HEADER_RE.fullmatch(line):
                continue
            if HEADER_ONLY_RE.fullmatch(line):
                if current_parts:
                    current_parts.append(line)
                    emit_entry(current_parts, path)
                    current_parts = []
                continue
            evidence_files.append(str(path))
            has_ref = bool(PAGE_REF_RE.search(line))
            if not seen_first_ref:
                if has_ref:
                    if preamble_lines:
                        emit_entry(preamble_lines, path)
                        preamble_lines = []
                    current_parts = [line]
                    seen_first_ref = True
                    if has_ref:
                        emit_entry(current_parts, path)
                        current_parts = []
                    continue
                preamble_lines.append(line)
                continue

            if has_ref:
                if current_parts:
                    current_text = normalize(" ".join(current_parts))
                    if looks_like_heading(current_text):
                        emit_entry(current_parts, path)
                        current_parts = [line]
                    else:
                        current_parts.append(line)
                else:
                    current_parts = [line]
                emit_entry(current_parts, path)
                current_parts = []
            else:
                if current_parts:
                    current_parts.append(line)
                else:
                    current_parts = [line]

    if preamble_lines:
        # The opening heading block has no page reference, but it is useful as
        # a structural breadcrumb for the volume.
        entry_order = len(entries) + 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entry_raw = normalize(" ".join(preamble_lines))
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": entry_raw or None,
                "lemma_display": entry_raw or None,
                "lemma_norm": norm_sort(entry_raw),
                "lemma_sort": norm_sort(entry_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": section_start_path,
                "editorial_anchor_file": files[0].as_posix(),
                "target_file_best": files[0].as_posix(),
                "confidence": 0.86,
                "raw_json": {
                    "source_file": files[0].as_posix(),
                    "section_kind": "ordo_rerum",
                    "note": "Opening heading block without explicit page reference.",
                },
            }
        )
    evidence_files = list(dict.fromkeys(evidence_files))
    return entries, refs, helper_entries, evidence_files, section_start_path, section_end_path


def build_helper_request(volume_id: str, source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    helper_output_json.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, default={})


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in helper_output.get("entries") or []:
        entry_id = entry.get("entry_id")
        if not entry_id:
            continue
        result[entry_id] = entry
    return result


def assemble_payload(
    source_root: Path,
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    helper_output: dict[str, Any],
    evidence_files: list[str],
    section_start_path: str,
    section_end_path: str,
) -> dict[str, Any]:
    helper_by_id = helper_map(helper_output)
    for entry in entries:
        helper_entry = helper_by_id.get(entry["entry_key"].replace(":entry:", "_"))
        if helper_entry:
            entry["raw_json"]["helper"] = {
                "status": helper_entry.get("status"),
                "candidate_role": (helper_entry.get("best_candidate") or {}).get("candidate_role"),
                "reason_summary": (helper_entry.get("best_candidate") or {}).get("reason_summary"),
                "best_candidate": helper_entry.get("best_candidate"),
                "top_candidates": [
                    {
                        "file": candidate.get("file"),
                        "probability": candidate.get("probability"),
                        "candidate_role": candidate.get("candidate_role"),
                        "reason_summary": candidate.get("reason_summary"),
                    }
                    for candidate in (helper_entry.get("candidates") or [])[:3]
                ],
            }
            best = helper_entry.get("best_candidate") or {}
            best_file = best.get("file")
            if best_file:
                entry["target_file_best"] = best_file
        if entry.get("target_file_best") == entry.get("editorial_anchor_file"):
            entry["confidence"] = min(float(entry.get("confidence") or 0.0), 0.9) if entry.get("inferred_printed_page") is None else entry.get("confidence")

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1155,
            "page_end": 1164,
            "file_start": section_start_path,
            "file_end": section_end_path,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing contents table printed as ORDO RERUM / QUAE IN HOC TOMO CONTINENTUR.",
                "source_files": [
                    section_start_path,
                    section_end_path,
                ],
                "evidence_files": evidence_files[:],
            },
        }
    ]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved the printed-page anchors conservatively against the local page map and helper output.",
            "evidence_files": evidence_files[:],
        },
        "notes": [
            "The volume's usable index material here is the final ORDO RERUM table of contents; earlier catalog-style front matter was inspected but not modeled as the target section.",
            "OCR file suffixes are kept distinct from printed page references; helper evidence is preserved in raw_json for each entry.",
        ],
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL107 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root
    if not source_root.exists():
        raise SystemExit(f"Missing source_root: {source_root}")

    files = discover_text_files(source_root)
    section_files = [path for path in files if 582 <= file_seq(path) <= 586]
    if not section_files:
        raise SystemExit("Could not locate the PL107 ORDO RERUM tail files.")

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve the PL107 closing ORDO RERUM table and write the final payload.",
            "completed": [
                "Inspected the OCR tail and confirmed the final contents table is the recoverable index section.",
            ],
            "pending": [
                "Generate helper request and run index_target_locator.py.",
                "Assemble entries, refs, coverage, and final JSON.",
            ],
            "blocked": [],
            "notes": [
                "The front-matter ELENCHUS and CATALOGUS pages were inspected but treated as separate editorial matter.",
            ],
        },
    )

    page_map = build_page_map(files)
    entries, refs, helper_entries, evidence_files, section_start_path, section_end_path = parse_section_entries(section_files, page_map)
    helper_request = build_helper_request(VOLUME_ID, source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = assemble_payload(source_root, entries, refs, helper_output, evidence_files, section_start_path, section_end_path)
    write_json(args.output_file, payload)

    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "section_count": len(payload["sections"]),
            "evidence_files": evidence_files,
        },
    )
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Final payload written and ready for validation.",
            "completed": [
                "Inspected the OCR tail and confirmed the final contents table is the recoverable index section.",
                "Generated helper request and ran index_target_locator.py.",
                "Assembled the final payload and intermediate fragments.",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The payload intentionally models the closing contents table as ordo_rerum.",
            ],
        },
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: repair the PL130 alphabetical payload in place.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pl130_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL130/text \
    --input /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL130_alphabetical_indices.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL130_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_ws(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def build_page_map(source_root: Path) -> dict[int, str]:
    """Map editorial page numbers to OCR files using page headers."""
    page_map: dict[int, str] = {}
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize_ws(parsed.get("header_text") or "") or ""
        numbers = [int(n) for n in re.findall(r"\b\d{1,4}\b", header)]
        if not numbers:
            continue
        for number in numbers[:2]:
            page_map[number] = str(path)
    return page_map


def ensure_source_files(raw_json: dict[str, Any]) -> None:
    source_files = list(raw_json.get("source_files") or [])
    source_file = raw_json.get("source_file")
    if source_file and source_file not in source_files:
        source_files.insert(0, source_file)
    if source_files:
        raw_json["source_files"] = source_files


def repair_entries(entries: list[dict[str, Any]]) -> int:
    repaired = 0
    for entry in entries:
        raw_json = entry.get("raw_json")
        if isinstance(raw_json, dict):
            before = list(raw_json.get("source_files") or [])
            ensure_source_files(raw_json)
            if raw_json.get("source_files") != before:
                repaired += 1
            entry["raw_json"] = raw_json
    return repaired


def repair_refs(refs: list[dict[str, Any]], page_map: dict[int, str]) -> tuple[int, int]:
    source_files_added = 0
    target_files_resolved = 0
    for ref in refs:
        raw_json = ref.get("raw_json")
        if isinstance(raw_json, dict):
            before = list(raw_json.get("source_files") or [])
            ensure_source_files(raw_json)
            if raw_json.get("source_files") != before:
                source_files_added += 1
            ref["raw_json"] = raw_json
        page_ref_int = ref.get("page_ref_int")
        if ref.get("target_file") is None and isinstance(page_ref_int, int):
            target_file = page_map.get(page_ref_int)
            if target_file:
                ref["target_file"] = target_file
                ref["target_file_probability"] = 0.98
                target_files_resolved += 1
    return source_files_added, target_files_resolved


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair the PL130 alphabetical payload.")
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    payload = read_json(args.input)
    page_map = build_page_map(args.source_root.resolve())

    entries = payload.get("entries")
    refs = payload.get("refs")
    if not isinstance(entries, list) or not isinstance(refs, list):
        raise SystemExit("payload is missing entries or refs arrays")

    entries_repaired = repair_entries(entries)
    ref_source_files_added, ref_target_files_resolved = repair_refs(refs, page_map)

    payload["generated_at"] = now_iso()
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        notes = list(coverage.get("repair_notes") or [])
        note = (
            f"{datetime.now(timezone.utc).date().isoformat()}: "
            f"added source_files to {entries_repaired} entries and {ref_source_files_added} refs; "
            f"resolved {ref_target_files_resolved} ref target_file locators from OCR page headers."
        )
        if note not in notes:
            notes.append(note)
        coverage["repair_notes"] = notes
        payload["coverage"] = coverage

    notes = list(payload.get("notes") or [])
    summary = (
        f"PL130 repair applied: source_files normalized on entries/refs and "
        f"{ref_target_files_resolved} ref target_file locators resolved from local OCR page headers."
    )
    if summary not in notes:
        notes.append(summary)
    payload["notes"] = notes

    write_json(args.output, payload)


if __name__ == "__main__":
    main()

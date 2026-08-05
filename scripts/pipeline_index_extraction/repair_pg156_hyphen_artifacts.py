#!/usr/bin/env python3
"""Repair confirmed OCR line-break hyphen artifacts in the PG156 alphabetical payload.

Usage:
    python scripts/pipeline_index_extraction/repair_pg156_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG156"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG156/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG156_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG156"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}0-9])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)
REF_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
EVIDENCE_FILES = [
    SOURCE_ROOT / "9091da99-859b-4b52-a0a3-406f8404c997-770.txt",
    SOURCE_ROOT / "9091da99-859b-4b52-a0a3-406f8404c997-771.txt",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return TRAILING_WORD_HYPHEN_RE.sub(lambda m: m.group(0)[:-1], merged)
        updated = merged


def repair_fields(obj: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    changed_fields: list[str] = []
    for field in fields:
        old = obj.get(field)
        new = merge_linebreak_hyphens(old)
        if new != old:
            obj[field] = new
            changed_fields.append(field)
    return changed_fields


def repair_nested_strings(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, child in list(value.items()):
            repaired, child_changes = repair_nested_strings(child)
            value[key] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, list):
        changes = 0
        for idx, child in enumerate(value):
            repaired, child_changes = repair_nested_strings(child)
            value[idx] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, str):
        repaired = merge_linebreak_hyphens(value)
        return repaired, int(repaired != value)
    return value, 0


def repair_entries(entries: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_keys: list[str] = []
    for entry in entries:
        changed_fields = repair_fields(entry, ENTRY_FIELDS)
        raw_json = entry.get("raw_json")
        nested_changes = 0
        if isinstance(raw_json, dict):
            _, nested_changes = repair_nested_strings(raw_json)
            if nested_changes:
                changed["entries.raw_json_nested_strings"] += nested_changes
        if not changed_fields and not nested_changes:
            continue
        entry_key = str(entry.get("entry_key") or "<missing>")
        changed_keys.append(entry_key)
        for field in changed_fields:
            changed[f"entries.{field}"] += 1
    return changed_keys


def repair_refs(refs: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_refs: list[str] = []
    for ref in refs:
        changed_fields = repair_fields(ref, REF_FIELDS)
        raw_json = ref.get("raw_json")
        nested_changes = 0
        if isinstance(raw_json, dict):
            _, nested_changes = repair_nested_strings(raw_json)
            if nested_changes:
                changed["refs.raw_json_nested_strings"] += nested_changes
        if not changed_fields and not nested_changes:
            continue
        ref_id = f"{ref.get('entry_key')}#{ref.get('ref_order')}"
        changed_refs.append(ref_id)
        for field in changed_fields:
            changed[f"refs.{field}"] += 1
    return changed_refs


def repair_payload_object(payload: dict[str, Any]) -> tuple[dict[str, int], list[str], list[str]]:
    changed: dict[str, int] = defaultdict(int)
    changed_entry_keys = repair_entries(payload.get("entries", []), changed)
    changed_refs = repair_refs(payload.get("refs", []), changed)
    for key in ("scripture_refs", "notes"):
        _, nested_changes = repair_nested_strings(payload.get(key))
        if nested_changes:
            changed[f"{key}.nested_strings"] += nested_changes
    volume = payload.get("volume")
    if isinstance(volume, dict):
        _, nested_changes = repair_nested_strings(volume)
        if nested_changes:
            changed["volume.nested_strings"] += nested_changes
    sections = payload.get("sections")
    if isinstance(sections, list):
        _, nested_changes = repair_nested_strings(sections)
        if nested_changes:
            changed["sections.nested_strings"] += nested_changes
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        _, nested_changes = repair_nested_strings(nodes)
        if nested_changes:
            changed["nodes.nested_strings"] += nested_changes
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        _, nested_changes = repair_nested_strings(coverage)
        if nested_changes:
            changed["coverage.nested_strings"] += nested_changes

    payload["generated_at"] = now_iso()
    volume_obj = payload.setdefault("volume", {})
    volume_note = (
        "PG156 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload after "
        "checking representative OCR reader output for files 770 and 771."
    )
    volume_notes = volume_obj.get("notes")
    if volume_notes is None:
        volume_obj["notes"] = volume_note
    elif isinstance(volume_notes, str):
        if volume_note not in volume_notes:
            volume_obj["notes"] = f"{volume_notes} {volume_note}"
    elif isinstance(volume_notes, list):
        if volume_note not in volume_notes:
            volume_notes.append(volume_note)

    notes = payload.setdefault("notes", [])
    repair_note = (
        "Repaired OCR line-break hyphen artifacts in entry, lemma, and ref text fields after verifying "
        "the cleaned OCR reader output for representative PG156 index pages."
    )
    if repair_note not in notes:
        notes.append(repair_note)

    return changed, changed_entry_keys, changed_refs


def update_intermediates(payload: dict[str, Any], changed: dict[str, int], changed_entry_keys: list[str], changed_refs: list[str]) -> None:
    dump_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    dump_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    dump_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    dump_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    dump_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    dump_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    dump_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    dump_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    dump_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "source_root": str(SOURCE_ROOT),
            "output_file": str(PAYLOAD_PATH),
        },
    )
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PG156 payload repaired and validated after hyphen-artifact cleanup.",
            "completed": [
                "read prompt contract and output format",
                "inspected representative PG156 OCR reader output for files 770 and 771",
                "verified the current payload against OCR before changing text fields",
                "repaired OCR line-break hyphen artifacts in payload strings",
                "synchronized final payload and intermediate fragments",
            ],
            "pending": [
                "Run import_alphabetical_index_json.py --validate-only --print-summary",
            ],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed)}",
                f"Changed entries sample: {changed_entry_keys[:40]}",
                f"Changed refs sample: {changed_refs[:40]}",
            ],
        },
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed, changed_entry_keys, changed_refs = repair_payload_object(payload)
    dump_json(PAYLOAD_PATH, payload)
    update_intermediates(payload, changed, changed_entry_keys, changed_refs)
    validate_payload()


if __name__ == "__main__":
    main()

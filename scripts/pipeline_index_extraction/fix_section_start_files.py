#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/fix_section_start_files.py \
    --payload /path/to/VOLUME_alphabetical_indices.json \
    --entries /path/to/intermediate_payloads/VOLUME/entries.json \
    --refs /path/to/intermediate_payloads/VOLUME/refs.json \
    --todo /path/to/intermediate_payloads/VOLUME/todo.json

Rewrites `section_start_file` fields so they point to OCR file paths inside the
current volume's `source_root`, using the section map stored in the canonical
payload.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def is_bad_section_start_file(value: Any, source_root: str) -> bool:
    if not isinstance(value, str):
        return True
    if not value:
        return True
    if value.isdigit():
        return True
    return source_root not in value


def build_maps(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    section_to_file: dict[str, str] = {}
    entry_to_section: dict[str, str] = {}

    for section in payload.get("sections", []):
        section_key = section.get("section_key")
        file_start = section.get("file_start")
        if isinstance(section_key, str) and isinstance(file_start, str):
            section_to_file[section_key] = file_start

    for entry in payload.get("entries", []):
        entry_key = entry.get("entry_key")
        section_key = entry.get("section_key")
        if isinstance(entry_key, str) and isinstance(section_key, str):
            entry_to_section[entry_key] = section_key

    return section_to_file, entry_to_section


def fix_entries(entries: list[dict[str, Any]], section_to_file: dict[str, str], source_root: str) -> int:
    fixed = 0
    for entry in entries:
        section_key = entry.get("section_key")
        if not isinstance(section_key, str):
            continue
        target = section_to_file.get(section_key)
        if not target:
            continue
        if is_bad_section_start_file(entry.get("section_start_file"), source_root):
            entry["section_start_file"] = target
            fixed += 1
    return fixed


def fix_refs(refs: list[dict[str, Any]], entry_to_section: dict[str, str], section_to_file: dict[str, str], source_root: str) -> int:
    fixed = 0
    for ref in refs:
        entry_key = ref.get("entry_key")
        if not isinstance(entry_key, str):
            continue
        section_key = entry_to_section.get(entry_key)
        if not section_key:
            continue
        target = section_to_file.get(section_key)
        if not target:
            continue
        if is_bad_section_start_file(ref.get("section_start_file"), source_root):
            ref["section_start_file"] = target
            fixed += 1
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--entries", required=True, type=Path)
    parser.add_argument("--refs", required=True, type=Path)
    parser.add_argument("--todo", required=True, type=Path)
    args = parser.parse_args()

    payload = load_json(args.payload)
    source_root = payload["volume"]["source_root"]
    section_to_file, entry_to_section = build_maps(payload)

    payload_entries_fixed = fix_entries(payload.get("entries", []), section_to_file, source_root)
    payload_refs_fixed = fix_refs(payload.get("refs", []), entry_to_section, section_to_file, source_root)
    write_json(args.payload, payload)

    entries = load_json(args.entries)
    entries_fixed = fix_entries(entries, section_to_file, source_root)
    write_json(args.entries, entries)

    refs = load_json(args.refs)
    refs_fixed = fix_refs(refs, entry_to_section, section_to_file, source_root)
    write_json(args.refs, refs)

    todo = load_json(args.todo)
    todo["updated_at"] = "2026-07-19T19:06:56+00:00"
    todo["current_focus"] = "Repair PL076 section_start_file paths in payload and intermediate fragments after validation failure."
    todo["completed"] = [
        "section headings mapped",
        "helper request written and executed",
        "entries, nodes, refs, and closure fragments assembled",
        "section_start_file repair applied to payload and intermediate fragments",
    ]
    todo["pending"] = [
        "rerun import validation on the repaired payload",
    ]
    todo["blocked"] = []
    todo["notes"] = [
        "Keep OCR literals intact.",
        "Do not conflate OCR file suffixes with editorial page numbers.",
        f"Fixed {payload_entries_fixed} payload entries, {payload_refs_fixed} payload refs, {entries_fixed} intermediate entries, and {refs_fixed} intermediate refs.",
    ]
    write_json(args.todo, todo)

    print(
        json.dumps(
            {
                "payload_entries_fixed": payload_entries_fixed,
                "payload_refs_fixed": payload_refs_fixed,
                "entries_fixed": entries_fixed,
                "refs_fixed": refs_fixed,
                "payload_path": str(args.payload),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

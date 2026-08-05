#!/usr/bin/env python3
"""Merge validated PL021 fragment objects into the final alphabetical payload.

Usage:
  python scripts/pipeline_index_extraction/merge_pl021_fragment_fixes.py

This script patches the current PL021 final payload by importing stable
entries/refs that exist in the validated fragment assembly but were omitted
from the canonical output file.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL021_alphabetical_indices.json"
FRAGMENT_PATH = ROOT / "data/intermediate_payloads/PL021/chunks/section_001_part_001.json"

MISSING_ENTRY_KEYS = [
    "PL021:candidate-section:001:PL021:chunk:001:001:002",
    "PL021:candidate-section:001:PL021:chunk:001:001:003",
    "PL021:candidate-section:001:PL021:chunk:001:001:012",
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    fragment = load_json(FRAGMENT_PATH)

    existing_entries = {item["entry_key"] for item in payload["entries"]}
    existing_refs = {(item["entry_key"], item["ref_order"]) for item in payload["refs"]}

    fragment_entries = {item["entry_key"]: item for item in fragment["entries"]}
    fragment_refs = fragment["refs"]

    additions = []
    for key in MISSING_ENTRY_KEYS:
        if key in fragment_entries and key not in existing_entries:
            additions.append(fragment_entries[key])

    payload["entries"].extend(additions)
    payload["entries"].sort(
        key=lambda item: (
            item["section_key"],
            item["entry_order"],
            item["entry_key"],
        )
    )

    ref_additions = []
    for ref in fragment_refs:
        ref_key = (ref["entry_key"], ref["ref_order"])
        if ref["entry_key"] in MISSING_ENTRY_KEYS and ref_key not in existing_refs:
            ref_additions.append(ref)

    payload["refs"].extend(ref_additions)
    payload["refs"].sort(
        key=lambda item: (
            item["entry_key"],
            item["ref_order"],
            item.get("page_ref_int") if item.get("page_ref_int") is not None else -1,
            item.get("ref_raw", ""),
        )
    )

    # Refresh generated_at to reflect the patched canonical file.
    payload["generated_at"] = "2026-07-28T00:22:16+00:00"

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

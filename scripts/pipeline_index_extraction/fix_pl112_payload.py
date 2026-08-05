#!/usr/bin/env python3
"""Repair the PL112 alphabetical payload keying.

Run from the repo root with:
  python scripts/pipeline_index_extraction/fix_pl112_payload.py \
    --input data/alphabetical_index_payloads/PL112_alphabetical_indices.json \
    --output data/alphabetical_index_payloads/PL112_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


ENTRY_KEY_RE = re.compile(r"^(?P<prefix>.+:entry:)(?P<num>\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the current payload JSON")
    parser.add_argument("--output", required=True, help="Path to write the repaired payload JSON")
    return parser.parse_args()


def entry_num(entry_key: str) -> int:
    match = ENTRY_KEY_RE.match(entry_key)
    if not match:
        raise ValueError(f"Unexpected entry_key format: {entry_key!r}")
    return int(match.group("num"))


def make_entry_key(volume_id: str, number: int, width: int) -> str:
    return f"{volume_id}:entry:{number:0{width}d}"


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    payload = json.loads(input_path.read_text())
    volume_id = payload["volume"]["volume_id"]

    sections = payload.get("sections", [])
    entries = payload.get("entries", [])
    refs = payload.get("refs", [])

    if not sections or not entries:
        raise ValueError("Expected a non-empty payload with sections and entries")

    section_offsets: dict[str, int] = {}
    section_width = max(len(str(len(entries))), 5)
    running = 0
    for section in sections:
        section_key = section["section_key"]
        section_offsets[section_key] = running
        running += sum(1 for entry in entries if entry["section_key"] == section_key)

    section_kind_to_key = {}
    for section in sections:
        section_kind = section.get("section_kind")
        section_key = section["section_key"]
        if section_kind in section_kind_to_key and section_kind_to_key[section_kind] != section_key:
            raise ValueError(
                f"Ambiguous section_kind {section_kind!r}; this repair script expects unique section kinds"
            )
        section_kind_to_key[section_kind] = section_key

    # Remap entries to volume-global keys.
    old_to_new_by_section: dict[str, dict[str, str]] = defaultdict(dict)
    for index, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        section_key = entry["section_key"]
        new_key = make_entry_key(volume_id, index, section_width)
        old_to_new_by_section[section_key][old_key] = new_key
        entry["entry_key"] = new_key

    # Remap refs to the correct global entry key, then normalize ref_order within each entry.
    ref_order_counts: dict[str, int] = defaultdict(int)
    for ref in refs:
        raw_json = ref.get("raw_json") or {}
        section_kind = raw_json.get("section_kind")
        section_key = section_kind_to_key.get(section_kind)
        if section_key is None:
            raise ValueError(f"Unable to map ref section_kind {section_kind!r} to a section_key")

        old_entry_key = ref["entry_key"]
        new_entry_key = old_to_new_by_section[section_key].get(old_entry_key)
        if new_entry_key is None:
            raise ValueError(
                f"Unable to map ref entry_key {old_entry_key!r} in section {section_key!r}"
            )

        ref["entry_key"] = new_entry_key
        ref_order_counts[new_entry_key] += 1
        ref["ref_order"] = ref_order_counts[new_entry_key]

    # Keep refs in a stable, per-entry order. The existing array order is already by entry traversal.
    payload["entries"] = entries
    payload["refs"] = refs

    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

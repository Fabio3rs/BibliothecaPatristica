#!/usr/bin/env python3
"""Fix known PL015 alphabetical payload schema issues.

Usage:
  python scripts/pipeline_index_extraction/fix_pl015_alphabetical_payload.py \
    /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL015_alphabetical_indices.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fix_pl015_alphabetical_payload.py <payload_json>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    data = json.loads(path.read_text())

    for section in data.get("sections", []):
        section.setdefault("volume_id", data.get("volume", {}).get("volume_id", "PL015"))

    for entry in data.get("entries", []):
        if entry.get("entry_kind") == "ordinal_group":
            entry["entry_kind"] = "heading_group"

    drop_ref_raws = {
        "ibid., 12 et seq.",
        "ibid., 27",
        "ibid., 215",
        "ibid., 21",
        "ibid., 30",
        "ibid.",
        "Ibid.",
    }
    refs = data.get("refs", [])
    filtered_refs = [ref for ref in refs if ref.get("ref_raw") not in drop_ref_raws]

    # Keep ref_order stable within each entry after removing bare remissions.
    per_entry: dict[str, list[dict]] = {}
    for ref in filtered_refs:
        per_entry.setdefault(ref["entry_key"], []).append(ref)
    for ref_list in per_entry.values():
        ref_list.sort(key=lambda item: item["ref_order"])
        for idx, ref in enumerate(ref_list, start=1):
            ref["ref_order"] = idx
    data["refs"] = [ref for entry_key in sorted(per_entry) for ref in per_entry[entry_key]]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

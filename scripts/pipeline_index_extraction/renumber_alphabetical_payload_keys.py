#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/renumber_alphabetical_payload_keys.py --payload <payload.json> [--intermediate-dir <dir>]
# Renumbers alphabetical index entry keys to be globally unique and remaps refs to the new keys.

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def renumber_entries(volume_id: str, entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    occurrence_map: dict[str, list[str]] = defaultdict(list)
    for idx, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        new_key = f"{volume_id}:entry:{idx:04d}"
        entry["entry_key"] = new_key
        occurrence_map[old_key].append(new_key)
    return occurrence_map


def remap_refs(refs: list[dict[str, Any]], occurrence_map: dict[str, list[str]]) -> None:
    seen: dict[str, int] = defaultdict(int)
    i = 0
    while i < len(refs):
        old_key = refs[i]["entry_key"]
        j = i + 1
        while j < len(refs) and refs[j]["entry_key"] == old_key:
            j += 1

        occurrence_index = seen[old_key]
        if old_key not in occurrence_map or occurrence_index >= len(occurrence_map[old_key]):
            raise ValueError(f"Missing entry_key mapping for {old_key} occurrence {occurrence_index + 1}")

        new_key = occurrence_map[old_key][occurrence_index]
        for k in range(i, j):
            refs[k]["entry_key"] = new_key

        seen[old_key] += 1
        i = j


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, help="Path to the canonical alphabetical payload JSON.")
    parser.add_argument(
        "--intermediate-dir",
        help="Optional intermediate directory; if provided, entries.json and refs.json are updated there too.",
    )
    args = parser.parse_args()

    payload_path = Path(args.payload)
    payload = load_json(payload_path)

    volume_id = payload["volume"]["volume_id"]
    entries = payload["entries"]
    refs = payload["refs"]

    occurrence_map = renumber_entries(volume_id, entries)
    remap_refs(refs, occurrence_map)

    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["entries"] = entries
    payload["refs"] = refs

    write_json(payload_path, payload)

    if args.intermediate_dir:
        intermediate_dir = Path(args.intermediate_dir)
        write_json(intermediate_dir / "entries.json", entries)
        write_json(intermediate_dir / "refs.json", refs)


if __name__ == "__main__":
    main()

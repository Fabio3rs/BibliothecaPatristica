#!/usr/bin/env python3
"""Repair the PL187 alphabetical payload after extraction.

Usage:
  python scripts/pipeline_index_extraction/fix_pl187_payload.py \
    --payload /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL187_alphabetical_indices.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL187

The script renumbers entry keys globally, remaps refs to the new keys, removes
pure editorial remissions without a material locator, and fills non-numeric
target locators so the payload validates cleanly.
"""

from __future__ import annotations

import argparse
import json
import re
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


def new_entry_key(volume_id: str, order: int) -> str:
    return f"{volume_id}:entry:{order:04d}"


def renumber_entries(volume_id: str, entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    occurrence_map: dict[str, list[str]] = defaultdict(list)
    for idx, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        entry["entry_key"] = new_entry_key(volume_id, idx)
        entry["entry_order"] = idx
        occurrence_map[old_key].append(entry["entry_key"])
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


def fill_missing_target_anchor(ref: dict[str, Any]) -> None:
    if ref.get("ref_kind") != "target_locator":
        return
    if ref.get("page_ref_raw") is not None or ref.get("target_file") is not None:
        return
    if ref.get("range_start_raw") is not None or ref.get("range_end_raw") is not None:
        return

    raw = (ref.get("ref_raw") or "").strip()
    stripped = re.sub(r"^[cC]\.\s*", "", raw).strip()
    if stripped:
        ref["page_ref_raw"] = stripped
        ref["page_ref_int"] = None


def is_pure_remission(ref: dict[str, Any]) -> bool:
    if ref.get("ref_kind") != "target_locator":
        return False
    if any(ref.get(field) is not None for field in ("page_ref_raw", "target_file", "range_start_raw", "range_end_raw")):
        return False
    raw = (ref.get("ref_raw") or "").strip().lower()
    return raw.startswith("vid.") or raw.startswith("vide") or raw.startswith("voir") or raw.startswith("id.")


def maybe_mark_entry_cross_reference(entry: dict[str, Any]) -> None:
    raw = (entry.get("entry_raw") or "").lower()
    if "vid." in raw or raw.startswith("vide") or raw.startswith("voir") or raw.startswith("id."):
        entry["entry_kind"] = "cross_reference"


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

    filtered_refs: list[dict[str, Any]] = []
    for ref in refs:
        if is_pure_remission(ref):
            continue
        fill_missing_target_anchor(ref)
        filtered_refs.append(ref)
    refs = filtered_refs

    for entry in entries:
        maybe_mark_entry_cross_reference(entry)

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

#!/usr/bin/env python3
"""Usage: renumber an alphabetical payload's node_order and entry_order fields per section.

Run:
  python scripts/pipeline_index_extraction/renumber_alphabetical_payload.py \
    --input <payload.json> --output <payload.fixed.json>
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def renumber_by_section(items: list[dict], order_field: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    section_order: list[str] = []
    for item in items:
        section_key = item["section_key"]
        if section_key not in grouped:
            section_order.append(section_key)
        grouped[section_key].append(item)

    rebuilt: list[dict] = []
    for section_key in section_order:
        for new_order, item in enumerate(grouped[section_key], start=1):
            if item.get(order_field) != new_order:
                item = dict(item)
                item[order_field] = new_order
            rebuilt.append(item)
    return rebuilt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    payload["nodes"] = renumber_by_section(payload.get("nodes", []), "node_order")
    payload["entries"] = renumber_by_section(payload.get("entries", []), "entry_order")

    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()

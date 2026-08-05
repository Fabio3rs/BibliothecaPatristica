#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_pl118_final_payload.py --final <final.json> --chunk <chunk.json> --output <output.json>
# Rebuilds the PL118 alphabetical payload so the final file preserves the validated chunk stable keys.

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the PL118 final alphabetical payload from validated chunk data.")
    parser.add_argument("--final", required=True, type=Path, help="Existing final payload JSON path")
    parser.add_argument("--chunk", required=True, type=Path, help="Validated chunk JSON path")
    parser.add_argument("--output", required=True, type=Path, help="Output payload JSON path")
    args = parser.parse_args()

    final_payload = load_json(args.final)
    chunk_payload = load_json(args.chunk)

    if len(final_payload.get("entries", [])) != len(chunk_payload.get("entries", [])):
        raise SystemExit("entry count mismatch between final payload and validated chunk")
    if len(final_payload.get("refs", [])) != len(chunk_payload.get("refs", [])):
        raise SystemExit("ref count mismatch between final payload and validated chunk")

    payload = copy.deepcopy(final_payload)

    if not payload.get("sections"):
        raise SystemExit("final payload does not contain a section to repair")
    if not chunk_payload.get("sections"):
        raise SystemExit("chunk payload does not contain section data")

    payload["generated_at"] = utc_now_z()
    payload["sections"][0]["section_key"] = chunk_payload["section_id"]
    payload["nodes"] = []
    payload["entries"] = chunk_payload["entries"]
    payload["refs"] = chunk_payload["refs"]

    notes = list(payload.get("notes") or [])
    repair_note = (
        "Rebuilt from the validated chunk checkpoint; preserved stable chunk entry/ref keys and removed the synthetic node from the renumbered draft."
    )
    if repair_note not in notes:
        notes.append(repair_note)
    payload["notes"] = notes

    write_json(args.output, payload)


if __name__ == "__main__":
    main()

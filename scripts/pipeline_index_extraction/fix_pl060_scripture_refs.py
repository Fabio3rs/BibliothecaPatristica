#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_pl060_scripture_refs.py
# Remove contaminated scripture_refs from the PL060 alphabetical payload and record the rerun note.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


PAYLOAD_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL060_alphabetical_indices.json")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    scripture_refs = payload.get("scripture_refs", [])
    removed_count = len(scripture_refs)
    if removed_count == 0:
        return

    payload["scripture_refs"] = []
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    notes = payload.setdefault("notes", [])
    rerun_note = (
        "2026-07-23 rerun: removed 19 contaminated scripture_refs that were page-level dumps "
        "from the alphabetical general section rather than discrete biblical citations."
    )
    if rerun_note not in notes:
        notes.append(rerun_note)

    PAYLOAD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

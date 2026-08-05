#!/usr/bin/env python3
"""Repair PG016.03 alphabetical payload split-word hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg016_03_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG016.03"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG016.03_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG016.03"

REPAIRS = {
    "Versus in Eleu- siniis dictus": "Versus in Eleusiniis dictus",
    "Versus anteæ ignoti, Parme- nidi vel Pampho Atheniensi ascripti": "Versus anteæ ignoti, Parmenidi vel Pampho Atheniensi ascripti",
    "Reve- latio magna": "Revelatio magna",
    "Ἀναξαγόρας Ἡγησιβούλου ὁ Κλα- ζομένιος": "Ἀναξαγόρας Ἡγησιβούλου ὁ Κλαζομένιος",
    "Ἀναξίμανδρος Πραξιάδου Μιλή- σιος": "Ἀναξίμανδρος Πραξιάδου Μιλήσιος",
    "Δημόκριτος Δαμασίππου Ἀβδηρί- της": "Δημόκριτος Δαμασίππου Ἀβδηρίτης",
    "ὁ κολο- βοδάκτυλος": "ὁ κολοβοδάκτυλος",
    "Περαιτικὴ αἱρε- σις": "Περαιτικὴ αἵρεσις",
    "Περαιτικὰ συν- τάγματα": "Περαιτικὰ συντάγματα",
    "Πυθαγόρας, ὃν Σάμιόν τινες λέ- γουσιν": "Πυθαγόρας, ὃν Σάμιόν τινες λέγουσιν",
}

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]\-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def has_linebreak_hyphen(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value).strip()
    return text.endswith("-") or bool(LINEBREAK_HYPHEN_RE.search(text))


def repair_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    repaired = value
    for before, after in REPAIRS.items():
        repaired = repaired.replace(before, after)
    return repaired


def main() -> None:
    data = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    repaired_entries: list[str] = []

    for entry in data["entries"]:
        before = {field: entry.get(field) for field in ("lemma_raw", "lemma_display", "entry_raw")}
        for field in ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw", "context_raw"):
            entry[field] = repair_text(entry.get(field))
        after = {field: entry.get(field) for field in ("lemma_raw", "lemma_display", "entry_raw")}
        if before != after:
            repaired_entries.append(entry["entry_key"])
            raw_json = entry.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            notes.append(
                "PG016.03 rerun merged OCR line-break hyphenation after checking the cleaned OCR reader output."
            )

    entry_keys = {entry["entry_key"] for entry in data["entries"]}
    missing_refs = [
        (idx, ref["entry_key"])
        for idx, ref in enumerate(data["refs"])
        if ref["entry_key"] not in entry_keys
    ]
    if missing_refs:
        raise SystemExit(f"refs with missing entry_key remain: {missing_refs[:20]}")

    residual_entries = []
    for idx, entry in enumerate(data["entries"]):
        for field in ("lemma_raw", "lemma_display", "entry_raw"):
            if has_linebreak_hyphen(entry.get(field)):
                residual_entries.append((idx, entry["entry_key"], field, entry.get(field)))
    if residual_entries:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual_entries[:20]}")

    timestamp = now_iso()
    data["generated_at"] = timestamp
    note = (
        "PG016.03 rerun repaired validation-blocking OCR line-break hyphen artifacts in the "
        "profane-source and onomastic sections; the dangling ref errors were the importer cascade "
        "from those invalid entries."
    )
    if note not in data["notes"]:
        data["notes"].append(note)

    write_json(PAYLOAD_PATH, data)

    write_json(INTERMEDIATE_DIR / "entries.json", data["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", data["refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", data["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", data["notes"])
    write_json(INTERMEDIATE_DIR / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": timestamp})
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG016.03 import validation failure; ready for final validation.",
            "completed": [
                "Read prior validation failure and inspected the exact rejected entries",
                "Verified split-word artifacts against OCR reader output for files 553, 554, and 556",
                "Repaired validation-blocking entry text fields and refreshed intermediate checkpoints",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "Numeric range spacing such as 56- 58 is not a split-word artifact and was left unchanged.",
                "The missing entry_key errors are expected to disappear once the importer accepts the repaired entries.",
            ],
        },
    )

    print(json.dumps({"repaired_entries": repaired_entries, "count": len(repaired_entries)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

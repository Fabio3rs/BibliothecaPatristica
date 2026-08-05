#!/usr/bin/env python3
"""Repair confirmed OCR line-break hyphen artifacts in PG139 ORDO RERUM payload.

Usage:
    python scripts/pipeline_index_extraction/repair_pg139_hyphen_artifacts.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG139_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG139/todo.json"


REPLACEMENTS = {
    3: [
        ("Virgi- nis", "Virginis"),
        ("virgi nis", "virginis"),
    ],
    47: [
        ("Ju- dæorum", "Judæorum"),
        ("ju dæorum", "judæorum"),
    ],
    50: [
        ("philosopho- rumque", "philosophorumque"),
        ("philosopho rumque", "philosophorumque"),
    ],
    56: [
        ("Pla- netarum", "Planetarum"),
        ("pla netarum", "planetarum"),
    ],
    76: [
        ("pro- cella", "procella"),
        ("pro cella", "procella"),
    ],
    77: [
        ("animo-rum", "animorum"),
        ("animo- rum", "animorum"),
        ("animo rum", "animorum"),
    ],
    81: [
        ("Sama- riam", "Samariam"),
        ("sama riam", "samariam"),
    ],
    83: [
        ("se- ctæ", "sectæ"),
        ("se ctae", "sectae"),
        ("se ctæ", "sectæ"),
    ],
    98: [
        ("incompre- hensibilis", "incomprehensibilis"),
        ("Spi- ritum", "Spiritum"),
        ("incompre hensibilis", "incomprehensibilis"),
        ("spi ritum", "spiritum"),
    ],
    99: [
        ("homi- nis", "hominis"),
        ("homi nis", "hominis"),
    ],
    102: [
        ("Spi- ritu", "Spiritu"),
        ("spi ritu", "spiritu"),
    ],
    108: [
        ("indi- vidui", "individui"),
        ("indi vidui", "individui"),
    ],
    113: [
        ("com- prehendi", "comprehendi"),
        ("com prehendi", "comprehendi"),
    ],
    115: [
        ("considera- tio", "consideratio"),
        ("considera tio", "consideratio"),
    ],
    116: [
        ("notion- bus", "notionbus"),
        ("notion bus", "notionbus"),
    ],
    122: [
        ("charisma- tis", "charismatis"),
        ("charisma tis", "charismatis"),
    ],
    124: [
        ("æqui- librio", "æquilibrio"),
        ("aequi librio", "aequilibrio"),
        ("æqui librio", "æquilibrio"),
    ],
    125: [
        ("præ- cedentibus", "præcedentibus"),
        ("prae cedentibus", "praecedentibus"),
        ("præ cedentibus", "præcedentibus"),
    ],
    128: [
        ("Pater- nitatis", "Paternitatis"),
        ("pater nitatis", "paternitatis"),
    ],
    130: [
        ("Quo- modo", "Quomodo"),
        ("quo modo", "quomodo"),
    ],
    134: [
        ("præ- cipua", "præcipua"),
        ("prae cipua", "praecipua"),
        ("præ cipua", "præcipua"),
    ],
    143: [
        ("im- mortalitate", "immortalitate"),
        ("im mortalitate", "immortalitate"),
    ],
    146: [
        ("ani- mæ", "animæ"),
        ("ani mae", "animae"),
        ("ani mæ", "animæ"),
    ],
    149: [
        ("co- ligi", "coligi"),
        ("co ligi", "coligi"),
    ],
    150: [
        ("homi- nem", "hominem"),
        ("homi nem", "hominem"),
    ],
}


def apply_replacements(text: str | None, replacements: list[tuple[str, str]]) -> str | None:
    if text is None:
        return None
    updated = text
    for old, new in replacements:
        updated = updated.replace(old, new)
    return updated


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    for index, replacements in REPLACEMENTS.items():
        entry = payload["entries"][index - 1]
        for field in ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw"):
            entry[field] = apply_replacements(entry.get(field), replacements)

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    todo = json.loads(TODO_PATH.read_text())
    todo["updated_at"] = payload["generated_at"]
    todo["current_focus"] = "PG139 hyphen-artifact repair validated"
    todo["completed"] = [
        "read prompt contract and output format",
        "inspected OCR for the terminal index section",
        "verified failing hyphen-artifact entries against ORDO RERUM OCR files 727-731",
        "repaired confirmed line-break hyphen artifacts in PG139 payload",
    ]
    todo["pending"] = [
        "Run import_alphabetical_index_json.py --validate-only --print-summary"
    ]
    todo["blocked"] = []
    todo["notes"] = [
        "Only confirmed OCR line-break hyphens were merged.",
        "The filtered tail section is the real extraction target.",
        "Preserve OCR literals and page numbers separately from OCR file suffixes.",
    ]
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

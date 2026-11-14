#!/usr/bin/env python3
"""Build the bounded PL157 ORDO RERUM fragment from reviewed OCR pages."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "teste/PL157/text"
OUTPUT = ROOT / "data/index_intermediate_payloads/PL157/chunks/section_002_part_001.json"
PREFIX = "26ce5fc7-ef51-439e-a7c7-92ac5fca351d-"
OWNED_NUMBERS = range(656, 661)
SECTION_KEY = "PL157:candidate-section:002"
CHUNK_ID = "PL157:chunk:002:001"
FINGERPRINT = "f8b158e2c9eb4574453237a77ce2209c7a85e71c17d99aa77948f58ca227e1aa"


def source_name(number: int) -> str:
    return str(SOURCE_ROOT / f"{PREFIX}{number}.txt")


OWNED = [source_name(number) for number in OWNED_NUMBERS]
CONTEXT = [source_name(number) for number in range(652, 665)]


def read_body(path: str) -> list[str]:
    """Use the mandated conservative reader, preserving its joined OCR lines."""
    result = subprocess.run(
        ["python", "scripts/read_ocr_page_text.py", "--view", "body", path],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("<!--")
    ]


def ref_fields(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(\d{1,4})$", raw)
    if not match:
        return raw, None, None
    locator = match.group(1)
    return raw[:match.start()].rstrip(), locator, int(locator)


def entry_kind(target: str) -> str:
    letters = [char for char in target if char.isalpha()]
    return "contents_heading" if letters and all(char == char.upper() for char in letters) else "contents_entry"


def build_entries() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    order = 0
    for number in OWNED_NUMBERS:
        path = source_name(number)
        for raw in read_body(path):
            order += 1
            target, page_raw, page_int = ref_fields(raw)
            kind = entry_kind(target)
            entries.append(
                {
                    "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
                    "entry_order": order,
                    "entry_raw": raw,
                    "target_raw": target,
                    "target_file": None,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "note_raw": "heading" if kind == "contents_heading" else None,
                    "normalized_target": target,
                    "confidence": "high",
                    "raw_json": {
                        "source_files": [path],
                        "source_lines": [raw],
                        "entry_kind": kind,
                        "locator_system": "editorial_page" if page_raw is not None else None,
                    },
                }
            )
    return entries


def boundary(left: int, right: int, reason: str) -> dict[str, str]:
    return {
        "physical_left_file": source_name(left),
        "physical_right_file": source_name(right),
        "decision": "not_same_entry",
        "reason": reason,
    }


def main() -> None:
    entries = build_entries()
    payload = {
        "schema_version": 2,
        "volume_id": "PL157",
        "section_id": SECTION_KEY,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": OWNED,
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            boundary(656, 657, "File 656 ends with the complete Epist. XXXVII row and file 657 begins the independently numbered EPIST. XXXVIII row."),
            boundary(657, 658, "File 657 ends with the structural heading LIBER PRIMUS; file 658 begins the independently numbered CAP. I row."),
            boundary(658, 659, "File 658 ends with the complete XXVII donation row; file 659 begins the independently numbered XXVIII row."),
            boundary(659, 660, "File 659 ends with the structural heading LIBER SECUNDUS DEFLORATIONUM; file 660 begins the independently numbered Dominica I row."),
        ],
        "works": [],
        "sections": [
            {
                "section_key": SECTION_KEY,
                "work_key": None,
                "scope_kind": "volume_end",
                "index_kind": "ORDO_RERUM",
                "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
                "page_start": None,
                "page_end": None,
                "file_start": OWNED[0],
                "file_end": OWNED[-1],
                "confidence": "high",
                "raw_json": {
                    "source_files": OWNED,
                    "adjacent_context_files_checked": CONTEXT,
                    "section_kind": "volume_contents_table",
                    "header_evidence": [
                        "File 656 retains the preceding INDEX header with editorial pages 1303–1304, while its body opens the ORDO.",
                        "File 657 header continues the title: QUE IN HOC TOMO CONTINENTUR.",
                        "File 658 header: 1507. ORDO RERUM 1508.",
                        "File 659 header: 1509 QUÆ IN HOC TOMO CONTINENTUR. 1510.",
                        "File 660 header: 1311 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 1312.",
                    ],
                    "numbering_note": "The inconsistent header readings are retained as OCR evidence only. Terminal numbers in rows are editorial locators and are never mapped to physical file suffixes.",
                    "extra_file_evidence": {
                        "preceding_files_checked": [source_name(number) for number in range(652, 656)],
                        "following_files_checked": [source_name(number) for number in range(661, 665)],
                        "preceding_result": "Files 652–655 are the closing alphabetical INDEX RERUM/INDEX EORUM; 655 ends the sender index and establishes that the 656 body begins a new ORDO RERUM table.",
                        "following_result": "Files 661–664 are unrelated French material, advertising, or blanks; the table concludes in owned file 660 with the volume finis line.",
                        "target_locator": "not_run_by_chunk_contract",
                    },
                    "entries_status": "complete",
                    "entries_status_reason": "Every readable ORDO row and structural heading in owned files 656–660 is serialized once. All four physical boundaries begin distinct complete rows or a new structural division; no cross-file entry is joined.",
                },
                "entries": entries,
            }
        ],
        "notes": [
            "Bounded fragment only; it does not assemble the volume-level ORDO RERUM.",
            "OCR literals, including uncertain spellings and terminal locators, are retained without normalization.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(entries)} entries")


if __name__ == "__main__":
    main()

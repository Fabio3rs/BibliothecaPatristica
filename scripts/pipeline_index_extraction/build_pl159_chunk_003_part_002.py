#!/usr/bin/env python3
"""Build the bounded PL159 ORDO RERUM fragment for chunk 003:002."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


WORKPLAN = ROOT / "data/index_intermediate_payloads/PL159/workplan.json"
OUTPUT = ROOT / "data/index_intermediate_payloads/PL159/chunks/section_003_part_002.json"
SECTION_ID = "PL159:candidate-section:003"
SECTION_KEY = "PL159:section:003:volume-end-ordo"
CHUNK_ID = "PL159:chunk:003:002"
FINGERPRINT = "01c35778728aeaeb131f01912af1e38f0cb81755997c45c2b9b5a1d90faeeb57"
PREFIX = ROOT / "teste/PL159/text/77cd0fe8-c16d-4d38-aa61-9ca892a53bd5-"


def file_name(number: int) -> str:
    return str(PREFIX) + f"{number}.txt"


def read_body(number: int) -> list[str]:
    """Read through the mandated OCR reader, retaining its conservative literals."""
    result = subprocess.run(
        ["python", "scripts/read_ocr_page_text.py", "--view", "body", "--show-source", file_name(number)],
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


def entry_kind(raw: str) -> str:
    if raw.startswith("CAP."):
        return "chapter_entry"
    letters = [char for char in raw if char.isalpha()]
    if letters and all(char == char.upper() for char in letters):
        return "contents_heading"
    if raw.startswith(("EPIST.", "Epist.", "I. ", "II. ", "III. ", "IV. ", "V. ", "VI. ", "VII. ", "VIII. ", "IX. ", "X. ", "XI. ", "XII. ", "XIII. ", "XIV. ", "XV. ", "XVI. ", "XVII. ", "XVIII. ", "XIX. ", "XX. ", "XXI. ", "XXII. ", "XXIII. ", "XXIV. ", "XXV. ", "XXVI. ")):
        return "contents_entry"
    return "contents_entry"


def split_locator(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(\d{1,4})\.?$", raw)
    if not match:
        return raw, None, None
    locator = match.group(1)
    return raw[: match.start()].rstrip(), locator, int(locator)


def build_entries() -> list[dict[str, object]]:
    records: list[tuple[str, list[str], int, bool]] = []
    for number in (584, 585, 586):
        lines = read_body(number)
        if number == 585:
            # The top of the right-hand column contains only the terminal dash and
            # locator for the preceding numbered II entry; it is not an entry itself.
            index = lines.index("— 839")
            records.append((f"{lines[index - 1]} {lines[index]}", [lines[index - 1], lines[index]], number, True))
            lines = lines[: index - 1] + lines[index + 1 :]
        records.extend((line, [line], number, False) for line in lines)

    entries: list[dict[str, object]] = []
    for order, (raw, source_lines, number, dangling_locator) in enumerate(records, 1):
        target, page_raw, page_int = split_locator(raw)
        confidence = "medium" if dangling_locator or "su-" in raw or "Lugdunul" in raw else "high"
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
                "note_raw": "heading" if entry_kind(raw) == "contents_heading" else None,
                "normalized_target": target,
                "confidence": confidence,
                "raw_json": {
                    "source_files": [file_name(number)],
                    "source_lines": source_lines,
                    "entry_kind": entry_kind(raw),
                    "locator_system": "editorial_page" if page_raw is not None else None,
                    "ocr_uncertainty": (
                        "The OCR gives the end of this numbered item as `su-` followed by a standalone `— 839` at the top of the facing column; the literal components are preserved together without completing the word."
                        if dangling_locator
                        else None
                    ),
                },
            }
        )
    return entries


def main() -> None:
    workplan = _read_json(WORKPLAN)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    owned = [str(value) for value in chunk["physical_files"]]
    entries = build_entries()

    payload = {
        "schema_version": 2,
        "volume_id": "PL159",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": owned,
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            {
                "physical_left_file": file_name(583),
                "physical_right_file": file_name(584),
                "decision": "not_same_entry",
                "reason": "The overlap file ends with the complete CAP. 29 entry and its editorial locator 615; owned file 584 begins the independently numbered CAP. 30 entry.",
            },
            {
                "physical_left_file": file_name(584),
                "physical_right_file": file_name(585),
                "decision": "not_same_entry",
                "reason": "File 584 ends with the complete CAP. 149 row and its locator 686; file 585 begins the independently numbered CAP. 150 row.",
            },
            {
                "physical_left_file": file_name(585),
                "physical_right_file": file_name(586),
                "decision": "not_same_entry",
                "reason": "File 585 ends the independently numbered IX entry under EPISTOLÆ DIVERSORUM AD S. HUGONEM; file 586 begins the new numbered X entry, not a grammatical continuation.",
            },
            {
                "physical_left_file": file_name(586),
                "physical_right_file": file_name(587),
                "decision": "not_same_entry",
                "reason": "File 586 closes the contents table with `Index epistolarum sancti Anselmi. 1057` and the FINIS TOMI line; file 587 contains only the digitization footer.",
            },
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
                "file_start": file_name(584),
                "file_end": file_name(586),
                "confidence": "high",
                "raw_json": {
                    "source_files": owned,
                    "adjacent_context_files_checked": [str(value) for value in chunk["context_files"]],
                    "header_evidence": {
                        "file_583": "1149 QUÆ IN HOC TOMO CONTINENTUR. 1150",
                        "file_584": "1151 ORDO RERUM 1152",
                        "file_585": "1153 QUÆ IN HOC TOMO CONTINENTUR. 1154",
                        "file_586": "4155 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 4156",
                    },
                    "numbering_note": "The 4155/4156 header on file 586 is retained as OCR text and is consistent with CER for the local 1155/1156 sequence; it and every terminal numeral in the entries are editorial data, never physical-file identifiers.",
                    "extra_file_evidence": {
                        "files_checked": [file_name(number) for number in range(579, 591)],
                        "result": "Files 579–583 establish the continuing ORDO and the incoming CAP. 29/CAP. 30 boundary. Files 587–589 are footer, blank, or guard matter, and file 590 is a French Patrologie catalogue; none continues the contents table. No material target locator was run.",
                    },
                    "ocr_uncertainties": [
                        "The file-585 II entry ends in the literal `su-`; its isolated facing-column `— 839` locator was joined to that same entry without inventing the missing word.",
                        "OCR literals such as `slutos`, `æcharitate`, `Lugdunul`, `penis`, and `PISTOLA DUE` are retained without normalization.",
                    ],
                    "entries_status": "complete",
                    "entries_status_reason": "All 306 readable body lines in the three owned files are represented as 305 logical contents entries: the sole standalone `— 839` is the locator continuation of the immediately preceding II entry, not a separate item. No entry starts in context file 583 or continues into context file 587.",
                },
                "entries": entries,
            }
        ],
        "notes": [
            "Bounded extraction fragment only; this does not assemble the PL159 volume.",
            "The table has been transcribed in physical file order, with references retained as editorial locators.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

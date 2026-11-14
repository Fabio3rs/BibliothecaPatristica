#!/usr/bin/env python3
"""Build the bounded PL163 ORDO RERUM fragment for chunk 002:003."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


WORKPLAN = ROOT / "data/index_intermediate_payloads/PL163/workplan.json"
OUTPUT = ROOT / "data/index_intermediate_payloads/PL163/chunks/section_002_part_003.json"
SECTION_ID = "PL163:candidate-section:002"
SECTION_KEY = "PL163:section:002:ordo_rerum"
CHUNK_ID = "PL163:chunk:002:003"
FINGERPRINT = "b268e07608f8bc2e74b4661d3990d5f68ebf5795e9f5ef6c93d03a5ac523351e"
PREFIX = ROOT / "teste/PL163/text/b08fa0af-bf12-4e0d-8aaa-2875d3fa92f1-"


def file_name(number: int) -> str:
    return str(PREFIX) + f"{number}.txt"


def read_body(number: int) -> list[str]:
    """Use the required conservative reader, retaining its cleaned OCR literals."""
    result = subprocess.run(
        [
            "python",
            "scripts/read_ocr_page_text.py",
            "--view",
            "body",
            "--show-source",
            file_name(number),
        ],
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
    text = re.sub(r"^\d{1,4}\s+", "", raw)
    letters = [char for char in text if char.isalpha()]
    if letters and all(char == char.upper() for char in letters):
        return "contents_heading"
    return "contents_entry"


def split_locator(raw: str) -> tuple[str, str | None, int | None]:
    """Extract an editorial locator without treating it as a physical file id."""
    leading = re.match(r"^(\d{3,4})\s+(.+)$", raw)
    if leading:
        locator, target = leading.groups()
        return target, locator, int(locator)
    trailing = re.search(r"\s+(\d{1,4})\.?$", raw)
    if trailing:
        locator = trailing.group(1)
        return raw[: trailing.start()].rstrip(), locator, int(locator)
    return raw, None, None


def build_records() -> list[tuple[str, list[str], list[str], bool]]:
    records: list[tuple[str, list[str], list[str], bool]] = []
    page_755 = read_body(755)
    # This is the tail of CAP. XXV, which began on overlap file 754.
    for line in page_755[1:]:
        records.append((line, [file_name(755)], [line], False))

    for line in read_body(756):
        records.append((line, [file_name(756)], [line], False))

    page_757 = read_body(757)
    # CVII begins here and demonstrably continues with the first body line on 758.
    cvii = page_757[-1]
    page_758 = read_body(758)
    cvii_continuation = page_758.pop(0)
    # CLXXVI begins at the foot of 758 and continues at the head of 759.
    clxxvi = page_758.pop()
    page_759 = read_body(759)
    clxxvi_continuation = page_759.pop(0)
    records.extend(
        (line, [file_name(757)], [line], False) for line in page_757[:-1]
    )
    records.append(
        (
            cvii[:-1] + cvii_continuation,
            [file_name(757), file_name(758)],
            [cvii, cvii_continuation],
            True,
        )
    )

    # The two columns split CXL I after ``infestatione``; this is one entry.
    split_index = next(
        index for index, line in enumerate(page_758) if line.startswith("CXLI. ")
    )
    cxl_i = page_758[split_index]
    cxl_i_continuation = page_758[split_index + 1]
    for index, line in enumerate(page_758):
        if index == split_index:
            records.append(
                (
                    cxl_i + " " + cxl_i_continuation,
                    [file_name(758)],
                    [cxl_i, cxl_i_continuation],
                    True,
                )
            )
        elif index != split_index + 1:
            records.append((line, [file_name(758)], [line], False))
    records.append(
        (
            clxxvi[:-1] + clxxvi_continuation,
            [file_name(758), file_name(759)],
            [clxxvi, clxxvi_continuation],
            True,
        )
    )

    # PATROL CLXIII. and 48 are running/printed page matter.
    for line in page_759:
        if line in {"PATROL CLXIII.", "48"}:
            continue
        records.append((line, [file_name(759)], [line], False))
    return records


def build_entries() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for order, (raw, source_files, source_lines, joined) in enumerate(build_records(), 1):
        target, page_raw, page_int = split_locator(raw)
        kind = entry_kind(raw)
        ocr_uncertain = (
            "OCR literal preserved; this item has a page/column break proven by the following source line."
            if joined
            else None
        )
        entries.append(
            {
                "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
                "entry_order": order,
                "entry_raw": raw,
                "target_raw": target,
                "target_file": None,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": "col." if page_raw is not None else None,
                "note_raw": "heading" if kind == "contents_heading" else None,
                "normalized_target": target,
                "confidence": "medium" if joined or "..." in raw else "high",
                "raw_json": {
                    "source_files": source_files,
                    "source_lines": source_lines,
                    "entry_kind": kind,
                    "locator_system": "editorial_column" if page_raw is not None else None,
                    "ocr_uncertainty": ocr_uncertain,
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
        "volume_id": "PL163",
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
                "physical_left_file": file_name(754),
                "physical_right_file": file_name(755),
                "decision": "joined_entry",
                "reason": "CAP. XXV begins on overlap file 754 with `hor-` and continues on owned file 755 as `tis laborare`; ownership therefore remains with the prior chunk and this fragment emits no duplicate.",
            },
            {
                "physical_left_file": file_name(757),
                "physical_right_file": file_name(758),
                "decision": "joined_entry",
                "reason": "CVII begins on 757 as `Bri-` and the opening of 758 supplies `tamiae. — Gerardum... 1186`; the joined entry is emitted once with both physical sources.",
            },
            {
                "physical_left_file": file_name(758),
                "physical_right_file": file_name(759),
                "decision": "joined_entry",
                "reason": "CLXXVI begins on 758 as `ro-` and the opening of 759 supplies `gante Benedicto episcopo, corroborat. 1245`; the joined entry is emitted once with both physical sources.",
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
                "page_start": 1505,
                "page_end": 1514,
                "file_start": file_name(755),
                "file_end": file_name(759),
                "confidence": "high",
                "raw_json": {
                    "source_files": owned,
                    "adjacent_context_files_checked": [str(value) for value in chunk["context_files"]],
                    "section_kind": "volume_contents_table",
                    "header_evidence": {
                        "file_754": "1503 ORDO RERUM 1504",
                        "file_755": "4503 QUAE IN HOC TOMO CONTINENTUR. 4506",
                        "file_756": "4507 ORDO RERUM. 1508",
                        "file_757": "1509 QUÆ IN HOC TOMO CONTINENTUR. 1510",
                        "file_758": "1511 ORDO RERUM. 1512",
                        "file_759": "1513 QUÆ IN HOC TOMO CONTINENTUR. 1514",
                    },
                    "numbering_note": "The adjacent 1503/1504, 1507/1508, and 1509–1514 header sequence establishes that the 4503/4506 on file 755 is CER for 1505/1506. These are editorial pages; neither they nor entry locators identify physical OCR files.",
                    "extra_file_evidence": {
                        "files_checked": [file_name(number) for number in range(750, 764)],
                        "result": "Files 750–754 establish the continuing ORDO and the incoming CAP. XXV continuation. Files 760–763 establish the final table continuation/termination; no material target locator was run.",
                    },
                    "ocr_uncertainties": [
                        "OCR literals, including `Rot...`, `deuttero`, `fragriit`, and damaged words inside the Calixtus list, are preserved without speculative correction.",
                        "The standalone-looking first body lines of files 755, 758, and 759 are proven continuations and are not duplicated as new entries.",
                    ],
                    "entries_status": "complete",
                    "entries_status_reason": "All readable table lines beginning in owned files 755–759 are represented as logical entries. Three incoming continuations owned by files 754, 757, and 758 are excluded from duplicate emission; CVII and CLXXVI are each emitted by their owned left file with both sources.",
                },
                "entries": entries,
            }
        ],
        "notes": [
            "Bounded extraction fragment only; it does not assemble PL163.",
            "Entries preserve editorial page/column locators and physical source evidence separately.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the bounded PG012 closing ORDO RERUM fragment."""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


WORKPLAN_PATH = (
    PROJECT_ROOT / "data/index_intermediate_payloads/PG012/workplan.json"
)
OUTPUT_PATH = (
    PROJECT_ROOT
    / "data/index_intermediate_payloads/PG012/chunks/section_003_part_001.json"
)
CHUNK_ID = "PG012:chunk:003:001"
SECTION_ID = "PG012:candidate-section:003"
SECTION_KEY = "PG012:section:volume_end:ordo_rerum:001"
FINGERPRINT = "25698e74acd1c3d218f9f2ab8ec51b2c146c96c156afaea1bc0e1dd79ef88a10"

SOURCE_ROOT = PROJECT_ROOT / "teste/PG012/text"
FILES = {
    seq: SOURCE_ROOT / f"b1b3903f-d7e4-4e85-b584-58b718e0bdf8-{seq}.txt"
    for seq in range(850, 861)
}

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}


def clean_lines(text: str | None) -> list[str]:
    if not text:
        return []
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def blocks(path: Path, kind: str) -> list[list[str]]:
    root = ET.parse(path).getroot()
    return [
        clean_lines(block.text)
        for block in root.findall("bloco")
        if block.attrib.get("tipo") == kind
    ]


def split_reference(line: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(\d+)$", line)
    if not match:
        return line, None, None
    raw = match.group(1)
    return line[: match.start()].rstrip(), raw, int(raw)


def add_entry(
    entries: list[dict],
    *,
    text: str,
    source_file: Path,
    entry_kind: str,
    note: str | None = None,
    confidence: str = "medium",
    page_ref_raw: str | None = None,
    page_ref_int: int | None = None,
    raw_extra: dict | None = None,
) -> None:
    order = len(entries) + 1
    target = text
    entry_raw = text if page_ref_raw is None else f"{text} {page_ref_raw}"
    raw_json = {
        "source_files": [str(source_file)],
        "entry_kind": entry_kind,
    }
    if raw_extra:
        raw_json.update(raw_extra)
    entries.append(
        {
            "entry_key": f"{SECTION_ID}:{CHUNK_ID}:{order:03d}",
            "entry_order": order,
            "entry_raw": entry_raw,
            "target_raw": target,
            "target_file": None,
            "page_ref_raw": page_ref_raw,
            "page_ref_int": page_ref_int,
            "page_ref_col": None,
            "note_raw": note,
            "normalized_target": target,
            "confidence": confidence,
            "raw_json": raw_json,
        }
    )


def line_kind(target: str, page_ref_raw: str | None) -> tuple[str, str | None]:
    if page_ref_raw is not None:
        return "contents_entry", None
    heading_prefixes = (
        "SELECTA ",
        "HOMILIÆ ",
        "Homiliæ ",
        "EX COMMENTARIIS ",
        "Fragmentum homiliæ",
    )
    if target.startswith(heading_prefixes) or target in {"ORIGENES."}:
        return "contents_heading", "heading"
    return "contents_entry_without_visible_reference", "no visible reference in OCR"


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    entries: list[dict] = []

    add_entry(
        entries,
        text="ORDO RERUM",
        source_file=FILES[854],
        entry_kind="contents_heading",
        note="heading",
    )
    add_entry(
        entries,
        text="QUÆ IN HOC TOMO CONTINENTUR.",
        source_file=FILES[854],
        entry_kind="contents_heading",
        note="heading",
    )

    body_854 = blocks(FILES[854], "texto_principal")
    lines_854 = body_854[-2] + body_854[-1]
    join_left = "Homilia IV. — De decem plagis, quibus percussæ est"
    join_right = "Ægyptus. 317"
    left_index = lines_854.index(join_left)
    right_index = lines_854.index(join_right)
    lines_854[left_index] = f"{join_left} {join_right}"
    del lines_854[right_index]
    for line in lines_854:
        target, page_raw, page_int = split_reference(line)
        kind, note = line_kind(target, page_raw)
        add_entry(
            entries,
            text=target,
            source_file=FILES[854],
            entry_kind=kind,
            note=note,
            page_ref_raw=page_raw,
            page_ref_int=page_int,
            raw_extra=(
                {"within_scan_column_join": True}
                if target.startswith("Homilia IV. — De decem plagis")
                else None
            ),
        )

    body_855 = blocks(FILES[855], "texto_principal")
    duplicate = (
        "tur ad Jesum : Solve calceamentum de pedibus tuis. "
        "Locus enim in quo stas, terra sancta est. 852"
    )
    lines_855 = body_855[0] + [line for line in body_855[1] if line != duplicate]
    for line in lines_855:
        target, page_raw, page_int = split_reference(line)
        kind, note = line_kind(target, page_raw)
        raw_extra = None
        if target.startswith("Homilia VI. — De Pascha quod fecerunt"):
            raw_extra = {
                "discarded_duplicate_ocr_column_tail": duplicate,
                "duplicate_was_not_a_second_entry": True,
            }
        add_entry(
            entries,
            text=target,
            source_file=FILES[855],
            entry_kind=kind,
            note=note,
            page_ref_raw=page_raw,
            page_ref_int=page_int,
            raw_extra=raw_extra,
        )

    body_856 = blocks(FILES[856], "texto_principal")
    detached_reference_lists: list[list[str]] = []
    for block_number, block_lines in enumerate(body_856, start=1):
        labels = [line for line in block_lines if not re.fullmatch(r"\d+", line)]
        refs = [line for line in block_lines if re.fullmatch(r"\d+", line)]
        detached_reference_lists.append(refs)
        for line_number, label in enumerate(labels, start=1):
            add_entry(
                entries,
                text=label,
                source_file=FILES[856],
                entry_kind=(
                    "contents_heading"
                    if label == "EX COMMENTARIIS IN PSALMOS."
                    else "contents_entry_unaligned_reference"
                ),
                note=(
                    "heading"
                    if label == "EX COMMENTARIIS IN PSALMOS."
                    else "page-reference column is detached in OCR and cannot be aligned safely"
                ),
                confidence="low",
                raw_extra={
                    "ocr_block_number": block_number,
                    "label_order_within_block": line_number,
                    "page_reference_alignment": "unresolved_detached_ocr_column",
                },
            )

    add_entry(
        entries,
        text="FINIS TOMI DUODECIMI.",
        source_file=FILES[856],
        entry_kind="editorial_closure",
        note="editorial closure",
    )
    add_entry(
        entries,
        text="Paris. — Imprimerie J.-P. MIGNE.",
        source_file=FILES[856],
        entry_kind="printer_imprint",
        note="printer imprint",
    )

    physical_files = [str(FILES[seq]) for seq in range(854, 860)]
    context_files = [str(FILES[seq]) for seq in range(850, 861)]
    payload = {
        "schema_version": 2,
        "volume_id": "PG012",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": physical_files,
        "numbering_semantics": NUMBERING,
        "boundary_decisions": [
            {
                "physical_left_file": str(FILES[858]),
                "physical_right_file": str(FILES[859]),
                "decision": "not_same_entry",
                "reason": (
                    "File 858 is a guard leaf bearing the independent library notice "
                    "'THIS VOLUME / DOES NOT CIRCULATE / OUTSIDE THE LIBRARY'; file 859 "
                    "is another guard leaf bearing only the independent numeric library "
                    "identifier '3 2044 052 796 216'. Neither belongs to the ORDO RERUM, "
                    "and no editorial entry continues across this boundary."
                ),
            }
        ],
        "works": [],
        "sections": [
            {
                "section_key": SECTION_KEY,
                "work_key": None,
                "scope_kind": "volume_end",
                "index_kind": "ORDO RERUM",
                "heading_raw": "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": "Ordo rerum quæ in hoc tomo continentur",
                "page_start": 1703,
                "page_end": 1708,
                "file_start": str(FILES[854]),
                "file_end": str(FILES[856]),
                "confidence": "medium",
                "raw_json": {
                    "source_files": [str(FILES[seq]) for seq in range(854, 857)],
                    "adjacent_context_files": context_files,
                    "section_kind": "volume_contents_table",
                    "entries_status": "complete",
                    "editorial_pagination": {
                        "inferred_range": "1703-1708",
                        "method": (
                            "Local sequence: context file 853 reads 1701/1702, owned "
                            "file 855 reads 1705/1706, and owned file 856 reads "
                            "1707/1708; therefore file 854 is 1703/1704 despite its "
                            "corrupt OCR header."
                        ),
                        "raw_headers": [
                            {
                                "physical_file": str(FILES[853]),
                                "header_raw": "1701 INDEX ANALYTICUS. 1702",
                                "role": "adjacent_context",
                            },
                            {
                                "physical_file": str(FILES[854]),
                                "header_raw": "1763 ORDO RERUM. 174",
                                "inferred_editorial_pair": [1703, 1704],
                                "ocr_corrupt": True,
                            },
                            {
                                "physical_file": str(FILES[855]),
                                "header_raw": "1705 QUÆ IN HOC TOMO CONTINENTUR. 1706",
                                "inferred_editorial_pair": [1705, 1706],
                            },
                            {
                                "physical_file": str(FILES[856]),
                                "header_raw": "1707 ORDO RERUM 1708",
                                "inferred_editorial_pair": [1707, 1708],
                            },
                        ],
                    },
                    "detached_reference_columns": [
                        {
                            "physical_file": str(FILES[856]),
                            "ocr_block_number": index + 1,
                            "reference_literals": refs,
                            "decision": (
                                "Preserved verbatim but not assigned to title entries "
                                "because OCR flattened the title and number columns "
                                "separately and omitted or corrupted enough numbers to "
                                "make positional alignment unsafe."
                            ),
                        }
                        for index, refs in enumerate(detached_reference_lists)
                    ],
                    "extra_file_evidence": {
                        "checked_files": context_files,
                        "findings": [
                            (
                                "Files 850-853 are the preceding INDEX ANALYTICUS; its "
                                "last entry begins in file 853 and continues with "
                                "'et veritas, 217.' at the start of file 854, so that "
                                "continuation and the remaining alphabetical entries "
                                "before the ORDO heading are outside this section."
                            ),
                            (
                                "File 857 has only a Google digitization footer, files "
                                "858-859 are independent guard/library marks, and context "
                                "file 860 is empty; the ORDO therefore ends in file 856."
                            ),
                        ],
                    },
                    "evidence": [
                        "ORDO RERUM",
                        "QUÆ IN HOC TOMO CONTINENTUR.",
                        "ORIGENES.",
                        "FINIS TOMI DUODECIMI.",
                    ],
                    "notes": [
                        "Structural headings are retained as entries for line-level coverage.",
                        (
                            "File 856's two title lists contain respectively 79 and 82 "
                            "labels, while their detached numeric lists contain only 73 "
                            "and 77 readable literals. The reference literals remain in "
                            "raw_json without speculative title assignment."
                        ),
                        (
                            "The repeated right-column tail ending in page reference 852 "
                            "on file 855 is OCR duplication of the preceding Homilia VI "
                            "entry and is not emitted twice."
                        ),
                    ],
                },
                "entries": entries,
            }
        ],
        "notes": [
            (
                "This fragment covers only the closing ORDO RERUM beginning mid-scan "
                "on physical file 854 and ending with the volume closure on file 856."
            ),
            (
                "The preceding INDEX ANALYTICUS material on file 854 is outside the "
                "general works-index pipeline and is not re-emitted."
            ),
            (
                "Files 857-859 are blank/guard matter and contribute no ORDO entries; "
                "the mandatory 858→859 boundary is recorded as not_same_entry."
            ),
        ],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _validate_fragment(
        OUTPUT_PATH,
        workplan,
        chunk,
        require_input_fingerprint=True,
    )
    print(f"wrote {OUTPUT_PATH} with {len(entries)} entries")


if __name__ == "__main__":
    main()

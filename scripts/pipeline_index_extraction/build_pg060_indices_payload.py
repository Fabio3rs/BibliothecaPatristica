#!/usr/bin/env python3
"""Assemble the reviewed PG060 opening-index payload from stable fragments."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "teste/PG060/text"
ASSEMBLED = ROOT / "data/index_intermediate_payloads/PG060/assembled_fragments.json"
OUTPUT = ROOT / "data/index_payloads/PG060_indices.json"


def file_for(suffix: int) -> str:
    matches = list(SOURCE_ROOT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one PG060 OCR file for suffix {suffix}: {matches}")
    return str(matches[0])


def by_key(items: list[dict], key: str, value: str) -> dict:
    for item in items:
        if item.get(key) == value:
            return deepcopy(item)
    raise KeyError(f"Missing stable fragment {key}={value}")


def target_entries(section: dict, mappings: dict[int, str]) -> None:
    for entry in section["entries"]:
        target = mappings.get(entry["entry_order"])
        if target:
            entry["target_file"] = target
            entry.setdefault("raw_json", {})["target_file_evidence"] = "direct OCR opening/boundary inspection"


def main() -> None:
    assembled = json.loads(ASSEMBLED.read_text(encoding="utf-8"))["data"]

    title_pages = by_key(assembled["sections"], "section_key", "PG060:section:volume_front:title_pages:001")
    elenchus = by_key(assembled["sections"], "section_key", "PG060:section:volume_front:elenchus:002")
    ordo = by_key(assembled["sections"], "section_key", "PG060:section:volume_end:ordo_rerum:003")

    # The fragment is the authoritative transcription.  Only direct OCR target anchors
    # below are added; no file is inferred from an editorial number alone.
    target_entries(
        elenchus,
        {1: file_for(17), 2: file_for(388), 3: file_for(682)},
    )
    target_entries(
        ordo,
        {
            3: file_for(11),
            4: file_for(13),
            5: file_for(15),
            6: file_for(17),
            62: file_for(388),
            64: file_for(392),
            98: file_for(682),
        },
    )

    tome = by_key(assembled["works"], "work_key", "PG060:work:joannes_chrysostomus_opera_omnia_tomus_nonus")
    acts = by_key(assembled["works"], "work_key", "PG060:work:homiliae_in_acta_apostolorum")
    romans = by_key(assembled["works"], "work_key", "PG060:work:homiliae_in_epistolam_ad_romanos")
    spuria = by_key(assembled["works"], "work_key", "PG060:work:spuria")

    tome["raw_json"]["anchor_status"] = "volume_title_container; no separate closing boundary asserted"

    acts.update(
        {
            "start_page": 13,
            "end_page": 384,
            "start_file": file_for(17),
            "end_file": file_for(387),
            "confidence": "high",
        }
    )
    acts["raw_json"].pop("anchor_status", None)
    acts["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "declared_start_page": 13,
        "opening_evidence": [
            f"{file_for(17)}: logical header `13 IN ACTA APOSTOLORUM HOMIL. I. 14`, followed by `COMMENTARIUS IN ACTA APOSTOLORUM` and `HOMILIA I`.",
            f"{file_for(16)}: adjoining Greek presentation of the same commentary and Homily I; it does not displace the declared editorial page 13 carried by file 017.",
        ],
        "rejected_candidates": [
            f"{file_for(11)}: `Præfatio AD HOMILIAS IN ACTA APOSTOLORUM`, a real prefatory opening at editorial pages 3-10, not the declared col. 13 work start.",
            f"{file_for(15)}: `INDEX OPERUM ...`, an inventory page rather than the work target.",
        ],
        "closing_evidence": [
            f"{file_for(386)}: header `383 ... 384` continues HOMILIA LV.",
            f"{file_for(387)}: Greek continuation of HOMILIA LV reaches its explicit textual close; {file_for(388)} begins the Romanos preface.",
        ],
    }

    romans.update(
        {
            "start_page": 385,
            "end_page": 682,
            "start_file": file_for(388),
            "end_file": file_for(682),
            "confidence": "high",
        }
    )
    romans["raw_json"].pop("anchor_status", None)
    romans["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "opening_evidence": f"{file_for(388)} has `PRÆFATIO AD HOMILIAS IN EPISTOLAM AD ROMANOS`; {file_for(392)} then has `COMMENTARIUS IN EPISTOLAM AD ROMANOS`.",
        "closing_evidence": f"The closing table gives HOM. XXXII `675-682`; {file_for(682)} contains its Greek conclusion before the new `SPURIA` heading, so this physical file is shared across the boundary.",
    }

    spuria.update(
        {
            "start_page": 681,
            "end_page": 776,
            "start_file": file_for(682),
            "end_file": file_for(777),
            "confidence": "high",
        }
    )
    spuria["raw_json"].pop("anchor_status", None)
    spuria["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "opening_evidence": f"{file_for(682)} has the direct logical header `681 SPURIA. 682`, `SPURIA.`, and the first Greek spurious text after the Romanos conclusion.",
        "closing_evidence": f"{file_for(777)} is headed `775 DE CARITATE. 776` and finishes the last listed Sermo de caritate; {file_for(778)} begins the separate ORDO RERUM table.",
    }

    payload = {
        "volume": {
            "volume_id": "PG060",
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Graeca LX",
            "notes": [
                "Opening title pages, ELENCHUS, and the complete closing ORDO RERUM were transcribed from the validated chunks.",
                "OCR file suffixes remain physical locators only; all cited numerals are preserved as editorial references.",
                "The final ORDO RERUM is a volume contents table, not an alphabetical or analytical index.",
            ],
        },
        "works": [tome, acts, romans, spuria],
        "sections": [title_pages, elenchus, ordo],
        "notes": [
            "All stable works, sections, and 143 entries from assembled_fragments.json are retained.",
            "The prior PG060 payload lost the validated title-page section and 128 ORDO RERUM entries; this payload restores them.",
            "The former pending Acta anchor review is resolved by direct opening and boundary evidence; no work_anchor_rerun marker remains.",
            "Literal OCR variants in the ORDO RERUM, including truncated references such as 185-2, 221-23, 233-24, and 239-24, are preserved.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

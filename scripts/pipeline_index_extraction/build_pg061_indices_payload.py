#!/usr/bin/env python3
"""Rebuild the PG061 general-index payload from validated chunks and OCR anchors."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG061"
SOURCE_ROOT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_for(suffix: int) -> str:
    matches = list(SOURCE_ROOT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one OCR file for suffix {suffix}: {matches}")
    return str(matches[0])


def section_by_key(sections: list[dict], section_key: str) -> dict:
    for section in sections:
        if section["section_key"] == section_key:
            return deepcopy(section)
    raise KeyError(section_key)


def work_by_key(works: list[dict], work_key: str) -> dict:
    for work in works:
        if work["work_key"] == work_key:
            return deepcopy(work)
    raise KeyError(work_key)


def ensure_entry_fields(section: dict) -> None:
    for entry in section["entries"]:
        for key in (
            "target_file",
            "page_ref_raw",
            "page_ref_int",
            "page_ref_col",
            "note_raw",
            "normalized_target",
        ):
            entry.setdefault(key, None)
        entry.setdefault("raw_json", {})


def add_target(section: dict, entry_order: int, target_file: str, evidence: str) -> None:
    for entry in section["entries"]:
        if entry["entry_order"] == entry_order:
            entry["target_file"] = target_file
            entry.setdefault("raw_json", {})["target_file_evidence"] = evidence
            return
    raise KeyError(f"entry {entry_order} not found in {section['section_key']}")


def add_target_by_prefix(section: dict, prefix: str, target_file: str, evidence: str) -> None:
    matches = [entry for entry in section["entries"] if entry.get("target_raw", "").startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"expected one '{prefix}' entry, found {len(matches)}")
    matches[0]["target_file"] = target_file
    matches[0].setdefault("raw_json", {})["target_file_evidence"] = evidence


def main() -> None:
    assembled = read_json(ASSEMBLED)
    if assembled.get("status") != "complete" or assembled.get("consumed_chunk_count") != assembled.get("expected_chunk_count"):
        raise ValueError("PG061 assembled fragments are not a complete validated checkpoint")
    stable = assembled["data"]
    if len(stable["works"]) != 8 or len(stable["sections"]) != 3:
        raise ValueError("unexpected stable PG061 object count")

    w1_key = "PG061:work:homiliae_xliv_in_epistolam_primam_ad_corinthios"
    w2_key = "PG061:work:homiliae_xxx_in_epistolam_secundam_ad_corinthios"
    w3_key = "PG061:work:commentarius_in_epistolam_ad_galatas"
    w4_key = "PG061:work:spuria"

    front = section_by_key(stable["sections"], "PG061:section:volume_front:elenchus:001")
    preface = section_by_key(stable["sections"], "PG061:section:work_front:homiliae_ad_corinthios:praefatio:001")
    closing = section_by_key(stable["sections"], "PG061:section:volume_front:ordo_rerum:002")
    if [len(front["entries"]), len(preface["entries"]), len(closing["entries"])] != [4, 14, 129]:
        raise ValueError("validated PG061 entries were not fully available")

    for section in (front, preface, closing):
        ensure_entry_fields(section)
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    front["work_key"] = None
    add_target(front, 1, file_for(13), "direct title page: S. JOANNIS CHRYSOSTOMI and the first Corinthios argument at editorial 9-10")
    add_target(front, 2, file_for(387), "direct running header: 381 IN EPIST. II. AD COR. HOMIL. I. 382")
    add_target(front, 3, file_for(616), "direct title: IN EPISTOLAM AD GALATAS COMMENTARIUS (a), with 611/612 headers")
    add_target(front, 4, file_for(686), "direct logical header: 681 SPURIA. 682")

    preface["work_key"] = w1_key
    add_target(preface, 5, file_for(9), "direct preface heading")

    closing["section_key"] = "PG061:section:volume_end:ordo_rerum:002"
    closing["scope_kind"] = "volume_end"
    closing["work_key"] = None
    closing["raw_json"]["scope_note"] = "Closing volume contents table, introduced after the final spuria body text in physical file 807."
    closing["raw_json"]["heading_page_note"] = "The OCR literals are 801 ORDO RERUM. 802 and 803 ORDO RERUM. 804; they are editorial headers, not physical file suffixes."
    add_target(closing, 1, file_for(9), "direct preface heading")
    add_target(closing, 2, file_for(13), "direct work title and opening argument")
    add_target(closing, 3, file_for(12), "direct Greek argumentum presentation")
    add_target(closing, 4, file_for(14), "direct HOMILIA I header at editorial 11-12")
    add_target_by_prefix(closing, "S. JOANNIS CHRYSOSTOMI Homiliæ XXX in secundam", file_for(387), "direct IN EPIST. II. AD COR. HOMIL. I header")
    add_target_by_prefix(closing, "S. JOANNIS CHRYSOSTOMI in Epistolam ad Galatas", file_for(616), "direct Galatas title page")
    add_target_by_prefix(closing, "SPURIA.", file_for(686), "direct SPURIA header")
    add_target_by_prefix(closing, "MONITUM in sequentia", file_for(686), "spuria opening page and immediate continuation")
    add_target_by_prefix(closing, "CARITATEM secundum Deum", file_for(687), "direct CARITATEM SECUNDUM DEUM heading")
    add_target_by_prefix(closing, "Contra Judæos, in serpentem", file_for(799), "recurring direct header sequence begins at this file")

    works = [
        work_by_key(stable["works"], w1_key),
        work_by_key(stable["works"], w2_key),
        work_by_key(stable["works"], w3_key),
        work_by_key(stable["works"], w4_key),
    ]
    anchor_data = {
        w1_key: (9, 382, 13, 386, ["The direct title page is file 013 with editorial pair 9-10.", "File 386 carries the 381/382 terminal body; file 387 begins IN EPIST. II. AD COR. HOMIL. I."]),
        w2_key: (381, 610, 387, 615, ["File 387 directly begins IN EPIST. II. AD COR. HOMIL. I. at editorial 381/382.", "File 615 has 609 IN EPIST. II AD COR. HOMIL. XXX. 610; file 616 begins the Galatas title page."]),
        w3_key: (611, 680, 616, 685, ["File 616 has the explicit title IN EPISTOLAM AD GALATAS COMMENTARIUS (a), with 611/612 headers.", "Files 684-685 carry the final 679/680 Galatas commentary headers; file 686 begins 681 SPURIA. 682."]),
        w4_key: (681, 812, 686, 807, ["File 686 has the explicit 681 SPURIA. 682 opening header.", "The Spuria body continues into file 807 and ends immediately before the ORDO RERUM heading; the closing contents entry preserves its declared range 791-812 and FINIS TOMI follows in file 808."]),
    }
    merged_keys = {
        w1_key: "PG061:work:homiliae_i_corinthios:001",
        w2_key: "PG061:work:homiliae_ii_corinthios:002",
        w3_key: "PG061:work:commentarius_galatas:003",
        w4_key: "PG061:work:spuria:004",
    }
    for work in works:
        start_page, end_page, start_suffix, end_suffix, evidence = anchor_data[work["work_key"]]
        work.update({
            "start_page": start_page,
            "end_page": end_page,
            "start_file": file_for(start_suffix),
            "end_file": file_for(end_suffix),
            "source_section_key": front["section_key"],
            "confidence": "high",
        })
        raw = work.setdefault("raw_json", {})
        raw["assembled_fragments_consumed"] = True
        raw["merged_duplicate_validated_work_key"] = merged_keys[work["work_key"]]
        raw["anchor_resolution"] = {"status": "resolved", "evidence": evidence}
        raw.pop("work_anchor_rerun", None)

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Graeca Tomus LXI",
            "notes": [
                "The opening ELENCHUS, the first-work preface, and the complete closing ORDO RERUM were consumed from the validated chunk assembly.",
                "OCR file suffixes are physical locators only; cited numerals remain editorial references.",
            ],
            "scan_summary": {"front_index_file": 8, "final_index_files": [807, 808], "discovered_works": 4},
        },
        "works": works,
        "sections": [front, preface, closing],
        "notes": [
            "Consumed all eight validated work objects by semantically merging the four duplicate closing-ORDO representations into the four canonical works; all three validated sections and all 147 validated entries are retained.",
            "The closing ORDO RERUM is a volume-end contents table, not an alphabetical or analytical index.",
            "The Galatas work-anchor rerun is resolved from direct opening and terminal running-header evidence; no work_anchor_rerun marker remains.",
            "OCR literals and uncertain references in the 129-entry closing ORDO RERUM are preserved from the validated fragment.",
        ],
    }

    work_keys = {work["work_key"] for work in works}
    for section in payload["sections"]:
        if section.get("work_key") is not None and section["work_key"] not in work_keys:
            raise ValueError(f"unresolved section work key: {section['section_key']}")
    entries = [entry for section in payload["sections"] for entry in section["entries"]]
    entry_keys = [entry["entry_key"] for entry in entries]
    if len(entries) != 147 or len(entry_keys) != len(set(entry_keys)):
        raise ValueError("PG061 stable entry set was not retained exactly once")
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild PL116-1 general-index payload from validated fragments and OCR review."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PL116-1"
PAYLOAD_PATH = ROOT / "data/index_payloads/PL116-1_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PL116-1/assembled_fragments.json"
TEXT_ROOT = ROOT / "teste/PL116-1/text"


def file(seq: int) -> str:
    matches = sorted(TEXT_ROOT.glob(f"*-{seq:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected one PL116-1 OCR file for physical sequence {seq:03d}")
    return str(matches[0])


def note_anchor(work: dict, text: str) -> None:
    work.setdefault("raw_json", {}).setdefault("agent_anchor_checks", []).append(text)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled = json.loads(FRAGMENTS_PATH.read_text(encoding="utf-8"))
    if assembled["volume_id"] != VOLUME or assembled["status"] != "complete":
        raise ValueError("PL116-1 assembled fragments are not complete")
    if assembled["consumed_chunk_count"] != assembled["expected_chunk_count"]:
        raise ValueError("PL116-1 fragments omit a completed chunk")

    stable = assembled["data"]
    if len(stable["works"]) != 0 or len(stable["sections"]) != 2:
        raise ValueError("Unexpected PL116-1 validated-fragment structure")
    if sum(len(section["entries"]) for section in stable["sections"]) != 308:
        raise ValueError("PL116-1 validated entry count must be 308")

    works = {work["work_key"]: work for work in payload["works"]}
    expected_work_keys = {
        "ebbo_rhemensis",
        "hartmannus_s_galli",
        "ermanricus_augiensis",
        "erchambertus_frisingensis",
        "nithardus_s_richarii",
        "amulo_lugdunensis",
        "haymo_halberstatensis",
    }
    if set(works) != expected_work_keys:
        raise ValueError("PL116-1 work records unexpectedly changed")

    front_key = stable["sections"][0]["section_key"]
    for work in works.values():
        work["source_section_key"] = front_key

    # Each boundary below was established from an explicit local header/opening or a directly
    # observed change of work inside the same physical capture; no editorial page is inferred
    # from the OCR filename suffix.
    ebbo = works["ebbo_rhemensis"]
    ebbo["end_file"] = file(15)
    note_anchor(
        ebbo,
        "Physical 015 begins with Ebbo's poem II, then changes within the same capture to the explicit Hartmannus heading and CARMINA HARTMANNI; the Ebbo material therefore ends in physical 015.",
    )

    ermanricus = works["ermanricus_augiensis"]
    ermanricus["end_page"] = 30
    ermanricus["end_file"] = file(19)
    note_anchor(
        ermanricus,
        "Physical 018 has the direct 29 DE GRAMMATICA 30 header; physical 019 continues the Ermanricus material before the explicit Erchambertus title block. The final physical capture is shared with the following work.",
    )

    erchambertus = works["erchambertus_frisingensis"]
    erchambertus["start_file"] = file(19)
    erchambertus["end_file"] = file(20)
    erchambertus["raw_json"].pop("work_anchor_rerun", None)
    note_anchor(
        erchambertus,
        "Resolved review: physical 019 contains ERCHAMBERTUS / FRISINGENSIS EPISCOPUS / NOTITIA HISTORICA IN ERCHAMBERTUM, followed by ERCHAMBERTI and the Bartholomæus epistle incipit. Physical 020 retains the 33 FRAGMENTUM 34 header and the explicit ERCHAM BERTI FRAGMENTUM / Ex Breviario regum Francorum, et majorumdomus; physical 021 begins Nithardus.",
    )
    erchambertus["confidence"] = "high"

    haymo = works["haymo_halberstatensis"]
    haymo["end_page"] = 1084
    haymo["end_file"] = file(542)
    note_anchor(
        haymo,
        "Physical 541 bears the direct 1083 … 1084 HAYMONIS … COMMENT. IN ISAIAH header; physical 542 retains the end of that commentary above the new ORDO RERUM table. The final capture is shared with the closing table, so the local Haymo body is retained as ending there.",
    )

    # Reuse every validated section and every line item.  The front targets below were separately
    # located by direct headings/incipits; author-heading rows intentionally retain null targets.
    sections = copy.deepcopy(stable["sections"])
    front = sections[0]
    target_by_order = {
        2: file(8),
        3: file(9),
        4: file(12),
        5: file(14),
        7: file(15),
        9: file(16),
        11: file(19),
        12: file(20),
        14: file(26),
        16: file(42),
        17: file(52),
        18: file(56),
        19: file(74),
        21: file(99),
        22: file(99),
        23: file(347),
        24: file(357),
    }
    for entry in front["entries"]:
        order = entry["entry_order"]
        if order in target_by_order:
            entry["target_file"] = target_by_order[order]
            entry.setdefault("raw_json", {})["target_locator_evidence"] = "Direct local OCR title, heading, or incipit review; the cited number remains an editorial column reference."

    for section in sections:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    payload["sections"] = sections
    payload["volume"]["scan_summary"]["front_index_pages"] = [None]
    payload["volume"]["scan_summary"]["end_index_pages"] = ["1085-1086", "1087-1088"]
    payload["volume"]["scan_summary"]["work_blocks"] = [
        {"author_raw": work["author_raw"], "start_page": work["start_page"], "end_page": work["end_page"]}
        for work in sorted(works.values(), key=lambda item: item["work_order"])
    ]
    payload["volume"]["notes"] = [
        "Front inventory confirmed in physical OCR file 007: ELENCHUS / AUCTORUM ET OPERUM QUÆ IN HOC TOMO CXVI CONTINENTUR. Its printed page is not recoverable from the OCR header.",
        "Closing ORDO RERUM confirmed in physical files 542–543: the printed heading on 542 is 1085 ORDO RERUM 1086; 543 is the contiguous 1087–1088 continuation despite CER reading 4087/4088.",
        "No separate work-front chapter table was found; the closing ORDO RERUM is retained as the complete volume-end contents table.",
    ]
    payload["notes"] = [
        "Consumed all stable objects from assembled_fragments.json: 2 sections and 308 line-by-line entries.",
        "Resolved Erchambertus's pending work-anchor review from direct OCR on physical files 019–020.",
        "Front-matter targets were mapped only where direct local headings or incipits identify the body file; closing-table entries preserve their validated OCR literals and uncertainty.",
    ]

    work_keys = set(works)
    for section in payload["sections"]:
        if section["work_key"] is not None and section["work_key"] not in work_keys:
            raise ValueError(f"Dangling work key: {section['work_key']}")
        for entry in section["entries"]:
            target = entry.get("target_file")
            if target is not None and not Path(target).is_file():
                raise ValueError(f"Missing target locator: {target}")
    for work in works.values():
        if work["start_page"] is not None and work["end_page"] is not None and work["start_page"] > work["end_page"]:
            raise ValueError(f"Invalid printed range for {work['work_key']}")
        for field in ("start_file", "end_file"):
            if work[field] is not None and not Path(work[field]).is_file():
                raise ValueError(f"Missing work locator: {work[field]}")

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild PG055 from its validated opening-index chunk assembly.

The chunk runner's later retry cannot initialise its app-server in this
sandbox.  The complete assembled-fragments checkpoint is therefore the
authoritative transcription state; this assembler only makes OCR-verified
anchor and taxonomy repairs.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG055"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def source_file(name: str) -> str:
    path = TEXT / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return str(path)


FILES = {
    "title": source_file("1c4d70fe-de3e-43d0-81a9-489c636f169c-015.txt"),
    "elenchus": source_file("1c4d70fe-de3e-43d0-81a9-489c636f169c-016.txt"),
    "preface": source_file("1c4d70fe-de3e-43d0-81a9-489c636f169c-017.txt"),
    "collectio": source_file("1c4d70fe-de3e-43d0-81a9-489c636f169c-033.txt"),
    "expositio_start": source_file("1c4d70fe-de3e-43d0-81a9-489c636f169c-034.txt"),
    "expositio_end": source_file("57a00fb4-0daf-4675-a1bd-a0a82f799774-552.txt"),
    "dubia_start": source_file("57a00fb4-0daf-4675-a1bd-a0a82f799774-552.txt"),
    "dubia_end": source_file("7016f8bb-fd8a-48c0-b9f3-c242ecf331f8-557.txt"),
    "spuria_greek_opening": source_file("7016f8bb-fd8a-48c0-b9f3-c242ecf331f8-558.txt"),
    "spuria_start": source_file("7016f8bb-fd8a-48c0-b9f3-c242ecf331f8-559.txt"),
    "spuria_end": source_file("6d334885-8c52-426b-af78-026df431bf20-817.txt"),
    "ordo_start": source_file("6d334885-8c52-426b-af78-026df431bf20-818.txt"),
    "ordo_end": source_file("6d334885-8c52-426b-af78-026df431bf20-820.txt"),
}


def heading_norm(value: str) -> str:
    return re.sub(r"[.\s]+$", "", re.sub(r"\s+", " ", value).strip())


def append_anchor_evidence(entry: dict, evidence: str) -> None:
    raw = entry.setdefault("raw_json", {})
    raw["target_anchor_evidence"] = evidence


def build() -> dict:
    assembled = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled["status"] != "complete" or assembled["consumed_chunk_count"] != 4:
        raise ValueError("PG055 assembled fragments are not a complete four-chunk checkpoint")

    stable = assembled["data"]
    sections = copy.deepcopy(stable["sections"])
    if len(sections) != 4 or sum(len(section["entries"]) for section in sections) != 144:
        raise ValueError("unexpected stable PG055 section/entry counts")

    by_key = {section["section_key"]: section for section in sections}
    title_pages = by_key["PG055:section:volume_front:title_pages:001"]
    title_material = by_key["PG055:section:front:title_material:001"]
    front = by_key["PG055:section:volume_contents:tome_lv:elenchus_operum"]
    ordo = by_key["PG055:candidate-section:004"]

    # The title-page structures are validated fragment objects and remain in
    # the final payload, but PG/PL taxonomy calls them volume-front matter.
    for section in (title_pages, title_material):
        section["scope_kind"] = "volume_front"
        section["heading_norm"] = heading_norm(section["heading_raw"])
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    front["scope_kind"] = "volume_front"
    front["index_kind"] = "ELENCHUS"
    front["heading_norm"] = heading_norm(front["heading_raw"])
    front.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    front["raw_json"]["taxonomy_note"] = "The ELENCHUS is the volume-front inventory; its page is not reliably printed in OCR."

    # Direct titles and matching editorial headers resolve the prior anchors.
    front_entries = front["entries"]
    front_entries[0]["target_file"] = FILES["expositio_start"]
    append_anchor_evidence(front_entries[0], "File 034 directly opens IN PSALMUM III and is locally bounded as the printed 35-36 pair; file suffix was not used as pagination.")
    front_entries[1]["target_file"] = FILES["dubia_start"]
    append_anchor_evidence(front_entries[1], "File 552 has header 527 ... 528 and directly begins DUBIA OPUSCULA after the preceding Expositio text.")
    front_entries[2]["target_file"] = FILES["spuria_start"]
    append_anchor_evidence(front_entries[2], "File 559 has header 533 ARGUMENTUM PSALMORUM 534 and directly begins the Latin SPURIA block; file 558 is the parallel Greek SPURIA opening with its distinct 583-584 header.")

    ordo["scope_kind"] = "volume_end"
    ordo["index_kind"] = "ORDO RERUM"
    ordo["heading_norm"] = heading_norm(ordo["heading_raw"])
    ordo.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    ordo["raw_json"]["taxonomy_note"] = "The closing ORDO RERUM is a volume-end contents table, not an alphabetical/analytic index."
    # These three pointers have direct title/incipit support.  The other
    # validated entries retain null target_file rather than being guessed from
    # their printed ranges alone.
    for entry in ordo["entries"]:
        raw_target = entry["target_raw"].strip()
        if raw_target == "PRÆFATIO.":
            entry["target_file"] = FILES["preface"]
            append_anchor_evidence(entry, "File 017 directly bears PRÆFATIO / IN TOMUM QUINTUM.")
        elif raw_target.startswith("COLLECTIO Psalmorum"):
            entry["target_file"] = FILES["collectio"]
            append_anchor_evidence(entry, "File 033 directly bears Collectio (a) and its Psalmorum incipit.")
        elif raw_target.startswith("EXPOSITIO in Psalmum III"):
            entry["target_file"] = FILES["expositio_start"]
            append_anchor_evidence(entry, "File 034 directly begins IN PSALMUM III (a).")

    works = copy.deepcopy(stable["works"])
    if len(works) != 1:
        raise ValueError("unexpected stable PG055 work count")
    works[0]["work_key"] = "pg055-joannes-chrysostomus-opera-omnia-tomus-quintus"
    works[0]["work_order"] = 1
    works[0]["source_section_key"] = title_material["section_key"]
    works[0].setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    works.extend([
        {
            "work_key": "pg055-expositio-in-psalmos",
            "work_order": 2,
            "author_raw": "Ioannes Chrysostomus",
            "title_raw": "Expositio in Psalmos",
            "title_norm": "Expositio in Psalmos",
            "start_page": 35,
            "end_page": 526,
            "start_file": FILES["expositio_start"],
            "end_file": FILES["expositio_end"],
            "source_section_key": front["section_key"],
            "confidence": "high",
            "raw_json": {
                "evidence": [
                    "ELENCHUS: Expositio in Psalmos. col. 35.",
                    "File 034 directly opens IN PSALMUM III (a), with the same Chrysostom author heading; local estimator bounds it as 35-36.",
                    "File 552 contains the conclusion of the preceding exposition before the direct DUBIA OPUSCULA heading; file 551 has the 525-526 running header.",
                ],
                "anchor_review_resolution": "The former candidate at file 037 is only a later running-header occurrence. File 034 has direct opening text and independent local pagination evidence, so work_anchor_rerun is removed.",
            },
        },
        {
            "work_key": "pg055-dubia-opuscula",
            "work_order": 3,
            "author_raw": "Ioannes Chrysostomus",
            "title_raw": "Dubia Opuscula",
            "title_norm": "Dubia Opuscula",
            "start_page": 527,
            "end_page": 532,
            "start_file": FILES["dubia_start"],
            "end_file": FILES["dubia_end"],
            "source_section_key": front["section_key"],
            "confidence": "high",
            "raw_json": {
                "evidence": [
                    "ELENCHUS: Dubia. In psalmum L. 527.",
                    "File 552 has facing header 527 ... 528 and direct heading DUBIA OPUSCULA; its Greek companion file 553 repeats the direct heading with the same header pair.",
                    "File 557 is still the final PROOEMIA IN PSALMOS material at 531-532; the following inspected files 558-559 directly introduce SPURIA.",
                ],
                "anchor_review_resolution": "The prior tie between 552 and 553 is resolved for the canonical locator by the Latin opening on file 552, which matches the ELENCHUS's Latin declaration and has the 527-528 header. The parallel Greek opening is retained in evidence.",
            },
        },
        {
            "work_key": "pg055-spuria",
            "work_order": 4,
            "author_raw": "Ioannes Chrysostomus",
            "title_raw": "Spuria",
            "title_norm": "Spuria",
            "start_page": 533,
            "end_page": 784,
            "start_file": FILES["spuria_start"],
            "end_file": FILES["spuria_end"],
            "source_section_key": front["section_key"],
            "confidence": "high",
            "raw_json": {
                "evidence": [
                    "ELENCHUS: Spuria. In Psalmos. 533.",
                    "File 559 has header 533 ARGUMENTUM PSALMORUM 534 and directly begins the Latin SPURIA material.",
                    "File 558 is the corresponding Greek SPURIA opening, but bears its own 583-584 header; it is preserved as parallel opening evidence rather than substituted for the ELENCHUS's declared 533.",
                    "File 817 has the 783-784 pair; file 818 immediately begins the closing ORDO RERUM at 785-786.",
                ],
                "parallel_opening_file": FILES["spuria_greek_opening"],
            },
        },
    ])

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(TEXT),
            "volume_label": "Patrologia Graeca LV",
            "scan_summary": {
                "assembled_fragments": {
                    "path": str(ASSEMBLED),
                    "expected_chunk_count": assembled["expected_chunk_count"],
                    "consumed_chunk_count": assembled["consumed_chunk_count"],
                    "stable_entry_count": 144,
                },
                "front_index_file": FILES["elenchus"],
                "closing_contents_files": [FILES["ordo_start"], FILES["ordo_end"]],
            },
            "notes": [
                "The volume-front ELENCHUS is attested in file 016.",
                "The closing ORDO RERUM spans printed headers 785-790 in files 818-820.",
                "Printed ranges and physical OCR files are kept distinct; no file suffix is used as editorial pagination.",
            ],
        },
        "works": works,
        "sections": sections,
        "notes": [
            "Consumed every stable work, section, entry, and note from the complete four-chunk assembled_fragments.json checkpoint.",
            "The later chunk-runner retry failed before OCR reading because its Codex app-server could not initialise on a read-only filesystem; no fragment was replaced or dropped.",
            "Direct OCR review resolves the two pending anchors: Expositio begins at file 034 (printed 35-36) and Dubia at file 552 (printed 527-528).",
            "The 136 line-by-line ORDO RERUM entries are preserved as a closing contents section; entries without direct text evidence retain null target_file instead of a number-only guess.",
        ] + stable.get("notes", []),
    }
    return payload


if __name__ == "__main__":
    result = build()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)

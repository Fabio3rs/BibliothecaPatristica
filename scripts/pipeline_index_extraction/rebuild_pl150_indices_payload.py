#!/usr/bin/env python3
"""Rebuild the PL150 general-index payload from validated chunk fragments."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME = "PL150"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def file(seq: int) -> str:
    matches = list(TEXT.glob(f"*-{seq:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected one physical OCR file for suffix {seq}: {matches}")
    return str(matches[0])


def mark_consumed(payload: dict) -> None:
    for work in payload["works"]:
        work.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    for section in payload["sections"]:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True


def work_by_key(works: list[dict], key: str) -> dict:
    return next(work for work in works if work["work_key"] == key)


def main() -> None:
    document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    stable = copy.deepcopy(document["data"])
    works = stable["works"]
    sections = stable["sections"]
    mark_consumed(stable)

    lanfrancus = work_by_key(works, "PL150:work:lanfrancus_cantuariensis:001")
    lanfrancus.update(
        {
            "start_page": 1,
            "end_page": 780,
            "start_file": file(9),
            "end_file": file(392),
            "confidence": "high",
        }
    )
    lanfrancus["raw_json"]["anchor_evidence"] = [
        "File 009 directly opens B. LANFRANCUS CANTUARIENSIS ARCHIEPISCOPUS ET ANGLIÆ PRIMAS / PROLEGOMENA and prints editorial page 1 in its footer.",
        "File 392 carries the editorial header 779 AD OPERA B. LANFRANCI APPENDIX — CATAL. BEC. SÆC. XII. 780; file 393 directly begins Bonizo at 781.",
    ]

    bonizo = work_by_key(works, "PL150:work:bonizo_placentinus:001")
    bonizo.update(
        {
            "start_page": 781,
            "end_page": 870,
            "start_file": file(393),
            "end_file": file(437),
            "confidence": "high",
        }
    )
    bonizo["raw_json"].update(
        {
            "anchor_resolution": "resolved",
            "anchor_evidence": [
                "File 393 logical header is 781 BONIZO PLACENTINUS EPISC. — DE VITA ET SCRIPTIS BONIZONIS. 782 and its lower blocks begin BONIZO / PROLEGOMENA / BONIZONIS VITA, RES GESTÆ, SCRIPTA.",
                "Files 395–403 continue the Bonizo running headers; file 404 begins Liber ad amicum, and file 437 carries 869 DECRETALE. 870 plus BONIZONIS DECRETALE. File 438 directly opens Guillelmus, so it is excluded from Bonizo.",
            ],
        }
    )
    bonizo["raw_json"].pop("work_anchor_rerun", None)

    herimannus = work_by_key(works, "PL150:work:herimannus_metensis:001")
    herimannus.update({"start_page": 1177, "start_file": file(591), "confidence": "high"})
    herimannus["raw_json"]["direct_opening_evidence"] = (
        "File 591 has the logical header 1177 HERIMANNUS METENSIS EPISC. — NOTITIA HISTORICA. 1178 and directly opens the Herimannus notice after the explicit end of Willhelmus's Musica."
    )

    guido = work_by_key(works, "PL150:work:guido_abbas_farensis:001")
    guido.update({"start_file": file(598), "confidence": "high"})
    guido["raw_json"]["direct_opening_evidence"] = (
        "File 598 directly opens GUIDO ABBAS FARFENSIS / NOTITIA HISTORICA and GUIDONIS DISCIPLINA FARFENSIS ET MONASTERII S. PAULI ROMAE."
    )

    aribo = work_by_key(works, "PL150:work:aribo_scholasticus:001")
    aribo.update(
        {
            "start_page": 1307,
            "end_page": 1341,
            "start_file": file(656),
            "end_file": file(675),
            "confidence": "high",
        }
    )
    aribo["raw_json"].update(
        {
            "anchor_resolution": "resolved",
            "anchor_evidence": [
                "File 656 directly opens ANNO DOMINI MXC / ARIBO SCHOLASTICUS / MUSICA ARIBONIS; the next physical file supplies the 1309–1310 MUSICA header, confirming that the 1307 reference is editorial rather than the physical suffix.",
                "File 675 contains Explicit Musica Aribonis immediately before the Henricus opening. The closing ORDO lists Aribonis sententia and Alia sententia utiliter explanata at 1341, preserving the editorial endpoint without using filename order.",
            ],
        }
    )
    aribo["raw_json"].pop("work_anchor_rerun", None)

    henricus = work_by_key(works, "PL150:work:henricus_clericus_pomposianus:001")
    henricus.update({"start_page": 1341, "start_file": file(675), "confidence": "high"})
    henricus["raw_json"]["direct_opening_evidence"] = (
        "On file 675, immediately after Explicit Musica Aribonis, the OCR opens HENRICUS CLERICUS POMPOSIANUS / MONITUM IN OPUSCULUM SUBSEQUENS / EPISTOLA HENRICI CLERICI AD STEPHANUM; the closing ORDO records both at 1341."
    )

    front, closing, closing_continuation = sections
    front["raw_json"]["editorial_page_note"] = (
        "The ELENCHUS leaf has no reliable printed page pair. Its col. figures are editorial columns, not physical-file suffixes."
    )
    closing.update(
        {
            "index_kind": "ORDO RERUM",
            "page_start": 1673,
            "page_end": 1684,
            "file_start": file(839),
            "file_end": file(844),
        }
    )
    closing["raw_json"]["pagination_resolution"] = (
        "The 1673/1674 header on file 839 and the 1683 continuation on file 844 establish the 1673–1684 span. OCR reads 1075/1076 and 1079/1080 on intermediate sheets and 1691 at the right edge of file 844; these are retained in editorial_headers_raw as CER evidence."
    )
    closing_continuation.update({"index_kind": "ORDO RERUM", "page_start": 1685, "page_end": 1688})

    lanfrancus_front = {
        "section_key": "PL150:section:work_front:lanfrancus_prolegomena:001",
        "work_key": lanfrancus["work_key"],
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA",
        "heading_raw": "PROLEGOMENA.",
        "heading_norm": "Prolegomena",
        "page_start": 1,
        "page_end": 1,
        "file_start": file(9),
        "file_end": file(9),
        "confidence": "high",
        "raw_json": {
            "assembled_fragments_consumed": True,
            "entries_status": "not_a_table",
            "entries_status_reason": "This is a direct work opening with prose dedication and no line-item index.",
            "evidence": "B. LANFRANCUS / CANTUARIENSIS ARCHIEPISCOPUS ET ANGLIÆ PRIMAS / PROLEGOMENA.; printed footer 1.",
        },
        "entries": [],
    }
    bonizo_front = {
        "section_key": "PL150:section:work_front:bonizo_prolegomena:001",
        "work_key": bonizo["work_key"],
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA",
        "heading_raw": "BONIZONIS VITA, RES GESTÆ, SCRIPTA / PROLEGOMENA",
        "heading_norm": "Bonizonis vita res gestae scripta prolegomena",
        "page_start": 781,
        "page_end": 782,
        "file_start": file(393),
        "file_end": file(393),
        "confidence": "high",
        "raw_json": {
            "assembled_fragments_consumed": True,
            "entries_status": "not_a_table",
            "entries_status_reason": "This physical sheet begins prose prolegomena, not a line-item contents table.",
            "evidence": "781 BONIZO PLACENTINUS EPISC. — DE VITA ET SCRIPTIS BONIZONIS. 782; lower blocks read BONIZO / PROLEGOMENA / BONIZONIS VITA, RES GESTÆ, SCRIPTA.",
        },
        "entries": [],
    }
    sections.extend([lanfrancus_front, bonizo_front])

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PL",
            "source_root": str(TEXT),
            "volume_label": "Patrologia Latina CL",
            "notes": [
                "The front ELENCHUS is on physical file 008; its numbered literals are editorial columns.",
                "The validated fragments supply all 29 author/work records, three index-section objects, and 864 line-by-line entries.",
                "Closing alphabetical material before the Ordo rerum remains excluded from this general-index payload.",
            ],
        },
        "works": works,
        "sections": sections,
        "notes": [
            "Consumed every stable work, section, entry, and note from data/index_intermediate_payloads/PL150/assembled_fragments.json (29 works, 3 stable sections, 864 stable entries).",
            "Added two directly verified work-opening records for the requested PROLEGOMENA pages; neither is a line-item table.",
            "Resolved the Bonizo and Aribo anchor reruns with direct local OCR evidence; no work_anchor_rerun marker remains.",
            "Corrected the former physical-file/page conflation: Lanfrancus opens on printed page 1 in physical file 009; Bonizo on 781 in physical file 393; Aribo on 1307 in physical file 656.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild PL097's general-index payload from stable fragments and OCR anchors."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME = "PL097"
TEXT_ROOT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def physical_file(suffix: int) -> str:
    matches = list(TEXT_ROOT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected one physical file for {suffix}: {matches}")
    return str(matches[0])


FILES = {number: physical_file(number) for number in (7, 9, 10, 11, 12, 35, 36, 37, 58, 64, 65, 190, 249, 340, 353, 461, 503, 528, 543, 545, 546, 547, 548)}


def normalise(value: str | None) -> str:
    plain = (value or "").replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return " ".join(re.findall(r"[A-Za-zÀ-ÿ0-9]+", plain.upper()))


def parse_reference(entry: dict[str, Any]) -> None:
    """Complete the canonical entry fields without altering its stable OCR literal."""
    raw = entry.get("entry_raw") or ""
    target = raw.strip()
    reference: str | None = None
    match = re.search(r"\s+(Ibid\.|Ib\.\s*ad\.|\d+[.]?)$", target, flags=re.IGNORECASE)
    if match:
        reference = match.group(1)
        target = target[: match.start()].rstrip()
    entry.setdefault("target_raw", target or None)
    entry.setdefault("target_file", None)
    entry.setdefault("page_ref_raw", reference)
    entry.setdefault("page_ref_int", None)
    entry.setdefault("page_ref_col", None)
    entry.setdefault("note_raw", "Ibid." if reference and reference.lower().startswith("ib") else None)
    entry.setdefault("normalized_target", normalise(target))
    entry.setdefault("confidence", "medium")
    entry.setdefault("raw_json", {})
    if reference and reference.rstrip(".").isdigit():
        # These PL contents lists cite Migne columns.  Preserve the original literal
        # and do not confuse it with either an editorial sheet number or file suffix.
        entry["page_ref_col"] = int(reference.rstrip("."))
    elif reference:
        entry["page_ref_raw"] = reference


def canonical_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(entries)
    for order, entry in enumerate(result, start=1):
        entry["entry_order"] = order
        entry.pop("entry_key", None)
        parse_reference(entry)
        entry["raw_json"]["assembled_fragments_consumed"] = True
    return result


def map_targets(entries: list[dict[str, Any]], targets: dict[int, tuple[str, str]]) -> None:
    for entry in entries:
        mapping = targets.get(entry["entry_order"])
        if mapping:
            entry["target_file"], evidence = mapping
            entry["raw_json"]["target_evidence"] = evidence
            entry["confidence"] = "high"


def main() -> None:
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled_document.get("status") != "complete":
        raise ValueError("Validated fragments are not complete")
    assembled = assembled_document["data"]
    stable_sections = {section["section_key"]: copy.deepcopy(section) for section in assembled["sections"]}

    title_inventory = stable_sections["PL097:section:volume_front:title_inventory:001"]
    title_inventory["entries"] = canonical_entries(title_inventory["entries"])
    title_inventory["heading_norm"] = "PATROLOGIAE CURSUS COMPLETUS SERIES LATINA PRIOR PATROLOGIAE LATINAE TOMUS XCVII OPERUM BEATI CAROLI MAGNI IMPERATORIS TOMUS PRIMUS"
    title_inventory.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    elenchus = stable_sections["PL097:section:volume_front:elenchus:001"]
    elenchus["entries"] = canonical_entries(elenchus["entries"])
    elenchus["heading_raw"] = "SÆCULUM IX. ANNI 800-814.\nELENCHUS\nOPERUM QUÆ IN HOC TOMO XCVII CONTINENTUR."
    elenchus["heading_norm"] = "ELENCHUS OPERUM QUAE IN HOC TOMO XCVII CONTINENTUR"
    elenchus.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    map_targets(
        elenchus["entries"],
        {
            2: (FILES[11], "Direct title and author on file 011; file 012's 11/12 header confirms the adjacent printed sequence."),
            3: (FILES[36], "Direct stacked CODEX DIPLOMATICUS opening on file 036."),
            4: (FILES[36], "Direct SECTIO PRIMA — CAPITULARIA opening on file 036."),
            5: (FILES[36], "Direct STEPHANI BALUZII TUTELENSIS PRÆFATIO heading on file 036."),
            6: (FILES[58], "Direct GEORGII HENRICI PERTZ PRÆFATIO heading on file 058."),
            7: (FILES[64], "Direct IN PIPPINI ET CAROLI MAGNI DIPLOMATA / D. BOUQUET MONITUM heading on file 064."),
            8: (FILES[65], "Direct local opening follows Bouquet's monition; retained as a physical locator only after local title-context inspection."),
            9: (FILES[190], "Direct local body/header evidence identifies the Ludovici I et Lotharii Capitularia range."),
            10: (FILES[249], "Direct AB ANSEGISO ABBATE FONTANELLENSI heading on file 249."),
            11: (FILES[340], "Direct CAPITULARIA SPURIA heading on file 340."),
            12: (FILES[353], "Direct BENEDICTI DIACONI heading on file 353."),
            13: (FILES[461], "Direct I. PRIVILEGIA QUÆ AD PARTES GALLIÆ SPECTANT heading on file 461."),
            14: (FILES[503], "Direct II. PRIVILEGIA PER PARTES ITALIÆ header/title on file 503."),
            15: (FILES[528], "Direct III. PRIVILEGIA QUAE AD PARTES GERMANIAE SUPERIORIS ET INFERIORIS SPECTANT heading on file 528."),
            16: (FILES[543], "Direct APPENDIX AD PRIVILEGIA B. CAROLI MAGNI heading on file 543."),
            18: (FILES[546], "Direct ORDO RERUM heading begins in file 546 after the body material."),
        },
    )

    ordo = stable_sections["PL097:section:volume_end:ordo_rerum_contents"]
    ordo["entries"] = canonical_entries(ordo["entries"])
    ordo.update(
        {
            "work_key": None,
            "index_kind": "ORDO RERUM",
            "heading_raw": "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR",
            "page_start": 1083,
            "page_end": 1088,
            "file_start": FILES[546],
            "file_end": FILES[548],
            "confidence": "high",
        }
    )
    ordo.setdefault("raw_json", {}).update(
        {
            "assembled_fragments_consumed": True,
            "editorial_headers": [
                "1083 ORDO RERUM 1084",
                "1085 QUÉ IN HOC TOMO CONTINENTUR. 1086",
                "1087 ORDO RERUM QUAE IN HOC TOMO CONTINENTUR. 1088",
            ],
            "boundary_evidence": "File 545 carries 1081/1082 and still belongs to the Codex appendix; file 546 opens the ORDO. File 548 completes it, and file 549 is blank/guard matter.",
            "section_role": "Closing volume contents table, not an alphabetical or analytical index.",
        }
    )

    vita_front = {
        "section_key": "PL097:section:work_front:vita_prolegomena:001",
        "work_key": "PL097:work:vita_caroli_magni",
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA",
        "heading_raw": "B. CAROLI MAGNI IMPERATORIS\nVITA, AUCTORE EINHARDO.\nPROLEGOMENA.",
        "heading_norm": "B CAROLI MAGNI IMPERATORIS VITA AUCTORE EINHARDO PROLEGOMENA",
        "page_start": 9,
        "page_end": 24,
        "file_start": FILES[11],
        "file_end": physical_file(18),
        "confidence": "high",
        "raw_json": {
            "source_files": [FILES[11], FILES[12], physical_file(13), physical_file(17), physical_file(18)],
            "entries_status": "empty_by_structure",
            "entries_status_reason": "This is opening prose and editorial prolegomena, not a line-item table.",
            "anchor_evidence": "File 011 has the direct title and author; file 012's 11/12 running header and file 013's 13/14 PROLEGOMENA header establish the local printed sequence. The closing ORDO distinguishes Prolegomena at 9, Einhardi praefatio at 25, and the Vita proper at 27.",
        },
        "entries": [],
    }
    codex_front = {
        "section_key": "PL097:section:work_front:codex_prolegomena:001",
        "work_key": "PL097:work:codex_diplomaticus",
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA",
        "heading_raw": "CODEX DIPLOMATICUS.\nSECTIO PRIMA. — CAPITULARIA.\nPROLEGOMENA.\nSTEPHANI BALUZII TUTELENSIS PRÆFATIO.",
        "heading_norm": "CODEX DIPLOMATICUS SECTIO PRIMA CAPITULARIA PROLEGOMENA STEPHANI BALUZII TUTELENSIS PRAEFATIO",
        "page_start": 63,
        "page_end": 120,
        "file_start": FILES[36],
        "file_end": FILES[64],
        "confidence": "high",
        "raw_json": {
            "source_files": [FILES[36], FILES[37], physical_file(43), physical_file(53), physical_file(59), physical_file(63), FILES[64]],
            "entries_status": "empty_by_structure",
            "entries_status_reason": "The candidate pages are continuous prefatory prose under recurring headers, not a separate line-item index.",
            "anchor_evidence": "File 036 has the direct stacked title and Baluzii preface. File 037 has 65/66 CODEX DIPLOMATICUS — PROLEGOMENA; the ELENCHUS and closing ORDO both declare the Codex at 63. Files 043, 053, 059, and 063 only continue the same running-header range.",
        },
        "entries": [],
    }

    stable_container = copy.deepcopy(assembled["works"][0])
    stable_container["work_order"] = 1
    stable_container.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    stable_container["raw_json"]["classification"] = "Verified title-page container for this first tome; it is retained as a validated work object without collapsing its separately anchored contents."
    vita = {
        "work_key": "PL097:work:vita_caroli_magni",
        "work_order": 2,
        "author_raw": "EINHARDO.",
        "title_raw": "B. CAROLI MAGNI IMPERATORIS VITA, AUCTORE EINHARDO.",
        "title_norm": "B CAROLI MAGNI IMPERATORIS VITA AUCTORE EINHARDO",
        "start_page": 9,
        "end_page": 62,
        "start_file": FILES[11],
        "end_file": FILES[35],
        "source_section_key": vita_front["section_key"],
        "confidence": "high",
        "raw_json": {
            "anchor_resolution": "resolved by direct opening, local header sequence, and closing contents evidence",
            "anchor_evidence": [
                "File 011 directly prints B. CAROLI MAGNI IMPERATORIS / VITA, AUCTORE EINHARDO / PROLEGOMENA.",
                "File 012 has the 11/12 header, so the unnumbered direct opening sheet is the preceding 9/10 pair; the ELENCHUS and closing ORDO both cite the prolegomena at 9.",
                "File 035 has the explicit 61 VITA 62 header and contains the closing variant readings/notes; file 036 begins the distinct Codex diplomaticus.",
            ],
            "prior_work_anchor_rerun": "Resolved: the former page 11 was not supported by the direct opening sheet. The physical anchor remains file 011, while local editorial evidence supports column/page 9.",
        },
    }
    codex = {
        "work_key": "PL097:work:codex_diplomaticus",
        "work_order": 3,
        "author_raw": "STEPHANI BALUZII TUTELENSIS; GEORGII HENRICI PERTZ.",
        "title_raw": "CODEX DIPLOMATICUS.",
        "title_norm": "CODEX DIPLOMATICUS",
        "start_page": 63,
        "end_page": 1082,
        "start_file": FILES[36],
        "end_file": FILES[545],
        "source_section_key": codex_front["section_key"],
        "confidence": "high",
        "raw_json": {
            "anchor_resolution": "resolved by direct opening and explicit final header",
            "anchor_evidence": [
                "File 036 directly prints CODEX DIPLOMATICUS / SECTIO PRIMA — CAPITULARIA / PROLEGOMENA.",
                "File 037 begins the recurring body header 65 CODEX DIPLOMATICUS — PROLEGOMENA 66; the ELENCHUS and closing ORDO cite the opening at 63.",
                "File 545 has the direct 1081 CODEX DIPLOMATICUS — PRIVILEGIA — APPENDIX 1082 header; file 546 begins the separate ORDO RERUM.",
            ],
        },
    }

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PL",
            "source_root": str(TEXT_ROOT),
            "volume_label": "Patrologiæ Latinæ Tomus XCVII",
            "notes": [
                "The physical OCR suffix is not editorial pagination; the contents references in this Migne volume are columns/pages from the printed source.",
                "The opening ELENCHUS is on file 010; the closing ORDO RERUM occupies files 546–548 and is a volume contents table.",
            ],
            "scan_summary": {"validated_fragment_counts": assembled_document["counts"], "physical_file_count": 554},
        },
        "works": [stable_container, vita, codex],
        "sections": [title_inventory, elenchus, vita_front, codex_front, ordo],
        "notes": [
            "Consumed every stable object from assembled_fragments.json: 1 work, 3 sections, 315 line-by-line entries, and 6 fragment notes.",
            "The direct Vita opening is physically file 011 but editorially begins at 9; the earlier unresolved 11-page anchor was corrected from neighboring printed headers and the two independent contents tables.",
            "Files 043, 053, 059, and 063 were inspected as instructed and are recurring Codex prolegomena body pages, not indexes.",
            "The ORDO RERUM ends on file 548; file 549 is blank/guard matter and is excluded.",
        ] + assembled.get("notes", []),
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

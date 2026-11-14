#!/usr/bin/env python3
"""Reassemble PL121's validated fragments and repair directly reviewed anchors."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/index_payloads/PL121_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PL121/assembled_fragments.json"
SOURCE_ROOT = ROOT / "teste/PL121/text"


def literal_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.casefold().replace("æ", "ae").replace("œ", "oe")
    return re.sub(r"[^a-z0-9]+", "", value)


def unique_locator_map(payload: dict) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for section in payload["sections"]:
        for entry in section["entries"]:
            target_file = entry.get("target_file")
            key = literal_key(entry.get("target_raw"))
            if target_file and key:
                candidates.setdefault(key, set()).add(target_file)
    return {key: next(iter(paths)) for key, paths in candidates.items() if len(paths) == 1}


def add_anchor_evidence(work: dict, text: str) -> None:
    raw = work.setdefault("raw_json", {})
    raw.pop("work_anchor_rerun", None)
    raw["direct_anchor_review"] = text


def main() -> None:
    prior = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled = json.loads(FRAGMENTS_PATH.read_text(encoding="utf-8"))
    locators = unique_locator_map(prior)

    # These three sections are stable objects produced by the validated chunk assembler.
    # The former volume_back label is converted to the canonical PL scope value while
    # retaining its original assembler identity in raw_json.
    sections = copy.deepcopy(assembled["data"]["sections"])
    for section in sections:
        if section["index_kind"] == "ORDO_RERUM":
            section["index_kind"] = "ORDO RERUM"
        if section["scope_kind"] == "volume_back":
            section["raw_json"]["assembled_scope_kind"] = "volume_back"
            section["scope_kind"] = "volume_end"
        for entry in section["entries"]:
            key = literal_key(entry.get("target_raw"))
            if not entry.get("target_file") and key in locators:
                entry["target_file"] = locators[key]
                entry["raw_json"]["target_file_review"] = (
                    "Retained only where the prior PL121 payload had one unambiguous "
                    "locally verified target for the same OCR target literal."
                )

    works = copy.deepcopy(prior["works"])
    front_section_key = "PL121:section:volume_front:elenchus:001"
    for work in works:
        work["source_section_key"] = front_section_key
        key = work["work_key"]
        if key == "ratramnus_corbeiensis_monachus":
            work["end_file"] = str(SOURCE_ROOT / "5afc31b5-6ccd-4f33-9af9-78cbc6841afa-582.txt")
            work["raw_json"]["boundary_note"] = (
                "The composite Ratramnus corpus opens directly in file 010 with the "
                "author/title block for De prædestinatione Dei. Its omitted Cynocephalis "
                "letter is printed at the close in files 581–582; file 583 begins the "
                "closing ORDO RERUM."
            )
            add_anchor_evidence(
                work,
                "Files 009–011 were inspected. File 009 is the historical notice; file "
                "010 explicitly opens ‘RATRAMNI CORBEIENSIS MONACHI DE PRÆDESTINATIONE "
                "DEI LIBRI DUO’; file 011 begins its authorial preface. Candidates bearing "
                "De Corpore et Sanguine Domini are later component works or running headers, "
                "not the composite corpus opening.",
            )
        elif key == "aeneas_parisiensis_episcopus":
            work["start_file"] = str(SOURCE_ROOT / "aaa8684a-f26f-4260-8828-1384c9bc2eea-346.txt")
            work["end_page"] = 760
            work["end_file"] = str(SOURCE_ROOT / "8f656440-686f-421b-bf8f-31cd544b15a7-384.txt")
            add_anchor_evidence(
                work,
                "File 346 contains the direct title block ‘ÆNEÆ PARISIENSIS LIBER ADVERSUS "
                "GRÆCOS’ and Acherius’ monitum after the preceding notice; file 347 begins "
                "‘LIBER ADVERSVS GRÆCOS. PRÆFATIO AUCTORIS.’ File 349 is body text with a "
                "running header, not the opening. The work remains in file 384 under the "
                "759/760 header, while file 385 starts Herardus’ historical notice.",
            )
        elif key == "iso_sangallensis_monachus":
            work["start_file"] = str(SOURCE_ROOT / "8f656440-686f-421b-bf8f-31cd544b15a7-394.txt")
            work["end_page"] = 794
            work["end_file"] = str(SOURCE_ROOT / "5730cfae-d99d-45f6-ac30-bb3b1eb3da46-401.txt")
            add_anchor_evidence(
                work,
                "File 394 has the 779/780 header, Isonis historical notice, the explicit "
                "title ‘ISONIS DE MIRACULIS SANCTI OTHMARI LIBRI DUO’, and the authorial "
                "preface. File 395 starts LIBER PRIMUS and is therefore a running/body page. "
                "File 401 carries the 793/794 header for LIB. II; file 402 directly opens "
                "Grimaldus’ Liber Sacramentorum.",
            )

    notes: list[str] = []
    for note in [*prior.get("notes", []), *assembled["data"].get("notes", [])]:
        if note not in notes:
            notes.append(note)
    notes.append(
        "Reassembled every stable fragment section and entry (59 ELENCHUS lines, 777 ORDO RERUM lines, and the post-index marginal-folio artifact)."
    )
    notes.append(
        "The three pending work-anchor reviews were resolved by direct XML OCR inspection; no filename suffix was used as editorial pagination."
    )

    volume = copy.deepcopy(prior["volume"])
    volume_notes = volume.setdefault("notes", [])
    if not isinstance(volume_notes, list):
        volume_notes = [str(volume_notes)]
        volume["notes"] = volume_notes
    volume_notes.append(
        "Direct anchor review completed for Ratramnus, Æneas, and Iso; prior running-header candidates were rejected as starts."
    )

    result = {"volume": volume, "works": works, "sections": sections, "notes": notes}
    PAYLOAD_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

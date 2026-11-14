#!/usr/bin/env python3
"""Rebuild the PL112 general-index payload from validated fragments and OCR checks."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PL112"
PAYLOAD_PATH = ROOT / "data/index_payloads/PL112_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PL112/assembled_fragments.json"
TEXT_ROOT = ROOT / "teste/PL112/text"

# Each physical boundary was inspected against the next work's opening header.  The two
# shared scans deliberately occur at 243 (w4/w5) and 792 (w23/w24).
END_FILE_SEQUENCES = {
    1: 83, 2: 126, 3: 194, 4: 243, 5: 257, 6: 273, 7: 286,
    8: 293, 9: 321, 10: 330, 11: 350, 12: 359, 13: 420, 14: 428,
    15: 548, 16: 586, 17: 599, 18: 634, 19: 670, 20: 702,
    21: 719, 22: 757, 23: 792, 24: 793, 25: 795, 26: 841, 27: 844,
}


def physical_files() -> dict[int, Path]:
    files: dict[int, Path] = {}
    for path in TEXT_ROOT.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            files[int(match.group(1))] = path
    if len(files) != 894:
        raise ValueError(f"Expected 894 PL112 physical OCR files, found {len(files)}")
    return files


def add_anchor_evidence(work: dict, evidence: str) -> None:
    raw = work.setdefault("raw_json", {})
    checks = raw.setdefault("agent_anchor_checks", [])
    checks.append(evidence)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled = json.loads(FRAGMENTS_PATH.read_text(encoding="utf-8"))
    if assembled["volume_id"] != VOLUME or assembled["status"] != "complete":
        raise ValueError("PL112 assembled fragments are not a complete volume checkpoint")
    if assembled["consumed_chunk_count"] != assembled["expected_chunk_count"]:
        raise ValueError("Not every PL112 extraction chunk was consumed")

    stable = assembled["data"]
    if len(stable["works"]) != 0 or len(stable["sections"]) != 2:
        raise ValueError("Unexpected PL112 validated-fragment structure")
    if sum(len(section["entries"]) for section in stable["sections"]) != 457:
        raise ValueError("Validated PL112 entry count must be 457")

    files = physical_files()
    works = payload["works"]
    if len(works) != 27:
        raise ValueError("Expected 27 existing PL112 work records")

    front_key = stable["sections"][0]["section_key"]
    for position, work in enumerate(works, start=1):
        work["source_section_key"] = front_key
        end_sequence = END_FILE_SEQUENCES[position]
        work["end_file"] = str(files[end_sequence])
        add_anchor_evidence(
            work,
            f"Direct OCR boundary review: physical file {end_sequence:03d} remains in this work; the succeeding physical opening was checked separately. OCR physical sequence is retained only as a locator.",
        )

    # Both pending reruns are resolved by explicit opening text, rather than title similarity
    # or numeric matching alone.
    w20 = works[19]
    w20["start_file"] = str(files[671])
    w20["start_page"] = 1333
    w20["raw_json"].pop("work_anchor_rerun", None)
    add_anchor_evidence(
        w20,
        "Physical 671 has header '1333 PRÆF. IN LIB. CAN. PŒNIT. 1334', the explicit title 'DE QUÆSTIONIBUS CANONUM PŒNITENTIALIUM LIBRI TRES', its external-placement note, and 'RABANI PRÆFATIO.'; 670 is the preceding work and 672 continues this penitential material.",
    )

    w25 = works[24]
    w25["start_file"] = str(files[794])
    w25["start_page"] = 1579
    w25["raw_json"].pop("work_anchor_rerun", None)
    add_anchor_evidence(
        w25,
        "Physical 794 has the combined logical header '1579 … 1580' and 'B. RABANI DE INVENTIONE LINGUARUM / Ab Hebræa usque ad Theodiscam, et notis antiquis'; 793 is Glossæ and 795 is a running header/body continuation.",
    )

    # Direct printed headers at physical 634 and the beginning of 845 correct two prior
    # editorial end ranges.  Neither value is inferred from a filename suffix.
    works[17]["end_page"] = 1260
    add_anchor_evidence(
        works[17],
        "Physical 634 bears the editorial header 1259–1260 and contains De ecclesiastica disciplina; physical 635 begins the following De videndo Deum material.",
    )
    works[26]["end_page"] = 1680
    add_anchor_evidence(
        works[26],
        "Physical 844 is the final Dubia et spuria body page; physical 845 begins the excluded closing INDEX RERUM ET VERBORUM at 1681–1682. The OCR reads the left header on 844 as 1079, so the 1679–1680 sequence is recorded as contextual editorial evidence.",
    )

    # Consume every validated section/entry object.  Enrich the front inventory targets only
    # where each listed work has separately verified local OCR opening evidence.
    sections = stable["sections"]
    front_entries = sections[0]["entries"]
    for order, entry in enumerate(front_entries, start=1):
        if order <= 25:
            entry["target_file"] = works[order - 1]["start_file"]
        elif order in (26, 27):
            entry["target_file"] = works[25]["start_file"]
        elif order == 28:
            entry["target_file"] = works[26]["start_file"]
    for section in sections:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    payload["sections"] = sections

    payload["volume"].setdefault("notes", []).append(
        "Rebuilt from all validated PL112 fragments: 2 sections and 457 line-by-line entries."
    )
    payload.setdefault("notes", []).append(
        "Resolved the two pending work-anchor reviews from direct OCR openings and replaced all nonphysical work-end locators with inspected PL112 OCR files."
    )

    work_keys = {work["work_key"] for work in works}
    for section in payload["sections"]:
        if section["work_key"] is not None and section["work_key"] not in work_keys:
            raise ValueError(f"Dangling section work_key: {section['work_key']}")
    for work in works:
        if work["start_page"] is not None and work["end_page"] is not None:
            if work["start_page"] > work["end_page"]:
                raise ValueError(f"Invalid editorial range: {work['work_key']}")
        for field in ("start_file", "end_file"):
            if work[field] is not None and not Path(work[field]).is_file():
                raise ValueError(f"Missing physical locator {field} for {work['work_key']}")

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

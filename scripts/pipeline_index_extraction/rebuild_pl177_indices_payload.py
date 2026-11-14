#!/usr/bin/env python3
"""Rebuild PL177 from validated fragments and direct OCR boundary evidence."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PL177"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
REPORT = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "fragment_consumption_report.json"

sys.path.insert(0, str(ROOT))
from tools.indexing.index_fragment_assembly import verify_payload_consumes_fragments


EXPECTED_COUNTS = (1, 9, 1716)
FRONT_SECTION_KEY = "PL177:section:volume_front:elenchus:001"
ORDO_SECTION_KEY = "PL177:section:volume_end:ordo_rerum_contents"


def file_for(suffix: int) -> str:
    matches = list(TEXT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"expected one OCR file ending in {suffix:03d}: {matches}")
    return str(matches[0])


def by_key(items: list[dict], field: str, key: str) -> dict:
    return next(item for item in items if item[field] == key)


def merge_missing_anchors(section: dict, prior_section: dict | None) -> None:
    if prior_section is None:
        return
    for field in ("page_start", "page_end", "file_start", "file_end"):
        if section.get(field) is None and prior_section.get(field) is not None:
            section[field] = prior_section[field]


def add_boundary_evidence(work: dict, *, start: str, end: str) -> None:
    raw = work.setdefault("raw_json", {})
    raw.pop("work_anchor_rerun", None)
    raw["boundary_evidence"] = {
        "status": "resolved",
        "start": start,
        "end": end,
    }
    raw["manual_anchor_review"] = {
        "status": "resolved",
        "start_evidence": start,
        "end_evidence": end,
    }


def main() -> None:
    prior = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled_document.get("status") != "complete":
        raise ValueError("PL177 assembled fragments are not complete")

    stable = copy.deepcopy(assembled_document["data"])
    actual_counts = (
        len(stable["works"]),
        len(stable["sections"]),
        sum(len(section.get("entries") or []) for section in stable["sections"]),
    )
    if actual_counts != EXPECTED_COUNTS:
        raise ValueError(f"unexpected validated-fragment counts: {actual_counts}")

    works = copy.deepcopy(prior["works"])
    stable_work = stable["works"][0]
    work = by_key(works, "work_key", stable_work["work_key"])
    work.update(copy.deepcopy(stable_work))
    work.update(
        {
            "work_order": 9,
            "start_page": 381,
            "end_page": 456,
            "start_file": file_for(195),
            "end_file": file_for(232),
            "source_section_key": FRONT_SECTION_KEY,
        }
    )
    add_boundary_evidence(
        work,
        start=(
            "File 195 directly prints the title, Roberto Paululo attribution, "
            "PRÆFATIO, and Liber Primus capitula under the 381/382 header."
        ),
        end=(
            "File 232 contains the final De officiis text under the 455/456 header; "
            "later on the same scan a new centered LIBELLUS DE CANONE title and CAP. I open."
        ),
    )

    speculum = by_key(works, "work_key", "pl177_speculum_de_mysteriis_ecclesiae")
    speculum["end_file"] = file_for(194)
    speculum["end_page"] = 380
    add_boundary_evidence(
        speculum,
        start="File 172 directly opens SPECULUM DE MYSTERIIS ECCLESIÆ.",
        end=(
            "File 194 continues and completes the Speculum under the 379/380 header; "
            "file 195 directly opens the next work."
        ),
    )

    de_canone = by_key(works, "work_key", "pl177_de_canone_mystici_libaminis")
    de_canone["start_file"] = file_for(232)
    de_canone["start_page"] = 455
    add_boundary_evidence(
        de_canone,
        start=(
            "The volume ELENCHUS cites column 455, and file 232 directly prints "
            "LIBELLUS DE CANONE MYSTICI LIBAMINIS followed by CAP. I after the prior work."
        ),
        end=(
            "File 239 contains the end of De canone and the opening of the following "
            "Miscellanea material on the same physical scan."
        ),
    )

    prior_sections = {section["section_key"]: section for section in prior["sections"]}
    sections = stable["sections"]
    for section in sections:
        merge_missing_anchors(section, prior_sections.get(section["section_key"]))
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section.get("entries") or []:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    section_bounds = {
        "PL177:section:002:liber_primus_capitula": (381, 384, 195, 196),
        "PL177:candidate-section:003": (405, 405, 207, 207),
        ORDO_SECTION_KEY: (None, None, 624, 629),
    }
    for section_key, (page_start, page_end, file_start, file_end) in section_bounds.items():
        section = by_key(sections, "section_key", section_key)
        section.update(
            {
                "page_start": page_start,
                "page_end": page_end,
                "file_start": file_for(file_start),
                "file_end": file_for(file_end),
            }
        )

    closing_keys = {
        ORDO_SECTION_KEY,
        "PL177:candidate-section:005",
        "PL177:candidate-section:005:liber-sextus-continuation",
        "PL177:candidate-section:005:liber-septimus",
        "PL177:candidate-section:005:appendix-sermones-centum",
    }
    for section in sections:
        if section["section_key"] not in closing_keys:
            continue
        section["scope_kind"] = "volume_end"
        section.setdefault("raw_json", {})["logical_parent_section_key"] = ORDO_SECTION_KEY

    continuation_chain = {
        "PL177:candidate-section:005": ORDO_SECTION_KEY,
        "PL177:candidate-section:005:liber-sextus-continuation": "PL177:candidate-section:005",
        "PL177:candidate-section:005:liber-septimus": "PL177:candidate-section:005:liber-sextus-continuation",
        "PL177:candidate-section:005:appendix-sermones-centum": "PL177:candidate-section:005:liber-septimus",
    }
    for section_key, predecessor in continuation_chain.items():
        by_key(sections, "section_key", section_key).setdefault("raw_json", {})[
            "continuation_of_section_key"
        ] = predecessor

    notes: list[object] = []
    for note in [*stable.get("notes", []), *prior.get("notes", [])]:
        if isinstance(note, dict) and note.get("kind") in {
            "excluded_closing_alphabetical_or_citation_section",
            "excluded_closing_index_boundary",
        }:
            continue
        if note not in notes:
            notes.append(note)
    reconstruction_notes = [
        {
            "kind": "deterministic_reconstruction",
            "text": (
                "Reconstructed from all nine validated fragment sections and all 1716 "
                "stable entries. The 124 CAP. XLII-CLXV entries on file 635 remain part "
                "of the closing ORDO RERUM; only the INDEX RERUM ANALYTICUS announced "
                "after the table on file 636 belongs to the alphabetical pipeline."
            ),
        },
        {
            "kind": "shared_scan_boundaries",
            "text": (
                "Direct OCR review corrected Speculum through file 194 and De canone "
                "to begin on file 232, which also contains the end of De officiis."
            ),
        },
    ]
    for note in reconstruction_notes:
        if note not in notes:
            notes.append(note)

    payload = {
        "volume": copy.deepcopy(prior["volume"]),
        "works": works,
        "sections": sections,
        "notes": notes,
    }
    report = verify_payload_consumes_fragments(payload, assembled_document)
    if report.get("status") != "ok":
        raise ValueError(f"fragment consumption failed: {report}")
    if report["checks"]["ownership"]["excluded_non_owned_section_count"] != 0:
        raise ValueError(f"owned PL177 fragment was excluded: {report['checks']['ownership']}")

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Merge PL157's validated opening-index chunks into the canonical payload."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = ROOT / "data/index_payloads/PL157_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PL157/assembled_fragments.json"
FRONT_KEY = "PL157:section:volume_front:elenchus:001"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    fragments = read_json(FRAGMENTS_PATH)
    sections = fragments["data"]["sections"]
    if len(sections) != 2:
        raise ValueError(f"Expected two validated sections, found {len(sections)}")
    front, ordo = sections
    if front["section_key"] != FRONT_KEY or ordo["index_kind"] != "ORDO_RERUM":
        raise ValueError("Unexpected PL157 validated-fragment structure")

    # The chunks are authoritative for each of their complete, line-by-line tables.
    # Keep their stable keys and every validated entry; only use the human-readable
    # visible form of the ORDO heading in the canonical payload.
    ordo = dict(ordo)
    ordo["index_kind"] = "ORDO RERUM"
    ordo_raw = dict(ordo.get("raw_json") or {})
    ordo_raw["entries_status_reason"] = (
        "All 570 readable ORDO RERUM rows in files 656-660 were retained from the "
        "validated chunk assembly; OCR file suffixes were not used as editorial pagination."
    )
    ordo["raw_json"] = ordo_raw

    for work in payload["works"]:
        work["source_section_key"] = FRONT_KEY

    goffridus = next(
        work for work in payload["works"] if work["work_key"] == "goffridus_vindocinensis"
    )
    goffridus_raw = dict(goffridus.get("raw_json") or {})
    rerun = dict(goffridus_raw.get("work_anchor_rerun") or {})
    rerun.update(
        {
            "status": "ambiguous",
            "reason": "overlapping_editorial_boundaries_require_scan_layout_review",
            "inspection_reasons": ["end_page_overlaps_next_work"],
            "agent_review_evidence": [
                "OCR file 150 has the direct running header ‘291 APPEND. AD COFFRIDUM. 292’ and contains Goffridus appendix material.",
                "The immediately following OCR file 151 has the direct Thiofridus title and Notitia historica; its logical header preserves the uncertain facing pair ‘295 ... 294’.",
                "The Goffridus alphabetical index itself states ‘Hujus voluminis col. 9-293’, while the Thiofridus anchor is an estimator facing pair [293, 294]. The scan/column side at 293 cannot be resolved from the OCR text alone.",
            ],
            "checked_files": [
                "dc685f92-6f84-492f-9886-f1e73be8ff9c-149.txt",
                "dc685f92-6f84-492f-9886-f1e73be8ff9c-150.txt",
                "dc685f92-6f84-492f-9886-f1e73be8ff9c-151.txt",
            ],
        }
    )
    goffridus_raw["work_anchor_rerun"] = rerun
    goffridus["raw_json"] = goffridus_raw

    payload["sections"] = [front, ordo]
    payload["volume"]["notes"] = [
        "Front ELENCHUS verified in OCR file 008 and retained as 33 line-by-line author/work entries.",
        "The closing ORDO RERUM in OCR files 656-660 is retained as 570 validated line-by-line contents entries.",
        "The dense INDEX RERUM ET VERBORUM / INDEX EORUM in files 652-656 is alphabetical and belongs to the separate alphabetical-index pipeline; it is not an opening/general-works section here.",
        "The terminal ‘INDEX RERUM ET VERBORUM. 1295’ is a line of the ORDO RERUM, not an independently attested OCR section.",
    ]
    payload["notes"] = [
        "Merged every stable entry from assembled_fragments.json: 33 ELENCHUS entries and 570 ORDO RERUM entries.",
        "Removed the two former empty list-bearing sections: the Goffridus alphabetical index is out of pipeline scope, and the 1295 literal is only an ORDO entry.",
        "The Goffridus/Thiofridus boundary review remains explicitly ambiguous because the OCR preserves competing printed-column evidence on the shared transition.",
    ]
    PAYLOAD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

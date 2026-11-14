#!/usr/bin/env python3
"""Rebuild the PG059 general-index payload from validated chunks and OCR anchors."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG059"
PAYLOAD_PATH = ROOT / "data/index_payloads/PG059_indices.json"
ASSEMBLED_PATH = ROOT / "data/index_intermediate_payloads/PG059/assembled_fragments.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def key(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def entry_signature(entry: dict) -> tuple[str, str]:
    target = (entry.get("target_raw") or entry.get("entry_raw") or "").split("—", 1)[0]
    return str(entry.get("page_ref_raw") or ""), key(target)[:40]


def preserve_locators(entries: list[dict], prior_entries: list[dict]) -> None:
    """Retain only OCR-backed targets from the prior payload when the item is unambiguous."""
    prior: dict[tuple[str, str], list[dict]] = {}
    for entry in prior_entries:
        if entry.get("target_file"):
            prior.setdefault(entry_signature(entry), []).append(entry)
    for entry in entries:
        matches = prior.get(entry_signature(entry), [])
        if len(matches) == 1:
            match = matches[0]
            entry["target_file"] = match["target_file"]
            raw = dict(entry.get("raw_json") or {})
            raw["prior_target_evidence_preserved"] = match.get("raw_json") or {}
            entry["raw_json"] = raw


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    assembled = read_json(ASSEMBLED_PATH)
    if assembled.get("status") != "complete" or assembled.get("consumed_chunk_count") != assembled.get("expected_chunk_count"):
        raise ValueError("PG059 assembled fragments are not a complete validated checkpoint")
    data = assembled["data"]
    if data.get("works") is None or data.get("sections") is None:
        raise ValueError("PG059 assembled checkpoint lacks stable work or section objects")

    old_sections = {section["section_key"]: section for section in payload["sections"]}
    fragment_sections = {section["section_key"]: section for section in data["sections"]}
    expected = {
        "PG059:section:volume_front:001",
        "PG059:section:prefatory_matter:001",
        "PG059:section:volume_front:002",
    }
    if set(fragment_sections) != expected:
        raise ValueError("unexpected PG059 assembled section keys")

    front = fragment_sections["PG059:section:volume_front:001"]
    preserve_locators(front["entries"], old_sections[front["section_key"]]["entries"])
    front_raw = dict(front.get("raw_json") or {})
    front_raw["assembled_fragments_consumed"] = True
    front["raw_json"] = front_raw

    preface = fragment_sections["PG059:section:prefatory_matter:001"]
    preface["scope_kind"] = "volume_front"
    preface_raw = dict(preface.get("raw_json") or {})
    preface_raw["assembled_fragments_consumed"] = True
    preface["raw_json"] = preface_raw

    closing = fragment_sections["PG059:section:volume_front:002"]
    old_closing = old_sections["PG059:section:volume_end:001"]
    preserve_locators(closing["entries"], old_closing["entries"])
    closing["section_key"] = "PG059:section:volume_end:001"
    closing["scope_kind"] = "volume_end"
    closing["index_kind"] = "ORDO RERUM"
    closing_raw = dict(closing.get("raw_json") or {})
    closing_raw["assembled_fragments_consumed"] = True
    closing_raw["section_span_note"] = "The first ORDO RERUM heading appears midway through file 759 after the final spuria body text."
    closing_raw["closure"] = "FINIS TOMI QUINQUAGESIMI NONI."
    closing["raw_json"] = closing_raw

    work_front = old_sections["PG059:section:work_front:001"]
    work_front.setdefault("raw_json", {})["rechecked_against_ocr"] = (
        "Files 474-475 contain the explicit INDEX SPURIORUM catalogue, with file 475 headed 483 INDEX SPURIORUM 484."
    )

    payload["sections"] = [front, preface, work_front, closing]

    fragment_work_keys = {work["work_key"] for work in data["works"]}
    for work in payload["works"]:
        if work["work_key"] not in fragment_work_keys:
            raise ValueError(f"assembled checkpoint work lost: {work['work_key']}")
        work.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        if work["work_key"] == "PG059:work:spuria":
            work["raw_json"].pop("work_anchor_rerun", None)
            work["raw_json"]["anchor_review"] = {
                "status": "resolved",
                "evidence": [
                    "File 473 explicitly closes the Homiliae in Joannem: 'Homiliarum octoginta octo ... finis.'",
                    "File 474 begins the next block with the standalone title 'SPURIA.' and 'ADMONITIO IN SPURIA SEQUENTIA.'",
                    "The same file's split-page header reads '475 INDEX SPURIORUM. 481'; its right-side editorial page supports the declared printed start page 481 without deriving it from the OCR filename.",
                    "File 475 continues the work-front catalogue under '483 INDEX SPURIORUM. 484', confirming that file 474 is the opening of the spuria block rather than a later running-header occurrence.",
                ],
            }
            work["confidence"] = "high"

    notes = list(payload.get("notes") or [])
    notes.extend([
        "Consumed all 2 validated work objects, 3 validated sections, and 204 validated entries from assembled_fragments.json.",
        "Replaced the incomplete closing ORDO RERUM transcription with its validated 193-entry chunk; preserved prior OCR-backed target locators only where a unique textual signature matched.",
        "Resolved the Spuria anchor from direct OCR on files 473-475; the file 474 title and split editorial header support start_page 481 independently of the physical suffix.",
    ])
    payload["notes"] = list(dict.fromkeys(notes))

    work_keys = {work["work_key"] for work in payload["works"]}
    for section in payload["sections"]:
        if section.get("work_key") is not None and section["work_key"] not in work_keys:
            raise ValueError(f"unresolved section work key: {section['section_key']}")
    entry_keys = [entry["entry_key"] for section in payload["sections"] for entry in section["entries"]]
    if len(entry_keys) != len(set(entry_keys)):
        raise ValueError("duplicate entry_key after PG059 reassembly")
    if len(closing["entries"]) != 193 or len(preface["entries"]) != 9:
        raise ValueError("validated PG059 fragment entries were not fully consumed")

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/fix_pl041_alphabetical_payload.py

Repair the PL041 alphabetical payload by fixing OCR-segmentation spillover around
book transitions, restoring two missing entries, and filling the two invalid refs.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PL041_alphabetical_indices.json"

ENTRY_128 = "PL041:candidate-section:001:PL041:chunk:001:001:128"
ENTRY_153 = "PL041:candidate-section:001:PL041:chunk:001:001:153"
ENTRY_194 = "PL041:candidate-section:001:PL041:chunk:001:001:194"
ENTRY_216 = "PL041:candidate-section:001:PL041:chunk:001:001:216"
ENTRY_221 = "PL041:candidate-section:001:PL041:chunk:001:001:221"
NEW_ENTRY_153 = "PL041:candidate-section:001:PL041:chunk:001:001:153a"
NEW_ENTRY_216 = "PL041:candidate-section:001:PL041:chunk:001:001:216a"

FILE_437 = "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-437.txt"
FILE_438 = "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-438.txt"
SECTION_START = "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-431.txt"


def load_payload() -> dict:
    return json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))


def entry_map(entries: list[dict]) -> dict[str, dict]:
    return {entry["entry_key"]: entry for entry in entries}


def ref_map(refs: list[dict]) -> dict[str, dict]:
    return {ref["entry_key"]: ref for ref in refs}


def set_entry(
    entry: dict,
    *,
    entry_raw: str,
    inferred_printed_page: int,
    anchor_file: str,
    page_ref_raw: str,
    page_ref_int: int,
    confidence: float = 0.95,
) -> None:
    lemma_raw = entry_raw.rsplit(" ", 1)[0]
    entry["entry_raw"] = entry_raw
    entry["lemma_raw"] = lemma_raw
    entry["lemma_display"] = lemma_raw
    entry["lemma_norm"] = lemma_raw
    entry["lemma_sort"] = lemma_raw
    entry["context_raw"] = entry_raw
    entry["inferred_printed_page"] = inferred_printed_page
    entry["section_start_file"] = SECTION_START
    entry["editorial_anchor_file"] = anchor_file
    entry["target_file_best"] = anchor_file
    entry["confidence"] = confidence
    raw_json = dict(entry.get("raw_json") or {})
    raw_json["auto_parsed"] = True
    raw_json["source_files"] = [anchor_file]
    raw_json["page_ref_raw"] = page_ref_raw
    raw_json["page_ref_int"] = page_ref_int
    repairs = list(raw_json.get("repair_notes") or [])
    note = "2026-07-28: repaired segmentation spillover against local OCR."
    if note not in repairs:
        repairs.append(note)
    raw_json["repair_notes"] = repairs
    entry["raw_json"] = raw_json


def set_ref(
    ref: dict,
    *,
    ref_raw: str,
    page_ref_raw: str,
    page_ref_int: int,
    anchor_file: str,
    confidence: float = 0.95,
    inferred_note: str | None = None,
) -> None:
    ref["ref_raw"] = ref_raw
    ref["page_ref_raw"] = page_ref_raw
    ref["page_ref_int"] = page_ref_int
    ref["page_ref_col"] = None
    ref["line_ref_raw"] = None
    ref["range_start_raw"] = None
    ref["range_end_raw"] = None
    ref["target_file"] = anchor_file
    ref["target_file_probability"] = 0.98
    ref["section_start_file"] = SECTION_START
    ref["editorial_anchor_file"] = anchor_file
    ref["confidence"] = confidence
    raw_json = dict(ref.get("raw_json") or {})
    raw_json["auto_parsed"] = True
    raw_json["source_files"] = [anchor_file]
    repairs = list(raw_json.get("repair_notes") or [])
    note = inferred_note or "2026-07-28: repaired ref_raw/page_ref against local OCR."
    if note not in repairs:
        repairs.append(note)
    raw_json["repair_notes"] = repairs
    ref["raw_json"] = raw_json


def editorial_sort_key(entry_key: str) -> tuple[int, float]:
    parts = entry_key.split(":")
    chunk_fragment = parts[-2]
    local_token = parts[-1]
    chunk_rank = 0 if chunk_fragment == "002" else 1
    if local_token.endswith("a") and local_token[:-1].isdigit():
        local_rank = float(int(local_token[:-1])) + 0.5
    else:
        local_rank = float(int(local_token))
    return (chunk_rank, local_rank)


def build_new_entry(template: dict, *, entry_key: str, entry_order: int, entry_raw: str, inferred_printed_page: int, anchor_file: str, page_ref_raw: str, page_ref_int: int) -> dict:
    entry = deepcopy(template)
    entry["entry_key"] = entry_key
    entry["entry_order"] = entry_order
    set_entry(
        entry,
        entry_raw=entry_raw,
        inferred_printed_page=inferred_printed_page,
        anchor_file=anchor_file,
        page_ref_raw=page_ref_raw,
        page_ref_int=page_ref_int,
    )
    raw_json = dict(entry["raw_json"])
    raw_json["auto_parsed"] = False
    raw_json["manually_inserted"] = True
    raw_json["repair_notes"] = [
        "2026-07-28: restored missing entry lost in fragment assembly after OCR page-break spillover."
    ]
    entry["raw_json"] = raw_json
    return entry


def build_new_ref(template: dict, *, entry_key: str, ref_raw: str, page_ref_raw: str, page_ref_int: int, anchor_file: str) -> dict:
    ref = deepcopy(template)
    ref["entry_key"] = entry_key
    set_ref(
        ref,
        ref_raw=ref_raw,
        page_ref_raw=page_ref_raw,
        page_ref_int=page_ref_int,
        anchor_file=anchor_file,
        inferred_note="2026-07-28: restored missing ref from local OCR after page-break spillover.",
    )
    return ref


def main() -> None:
    payload = load_payload()
    payload["entries"] = [
        entry
        for entry in payload["entries"]
        if entry["entry_key"] not in {NEW_ENTRY_153, NEW_ENTRY_216}
    ]
    payload["refs"] = [
        ref
        for ref in payload["refs"]
        if ref["entry_key"] not in {NEW_ENTRY_153, NEW_ENTRY_216}
    ]

    entries = payload["entries"]
    refs = payload["refs"]

    entries_by_key = entry_map(entries)
    refs_by_key = ref_map(refs)

    set_entry(
        entries_by_key[ENTRY_128],
        entry_raw="De peccatoribus et angelis et hominibus, quorum perversitas non perturbat providentiam Dei. 435",
        inferred_printed_page=435,
        anchor_file=FILE_437,
        page_ref_raw="435",
        page_ref_int=435,
    )
    set_ref(
        refs_by_key[ENTRY_128],
        ref_raw="435",
        page_ref_raw="435",
        page_ref_int=435,
        anchor_file=FILE_437,
    )

    set_entry(
        entries_by_key[ENTRY_153],
        entry_raw="Quod arca quam Noe jussus est facere, in omni- bus Christum Ecclesiamque significet. ibid.",
        inferred_printed_page=472,
        anchor_file=FILE_437,
        page_ref_raw="ibid.",
        page_ref_int=472,
    )
    set_ref(
        refs_by_key[ENTRY_153],
        ref_raw="ibid.",
        page_ref_raw="ibid.",
        page_ref_int=472,
        anchor_file=FILE_437,
    )
    entries.append(
        build_new_entry(
            entries_by_key[ENTRY_153],
            entry_key=NEW_ENTRY_153,
            entry_order=154,
            entry_raw="De area atque diluvio, nec illis esse consentien- dum, qui solam historiam recipiunt sine allegorica signifi- catione; nec illis qui solas figuras defendunt repudiata hi- storica veritate. 475",
            inferred_printed_page=475,
            anchor_file=FILE_437,
            page_ref_raw="475",
            page_ref_int=475,
        )
    )
    refs.append(
        build_new_ref(
            refs_by_key[ENTRY_153],
            entry_key=NEW_ENTRY_153,
            ref_raw="475",
            page_ref_raw="475",
            page_ref_int=475,
            anchor_file=FILE_437,
        )
    )

    set_entry(
        entries_by_key[ENTRY_194],
        entry_raw="De filiis Joseph, quos Jacob prophetica manu; suarum transmutatione benedixit. 520",
        inferred_printed_page=520,
        anchor_file=FILE_438,
        page_ref_raw="520",
        page_ref_int=520,
    )
    set_ref(
        refs_by_key[ENTRY_194],
        ref_raw="520",
        page_ref_raw="520",
        page_ref_int=520,
        anchor_file=FILE_438,
    )

    set_entry(
        entries_by_key[ENTRY_216],
        entry_raw="De vario utriusque regni Hebræorum statu, donec ambo populi in captivitatem diverso tempore ducerentur, revocato postea Juda in regnum suum, quod novissime in Romanorum transiit potestatem. 558",
        inferred_printed_page=558,
        anchor_file=FILE_438,
        page_ref_raw="558",
        page_ref_int=558,
    )
    set_ref(
        refs_by_key[ENTRY_216],
        ref_raw="558",
        page_ref_raw="558",
        page_ref_int=558,
        anchor_file=FILE_438,
    )
    entries.append(
        build_new_entry(
            entries_by_key[ENTRY_216],
            entry_key=NEW_ENTRY_216,
            entry_order=218,
            entry_raw="De Prophetis qui vel apud Judæos postremi fue- runt, vel quos circa tempus nativitatis Christi Evangelica prodit historia. ibid.",
            inferred_printed_page=558,
            anchor_file=FILE_438,
            page_ref_raw="ibid.",
            page_ref_int=558,
        )
    )
    refs.append(
        build_new_ref(
            refs_by_key[ENTRY_216],
            entry_key=NEW_ENTRY_216,
            ref_raw="ibid.",
            page_ref_raw="ibid.",
            page_ref_int=558,
            anchor_file=FILE_438,
        )
    )

    set_ref(
        refs_by_key[ENTRY_221],
        ref_raw="ibid.",
        page_ref_raw="ibid.",
        page_ref_int=565,
        anchor_file=FILE_438,
        inferred_note="2026-07-28: inferred ibid. from adjacent entries V and VII in the same OCR opening after page-break split.",
    )
    raw_json_221 = dict(entries_by_key[ENTRY_221].get("raw_json") or {})
    raw_json_221["page_ref_raw"] = "ibid."
    raw_json_221["page_ref_int"] = 565
    repairs_221 = list(raw_json_221.get("repair_notes") or [])
    note_221 = "2026-07-28: preserved OCR wording but inferred the missing ibid. locator from adjacent entries V and VII."
    if note_221 not in repairs_221:
        repairs_221.append(note_221)
    raw_json_221["repair_notes"] = repairs_221
    entries_by_key[ENTRY_221]["raw_json"] = raw_json_221

    entries.sort(key=lambda entry: editorial_sort_key(entry["entry_key"]))
    for idx, entry in enumerate(entries, start=1):
        entry["entry_order"] = idx
    order_by_key = {entry["entry_key"]: entry["entry_order"] for entry in entries}
    refs.sort(key=lambda ref: (order_by_key[ref["entry_key"]], ref["ref_order"]))

    notes = list(payload.get("notes") or [])
    repair_note = (
        "2026-07-28: repaired PL041 transition spillover around entries 128, 153, 194, 216, "
        "restored missing XXVII/XXIV entries, and filled the missing ibid. locator for entry 221."
    )
    if repair_note not in notes:
        notes.append(repair_note)
    payload["notes"] = notes
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

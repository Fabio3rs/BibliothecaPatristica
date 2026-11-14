#!/usr/bin/env python3
"""Build the bounded closing *Ordo Rerum* fragment for PL096."""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


WORKPLAN_PATH = PROJECT_ROOT / "data/index_intermediate_payloads/PL096/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/index_intermediate_payloads/PL096/chunks/section_002_part_001.json"
VOLUME_ID = "PL096"
SECTION_ID = "PL096:candidate-section:002"
CHUNK_ID = "PL096:chunk:002:001"
FINGERPRINT = "cb10b6ae9d41430ac20e918e546dabb61e988f664fb71ac26826eefdca9b786c"
PREFIX = "2aa2c784-255e-4d16-957f-a50d57583e3b"
SOURCE_DIR = PROJECT_ROOT / "teste/PL096/text"

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\u00ad", "")
    return re.sub(r"\s+", " ", value).strip()


def is_upper_heading(value: str) -> bool:
    letters = "".join(ch for ch in value if ch.isalpha())
    return bool(letters) and letters == letters.upper()


def is_new_item(value: str) -> bool:
    return bool(re.match(
        r"(?:CAP(?:UT)?\.?|Cap\.?|SERM\.?|Epist(?:ola)?\.?|EPISTOLA|"
        r"VITA|NOTITIA|Monit(?:um|ium)|Præfatio|Prologus|Admonitio|"
        r"Relati|Incipit|LIBER|APPENDIX|OPUSCULUM|FRAGMENTUM|ALIUD|"
        r"CONTINUATIO|EPIGRAMMATA|EPIGRAMMA|SANCTUS|LEO PAPA|"
        r"BENEDICTUS|JOANNES PAPA|SYNODI|TESTAMENTUM)\b",
        value,
    ))


def physical_path(seq: int) -> Path:
    return SOURCE_DIR / f"{PREFIX}-{seq}.txt"


def extract_lines(path: Path) -> tuple[list[dict[str, object]], bool]:
    """Return text lines from the index part of one physical OCR file.

    File 787 has the final non-index page body before a second, explicit Ordo
    heading.  That earlier body is deliberately not made into index entries.
    """
    raw = path.read_text(encoding="utf-8")
    blocks = re.findall(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    lines: list[dict[str, object]] = []
    in_ordo = path.name.endswith("-787.txt") is False
    body_line = 0
    block_no = 0
    for kind, body in blocks:
        text = clean(body)
        if kind == "cabecalho":
            # The running head has 1589/1590; the later unnumbered heading is
            # the real start of the table on this scan.
            if "ORDO RERUM" in text and not re.search(r"\b15\d{2}\b", text):
                in_ordo = True
            continue
        if kind != "texto_principal" or not in_ordo:
            continue
        block_no += 1
        for raw_line in html.unescape(body).splitlines():
            line = clean(raw_line)
            if not line:
                continue
            body_line += 1
            lines.append({
                "text": line,
                "file": str(path),
                "block_no": block_no,
                "body_line": body_line,
            })
    return lines, in_ordo


def join_records(lines: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    records: list[list[dict[str, object]]] = []
    pending: list[dict[str, object]] = []
    for item in lines:
        text = str(item["text"])
        if pending:
            previous = str(pending[-1]["text"])
            # A finished heading without a reference is followed by a new table
            # item.  Lowercase text is a proven wrap, including every prompted
            # physical-page continuation.
            if (
                previous.endswith(".")
                and not text[:1].islower()
                and (is_new_item(text) or is_upper_heading(text))
            ):
                records.append(pending)
                pending = []
        pending.append(item)
        # Ordo entries consistently close with their internal/editorial locator.
        if re.search(r"\s\d+\.?$", text):
            records.append(pending)
            pending = []
    if pending:
        records.append(pending)
    return records


def normalized(value: str) -> str:
    value = value.lower().replace("æ", "ae").replace("œ", "oe")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def entry_kind(value: str) -> str:
    if is_upper_heading(value):
        return "structural_heading"
    if re.match(r"(?:CAP(?:UT)?\.?|Cap\.?)\b", value):
        return "chapter"
    if re.match(r"(?:LIBER|APPENDIX)\b", value):
        return "division"
    return "contents_line"


def make_entry(order: int, record: list[dict[str, object]]) -> dict[str, object]:
    # The OCR reader's normal within-block wrap rule is applied here explicitly:
    # a terminal hyphen is removed only when the immediately following OCR line
    # begins with a letter and semantic segmentation has kept both lines in the
    # same item.  This covers the all-capital ``EPISCO- / PUS`` wrap as well as
    # proven physical-page continuations, without touching lexical hyphens.
    parts: list[str] = []
    for item in record:
        text = str(item["text"])
        if parts and parts[-1].endswith("-") and text[:1].isalpha():
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)
    raw = " ".join(parts)
    match = re.search(r"\s(\d+)\.?$", raw)
    page = int(match.group(1)) if match else None
    source_files: list[str] = []
    for item in record:
        filename = str(item["file"])
        if filename not in source_files:
            source_files.append(filename)
    return {
        "entry_key": f"{SECTION_ID}:{CHUNK_ID}:{order:03d}",
        "entry_order": order,
        "entry_raw": raw,
        "target_raw": raw,
        "target_file": None,
        "page_ref_raw": match.group(1) if match else None,
        "page_ref_int": page,
        "page_ref_col": None,
        "note_raw": None,
        "normalized_target": normalized(raw),
        "confidence": 0.93 if match else 0.88,
        "raw_json": {
            "source_files": source_files,
            "source_locations": [
                {
                    "file": item["file"],
                    "block_no": item["block_no"],
                    "body_line": item["body_line"],
                }
                for item in record
            ],
            "entry_kind": entry_kind(raw),
            "transcription_policy": "OCR literal preserved; final terminal number is an editorial contents reference, never a physical file suffix.",
        },
    }


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    physical_files = [physical_path(seq) for seq in range(787, 793)]
    all_lines: list[dict[str, object]] = []
    for path in physical_files:
        lines, found_ordo = extract_lines(path)
        if path.name.endswith("-787.txt") and not found_ordo:
            raise ValueError("could not locate the explicit Ordo heading on file 787")
        all_lines.extend(lines)

    records = join_records(all_lines)
    entries = [make_entry(order, record) for order, record in enumerate(records, start=1)]
    section = {
        "section_key": SECTION_ID,
        "work_key": None,
        "scope_kind": "volume_end",
        "index_kind": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR",
        "page_start": 1589,
        "page_end": 1600,
        "file_start": str(physical_files[0]),
        "file_end": str(physical_files[-1]),
        "confidence": 0.97,
        "raw_json": {
            "source_files": [str(path) for path in physical_files],
            "editorial_header_sequence": {
                "787": "1589 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR 1590",
                "788": "1591 ORDO RERUM 1592",
                "789": "1593 QUÆ IN HOC TOMO CONTINENTUR 1594",
                "790": "1595 ORDO RERUM 1596",
                "791": "1597 QUÆ IN HOC TOMO CONTINENTUR 1598",
                "792": "1599 ORDO RERUM 1600",
            },
            "extra_investigation": {
                "files_checked": [str(physical_path(seq)) for seq in range(783, 797)],
                "searches_checked": [
                    "read_ocr_page_text --view xml --show-source across the complete 783-796 context window",
                    "manual review of every owned index column and the three required physical-page seams",
                ],
                "reason": "Files 783-786 establish that the first 787 body block belongs to the preceding diplomatic text, while file 793 begins a new complete chapter entry after this chunk.",
            },
            "omitted_non_index_body": {
                "file": str(physical_path(787)),
                "reason": "The initial 787 text block precedes the unnumbered Ordo heading and is the conclusion of the prior diplomatic work, not a readable line item of this contents table.",
            },
        },
        "entries": entries,
    }
    payload = {
        "schema_version": 2,
        "volume_id": VOLUME_ID,
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": [str(path) for path in physical_files],
        "numbering_semantics": NUMBERING,
        "boundary_decisions": [
            {
                "physical_left_file": str(physical_path(787)),
                "physical_right_file": str(physical_path(788)),
                "decision": "joined_entry",
                "reason": "The owned left column ends `CAP. XXIX. — Quod mysterium salutis, quod sensu`; the next page begins its lowercase syntactic continuation and supplies the editorial reference 124.",
            },
            {
                "physical_left_file": str(physical_path(788)),
                "physical_right_file": str(physical_path(789)),
                "decision": "joined_entry",
                "reason": "CAP. CXII begins on file 788 and its unfinished Trinitatis clause continues with lowercase `sit` on file 789, ending with editorial reference 158.",
            },
            {
                "physical_left_file": str(physical_path(789)),
                "physical_right_file": str(physical_path(790)),
                "decision": "joined_entry",
                "reason": "CAP. LXXXVIII ends file 789 with `fidem, spem`; file 790 supplies `et charitatem...` and the shared editorial reference 190.",
            },
            {
                "physical_left_file": str(physical_path(790)),
                "physical_right_file": str(physical_path(791)),
                "decision": "joined_entry",
                "reason": "The Leodegarius Vita Cap. XVI ends file 790 at `ad necandum Sanctum`; file 791 continues `mittuntur. 313`.",
            },
        ],
        "works": [],
        "sections": [section],
        "notes": [
            "All readable Ordo Rerum line items beginning in owned files 787-792 were serialized as individual logical entries; wrapped OCR lines and proven physical-page continuations were joined.",
            "The terminal numbers in entries are preserved as editorial contents references. They were not mapped to physical OCR filename suffixes.",
            "The first body block on physical file 787 was checked but excluded because it precedes the table heading and belongs to the preceding diplomatic text, whose entry ownership is outside this table extraction.",
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

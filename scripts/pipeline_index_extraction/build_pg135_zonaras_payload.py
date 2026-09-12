#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/build_pg135_zonaras_payload.py

Build a draft alphabetical-index payload for PG135 from the OCR files in
teste/PG135/text and write the final JSON payload plus a small helper request.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG135/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG135_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG135_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG135_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG135"

PREFIX = "5476e3f3-eeda-43f2-8979-5b1ccfa7d577"


SECTION1 = {
    "section_key": "PG135:alpha:analytic_subject:001",
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX RERUM MEMORABILIUM QUÆ IN ZONARÆ ANNALIBUS CONTINENTUR.",
    "page_start": 1061,
    "page_end": 1142,
    "file_start": f"{SOURCE_ROOT}/{PREFIX}-550.txt",
    "file_end": f"{SOURCE_ROOT}/{PREFIX}-591.txt",
}

SECTION2 = {
    "section_key": "PG135:alpha:ordo_rerum:001",
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "page_start": 1143,
    "page_end": 1144,
    "file_start": f"{SOURCE_ROOT}/{PREFIX}-591.txt",
    "file_end": f"{SOURCE_ROOT}/{PREFIX}-591.txt",
}

HEAD_RE = re.compile(
    r"^(?:INDEX IN ZONARÆ ANNALES\.?|INDEX RERUM MEMORABILIUM QUÆ IN ZONARÆ ANNALIBUS CONTINENTUR\.?|ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR\.?|ORDO RERUM\.?|INDEX RERUM MEMORABILIUM\.?)$"
)
PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒΆ-ΩἈ]{1,3}\.?$")
STRIP_HEADINGS = ("INDEX IN ZONARÆ ANNALES", "INDEX RERUM MEMORABILIUM", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR")
FIRST_LETTER_RE = re.compile(r"[A-ZÆŒΆ-ΩἈ]")
NUM_REF_RE = re.compile(
    r"(?P<prefix>\b(?:b\.?|B\.?)\s*)?(?P<num>\d{1,4})(?:\s*-\s*(?P<end>\d{1,4}))?"
)
IBID_RE = re.compile(r"\bibid\.?,?", re.IGNORECASE)
VIDE_RE = re.compile(r"\bvid(?:e|\.?|\.)\b", re.IGNORECASE)


@dataclass
class Segment:
    file_path: str
    text: str


def normalize_lemma(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    text = text.replace("—", " ")
    text = text.strip(" ,.;:")
    text = re.sub(r"\s+", " ", text)
    return text or None


def sort_norm(text: str | None) -> str | None:
    if not text:
        return None
    text = text.lower()
    text = text.replace("æ", "ae").replace("œ", "oe").replace("ß", "ss")
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def first_heading_letter(text: str | None) -> str | None:
    if not text:
        return None
    m = FIRST_LETTER_RE.search(text)
    if not m:
        return None
    ch = m.group(0)
    if ch in {"Æ", "æ"}:
        return "A"
    if ch in {"Œ", "œ"}:
        return "O"
    if ch in {"Ἀ"}:
        return "A"
    return ch.upper()


def iter_volume_files() -> list[Path]:
    return [SOURCE_ROOT / f"{PREFIX}-{i:03d}.txt" for i in range(550, 592)]


def page_num_from_path(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def body_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parsed = parse_ocr_page_xml(raw)
    lines: list[str] = []
    for line in parsed["body_text"].splitlines():
        s = line.strip()
        if not s:
            continue
        if HEAD_RE.match(s):
            continue
        if PAGE_ONLY_RE.match(s):
            continue
        if LETTER_ONLY_RE.match(s):
            continue
        if any(marker in s for marker in STRIP_HEADINGS):
            # Remove page headers but keep any mixed content line below.
            if s in STRIP_HEADINGS or s.endswith("ANNALES.") or s.startswith("ORDO RERUM"):
                continue
        lines.append(s)
    return lines


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=(?:[A-ZÆŒΆ-ΩἈ]|—))", text)
    return [p.strip() for p in parts if p.strip()]


def merge_fragments(parts: list[str]) -> list[str]:
    merged: list[str] = []
    for part in parts:
        if not merged:
            merged.append(part)
            continue
        if re.fullmatch(r"(?:ibid\.?,?|idem\.?,?|ibid|idem)", part, flags=re.IGNORECASE):
            merged[-1] = f"{merged[-1]} {part}"
            continue
        if re.fullmatch(r"[IVXLCDM]{1,6}\.?", part):
            merged[-1] = f"{merged[-1]} {part}"
            continue
        if re.fullmatch(r"\d{1,4}\.?", part):
            merged[-1] = f"{merged[-1]} {part}"
            continue
        merged.append(part)
    return merged


def extract_segments_section1(files: Iterable[Path]) -> list[Segment]:
    segments: list[Segment] = []
    carry = ""
    carry_file: Path | None = None
    for path in files:
        text = " ".join(body_lines(path))
        if not text:
            continue
        if carry:
            text = f"{carry} {text}"
            carry = ""
        parts = split_sentences(text)
        if text and text[-1] not in ".!?":
            if parts:
                carry = parts.pop()
                carry_file = path
        parts = merge_fragments(parts)
        for part in parts:
            if part:
                segments.append(Segment(str(path), part))
    if carry:
        segments.append(Segment(str(carry_file or files[-1]), carry))
    return segments


def split_ordo_page(path: Path) -> tuple[list[str], list[str]]:
    lines = body_lines(path)
    before: list[str] = []
    after: list[str] = []
    in_after = False
    for line in lines:
        if "JOANNES ZONARAS." in line or "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR" in line:
            in_after = True
            # keep any content after the split marker if the line is mixed
            marker = "JOANNES ZONARAS." if "JOANNES ZONARAS." in line else "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
            tail = line.split(marker, 1)
            if len(tail) == 2 and tail[1].strip():
                after.append(tail[1].strip())
            continue
        if in_after:
            after.append(line)
        else:
            before.append(line)
    return before, after


def extract_segments_section2(path: Path) -> list[Segment]:
    before, after = split_ordo_page(path)
    text = " ".join(after)
    parts = split_sentences(text)
    parts = merge_fragments(parts)
    return [Segment(str(path), part) for part in parts if part]


def parse_refs(segment: str, last_explicit: int | None) -> tuple[list[dict], int | None]:
    refs: list[dict] = []
    explicit_values: list[tuple[str, int | None, str | None, str]] = []
    for m in NUM_REF_RE.finditer(segment):
        prefix = (m.group("prefix") or "").strip()
        num = int(m.group("num"))
        end = m.group("end")
        if prefix.lower().startswith("b"):
            raw_prefix = "b "
        elif prefix:
            raw_prefix = prefix + " "
        else:
            raw_prefix = ""
        if end:
            explicit_values.append((m.group(0).strip(), num, int(end), "editorial_range"))
        else:
            explicit_values.append((m.group(0).strip(), num, None, "editorial_page"))
    if not explicit_values and IBID_RE.search(segment):
        if last_explicit is not None:
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": "ibid.",
                    "page_ref_raw": "ibid.",
                    "page_ref_int": last_explicit,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "confidence": 0.55,
                    "raw_json": {"inherited_from_previous_ref": last_explicit},
                }
            )
        return refs, last_explicit
    for raw, start, end, kind in explicit_values:
        if kind == "editorial_range" and end is not None:
            refs.append(
                {
                    "ref_kind": kind,
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(start),
                    "range_end_raw": str(end),
                    "confidence": 0.9,
                    "raw_json": {},
                }
            )
        else:
            refs.append(
                {
                    "ref_kind": kind,
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "confidence": 0.9,
                    "raw_json": {},
                }
            )
        last_explicit = start
    return refs, last_explicit


def entry_kind_for(segment: str) -> str:
    if VIDE_RE.search(segment) and not NUM_REF_RE.search(segment):
        return "cross_reference"
    if re.search(r"^\s*[IVXLCDM]{1,6}\.\s", segment):
        return "heading_group"
    if re.search(r"^\s*[A-ZÆŒΆ-ΩἈ]\s*$", segment):
        return "heading_group"
    return "lemma"


def build_entries(segments: list[Segment], section_key: str, section_start_file: str, initial_order: int = 1) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    refs: list[dict] = []
    node_map: dict[str, str] = {}
    node_order_counter: dict[str, int] = defaultdict(int)
    last_explicit_ref: int | None = None
    current_letter: str | None = None

    for idx, seg in enumerate(segments, start=initial_order):
        lemma = None
        if "," in seg.text:
            lemma = seg.text.split(",", 1)[0].strip()
        else:
            lemma = seg.text.split(".", 1)[0].strip()
        lemma = normalize_lemma(lemma)
        if not lemma:
            continue

        letter = first_heading_letter(lemma)
        if letter != current_letter and letter:
            current_letter = letter
            if letter not in node_map:
                node_order_counter[letter] += 1
                node_map[letter] = f"PG135:node:analytic_subject:letter:{letter}"

        e_kind = entry_kind_for(seg.text)
        if e_kind == "heading_group":
            lemma_display = lemma
        else:
            lemma_display = lemma
        entry_key = f"PG135:entry:{len(entries)+1:06d}"
        page_num = page_num_from_path(Path(seg.file_path))
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": node_map.get(letter) if letter else None,
            "entry_order": len(entries) + 1,
            "entry_kind": e_kind,
            "lemma_raw": lemma,
            "lemma_display": lemma_display,
            "lemma_norm": sort_norm(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": seg.text,
            "context_raw": None,
            "heading_letter": letter,
            "inferred_printed_page": None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": seg.file_path,
            "target_file_best": seg.file_path,
            "confidence": 0.84,
            "raw_json": {
                "source_file_seq": page_num,
            },
        }
        entry_refs, last_explicit_ref = parse_refs(seg.text, last_explicit_ref)
        if entry_refs:
            for ref_idx, ref in enumerate(entry_refs, start=1):
                ref["entry_key"] = entry_key
                ref["ref_order"] = ref_idx
                ref["target_file"] = seg.file_path
                ref["target_file_probability"] = 0.84
                ref["section_start_file"] = section_start_file
                ref["editorial_anchor_file"] = seg.file_path
                refs.append(ref)
        entries.append(entry)
    return entries, refs


def build_ordo_entries(segments: list[Segment], section_start_file: str) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    refs: list[dict] = []
    last_explicit_ref: int | None = None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        lemma = text.split(",", 1)[0].strip()
        lemma = normalize_lemma(lemma)
        if not lemma:
            continue
        e_kind = "heading_group"
        if re.match(r"^(?:LIBER|ORATIO|EPISTOLA|DIALOGUS|SUPPLICATIO|ALLOCUTIO|LAUDATIO|CANON|EXPOSITIO|DE |INDEX )", lemma, flags=re.IGNORECASE):
            e_kind = "heading_group"
        entry_key = f"PG135:entry:ordo:{len(entries)+1:06d}"
        refs_for_entry, last_explicit_ref = parse_refs(text, last_explicit_ref)
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION2["section_key"],
            "parent_node_key": None,
            "entry_order": len(entries) + 1,
            "entry_kind": e_kind,
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": sort_norm(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": text,
            "context_raw": None,
            "heading_letter": first_heading_letter(lemma),
            "inferred_printed_page": None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": seg.file_path,
            "target_file_best": seg.file_path,
            "confidence": 0.8,
            "raw_json": {},
        }
        if refs_for_entry:
            for i, ref in enumerate(refs_for_entry, start=1):
                ref["entry_key"] = entry_key
                ref["ref_order"] = i
                ref["target_file"] = seg.file_path
                ref["target_file_probability"] = 0.8
                ref["section_start_file"] = section_start_file
                ref["editorial_anchor_file"] = seg.file_path
                refs.append(ref)
        entries.append(entry)
    return entries, refs


def build_helper_request() -> dict:
    entries = [
        {
            "entry_id": "pg135_zonaras_aaron_039",
            "lemma_raw": "Aaron sacerdos designatur",
            "query_names": ["Aaron sacerdos designatur", "Aaron", "sacerdos"],
            "page_hints": ["39"],
            "page_hint_ints": [39],
            "context_raw": "Aaron sacerdos designatur, 39. Aaron vitulum aureum confla, 37.",
        },
        {
            "entry_id": "pg135_zonaras_abdenago_117",
            "lemma_raw": "Abdenago pro Azaria",
            "query_names": ["Abdenago pro Azaria", "Azaria", "Abdenago"],
            "page_hints": ["117"],
            "page_hint_ints": [117],
            "context_raw": "Abdenago pro Azaria. 117.",
        },
        {
            "entry_id": "pg135_zonaras_trajanus_585",
            "lemma_raw": "Trajani cruenta de Dacia victoriæ",
            "query_names": ["Trajani cruenta de Dacia victoriæ", "Trajani obitus", "Trajaniana persecutio"],
            "page_hints": ["585", "588"],
            "page_hint_ints": [585, 588],
            "context_raw": "Trajaniana persecutio, 558. Trajani cruenta de Dacia victoriæ, 585. Trajani obitus, 588.",
        },
        {
            "entry_id": "pg135_zonaras_zonaras_006",
            "lemma_raw": "Zonaræ prolixa enumeratio partium historiæ",
            "query_names": ["Zonaræ prolixa enumeratio partium historiæ", "Zonaras", "historiam scripserit"],
            "page_hints": ["6"],
            "page_hint_ints": [6],
            "context_raw": "Zonaræ prolixa enumeratio partium historiæ, 6. Zonaras cur historiam scripserit, 2.",
        },
        {
            "entry_id": "pg135_zonaras_ordo_439",
            "lemma_raw": "I. De electionibus pontificum",
            "query_names": ["I. De electionibus pontificum", "NOVELLÆ CONSTITUTIONES", "ISAACIUS ANGELUS IMP. CP."],
            "page_hints": ["439"],
            "page_hint_ints": [439],
            "context_raw": "I. De electionibus pontificum. 439. II. Ut tondeantur electorum pontificum uxores. 449.",
        },
    ]
    return {
        "volume_id": "PG135",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def main() -> None:
    files = iter_volume_files()
    section1_segments = extract_segments_section1(files[:-1])
    # Add the pre-ordo tail from page 591 to the alphabetical section.
    before, after = split_ordo_page(files[-1])
    if before:
        tail_text = " ".join(before)
        tail_parts = merge_fragments(split_sentences(tail_text))
        section1_segments.extend(Segment(str(files[-1]), part) for part in tail_parts if part)
    section2_segments = extract_segments_section2(files[-1])

    section1_entries, section1_refs = build_entries(
        section1_segments,
        SECTION1["section_key"],
        SECTION1["file_start"],
    )
    section2_entries, section2_refs = build_ordo_entries(
        section2_segments,
        SECTION2["file_start"],
    )

    nodes = []
    seen_letters: set[str] = set()
    node_index = 1
    for entry in section1_entries:
        letter = entry["heading_letter"]
        if not letter or letter in seen_letters:
            continue
        seen_letters.add(letter)
        nodes.append(
            {
                "node_key": f"PG135:node:analytic_subject:letter:{letter}",
                "section_key": SECTION1["section_key"],
                "parent_node_key": None,
                "node_order": node_index,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.9,
                "raw_json": {"source_token": letter},
            }
        )
        node_index += 1

    sections = [
        {
            "section_key": SECTION1["section_key"],
            "volume_id": "PG135",
            "work_key": None,
            "section_order": 1,
            "section_kind": SECTION1["section_kind"],
            "heading_raw": SECTION1["heading_raw"],
            "heading_norm": sort_norm(SECTION1["heading_raw"]),
            "heading_letter": None,
            "page_start": SECTION1["page_start"],
            "page_end": SECTION1["page_end"],
            "file_start": SECTION1["file_start"],
            "file_end": SECTION1["file_end"],
            "confidence": 0.95,
            "raw_json": {
                "section_start_heading": SECTION1["heading_raw"],
                "section_kind_reason": "Alphabetical index of memorable things and names in Zonaras, with letter-group headings and numeric locators.",
                "evidence_files": [SECTION1["file_start"], SECTION1["file_end"]],
            },
        },
        {
            "section_key": SECTION2["section_key"],
            "volume_id": "PG135",
            "work_key": None,
            "section_order": 2,
            "section_kind": SECTION2["section_kind"],
            "heading_raw": SECTION2["heading_raw"],
            "heading_norm": sort_norm(SECTION2["heading_raw"]),
            "heading_letter": None,
            "page_start": SECTION2["page_start"],
            "page_end": SECTION2["page_end"],
            "file_start": SECTION2["file_start"],
            "file_end": SECTION2["file_end"],
            "confidence": 0.9,
            "raw_json": {
                "section_start_heading": SECTION2["heading_raw"],
                "section_kind_reason": "Editorial table of contents / order of contents at the end of the volume.",
                "evidence_files": [SECTION2["file_start"]],
            },
        },
    ]

    all_entries = section1_entries + section2_entries
    all_refs = section1_refs + section2_refs

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PG135",
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Joannes Zonaras, Annalium Continuatio",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered from OCR pages 550-591, including the closing ordo rerum page.",
            "evidence_files": [SECTION1["file_start"], SECTION1["file_end"], SECTION2["file_start"]],
        },
        "notes": [
            {
                "note_kind": "extraction",
                "text": "Draft built from OCR page text and preserved literals, with bare ibid. inherited only when a prior explicit locator exists in the same run.",
            }
        ],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HELPER_REQUEST_PATH.write_text(json.dumps(build_helper_request(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE_DIR / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": "PG135",
                "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "current_focus": "Validate draft extraction and helper resolution for Zonaras index",
                "completed": [
                    "parsed OCR pages 550-591",
                    "separated analytical index and ordo rerum",
                    "wrote helper request draft",
                ],
                "pending": [
                    "run helper and inspect a few ambiguous anchors",
                    "validate final payload structure",
                ],
                "blocked": [],
                "notes": [
                    "Index OCR is highly segmented; sentence boundaries are approximate and should be spot-checked on a sample of entries.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

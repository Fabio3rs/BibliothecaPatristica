#!/usr/bin/env python3
"""Build PO015 alphabetical payload.

Usage:
  python scripts/pipeline_index_extraction/build_po015_alphabetical_payload.py --prepare-helper
  python scripts/index_target_locator.py --input data/alphabetical_index_payloads/PO015_helper_request.json --output data/alphabetical_index_payloads/PO015_helper_output.json --pretty
  python scripts/pipeline_index_extraction/build_po015_alphabetical_payload.py --assemble
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.common import page_number, page_sort_key
from patristica_pipeline.ocr_xml_utils import read_ocr_page


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PO015/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PO015_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PO015_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PO015_helper_output.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PO015"

SCRIPTURE_SECTION_KEY = "PO015:alpha:scripture_citations:001"
ONOMASTIC_SECTION_KEY = "PO015:alpha:onomastic_mixed:001"

BOOKS = {
    "GEN.": "Gênesis",
    "EXOD.": "Êxodo",
    "NUM.": "Números",
    "LEVIT.": "Levítico",
    "DEUTER.": "Deuteronômio",
    "JOS.": "Josué",
    "III REG.": "3 Reis",
    "IV REG.": "4 Reis",
    "PSALM.": "Salmos",
    "PSALM..": "Salmos",
    "JOB": "Jó",
    "SAP. SALOM.": "Sabedoria",
    "ECCLI.": "Eclesiástico",
    "OSEE": "Oséias",
    "MICH.": "Miquéias",
    "JOEL": "Joel",
    "JONAS": "Jonas",
    "HABAC.": "Habacuque",
    "ZACHAR.": "Zacarias",
    "MALACH.": "Malaquias",
    "ISA.": "Isaías",
    "JEREM.": "Jeremias",
    "BARUCH": "Baruc",
    "EZECH.": "Ezequiel",
    "DAN.": "Daniel",
    "MATTH.": "São Mateus",
    "MARC.": "São Marcos",
    "LUC.": "São Lucas",
    "JOAN.": "São João",
    "ACT. APOST.": "Atos dos Apóstolos",
    "ROM.": "Romanos",
    "I COR.": "1 Coríntios",
    "II COR.": "2 Coríntios",
    "GAL.": "Gálatas",
    "PHILIP.": "Filipenses",
    "I TIM.": "1 Timóteo",
    "II TIM.": "2 Timóteo",
    "HEB.": "Hebreus",
    "I PETR.": "1 Pedro",
    "APOC.": "Apocalipse",
}
BOOK_LABELS = sorted(BOOKS, key=len, reverse=True)

ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}


def roman_to_int(value: str | None) -> int | None:
    if not value:
        return None
    value = re.sub(r"[^IVXLCDM]", "", value.upper())
    if not value:
        return None
    total = 0
    prev = 0
    for char in reversed(value):
        num = ROMAN_VALUES.get(char, 0)
        total += -num if num < prev else num
        prev = max(prev, num)
    return total or None


def clean_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(ch) != "Mn"
    )
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def scripture_blocks() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for seq in range(293, 297):
        path = next(SOURCE_ROOT.glob(f"*-{seq:03d}.txt"))
        page = read_ocr_page(path)
        for block in page.blocks:
            if block.tipo != "texto_principal":
                continue
            text = block.content_clean.strip()
            if not text or "[ornamento" in text:
                continue
            if seq == 295 and "|" in text:
                left: list[str] = []
                right: list[str] = []
                for line in text.splitlines():
                    if "|" in line:
                        a, b = line.split("|", 1)
                        left.append(a.strip())
                        right.append(b.strip())
                    else:
                        left.append(line.strip())
                items.append((path, "\n".join(left)))
                items.append((path, "\n".join(right)))
            else:
                items.append((path, text))
    return items


def split_index_line(line: str) -> tuple[str, str, str] | None:
    line = re.sub(r"\s+", " ", line.strip())
    if not line:
        return None
    parts = re.split(r"\s+(?:\.\s*){2,}", line, maxsplit=1)
    if len(parts) != 2:
        return None
    citation = parts[0].strip()
    locator = parts[1].strip().rstrip()
    if not re.match(r"\d", locator):
        return None
    return citation, locator, line


def parse_material_locator(locator: str) -> tuple[int | None, str | None]:
    fixed = locator.replace(";", ",")
    match = re.match(r"(?P<page>\d+)(?:\s*[,\.]\s*(?P<line>.*?))?\.?$", fixed)
    if not match:
        return None, None
    return int(match.group("page")), (match.group("line") or None)


def starts_book(citation: str) -> tuple[str, str] | None:
    upper = citation.upper()
    for label in BOOK_LABELS:
        if upper.startswith(label):
            return citation[: len(label)].strip(), citation[len(label) :].strip(" ,.")
    return None


def first_verse_numbers(ref_tail: str) -> tuple[int | None, int | None, int | None, int | None, bool]:
    chapter_start = verse_start = chapter_end = verse_end = None
    is_range = False
    text = ref_tail.strip()
    if "," in text:
        chapter_raw, verse_raw = text.split(",", 1)
    else:
        chapter_raw, verse_raw = text, ""
    chapter_raw = chapter_raw.strip()
    chapter_start = int(chapter_raw) if chapter_raw.isdigit() else roman_to_int(chapter_raw)
    verse_match = re.search(r"\d+", verse_raw)
    if verse_match:
        verse_start = int(verse_match.group())
    range_match = re.search(r"\b(\d+)\s*[a-z]?\s*-\s*(?:(?P<chap>[ivxlcdmIVXLCDM]+|\d+)\s*,\s*)?(?P<verse>\d+)", verse_raw)
    if range_match:
        is_range = True
        verse_end = int(range_match.group("verse"))
        if range_match.group("chap"):
            raw = range_match.group("chap")
            chapter_end = int(raw) if raw.isdigit() else roman_to_int(raw)
        else:
            chapter_end = chapter_start
    return chapter_start, verse_start, chapter_end, verse_end, is_range


def resolve_scripture_lines() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_book_raw: str | None = None
    current_book_norm: str | None = None
    current_chapter_raw: str | None = None
    current_ref_tail: str | None = None
    for source_file, block_text in scripture_blocks():
        for line in block_text.splitlines():
            split = split_index_line(line)
            if not split:
                continue
            citation_raw, material_raw, line_raw = split
            book_hit = starts_book(citation_raw)
            inherited_book = False
            inherited_chapter = False
            if book_hit:
                current_book_raw, rest = book_hit
                current_book_norm = BOOKS.get(current_book_raw.upper().rstrip("." ) + ".", BOOKS.get(current_book_raw.upper(), current_book_raw))
                ref_tail = rest
            else:
                inherited_book = True
                rest = citation_raw.strip()
                rest = re.sub(r"^(?:—\s*)+", "", rest).strip(" ,.")
                if not rest:
                    ref_tail = current_ref_tail
                    inherited_chapter = True
                elif "," not in rest and current_chapter_raw:
                    ref_tail = f"{current_chapter_raw}, {rest}"
                    inherited_chapter = True
                else:
                    ref_tail = rest
            if not current_book_raw or not current_book_norm or not ref_tail:
                continue
            if "," in ref_tail:
                current_chapter_raw = ref_tail.split(",", 1)[0].strip()
            current_ref_tail = ref_tail
            page_int, line_ref = parse_material_locator(material_raw)
            chapter_start, verse_start, chapter_end, verse_end, is_range = first_verse_numbers(ref_tail)
            rows.append(
                {
                    "source_file": str(source_file),
                    "file_seq": page_number(source_file),
                    "entry_raw": line_raw,
                    "citation_raw": citation_raw,
                    "material_raw": material_raw,
                    "page_ref_int": page_int,
                    "line_ref_raw": line_ref,
                    "book_raw": current_book_raw,
                    "book_norm": current_book_norm,
                    "ref_raw": ref_tail,
                    "chapter_start": chapter_start,
                    "verse_start": verse_start,
                    "chapter_end": chapter_end,
                    "verse_end": verse_end,
                    "is_range": is_range,
                    "inherited_book": inherited_book,
                    "inherited_chapter": inherited_chapter,
                }
            )
    return rows


def helper_summary(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    candidates = item.get("candidates") or []
    return {
        "status": item.get("status"),
        "best_target_file": (item.get("best_candidate") or {}).get("file"),
        "best_probability": (item.get("best_candidate") or {}).get("probability"),
        "top_candidates": [
            {
                "file": c.get("file"),
                "probability": c.get("probability"),
                "inferred_printed_page": c.get("inferred_printed_page"),
                "candidate_role": c.get("candidate_role"),
                "reason_summary": c.get("reason_summary"),
            }
            for c in candidates[:3]
        ],
    }


def build_scripture_payload(helper_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    for order, row in enumerate(resolve_scripture_lines(), start=1):
        entry_key = f"PO015:entry:bib:{order:04d}"
        helper_id = f"po015_bib_{order:04d}"
        helper = helper_by_id.get(helper_id)
        best = (helper or {}).get("best_candidate") or {}
        target_file = best.get("file")
        probability = best.get("probability")
        lemma = f"{row['book_raw']} {row['ref_raw']}"
        confidence = min(0.95, max(0.72, (probability or 0.72))) if target_file else 0.68
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SCRIPTURE_SECTION_KEY,
                "parent_node_key": None,
                "entry_order": order,
                "entry_kind": "scripture_citation",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": clean_norm(lemma),
                "lemma_sort": clean_norm(lemma),
                "entry_raw": row["entry_raw"],
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": row["page_ref_int"],
                "section_start_file": str(next(SOURCE_ROOT.glob("*-293.txt"))),
                "editorial_anchor_file": row["source_file"],
                "target_file_best": target_file,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_file": row["source_file"],
                    "file_seq": row["file_seq"],
                    "source_section": "TABLE DES CITATIONS BIBLIQUES",
                    "helper_summary": helper_summary(helper),
                    "inheritance": {
                        "book_inherited": row["inherited_book"],
                        "chapter_inherited": row["inherited_chapter"],
                    },
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page_line",
                "ref_raw": row["material_raw"],
                "page_ref_raw": str(row["page_ref_int"]) if row["page_ref_int"] is not None else None,
                "page_ref_int": row["page_ref_int"],
                "page_ref_col": None,
                "line_ref_raw": row["line_ref_raw"],
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": str(next(SOURCE_ROOT.glob("*-293.txt"))),
                "editorial_anchor_file": row["source_file"],
                "confidence": round(confidence, 6),
                "raw_json": {"helper_entry_id": helper_id, "helper_summary": helper_summary(helper)},
            }
        )
        scripture_refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_role": "citation",
                "ref_raw": row["ref_raw"],
                "book_raw": row["book_raw"],
                "book_norm": row["book_norm"],
                "chapter_start": row["chapter_start"],
                "verse_start": row["verse_start"],
                "chapter_end": row["chapter_end"],
                "verse_end": row["verse_end"],
                "is_range": row["is_range"],
                "confidence": 0.86 if row["chapter_start"] else 0.74,
                "raw_json": {
                    "source_line": row["entry_raw"],
                    "inherited_book": row["book_raw"] if row["inherited_book"] else None,
                    "inherited_chapter": row["inherited_chapter"],
                },
            }
        )
    return entries, refs, scripture_refs


def prepare_helper() -> None:
    prior_request = json.loads(HELPER_REQUEST.read_text(encoding="utf-8"))
    entries = list(prior_request["entries"])
    for order, row in enumerate(resolve_scripture_lines(), start=1):
        entries.append(
            {
                "entry_id": f"po015_bib_{order:04d}",
                "lemma_raw": f"{row['book_raw']} {row['ref_raw']}",
                "query_names": [row["book_raw"], row["book_norm"]],
                "page_hints": [str(row["page_ref_int"])] if row["page_ref_int"] is not None else [],
                "page_hint_ints": [row["page_ref_int"]] if row["page_ref_int"] is not None else [],
                "context_raw": row["entry_raw"],
            }
        )
    payload = {
        "volume_id": "PO015",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }
    HELPER_REQUEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "scripture_rows.json").write_text(
        json.dumps(resolve_scripture_lines(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def assemble() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    helper = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    helper_by_id = {item["entry_id"]: item for item in helper.get("entries", [])}
    bib_entries, bib_refs, bib_scripture_refs = build_scripture_payload(helper_by_id)

    scripture_section = {
        "section_key": SCRIPTURE_SECTION_KEY,
        "volume_id": "PO015",
        "work_key": None,
        "section_order": 1,
        "section_kind": "scripture_index",
        "heading_raw": "TABLE DES CITATIONS BIBLIQUES",
        "heading_norm": "table des citations bibliques",
        "heading_letter": None,
        "page_start": 285,
        "page_end": 288,
        "file_start": str(next(SOURCE_ROOT.glob("*-293.txt"))),
        "file_end": str(next(SOURCE_ROOT.glob("*-296.txt"))),
        "confidence": 0.98,
        "raw_json": {
            "source": "Detected scripture citation table in candidate window.",
            "section_scope": "TABLE DES CITATIONS BIBLIQUES on OCR files 293-296",
            "section_kind_reason": "Biblical book headings with chapter/verse rows and material page-line locators.",
            "neighbor_check": [
                "OCR file 292 is body text before the table.",
                "OCR file 297 starts TABLE DES NOMS PROPRES.",
            ],
        },
    }

    sections = [scripture_section]
    for section in payload["sections"]:
        if section["section_key"] == ONOMASTIC_SECTION_KEY:
            section = dict(section)
            section["section_order"] = 2
            section["raw_json"] = dict(section.get("raw_json") or {})
            section["raw_json"]["section_kind_reason"] = "Greek proper-name table; biblical citation names are explicitly excluded by the printed note."
        sections.append(section)

    payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["volume"]["notes"] = [
        "Scripture citation table extracted from TABLE DES CITATIONS BIBLIQUES on OCR files 293-296.",
        "Onomastic index extracted from TABLE DES NOMS PROPRES on OCR files 297-299.",
        "OCR file 300 is a final contents index and is not serialized as an alphabetical index payload section.",
    ]
    payload["sections"] = sections
    payload["entries"] = bib_entries + payload["entries"]
    payload["refs"] = bib_refs + payload["refs"]
    payload["scripture_refs"] = bib_scripture_refs
    payload["coverage"] = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the scripture citation table and the onomastic table from OCR files 293-299.",
        "evidence_files": [
            str(next(SOURCE_ROOT.glob(f"*-{seq:03d}.txt"))) for seq in range(293, 300)
        ],
    }
    payload["notes"] = [
        "TABLE DES CITATIONS BIBLIQUES begins on OCR file 293 and continues through OCR file 296.",
        "TABLE DES NOMS PROPRES begins on OCR file 297 and continues through OCR file 299.",
        "Biblical rows with dash leaders inherit book and chapter only from the nearest explicit biblical heading.",
        "Printed page/material references inside table rows are kept separate from OCR file suffixes and biblical chapter/verse references.",
    ]
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = {
        "volume_id": "PO015",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "current_focus": "PO015 payload assembled and validated locally",
        "completed": [
            "verified scripture and onomastic section windows in OCR",
            "generated combined helper request",
            "assembled scripture entries, material refs, and scripture_refs",
            "preserved existing onomastic entries and refs",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "OCR file 300 is a contents index, not an alphabetical/scripture table section.",
            "The helper output now includes po015_bib_* entries as well as prior po015_noms_* entries.",
        ],
    }
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-helper", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    if args.prepare_helper:
        prepare_helper()
    if args.assemble:
        assemble()
    if not args.prepare_helper and not args.assemble:
        parser.error("choose --prepare-helper or --assemble")


if __name__ == "__main__":
    main()

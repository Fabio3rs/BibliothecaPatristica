#!/usr/bin/env python3
"""Build PO016 scripture-index payload from verified OCR table lines.

Usage: python scripts/pipeline_index_extraction/build_po016_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "teste/PO016/text"
OUT = PROJECT_ROOT / "data/alphabetical_index_payloads/PO016_alphabetical_indices.json"
HELPER_REQUEST = PROJECT_ROOT / "data/alphabetical_index_payloads/PO016_helper_request.json"
INTERMEDIATE = PROJECT_ROOT / "data/intermediate_payloads/PO016"


BOOK_NORMS = {
    "Actes": "Atos",
    "Apocalypse": "Apocalipse",
    "Baruch": "Baruc",
    "Daniel": "Daniel",
    "Deutéronome": "Deuteronômio",
    "Exode": "Êxodo",
    "Galates": "Gálatas",
    "Genèse": "Gênesis",
    "Hébr.": "Hebreus",
    "Hébreux": "Hebreus",
    "I Cor.": "1 Coríntios",
    "I Corinth.": "1 Coríntios",
    "I Jean": "1 João",
    "I Pierre": "1 Pedro",
    "I Thessal.": "1 Tessalonicenses",
    "I Timothée": "1 Timóteo",
    "II Cor.": "2 Coríntios",
    "II Corinth.": "2 Coríntios",
    "II Timothée": "2 Timóteo",
    "Isaïe": "Isaías",
    "Jacques": "Tiago",
    "Jean": "São João",
    "Lévitique": "Levítico",
    "Luc": "São Lucas",
    "Marc": "São Marcos",
    "Matthieu": "São Mateus",
    "Proverbes": "Provérbios",
    "Psaumes": "Salmos",
    "Rom.": "Romanos",
    "Romains": "Romanos",
    "Tite": "Tito",
}

ROMAN = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
    "XXI": 21,
    "XXII": 22,
    "XXIII": 23,
    "XXIV": 24,
    "XXV": 25,
    "XXVI": 26,
    "XXVII": 27,
    "XXVIII": 28,
    "XXIX": 29,
    "XXX": 30,
    "XXXI": 31,
    "XXXII": 32,
    "XXXIII": 33,
    "XL": 40,
    "XLII": 42,
    "L": 50,
    "LII": 52,
    "LXI": 61,
    "LXXXIV": 84,
    "XCI": 91,
    "XCV": 95,
    "CIII": 103,
    "CXVI": 116,
    "CXVIII": 118,
    "CXLII": 142,
}


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text


def reader_pages(pages: str) -> list[dict]:
    cmd = [
        "python",
        "scripts/read_ocr_page_text.py",
        "--volume",
        "PO016",
        "--pages",
        pages,
        "--view",
        "body",
        "--show-source",
        "--json",
    ]
    data = subprocess.check_output(cmd, cwd=PROJECT_ROOT, text=True)
    parsed = json.loads(data)
    return parsed if isinstance(parsed, list) else [parsed]


def parse_headers() -> dict[str, dict[int, str]]:
    maps: dict[str, dict[int, str]] = {"perle": {}, "homelie": {}}
    for path in sorted(SOURCE_ROOT.glob("*.txt")):
        try:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        headers = []
        for block in root.iter("bloco"):
            if block.attrib.get("tipo") == "cabecalho":
                headers.append(" ".join("".join(block.itertext()).split()))
        header = " ".join(headers)
        match = re.search(r"\[(\d+)\]", header)
        if not match:
            continue
        printed_page = int(match.group(1))
        abs_path = str(path)
        if "LA PERLE" in header or "CH." in header or "CHAPITRE" in header:
            maps["perle"].setdefault(printed_page, abs_path)
        if "HOMÉLIE DE SÉVÈRE" in header or "VERSIONS DE PAUL" in header:
            maps["homelie"].setdefault(printed_page, abs_path)
    return maps


def split_index_line(line: str) -> tuple[str, list[int]] | None:
    line = line.strip()
    if not line or line in {"Pages", "Pages."} or line.endswith("TESTAMENT."):
        return None
    match = re.match(r"^(.*?)[.\s]+(\d[\d,\s-]*)$", line)
    if not match:
        return None
    left = re.sub(r"(?:\s*\.)+\s*$", "", match.group(1)).strip()
    pages = [int(x) for x in re.findall(r"\d+", match.group(2))]
    return left, pages


def parse_ref_part(ref_part: str) -> dict[str, object]:
    clean = ref_part.replace("(LXX)", "").replace(" et ", ", ")
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    first = re.match(r"([ivxlcdmIVXLCDM]+|\d+)", clean)
    chapter = None
    if first:
        token = first.group(1)
        chapter = ROMAN.get(token.upper()) if token.isalpha() else int(token)
    rest = clean[first.end():].strip(" ,") if first else clean
    nums = [int(x) for x in re.findall(r"\d+", rest)]
    verse_start = nums[0] if nums else None
    verse_end = None
    is_range = bool(re.search(r"\d+\s*-\s*\d+", rest))
    if is_range and len(nums) >= 2:
        verse_end = nums[1]
    return {
        "chapter_start": chapter,
        "verse_start": verse_start,
        "chapter_end": None,
        "verse_end": verse_end,
        "is_range": is_range,
    }


def suffix_map() -> dict[int, str]:
    mapped = {}
    for path in SOURCE_ROOT.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            mapped.setdefault(int(match.group(1)), str(path))
    return mapped


def resolve_target(page_ref: int, target_map: dict[int, str], suffixes: dict[int, str], fallback_offset: int) -> tuple[str | None, str]:
    if page_ref in target_map:
        return target_map[page_ref], "printed_page_header_map"
    fallback = suffixes.get(page_ref + fallback_offset)
    if fallback:
        return fallback, "local_pagination_offset_fallback"
    return None, "unresolved_after_header_and_offset"


def parse_entries(
    section_key: str,
    pages: list[dict],
    target_map: dict[int, str],
    suffixes: dict[int, str],
    fallback_offset: int,
    start_order: int = 1,
):
    entries = []
    refs = []
    scripture_refs = []
    nodes = []
    current_book = None
    current_testament_node = None
    node_order = 0
    entry_order = start_order

    for page in pages:
        for block in page["blocks"]:
            if block.get("tipo") != "texto_principal":
                continue
            for raw_line in block["content_clean"].splitlines():
                line = raw_line.strip()
                if not line or line in {"Pages", "Pages."}:
                    continue
                if line.endswith("TESTAMENT."):
                    node_order += 1
                    current_testament_node = f"{section_key}:node:{node_order:03d}"
                    nodes.append({
                        "node_key": current_testament_node,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "heading_group",
                        "label_raw": line,
                        "label_norm": line.rstrip(".").title(),
                        "label_sort": normalize_key(line),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(PROJECT_ROOT / page["file"])},
                    })
                    continue
                split = split_index_line(line)
                if not split:
                    continue
                left, page_refs = split
                if left.startswith("—"):
                    if not current_book:
                        continue
                    ref_part = left.lstrip("—").strip()
                    book_raw = current_book
                else:
                    book_match = re.match(r"(.+?),\s*(.+)$", left)
                    if not book_match:
                        continue
                    book_raw = book_match.group(1).strip()
                    ref_part = book_match.group(2).strip()
                    current_book = book_raw
                    node_order += 1
                    parent = current_testament_node
                    node_key = f"{section_key}:node:{node_order:03d}"
                    nodes.append({
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": parent,
                        "node_order": node_order,
                        "node_kind": "heading_group",
                        "label_raw": book_raw,
                        "label_norm": BOOK_NORMS.get(book_raw, book_raw),
                        "label_sort": normalize_key(book_raw),
                        "node_level": 2 if parent else 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(PROJECT_ROOT / page["file"])},
                    })

                entry_key = f"PO016:entry:{entry_order:04d}"
                lemma_raw = f"{book_raw}, {ref_part}"
                source_file = str(PROJECT_ROOT / page["file"])
                target_best = None
                target_method = "no_page_ref"
                if page_refs:
                    target_best, target_method = resolve_target(page_refs[0], target_map, suffixes, fallback_offset)
                entries.append({
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "scripture_citation",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": f"{BOOK_NORMS.get(book_raw, book_raw)} {ref_part}",
                    "lemma_sort": normalize_key(lemma_raw),
                    "entry_raw": line,
                    "context_raw": None,
                    "heading_letter": None,
                    "inferred_printed_page": None,
                    "section_start_file": str(PROJECT_ROOT / pages[0]["file"]),
                    "editorial_anchor_file": source_file,
                    "target_file_best": target_best,
                    "confidence": 0.94 if target_best else 0.86,
                    "raw_json": {
                        "source_file": source_file,
                        "source_file_seq": page["file_seq"],
                        "page_refs": page_refs,
                        "book_inheritance": "explicit" if not left.startswith("—") else "inherited_from_previous_book",
                        "target_locator_method": target_method,
                    },
                })
                if len(re.findall(r"\d+", ref_part)) <= 6:
                    parsed = parse_ref_part(ref_part)
                    scripture_refs.append({
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_role": "citation",
                        "ref_raw": lemma_raw,
                        "book_raw": book_raw,
                        "book_norm": BOOK_NORMS.get(book_raw, book_raw),
                        **parsed,
                        "confidence": 0.9 if parsed["chapter_start"] else 0.75,
                        "raw_json": {"source_entry_raw": line},
                    })
                else:
                    entries[-1]["raw_json"]["scripture_ref_parse_status"] = "omitted_complex_multi_verse_list"
                for ref_order, page_ref in enumerate(page_refs, 1):
                    target_file, locator_method = resolve_target(page_ref, target_map, suffixes, fallback_offset)
                    refs.append({
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(page_ref),
                        "page_ref_raw": str(page_ref),
                        "page_ref_int": page_ref,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": 0.94 if target_file else None,
                        "section_start_file": str(PROJECT_ROOT / pages[0]["file"]),
                        "editorial_anchor_file": source_file,
                        "confidence": 0.94 if target_file else 0.8,
                        "raw_json": {"source_entry_raw": line, "locator_method": locator_method},
                    })
                entry_order += 1
    return nodes, entries, refs, scripture_refs


def main() -> None:
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    page_maps = parse_headers()
    suffixes = suffix_map()
    perle_pages = reader_pages("771-772")
    homelie_pages = reader_pages("877")

    sections = [
        {
            "section_key": "PO016:alpha:scripture_index:001",
            "volume_id": "PO016",
            "work_key": None,
            "section_order": 1,
            "section_kind": "scripture_index",
            "heading_raw": "TABLE DES CITATIONS DE L'ÉCRITURE",
            "heading_norm": "Table des citations de l'Écriture",
            "heading_letter": None,
            "page_start": 167,
            "page_end": 168,
            "file_start": str(PROJECT_ROOT / perle_pages[0]["file"]),
            "file_end": str(PROJECT_ROOT / perle_pages[-1]["file"]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Scripture citation table for La Perle précieuse, listed in the fascicle table of contents.",
                "neighboring_pages_checked": ["770", "773", "774"],
            },
        },
        {
            "section_key": "PO016:alpha:scripture_index:002",
            "volume_id": "PO016",
            "work_key": None,
            "section_order": 2,
            "section_kind": "scripture_index",
            "heading_raw": "TABLE DES CITATIONS DE LA SAINTE ÉCRITURE",
            "heading_norm": "Table des citations de la Sainte Écriture",
            "heading_letter": None,
            "page_start": 103,
            "page_end": 103,
            "file_start": str(PROJECT_ROOT / homelie_pages[0]["file"]),
            "file_end": str(PROJECT_ROOT / homelie_pages[-1]["file"]),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Scripture citation table for Homélie LXXVII, followed by table of contents and catalogue pages.",
                "neighboring_pages_checked": ["876", "878", "879"],
            },
        },
    ]

    nodes1, entries1, refs1, scripture1 = parse_entries(
        sections[0]["section_key"], perle_pages, page_maps["perle"], suffixes, 604, 1
    )
    nodes2, entries2, refs2, scripture2 = parse_entries(
        sections[1]["section_key"], homelie_pages, page_maps["homelie"], suffixes, 774, len(entries1) + 1
    )
    nodes = nodes1 + nodes2
    entries = entries1 + entries2
    refs = refs1 + refs2
    scripture_refs = scripture1 + scripture2

    helper_entries = []
    for entry in entries:
        hints = entry["raw_json"].get("page_refs", [])
        if not hints:
            continue
        helper_entries.append({
            "entry_id": entry["entry_key"].replace(":", "_").lower(),
            "lemma_raw": entry["lemma_raw"],
            "query_names": [entry["lemma_raw"], entry["lemma_norm"]],
            "page_hints": [str(h) for h in hints[:3]],
            "page_hint_ints": hints[:3],
            "context_raw": entry["entry_raw"],
        })
    HELPER_REQUEST.write_text(json.dumps({
        "volume_id": "PO016",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": 1,
        "generated_at": now,
        "volume": {
            "volume_id": "PO016",
            "collection": "PO",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PO016",
            "notes": [
                "Rerun corrected the previous empty checkpoint: OCR files 771-772 and 877 contain real scripture citation indexes.",
                "Closing catalogue pages 887-891 were verified as editorial catalogue material and are not serialized as alphabetical index entries.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "All recoverable line items from the two verified scripture citation tables were parsed; neighboring catalogue and table-of-contents pages were excluded.",
            "evidence_files": [
                str(PROJECT_ROOT / perle_pages[0]["file"]),
                str(PROJECT_ROOT / perle_pages[-1]["file"]),
                str(PROJECT_ROOT / homelie_pages[0]["file"]),
                str(SOURCE_ROOT / "b43685fd-6cbe-49e2-9dd3-87cf38a31a4d-878.txt"),
                str(SOURCE_ROOT / "b43685fd-6cbe-49e2-9dd3-87cf38a31a4d-889.txt"),
            ],
            "entry_count": len(entries),
            "ref_count": len(refs),
            "scripture_ref_count": len(scripture_refs),
        },
        "notes": [
            "Sections are scripture indexes, not ordinary alphabetical subject indexes.",
            "Book names are inherited only from explicit printed biblical book labels; section titles are not used as book names.",
            "Material target files were assigned from printed-page signals in local OCR headers, not from OCR file suffix equality.",
        ],
    }
    for name, value in [
        ("sections.json", sections),
        ("nodes.json", nodes),
        ("entries.json", entries),
        ("refs.json", refs),
        ("scripture_refs.json", scripture_refs),
        ("coverage.json", payload["coverage"]),
        ("notes.json", payload["notes"]),
    ]:
        (INTERMEDIATE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "todo.json").write_text(json.dumps({
        "volume_id": "PO016",
        "updated_at": now,
        "current_focus": "PO016 scripture-index payload completed",
        "completed": [
            "verified candidate sections 771-772 and 877",
            "excluded table of contents and catalogue pages",
            "serialized entries, material refs, and scripture refs",
        ],
        "pending": [],
        "blocked": [],
        "notes": ["Previous empty payload was a checkpoint and has been superseded."],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "entries": len(entries),
        "refs": len(refs),
        "scripture_refs": len(scripture_refs),
        "helper_entries": len(helper_entries),
        "written": str(OUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

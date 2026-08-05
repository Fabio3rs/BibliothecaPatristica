#!/usr/bin/env python3
"""Rebuild PO012 alphabetical payload from the prior checkpoint plus OCR-missing sections.

Usage:
  python scripts/pipeline_index_extraction/rebuild_po012_alphabetical_payload.py \
    --checkpoint data/alphabetical_index_payloads/PO012_alphabetical_indices.json \
    --helper-request data/alphabetical_index_payloads/PO012_helper_request.json \
    --helper-output data/alphabetical_index_payloads/PO012_helper_output.json \
    --output data/alphabetical_index_payloads/PO012_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.common import page_number, page_sort_key
from patristica_pipeline.ocr_xml_utils import read_ocr_page

VOLUME_ID = "PO012"
COLLECTION = "PO"
SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PO012/text")

REF_RE = re.compile(
    r"(?<!\w)(?:\d{1,3}(?:[₀-₉_]\d+|[₀-₉]+|[,،]\d+)?(?:[-₋]\d+)?(?:\s*n\.\s*[₀-₉\d]+|\s*n\.\s*\d+)?|n\.\s*\d+)"
)
SYRIAC_RE = re.compile(r"[\u0700-\u074F]")
GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
LEADER_RE = re.compile(r"\s*\.{2,}\s*")

BOOK_MAP = {
    "Genèse": "Gênesis",
    "Exode": "Êxodo",
    "Nombres": "Números",
    "Deutéronome": "Deuteronômio",
    "Psaumes": "Salmos",
    "Isaïe": "Isaías",
    "Jérémie": "Jeremias",
    "Lamentations": "Lamentações",
    "Baruch": "Baruc",
    "Ézéchiel": "Ezequiel",
    "Osée": "Oseias",
    "Joël": "Joel",
    "Amos": "Amós",
    "Michée": "Miqueias",
    "Habakuk": "Habacuque",
    "Zacharie": "Zacarias",
    "Matthieu": "São Mateus",
    "Marc": "São Marcos",
    "Jean": "São João",
    "Actes": "Atos dos Apóstolos",
    "Rom.": "Romanos",
    "Galates": "Gálatas",
    "Éphésiens": "Efésios",
    "I Jean": "1 João",
}

ROMAN_MAP = {
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
    "XXXIV": 34,
    "XXXV": 35,
    "XXXVI": 36,
    "XXXVII": 37,
    "XXXVIII": 38,
    "XXXIX": 39,
    "XL": 40,
    "XLIX": 49,
    "L": 50,
    "LII": 52,
    "LIII": 53,
    "LIV": 54,
    "LVI": 56,
    "LVII": 57,
    "LXI": 61,
    "LXII": 62,
    "LXIII": 63,
    "LXV": 65,
    "LXVI": 66,
}


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip().lower()


def file_by_seq(seq: int) -> str:
    for path in sorted(SOURCE_ROOT.glob("*.txt"), key=page_sort_key):
        if page_number(path) == seq:
            return str(path)
    raise SystemExit(f"Missing OCR file seq {seq}")


def pages_json(seqs: list[int]) -> list[dict[str, Any]]:
    out = []
    for seq in seqs:
        path = Path(file_by_seq(seq))
        page = read_ocr_page(path)
        item = page.to_dict(include_raw=False)
        item["file"] = str(path)
        item["file_seq"] = seq
        out.append(item)
    return out


def body_lines(page: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for block in page["blocks"]:
        if block.get("tipo") != "texto_principal":
            continue
        for line in block.get("content_clean", "").splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if line and line != "Pages.":
                lines.append(line)
    return lines


def starts_index_entry(line: str, scripts: str) -> bool:
    if not line:
        return False
    if line.startswith(("—", "-", "–")):
        return True
    if scripts == "greek":
        return bool(GREEK_RE.search(line))
    return bool(SYRIAC_RE.search(line))


def split_lemma(entry_raw: str) -> tuple[str | None, str | None]:
    match = REF_RE.search(entry_raw)
    if not match:
        return entry_raw.strip(), norm_text(entry_raw)
    lemma = entry_raw[: match.start()].strip(" ,.;")
    return lemma or None, norm_text(lemma) if lemma else None


def material_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs = []
    seen = set()
    for match in REF_RE.finditer(entry_raw):
        raw = re.sub(r"\s+", " ", match.group(0)).strip()
        if not raw or raw in {"n. 1", "n. 2", "n. 3", "n. 4", "n. 5"}:
            continue
        page_m = re.search(r"\d{1,3}", raw)
        if not page_m:
            continue
        key = (raw, page_m.group(0))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": page_m.group(0),
                "page_ref_int": int(page_m.group(0)),
                "line_ref_raw": raw[len(page_m.group(0)) :].strip() or None,
            }
        )
    return refs


def parse_simple_entries(pages: list[dict[str, Any]], scripts: str) -> list[dict[str, Any]]:
    entries = []
    current: list[str] = []
    current_file = None
    for page in pages:
        for line in body_lines(page):
            if scripts == "greek" and re.fullmatch(r"[A-ZΑ-Ω]", line):
                continue
            if starts_index_entry(line, scripts):
                if current:
                    entries.append({"lines": current, "file": current_file})
                current = [line]
                current_file = page["file"]
            elif current:
                current.append(line)
        if current:
            entries.append({"lines": current, "file": current_file})
            current = []
            current_file = None
    return entries


def parse_patristic_entries(page: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for line in body_lines(page):
        if line == "Pages.":
            continue
        entries.append({"lines": [line], "file": page["file"]})
    return entries


def split_bible_line(line: str) -> tuple[str, list[str]]:
    parts = LEADER_RE.split(line)
    if len(parts) < 2:
        return line, []
    left = parts[0].strip(" .")
    right = parts[-1].strip(" .")
    return left, re.findall(r"\d{1,3}", right)


def parse_roman(token: str | None) -> int | None:
    if not token:
        return None
    token = token.strip(".,()")
    return ROMAN_MAP.get(token)


def parse_scripture_left(left: str, inherited_book: str | None, inherited_chapter: int | None) -> tuple[str | None, int | None, int | None, str]:
    explicit_book = None
    for book in sorted(BOOK_MAP, key=len, reverse=True):
        if left.startswith(book + " ") or left.startswith(book + ",") or left == book:
            explicit_book = book
            break
    work = left
    book = inherited_book
    if explicit_book:
        book = explicit_book
        work = left[len(explicit_book) :].lstrip(" ,")
    elif left.startswith("—"):
        work = left.lstrip("— ").strip()
    if not book:
        return None, None, None, work
    roman = re.search(r"\b([IVXLCDM]+)\b", work)
    chapter = parse_roman(roman.group(1)) if roman else inherited_chapter
    tail = work[roman.end() :] if roman else work
    nums = [int(n) for n in re.findall(r"\b\d{1,3}\b", tail)]
    verse = nums[0] if nums else None
    return book, chapter, verse, work


def build_extra_sections_and_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections = [
        {
            "section_key": "PO012:alpha:foreign_terms_syriac:003",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "foreign_terms",
            "heading_raw": "TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES",
            "heading_norm": "table des mots syriaques étrangers ou remarquables",
            "heading_letter": None,
            "page_start": 151,
            "page_end": 156,
            "file_start": file_by_seq(161),
            "file_end": file_by_seq(166),
            "confidence": 0.95,
            "raw_json": {"source": "OCR-confirmed Syriac foreign/remarquable words table.", "section_kind_reason": "Term index, mapped to foreign_terms."},
        },
        {
            "section_key": "PO012:alpha:foreign_terms_greek:004",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 4,
            "section_kind": "foreign_terms",
            "heading_raw": "TABLE DES MOTS GRECS CITÉS DANS LES MSS.",
            "heading_norm": "table des mots grecs cités dans les mss.",
            "heading_letter": None,
            "page_start": 157,
            "page_end": 157,
            "file_start": file_by_seq(167),
            "file_end": file_by_seq(167),
            "confidence": 0.98,
            "raw_json": {"source": "OCR-confirmed Greek words table.", "section_kind_reason": "Term index, mapped to foreign_terms."},
        },
        {
            "section_key": "PO012:alpha:author_index_patristic:005",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 5,
            "section_kind": "author_index",
            "heading_raw": "TABLE DES CITATIONS DES PÈRES DE L'ÉGLISE",
            "heading_norm": "table des citations des pères de l'église",
            "heading_letter": None,
            "page_start": 162,
            "page_end": 162,
            "file_start": file_by_seq(172),
            "file_end": file_by_seq(172),
            "confidence": 0.96,
            "raw_json": {"source": "OCR-confirmed patristic citations table.", "section_kind_reason": "Cited Fathers table, mapped to author_index."},
        },
        {
            "section_key": "PO012:alpha:scripture_index_irenee:006",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 6,
            "section_kind": "scripture_index",
            "heading_raw": "TABLE DES CITATIONS DE L'ÉCRITURE",
            "heading_norm": "table des citations de l'écriture",
            "heading_letter": None,
            "page_start": 745,
            "page_end": 746,
            "file_start": file_by_seq(761),
            "file_end": file_by_seq(762),
            "confidence": 0.97,
            "raw_json": {"source": "OCR-confirmed Scripture citations table for the Saint Irénée material."},
        },
    ]
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    def add_entry(section: dict[str, Any], prefix: str, order: int, raw: str, source_file: str, kind: str = "lemma") -> str:
        lemma, lemma_norm = split_lemma(raw)
        key = f"PO012:entry:{prefix}:{order:04d}"
        first_ref = material_refs(raw)
        entries.append(
            {
                "entry_key": key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": order,
                "entry_kind": kind,
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": lemma_norm,
                "lemma_sort": lemma_norm,
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": first_ref[0]["page_ref_int"] if first_ref else None,
                "section_start_file": section["file_start"],
                "editorial_anchor_file": None,
                "target_file_best": None,
                "confidence": 0.82,
                "raw_json": {"source_file": source_file, "group_lines": raw.splitlines(), "added_on_rerun": True},
            }
        )
        for idx, ref in enumerate(first_ref, start=1):
            refs.append(
                {
                    "entry_key": key,
                    "ref_order": idx,
                    "ref_kind": "editorial_page_line" if ref.get("line_ref_raw") else "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": ref.get("line_ref_raw"),
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": section["file_start"],
                    "editorial_anchor_file": None,
                    "confidence": 0.74,
                    "raw_json": {"source_entry_raw": raw, "added_on_rerun": True},
                }
            )
        return key

    order = 1
    for item in parse_simple_entries(pages_json([161, 162, 163, 164, 165, 166]), "syriac"):
        add_entry(sections[0], "syrterm", order, " ".join(item["lines"]), item["file"], "sublemma" if item["lines"][0].startswith("—") else "lemma")
        order += 1

    order = 1
    node_order = 1
    current_node = None
    for line in body_lines(pages_json([167])[0]):
        if re.fullmatch(r"[A-ZΑ-Ω]", line):
            current_node = f"PO012:node:greek:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_node,
                    "section_key": sections[1]["section_key"],
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": norm_text(line),
                    "label_sort": norm_text(line),
                    "node_level": 1,
                    "confidence": 0.9,
                    "raw_json": {"source_file": sections[1]["file_start"], "added_on_rerun": True},
                }
            )
            node_order += 1
            continue
        key = add_entry(sections[1], "grterm", order, line, sections[1]["file_start"])
        entries[-1]["parent_node_key"] = current_node
        order += 1

    order = 1
    for item in parse_patristic_entries(pages_json([172])[0]):
        kind = "sublemma" if item["lines"][0].startswith("—") else "lemma"
        add_entry(sections[2], "patr", order, item["lines"][0], item["file"], kind)
        order += 1

    order = 1
    current_book = None
    current_chapter = None
    current_node = None
    for page in pages_json([761, 762]):
        for line in body_lines(page):
            if line in {"I. Ancien Testament.", "II. Nouveau Testament."}:
                current_node = f"PO012:node:irenee:{len([n for n in nodes if n['section_key'] == sections[3]['section_key']]) + 1:03d}"
                nodes.append(
                    {
                        "node_key": current_node,
                        "section_key": sections[3]["section_key"],
                        "parent_node_key": None,
                        "node_order": len([n for n in nodes if n["section_key"] == sections[3]["section_key"]]) + 1,
                        "node_kind": "heading_group",
                        "label_raw": line,
                        "label_norm": norm_text(line),
                        "label_sort": norm_text(line),
                        "node_level": 1,
                        "confidence": 0.92,
                        "raw_json": {"source_file": page["file"], "added_on_rerun": True},
                    }
                )
                continue
            left, mat_pages = split_bible_line(line)
            book, chapter, verse, work = parse_scripture_left(left, current_book, current_chapter)
            if book:
                current_book = book
                if chapter:
                    current_chapter = chapter
            key = f"PO012:entry:bib_irenee:{order:04d}"
            entries.append(
                {
                    "entry_key": key,
                    "section_key": sections[3]["section_key"],
                    "parent_node_key": current_node,
                    "entry_order": order,
                    "entry_kind": "scripture_citation",
                    "lemma_raw": left,
                    "lemma_display": left,
                    "lemma_norm": norm_text(left),
                    "lemma_sort": norm_text(left),
                    "entry_raw": line,
                    "context_raw": None,
                    "heading_letter": None,
                    "inferred_printed_page": int(mat_pages[0]) if mat_pages else None,
                    "section_start_file": sections[3]["file_start"],
                    "editorial_anchor_file": None,
                    "target_file_best": None,
                    "confidence": 0.82 if book else 0.55,
                    "raw_json": {"source_file": page["file"], "book_context": current_book, "added_on_rerun": True},
                }
            )
            for idx, mat in enumerate(mat_pages, start=1):
                refs.append(
                    {
                        "entry_key": key,
                        "ref_order": idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": mat,
                        "page_ref_raw": mat,
                        "page_ref_int": int(mat),
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": None,
                        "target_file_probability": None,
                        "section_start_file": sections[3]["file_start"],
                        "editorial_anchor_file": None,
                        "confidence": 0.72,
                        "raw_json": {"source_entry_raw": line, "added_on_rerun": True},
                    }
                )
            if book:
                scripture_refs.append(
                    {
                        "entry_key": key,
                        "ref_order": 1,
                        "ref_role": "citation",
                        "ref_raw": work,
                        "book_raw": book,
                        "book_norm": BOOK_MAP.get(book),
                        "chapter_start": chapter,
                        "verse_start": verse,
                        "chapter_end": chapter,
                        "verse_end": None,
                        "is_range": "-" in work or "suiv" in work,
                        "confidence": 0.75 if not left.startswith("—") else 0.68,
                        "raw_json": {"source_line": line, "inherited_book": None if book in left else book, "added_on_rerun": True},
                    }
                )
            order += 1

    return sections, nodes, entries, refs, scripture_refs


def build_helper_request(payload: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for entry in payload["entries"]:
        page_hints = sorted({r["page_ref_int"] for r in payload["refs"] if r["entry_key"] == entry["entry_key"] and r.get("page_ref_int")})
        if not page_hints and entry.get("inferred_printed_page"):
            page_hints = [entry["inferred_printed_page"]]
        if not page_hints:
            continue
        q = [x for x in [entry.get("lemma_raw"), entry.get("lemma_display"), entry.get("entry_raw")] if x]
        entries.append(
            {
                "entry_id": entry["entry_key"].replace("PO012:entry:", "po012_").replace(":", "_"),
                "entry_key": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": list(dict.fromkeys(q[:3])),
                "page_hints": [str(n) for n in page_hints[:80]],
                "page_hint_ints": page_hints[:80],
                "context_raw": entry.get("context_raw") or entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def summarize_helper(item: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for cand in item.get("candidates", [])[:5]:
        candidates.append(
            {
                "rank": cand.get("rank"),
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:6]],
            }
        )
    return {
        "entry_id": item.get("entry_id"),
        "status": item.get("status"),
        "best_candidate": item.get("best_candidate"),
        "candidates": candidates,
        "debug": item.get("debug"),
    }


def apply_helper(payload: dict[str, Any], helper_output: dict[str, Any]) -> None:
    by_entry_id = {item["entry_id"]: item for item in helper_output.get("entries", [])}
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in payload["refs"]:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in payload["entries"]:
        helper_id = entry["entry_key"].replace("PO012:entry:", "po012_").replace(":", "_")
        item = by_entry_id.get(helper_id)
        if not item:
            continue
        best = item.get("best_candidate")
        if best and best.get("candidate_role") == "target_candidate":
            entry["target_file_best"] = best.get("file")
            entry["editorial_anchor_file"] = best.get("file")
            entry["confidence"] = max(float(entry.get("confidence") or 0), float(best.get("probability") or 0))
        entry.setdefault("raw_json", {})["helper"] = summarize_helper(item)
        for ref in refs_by_entry.get(entry["entry_key"], []):
            if best and best.get("candidate_role") == "target_candidate":
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")
                ref["editorial_anchor_file"] = best.get("file")
                ref["confidence"] = max(float(ref.get("confidence") or 0), min(0.95, float(best.get("probability") or 0)))
            ref.setdefault("raw_json", {})["helper"] = summarize_helper(item)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--helper-request", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--todo", type=Path, default=Path("data/intermediate_payloads/PO012/todo.json"))
    ap.add_argument("--apply-helper", action="store_true", help="Apply an existing helper output to the pre-helper payload and write final output.")
    args = ap.parse_args()

    if args.apply_helper:
        prehelper = args.output.with_suffix(".prehelper.json")
        payload = json.loads(prehelper.read_text(encoding="utf-8"))
        helper_output = json.loads(args.helper_output.read_text(encoding="utf-8"))
        apply_helper(payload, helper_output)
        payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.todo.write_text(
            json.dumps(
                {
                    "volume_id": VOLUME_ID,
                    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "current_focus": "Final payload written after helper application",
                    "completed": ["OCR section confirmation", "helper request build", "index_target_locator run", "helper output applied"],
                    "pending": ["final validation"],
                    "blocked": [],
                    "notes": ["TABLE ANALYTIQUE 662-664 excluded as table of contents/editorial closure."],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.output}")
        return

    payload = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    existing_sections = {s["section_key"] for s in payload["sections"]}
    sections, nodes, entries, refs, scripture_refs = build_extra_sections_and_entries()
    if not any(key.startswith("PO012:alpha:foreign_terms_syriac") for key in existing_sections):
        payload["sections"].extend(sections)
        payload["nodes"].extend(nodes)
        payload["entries"].extend(entries)
        payload["refs"].extend(refs)
        payload["scripture_refs"].extend(scripture_refs)

    payload["sections"] = sorted(payload["sections"], key=lambda s: s["section_order"])
    payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    notes = payload["volume"].setdefault("notes", [])
    note = "Rerun added OCR-confirmed foreign-terms, patristic-citations, and second Scripture-citations sections; TABLE ANALYTIQUE files 662-664 remain excluded as table of contents/editorial closure."
    if note not in notes:
        notes.append(note)
    payload["coverage"] = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered OCR-confirmed index sections, including prior onomastic/Bible tables and rerun-added foreign terms, Greek terms, Fathers citations, and Saint Irénée Scripture citations.",
        "evidence_files": sorted(
            set(payload.get("coverage", {}).get("evidence_files", []))
            | {s["file_start"] for s in sections}
            | {s["file_end"] for s in sections}
        ),
    }
    if "notes" not in payload or not isinstance(payload["notes"], list):
        payload["notes"] = []
    payload["notes"].append(
        {
            "type": "rerun",
            "message": "Added sections present in filtered OCR but absent from previous checkpoint; excluded TABLE ANALYTIQUE as non-alphabetical table of contents.",
            "files_checked": [file_by_seq(n) for n in [161, 167, 172, 662, 663, 664, 761, 762]],
        }
    )

    request = build_helper_request(payload)
    args.helper_request.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_payload = args.output.with_suffix(".prehelper.json")
    tmp_payload.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.todo.parent.mkdir(parents=True, exist_ok=True)
    args.todo.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "current_focus": "Run helper and apply material locators to rerun-added entries",
                "completed": ["OCR section confirmation", "pre-helper payload assembly", "helper request build"],
                "pending": ["run index_target_locator", "apply helper output", "validate final JSON"],
                "blocked": [],
                "notes": ["TABLE ANALYTIQUE 662-664 excluded as table of contents/editorial closure."],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {tmp_payload}")
    print(f"wrote {args.helper_request} entries={len(request['entries'])}")


if __name__ == "__main__":
    main()

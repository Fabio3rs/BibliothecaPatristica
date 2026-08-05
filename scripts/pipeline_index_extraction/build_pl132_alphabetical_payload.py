#!/usr/bin/env python3
"""Usage: build the PL132 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl132_alphabetical_payload.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL132"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL132/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL132_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL132_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL132_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL132"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SECTIONS_JSON = INTERMEDIATE_DIR / "sections.json"
NODES_JSON = INTERMEDIATE_DIR / "nodes.json"
ENTRIES_JSON = INTERMEDIATE_DIR / "entries.json"
REFS_JSON = INTERMEDIATE_DIR / "refs.json"
SCRIPTURE_REFS_JSON = INTERMEDIATE_DIR / "scripture_refs.json"
PAYLOAD_JSON = INTERMEDIATE_DIR / "volume.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_FILES = [SOURCE_ROOT / f"e3295aba-5b29-4699-bd4d-abd7a61211df-{seq}.txt" for seq in range(550, 559)]
SECTION_KEY = f"{VOLUME_ID}:alpha:alphabetical_general:001"

HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
NUM_LOC_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:et\s+seq\.|et\s+seqq\.|seq\.|seqq\.|ibid\.?|ibid))?", re.I)
BOUNDARY_RE = re.compile(r"\.\s+(?=[A-ZÆŒ])")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
REMISSION_SPLIT_RE = re.compile(r"\bvide\b|\bvid\.\b|\bvoir\b|\bcf\.\b", re.I)


@dataclass
class Fragment:
    text: str
    source_file: str
    source_seq: int


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_page_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed.get("all_text", "").splitlines():
        text = normalize(raw)
        if not text:
            continue
        if text.isdigit() or text == "Digitized by Google":
            continue
        if text in {
            "INDEX AD LIBROS REGINONIS",
            "INDEX AD LIBROS REGINONIS DE ECCLESIASTICIS DISCIPLINIS Hujus voluminis col. 185-488.",
            "Revocatur Lector ad numeros crassiori charactere textui intermixtos.",
        }:
            continue
        if LETTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def split_line_fragments(text: str) -> list[str]:
    fragments = [normalize(text)]
    for _ in range(4):
        new_fragments: list[str] = []
        changed = False
        for fragment in fragments:
            start = 0
            for match in BOUNDARY_RE.finditer(fragment):
                left = fragment[start : match.start() + 1]
                tail = left[-50:]
                if re.search(r"(?:\d{1,4}|ibid\.?|et seq\.?|et seqq\.?|seq\.?|seqq\.?)\s*$", tail, re.I):
                    new_fragments.append(normalize(fragment[start : match.start() + 1]))
                    start = match.end()
                    changed = True
            new_fragments.append(normalize(fragment[start:]))
        fragments = [frag for frag in new_fragments if frag]
        if not changed:
            break
    return fragments


def remove_prefix_noise(text: str) -> str:
    value = normalize(text)
    value = re.sub(r"^(?:[A-ZÆŒ]\s+)+", "", value)
    value = re.sub(r"^(?:\d{3,4}\s+)?INDEX AD LIBROS REGINONIS(?: DE ECCLESIASTICIS DISCIPLINIS)?\.?\s*\d{0,4}\s*", "", value, flags=re.I)
    value = re.sub(r"^(?:\d{3,4}\s+)?DE ECCL\. DISCIPL\.?\s*\d{0,4}\s*", "", value, flags=re.I)
    value = re.sub(r"^(?:\d{3,4}\s+)?REGINONIS\.?\s*\d{0,4}\s*", "", value, flags=re.I)
    value = value.strip(" .;:")
    return value


def merge_lowercase_continuations(fragments: list[Fragment]) -> list[Fragment]:
    merged: list[Fragment] = []
    for fragment in fragments:
        first = fragment.text[:1]
        if merged and first and first.islower():
            previous = merged[-1]
            merged[-1] = Fragment(
                text=f"{previous.text} {fragment.text}".strip(),
                source_file=previous.source_file,
                source_seq=previous.source_seq,
            )
            continue
        merged.append(fragment)
    return merged


def extract_entry_lemmata(fragment: str) -> tuple[str | None, str, str]:
    text = remove_prefix_noise(fragment)
    if not text:
        return None, "", "lemma"
    lower = text.lower()
    if re.search(r"\bvide\b|\bvid\.\b|\bvoir\b|\bcf\.\b", lower):
        if re.search(r"\d", text):
            lemma = text
            first_loc = NUM_LOC_RE.search(text)
            if first_loc:
                lemma = normalize(text[: first_loc.start()]).rstrip(" ,;:.")
            return lemma or None, text, "lemma"
        lemma = normalize(REMISSION_SPLIT_RE.split(text, maxsplit=1)[0]).rstrip(" ,;:.")
        return lemma or None, text, "cross_reference"
    first_loc = NUM_LOC_RE.search(text)
    if first_loc:
        lemma = normalize(text[: first_loc.start()]).rstrip(" ,;:.")
        return lemma or None, text, "lemma"
    return text.rstrip(" ,;:."), text, "lemma"


def extract_refs(fragment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in NUM_LOC_RE.finditer(fragment):
        ref_raw = normalize(match.group(0)).rstrip(" ,;:.")
        page_ref_int = int(match.group(1))
        key = (ref_raw, str(page_ref_int))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "ref_kind": "editorial_range" if re.search(r"(et seq\.|et seqq\.|seq\.|seqq\.)", ref_raw, re.I) else "editorial_page",
            }
        )
    return refs


def build_fragments() -> list[Fragment]:
    fragments: list[Fragment] = []
    started = False
    for path in INDEX_FILES:
        seq = int(path.stem.rsplit("-", 1)[-1])
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        for raw in parsed.get("all_text", "").splitlines():
            text = normalize(raw)
            if not text or text.isdigit() or text == "Digitized by Google" or LETTER_RE.fullmatch(text):
                continue
            if text in {
                "INDEX AD LIBROS REGINONIS",
                "INDEX AD LIBROS REGINONIS DE ECCLESIASTICIS DISCIPLINIS Hujus voluminis col. 185-488.",
                "Revocatur Lector ad numeros crassiori charactere textui intermixtos.",
            }:
                continue
            if not started:
                if "Abbates in episcoporum potestate consistant" not in text:
                    continue
                started = True
            for frag in split_line_fragments(text):
                frag = remove_prefix_noise(frag)
                if not frag:
                    continue
                if frag in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                    continue
                fragments.append(Fragment(text=frag, source_file=str(path), source_seq=seq))
    return merge_lowercase_continuations(fragments)


def build_nodes(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_letters: list[str] = []
    for entry in entries:
        letter = entry.get("heading_letter")
        if letter and letter not in seen_letters:
            seen_letters.append(letter)
    nodes: list[dict[str, Any]] = []
    for order, letter in enumerate(seen_letters, start=1):
        node_key = f"{VOLUME_ID}:node:letter:{order:02d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source": "first_letter_grouping"},
            }
        )
    return nodes


def target_lookup_helper(best: dict[str, Any] | None) -> str | None:
    if not best:
        return None
    return best.get("file")


def build_helper_request(fragments: list[Fragment]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for idx, frag in enumerate(fragments, start=1):
        lemma_raw, context_raw, entry_kind = extract_entry_lemmata(frag.text)
        if not lemma_raw:
            lemma_raw = frag.text[:80]
        page_hints = [ref["page_ref_int"] for ref in extract_refs(frag.text)]
        page_hints = list(dict.fromkeys(page_hints))[:3]
        entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_alpha_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw],
                "page_hints": [str(p) for p in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": context_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request: dict[str, Any]) -> dict[str, Any]:
    write_json(HELPER_REQUEST_JSON, helper_request)
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        check=True,
    )
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def build_payload(helper_output: dict[str, Any], fragments: list[Fragment]) -> dict[str, Any]:
    helper_map = {item["entry_id"]: item for item in helper_output.get("entries", [])}

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    helper_target_files: list[str] = []

    current_letter = None
    entry_counter = 0
    for idx, frag in enumerate(fragments, start=1):
        lemma_raw, context_raw, entry_kind = extract_entry_lemmata(frag.text)
        if not lemma_raw:
            continue
        first_letter = re.sub(r"[^A-ZÆŒ].*$", "", lemma_raw[:1].upper()) or None
        if first_letter and first_letter != current_letter:
            current_letter = first_letter
        entry_counter += 1
        entry_id = f"{VOLUME_ID.lower()}_alpha_{idx:04d}"
        helper_entry = helper_map.get(entry_id, {})
        best = helper_entry.get("best_candidate")
        if best and best.get("file"):
            helper_target_files.append(best["file"])
        entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
        page_refs = extract_refs(frag.text)
        first_page = page_refs[0]["page_ref_int"] if page_refs else None
        target_file_best = target_lookup_helper(best)
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_raw.lower(),
            "lemma_sort": lemma_raw.lower(),
            "entry_raw": frag.text,
            "context_raw": context_raw,
            "heading_letter": first_letter,
            "inferred_printed_page": first_page,
            "section_start_file": str(INDEX_FILES[0]),
            "editorial_anchor_file": frag.source_file,
            "target_file_best": target_file_best,
            "confidence": 0.88 if page_refs else 0.72,
            "raw_json": {
                "source_file": frag.source_file,
                "source_seq": frag.source_seq,
                "helper": {
                    "status": helper_entry.get("status"),
                    "candidate_role": best.get("candidate_role") if best else None,
                    "reason_summary": best.get("reason_summary") if best else None,
                    "best_candidate": best,
                },
                "grouped_fragment": True,
            },
        }
        entries.append(entry)
        for ref_order, ref in enumerate(page_refs, start=1):
            target_file = best.get("file") if best else None
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": best.get("probability") if best else None,
                    "section_start_file": str(INDEX_FILES[0]),
                    "editorial_anchor_file": frag.source_file,
                    "confidence": 0.84 if target_file else 0.7,
                    "raw_json": {
                        "source_file": frag.source_file,
                        "source_seq": frag.source_seq,
                        "helper": {
                            "status": helper_entry.get("status"),
                            "candidate_role": best.get("candidate_role") if best else None,
                            "reason_summary": best.get("reason_summary") if best else None,
                        },
                    },
                }
            )

    nodes = build_nodes(entries)
    for entry in entries:
        parent = None
        for node in nodes:
            if node["label_raw"] == entry["heading_letter"]:
                parent = node["node_key"]
                break
        entry["parent_node_key"] = parent

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX AD LIBROS REGINONIS DE ECCLESIASTICIS DISCIPLINIS.",
        "heading_norm": "index ad libros reginonis de ecclesiasticis disciplinis",
        "heading_letter": "A",
        "page_start": 1091,
        "page_end": 1108,
        "file_start": str(INDEX_FILES[0]),
        "file_end": str(INDEX_FILES[-1]),
        "confidence": 0.97,
        "raw_json": {
            "section_kind_reason": "Explicit INDEX AD LIBROS REGINONIS heading followed by A-Z alphabetical index entries.",
            "source_files": [str(path) for path in INDEX_FILES],
            "pagination_note": "OCR file 558 likely misread printed 1107 as 4407 in the final header; the entry stream remains continuous.",
        },
    }

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the PL132 alphabetical index tail from OCR files 550-558 and resolved material targets with the helper for the extracted grouped fragments.",
        "evidence_files": [str(path) for path in INDEX_FILES],
    }

    helper_summary = helper_output.get("entries", [])
    notes = [
        "The extracted index begins after the Regino materia and continues through the alphabetic tail on files 550-558.",
        "The final OCR header on file 558 appears to be a series-number drift (4407/4408) for the same index tail; it is preserved in raw_json only as a note.",
        f"Helper entries resolved: {len([item for item in helper_summary if item.get('status') == 'resolved'])}; ambiguous: {len([item for item in helper_summary if item.get('status') == 'ambiguous'])}.",
    ]

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 132",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def update_todo(helper_status: str, entry_count: int) -> None:
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PL132 alphabetical payload after helper verification.",
            "completed": [
                "OCR tail inspected",
                f"{entry_count} grouped fragments extracted",
                f"helper status: {helper_status}",
            ],
            "pending": [
                "validate final payload file",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals and page refs separate from file suffixes.",
                "The index is treated as one alphabetical section with letter-group nodes.",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-file", default=str(OUTPUT_FILE))
    args = parser.parse_args()

    fragments = build_fragments()
    helper_request = build_helper_request(fragments)
    helper_output = run_helper(helper_request)

    payload = build_payload(helper_output, fragments)
    write_json(SECTIONS_JSON, payload["sections"])
    write_json(NODES_JSON, payload["nodes"])
    write_json(ENTRIES_JSON, payload["entries"])
    write_json(REFS_JSON, payload["refs"])
    write_json(SCRIPTURE_REFS_JSON, payload["scripture_refs"])
    write_json(PAYLOAD_JSON, payload)
    update_todo(f"resolved={sum(1 for item in helper_output.get('entries', []) if item.get('status') == 'resolved')}", len(payload["entries"]))
    write_json(Path(args.output_file), payload)


if __name__ == "__main__":
    main()

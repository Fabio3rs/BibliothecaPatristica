#!/usr/bin/env python3
"""Build the PL082 alphabetical-index helper request and final payload.

Run from the repository root, for example:

    python scripts/pipeline_index_extraction/build_pl082_alphabetical_payload.py \
      --source-root /homessddata/Projects/pdfocr/teste/PL082/text \
      --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL082_helper_request.json \
      --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL082_alphabetical_indices.json

The script extracts the `INDEX RERUM ET VERBORUM` page, builds the helper input
for every recovered lemma, and writes a conservative final JSON payload with
the recovered alphabetical entries.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


LEMMA_RE = re.compile(r"^(?P<lemma>[^,<]+?),\s*\[ilegivel\]\.?$")
HEADING_RE = re.compile(r"INDEX RERUM ET VERBORUM", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def parse_page(path: Path) -> tuple[list[dict], dict[str, list[str]]]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    blocks: list[tuple[str, str]] = []
    for bloco in root.findall("bloco"):
        block_type = (bloco.attrib.get("tipo") or "").strip().lower()
        content = "".join(bloco.itertext()).replace("\xa0", " ")
        if content:
            blocks.append((block_type, content))

    entries: list[dict] = []
    letter_nodes: dict[str, list[str]] = {k: [] for k in "ABCD"}

    entry_order = 0
    for block_type, content in blocks:
        if block_type != "texto_principal":
            continue
        for raw_line in [line.strip() for line in content.splitlines() if line.strip()]:
            match = LEMMA_RE.match(raw_line)
            if not match:
                continue
            lemma_raw = match.group("lemma").strip()
            first_letter = lemma_raw[0].upper()
            if first_letter not in letter_nodes:
                # Keep only the letter groups actually present in this page.
                # The recovered page is limited to A-D.
                continue
            entry_order += 1
            entry_id = f"pl082_loc_{entry_order:04d}"
            entries.append(
                {
                    "entry_id": entry_id,
                    "lemma_raw": lemma_raw,
                    "query_names": [lemma_raw],
                    "page_hints": [],
                    "page_hint_ints": [],
                    "context_raw": raw_line,
                }
            )
            letter_nodes[first_letter].append(entry_id)

    return entries, letter_nodes


def build_helper_request(volume_id: str, source_root: str, entries: list[dict]) -> dict:
    return {
        "volume_id": volume_id,
        "source_root": source_root,
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": entries,
    }


def build_payload(volume_id: str, source_root: str, page_path: Path) -> dict:
    entries, letter_nodes = parse_page(page_path)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "")
    section_key = f"{volume_id}:alpha:alphabetical_general:001"
    sections = [
        {
            "section_key": section_key,
            "volume_id": volume_id,
            "work_key": None,
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX RERUM ET VERBORUM.",
            "heading_norm": normalize_text("INDEX RERUM ET VERBORUM."),
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(page_path),
            "file_end": str(page_path),
            "confidence": 0.98,
            "raw_json": {
                "source_file": str(page_path),
                "section_kind_reason": "Alphabetical index of things and words with A-D gutter markers; numerical locators are marked illegible in the OCR transcription.",
            },
        }
    ]

    nodes: list[dict] = []
    node_keys = {}
    node_order = 0
    for letter in "ABCD":
        if not letter_nodes.get(letter):
            continue
        node_order += 1
        node_key = f"{volume_id}:node:{letter.lower()}:{node_order:03d}"
        node_keys[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.96,
                "raw_json": {
                    "source_file": str(page_path),
                    "note": f"Gutter marker {letter} on the recovered index page.",
                },
            }
        )

    payload_entries: list[dict] = []
    refs: list[dict] = []
    for idx, entry in enumerate(entries, start=1):
        lemma_raw = entry["lemma_raw"]
        letter = lemma_raw[0].upper()
        node_key = node_keys.get(letter)
        payload_entries.append(
            {
                "entry_key": f"{volume_id}:entry:{idx:04d}",
                "section_key": section_key,
                "parent_node_key": node_key,
                "entry_order": idx,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize_text(lemma_raw),
                "lemma_sort": normalize_text(lemma_raw),
                "entry_raw": entry["context_raw"],
                "context_raw": entry["context_raw"],
                "heading_letter": letter,
                "inferred_printed_page": None,
                "section_start_file": str(page_path),
                "editorial_anchor_file": str(page_path),
                "target_file_best": str(page_path),
                "confidence": 0.9,
                "raw_json": {
                    "source_line": entry["context_raw"],
                    "note": "The OCR transcription preserves the lemma but masks the numeric locator as [ilegivel]; no material reference was invented.",
                },
            }
        )

    return {
        "schema_version": "1.0",
        "generated_at": now,
        "volume": {
            "volume_id": volume_id,
            "collection": volume_id[:2],
            "source_root": source_root,
            "volume_label": volume_id,
            "notes": "Alphabetical index material recovered from the `INDEX RERUM ET VERBORUM` page; printed locators are illegible in OCR.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": payload_entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "partial_recovery",
            "entries_status_reason": "The alphabetical lemmata were recovered from the index page, but the printed numerical locators are transcribed as [ilegivel] and cannot be serialized as material refs without invention.",
            "evidence_files": [str(page_path)],
        },
        "notes": [
            "Recovered the single alphabetical index page and preserved the OCR literals.",
            "No biblical references were present in this index block.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--helper-request", required=True)
    ap.add_argument("--helper-output")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    source_root = Path(args.source_root)
    page_path = None
    for candidate in sorted(source_root.glob("*.txt")):
        text = candidate.read_text(encoding="utf-8")
        if HEADING_RE.search(text):
            page_path = candidate
            break
    if page_path is None:
        raise SystemExit("Could not find INDEX RERUM ET VERBORUM page in source_root.")

    entries, _ = parse_page(page_path)
    helper_request = build_helper_request("PL082", str(source_root), entries)
    Path(args.helper_request).write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = build_payload("PL082", str(source_root), page_path)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

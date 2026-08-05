#!/usr/bin/env python3
"""Build the final PL202 alphabetical-index payload from parsed OCR and helper output.

Run:
  python scripts/pipeline_index_extraction/build_pl202_payload.py \
    --parsed /homessddata/Projects/pdfocr/data/intermediate_payloads/PL202/parsed_entries.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL202_helper_output.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL202_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


SECTION_KEY = "PL202:alpha:ordo_rerum:001"
SECTION_START_FILE = "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-788.txt"
SECTION_END_FILE = "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-795.txt"

PAGE_RE = re.compile(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d+(?:-\d+)?)(?:\.)?$")


def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^0-9a-zà-ÿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_page(raw: str):
    m = PAGE_RE.match(raw)
    if not m:
        return None, None, None
    lemma = m.group("lemma").rstrip()
    page = m.group("page")
    if "-" in page:
        start, end = page.split("-", 1)
        return lemma, page, (int(start), start, end)
    return lemma, page, (int(page), None, None)


def load_helper_map(helper_request: dict, helper_output: dict):
    by_context = {}
    by_entry_id = {}
    request_by_context = {}
    for item in helper_request.get("entries", []):
        request_by_context.setdefault(item.get("context_raw"), []).append(item)
    for item in helper_output.get("entries", []):
        by_entry_id[item["entry_id"]] = item
    return request_by_context, by_entry_id


def compact_helper(entry: dict):
    best = entry.get("best_candidate")
    top = []
    for cand in entry.get("candidates", [])[:3]:
        top.append({
            "file": cand.get("file"),
            "probability": cand.get("probability"),
            "candidate_role": cand.get("candidate_role"),
            "reason_summary": cand.get("reason_summary"),
        })
    result = {
        "status": entry.get("status"),
        "best_candidate": None,
        "top_candidates": top,
    }
    if best:
        result["best_candidate"] = {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", required=True)
    parser.add_argument("--helper-request", required=True)
    parser.add_argument("--helper-output", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    parsed = json.loads(Path(args.parsed).read_text())
    helper_request = json.loads(Path(args.helper_request).read_text())
    helper_output = json.loads(Path(args.helper_output).read_text())
    request_by_context, helper_by_entry_id = load_helper_map(helper_request, helper_output)

    sections = [{
        "section_key": SECTION_KEY,
        "volume_id": "PL202",
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 9,
        "page_end": 1579,
        "file_start": SECTION_START_FILE,
        "file_end": SECTION_END_FILE,
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Closing contents table spanning OCR files 788-795; OCR body text after the close belongs to later volume material and is excluded.",
            "evidence_files": [
                SECTION_START_FILE,
                "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-791.txt",
                "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-794.txt",
                SECTION_END_FILE,
            ],
            "helper_used": True,
            "helper_output": "/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL202_helper_output.json",
        },
    }]

    entries_out = []
    refs_out = []

    parsed_entries = parsed["entries"]
    entry_counter = 0
    for item in parsed_entries:
        raw = item["entry_raw"]
        lemma, page_raw, page_data = parse_page(raw)
        entry_counter += 1
        entry_key = f"PL202:entry:{entry_counter:04d}"
        source_file = item["source_file"]
        request_item = request_by_context.get(raw, [None])[0]
        helper_item = helper_by_entry_id.get(request_item["entry_id"]) if request_item else None
        page_lookup_resolved = bool(helper_item and helper_item.get("best_candidate") and helper_item["best_candidate"].get("file"))
        helper_compact = compact_helper(helper_item) if helper_item else None

        entry_obj = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": "heading_group",
            "lemma_raw": lemma if lemma is not None else raw,
            "lemma_display": lemma if lemma is not None else raw,
            "lemma_norm": normalize_text(lemma if lemma is not None else raw),
            "lemma_sort": normalize_text(lemma if lemma is not None else raw),
            "entry_raw": raw,
            "context_raw": raw,
            "heading_letter": None,
            "inferred_printed_page": page_data[0] if page_data else None,
            "section_start_file": SECTION_START_FILE,
            "editorial_anchor_file": source_file,
            "target_file_best": helper_item.get("best_candidate", {}).get("file") if helper_item else None,
            "confidence": 0.88 if page_raw else 0.82,
            "raw_json": {
                "source_file": source_file,
                "entry_kind_reason": "contents-table line from closing ORDO RERUM",
            },
        }

        if helper_item:
            entry_obj["raw_json"].update({
                "page_lookup_resolved": page_lookup_resolved,
                "helper_entry_id": helper_item.get("entry_id"),
                "helper_status": helper_compact["status"],
                "helper_best_candidate": helper_compact["best_candidate"],
                "helper_top_candidates": helper_compact["top_candidates"],
            })

        if page_raw:
            ref_obj = {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": page_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_data[0] if page_data else None,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page_data[1] if page_data and page_data[1] else None,
                "range_end_raw": page_data[2] if page_data and page_data[2] else None,
                "target_file": helper_item.get("best_candidate", {}).get("file") if helper_item else None,
                "target_file_probability": helper_item.get("best_candidate", {}).get("probability") if helper_item else None,
                "section_start_file": SECTION_START_FILE,
                "editorial_anchor_file": source_file,
                "confidence": 0.61 if helper_item and helper_item.get("best_candidate") else 0.5,
                "raw_json": {
                    "source_file": source_file,
                    "page_lookup_resolved": page_lookup_resolved,
                },
            }
            if helper_item:
                ref_obj["raw_json"].update({
                    "helper_entry_id": helper_item.get("entry_id"),
                    "helper_status": helper_compact["status"],
                    "helper_best_candidate": helper_compact["best_candidate"],
                    "helper_top_candidates": helper_compact["top_candidates"],
                })
            refs_out.append(ref_obj)

        entries_out.append(entry_obj)

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PL202",
            "collection": "PL",
            "source_root": "/homessddata/Projects/pdfocr/teste/PL202/text",
            "volume_label": "Patrologia Latina 202",
        },
        "sections": sections,
        "nodes": [],
        "entries": entries_out,
        "refs": refs_out,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table from OCR files 788-795 and preserved printed-page anchors as cited in the table of contents.",
            "evidence_files": [
                SECTION_START_FILE,
                "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-791.txt",
                "/homessddata/Projects/pdfocr/teste/PL202/text/a139d696-0ffa-4373-81f0-51d9631bb465-794.txt",
                SECTION_END_FILE,
            ],
        },
        "notes": [
            "Contents table OCR includes a few wrapped lines and late-page no-page headings; those were split conservatively.",
            "Helper output was used to anchor page-bearing lines to their most likely OCR pages.",
        ],
    }

    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

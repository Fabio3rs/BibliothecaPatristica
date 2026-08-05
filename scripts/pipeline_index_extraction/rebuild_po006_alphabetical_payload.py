#!/usr/bin/env python3
"""Rebuild the PO006 alphabetical payload by merging OCR-split entries and removing note/header contamination.

Usage:
  python scripts/pipeline_index_extraction/rebuild_po006_alphabetical_payload.py
  python scripts/pipeline_index_extraction/rebuild_po006_alphabetical_payload.py --apply-helper
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(ROOT := Path("/homessddata/Projects/pdfocr")))
from patristica_pipeline.common import page_number
from patristica_pipeline.ocr_xml_utils import read_ocr_page

PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PO006_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PO006_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PO006_helper_output.json"
SOURCE_ROOT = ROOT / "teste/PO006/text"

EARLY_SECTION_FILES = {
    "PO006:alpha:onomastic_mixed:early_all_names": [
        SOURCE_ROOT / "661f644d-637e-4d26-b06d-a692725ee9bb-469.txt",
        SOURCE_ROOT / "661f644d-637e-4d26-b06d-a692725ee9bb-470.txt",
    ],
    "PO006:alpha:onomastic_mixed:early_mysteres": [
        SOURCE_ROOT / "661f644d-637e-4d26-b06d-a692725ee9bb-471.txt",
        SOURCE_ROOT / "661f644d-637e-4d26-b06d-a692725ee9bb-472.txt",
    ],
}


REMOVE_ENTRY_KEYS = {
    "PO006:entry:0108",
    "PO006:entry:0109",
    "PO006:entry:0110",
    "PO006:entry:0165",
    "PO006:entry:0275",
    # OCR reader marks this as tipo="outro" ornamental decoration, not an index entry.
    "PO006:entry:0292",
    "PO006:entry:0332",
    "PO006:entry:0447",
    "PO006:entry:0522",
}

MERGE_GROUPS = [
    ["PO006:entry:0012", "PO006:entry:0013"],
    ["PO006:entry:0173", "PO006:entry:0174"],
    ["PO006:entry:0209", "PO006:entry:0210"],
    ["PO006:entry:0217", "PO006:entry:0218"],
    ["PO006:entry:0305", "PO006:entry:0306"],
    ["PO006:entry:0331", "PO006:entry:0333"],
    ["PO006:entry:0338", "PO006:entry:0339", "PO006:entry:0340"],
    ["PO006:entry:0402", "PO006:entry:0403"],
    ["PO006:entry:0405", "PO006:entry:0406"],
    ["PO006:entry:0408", "PO006:entry:0409"],
    ["PO006:entry:0420", "PO006:entry:0421"],
    ["PO006:entry:0465", "PO006:entry:0466", "PO006:entry:0467", "PO006:entry:0468", "PO006:entry:0469"],
    ["PO006:entry:0504", "PO006:entry:0505"],
    ["PO006:entry:0518", "PO006:entry:0519"],
    ["PO006:entry:0520", "PO006:entry:0521"],
]

MERGED_KEYS = {key for group in MERGE_GROUPS for key in group[1:]}
KEEPER_TO_GROUP = {group[0]: group for group in MERGE_GROUPS}
KEY_TO_KEEPER = {key: group[0] for group in MERGE_GROUPS for key in group}

LEMMA_OVERRIDES = {
    "PO006:entry:0173": "طلسنامرة Talesnâmara",
    "PO006:entry:0305": "Ben-'Obéïd al-Banah (Elia Ali)",
    "PO006:entry:0331": "Nicée (concile de)",
    "PO006:entry:0338": "Symbole",
    "PO006:entry:0402": "chori",
    "PO006:entry:0420": "episcopi",
    "PO006:entry:0465": "monasterium",
    "PO006:entry:0504": "solitudo",
    "PO006:entry:0518": "vita aeterna",
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def collapse_ws(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip()


def join_fragments(parts: list[str | None]) -> str | None:
    cleaned = [collapse_ws(part) for part in parts if collapse_ws(part)]
    if not cleaned:
        return None
    merged = cleaned[0]
    for part in cleaned[1:]:
        if merged.endswith("-"):
            merged = merged[:-1] + part
        else:
            merged = f"{merged} {part}"
    return collapse_ws(merged)


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    filtered = []
    for ch in decomposed:
        category = unicodedata.category(ch)
        if category.startswith("M"):
            continue
        if category.startswith(("L", "N")):
            filtered.append(ch)
        else:
            filtered.append(" ")
    return collapse_ws("".join(filtered))


SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
REF_RE = re.compile(r"\d+(?:\s*à\s*\d+)?[₀₁₂₃₄₅₆₇₈₉₋_\\-]*(?:\\s*et\\s+ss\\.)?", re.IGNORECASE)


def is_group_heading(line: str) -> bool:
    text = collapse_ws(line) or ""
    if not text:
        return False
    if len(text) <= 2 and not any(ch.isdigit() for ch in text):
        return True
    if re.fullmatch(r"[A-Z]", text):
        return True
    return False


def collect_body_lines(files: list[Path]) -> list[tuple[str, Path]]:
    lines: list[tuple[str, Path]] = []
    for path in files:
        page = read_ocr_page(path)
        for block in page.blocks:
            if block.tipo != "texto_principal":
                continue
            for raw_line in block.content_clean.splitlines():
                line = collapse_ws(raw_line)
                if not line or line == "¹":
                    continue
                lines.append((line, path))
    return lines


def split_index_lines(lines: list[tuple[str, Path]]) -> list[dict]:
    items: list[dict] = []
    current_node: str | None = None
    current_node_file: Path | None = None
    buffer: list[str] = []
    buffer_file: Path | None = None

    def flush() -> None:
        nonlocal buffer, buffer_file
        text = join_fragments(buffer)
        if text and any(ch.isdigit() for ch in text):
            items.append(
                {
                    "kind": "entry",
                    "text": text,
                    "source_file": buffer_file,
                    "heading": current_node,
                }
            )
        buffer = []
        buffer_file = None

    for line, path in lines:
        if is_group_heading(line):
            flush()
            current_node = line
            current_node_file = path
            items.append({"kind": "node", "text": line, "source_file": current_node_file})
            continue
        if buffer and REF_RE.search(" ".join(buffer)) and not line[0].isdigit():
            flush()
        if not buffer:
            buffer_file = path
        buffer.append(line)
    flush()
    return items


def extract_refs_from_entry(entry_text: str) -> list[dict]:
    refs: list[dict] = []
    for match in REF_RE.finditer(entry_text):
        raw = collapse_ws(match.group(0))
        if not raw:
            continue
        page_match = re.search(r"\d+", raw)
        if not page_match:
            continue
        page_ref_raw = page_match.group(0)
        page_ref_int = int(page_ref_raw)
        translated = raw.translate(SUBSCRIPT_DIGITS)
        line_match = re.search(r"\d+([0-9_\\-]+)$", translated)
        line_ref_raw = None
        if line_match and any(ch in raw for ch in "₀₁₂₃₄₅₆₇₈₉_"):
            line_ref_raw = raw[len(page_ref_raw):] or None
        ref_kind = "editorial_range" if " à " in raw or "et ss" in raw.casefold() else "editorial_page_line"
        refs.append(
            {
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": line_ref_raw,
                "range_start_raw": page_ref_raw if ref_kind == "editorial_range" else None,
                "range_end_raw": raw.split("à", 1)[1].strip().split()[0] if " à " in raw else None,
            }
        )
    return refs


def lemma_from_entry(entry_text: str) -> str:
    first_ref = REF_RE.search(entry_text)
    if first_ref:
        return collapse_ws(entry_text[: first_ref.start()].rstrip(" ,.;:")) or entry_text
    return entry_text


def next_entry_number(entries: list[dict]) -> int:
    max_num = 0
    for entry in entries:
        match = re.search(r":entry:(\d+)$", entry.get("entry_key", ""))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def next_node_number(nodes: list[dict]) -> int:
    max_num = 0
    for node in nodes:
        match = re.search(r":node:(\d+)$", node.get("node_key", ""))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def append_early_sections(payload: dict, helper_request_entries: list[dict], new_refs: list[dict]) -> None:
    existing_sections = {section["section_key"] for section in payload["sections"]}
    if all(key in existing_sections for key in EARLY_SECTION_FILES):
        return

    entry_number = next_entry_number(payload["entries"])
    node_number = next_node_number(payload["nodes"])
    section_specs = [
        {
            "section_key": "PO006:alpha:onomastic_mixed:early_all_names",
            "section_order": 1,
            "heading_raw": "PREMIÈRE TABLE DE TOUS LES NOMS PROPRES",
            "page_start": 459,
            "page_end": 460,
            "raw_source": "Early fascicle onomastic table; OCR note says figures refer to the corresponding page.",
        },
        {
            "section_key": "PO006:alpha:onomastic_mixed:early_mysteres",
            "section_order": 2,
            "heading_raw": "SECONDE TABLE DES NOMS PROPRES PARTICULIERS AU LIVRE DES MYSTÈRES",
            "page_start": 461,
            "page_end": 462,
            "raw_source": "Early fascicle onomastic table specific to Livre des Mystères; OCR note says figures refer to page and line.",
        },
    ]

    for section in payload["sections"]:
        section["section_order"] = section.get("section_order", 0) + 2

    added_sections: list[dict] = []
    for spec in section_specs:
        section_key = spec["section_key"]
        files = EARLY_SECTION_FILES[section_key]
        added_sections.append(
            {
                "section_key": section_key,
                "volume_id": "PO006",
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": "onomastic_mixed",
                "heading_raw": spec["heading_raw"],
                "heading_norm": normalize_text(spec["heading_raw"]),
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": str(files[0]),
                "file_end": str(files[-1]),
                "confidence": 0.97,
                "raw_json": {
                    "source": spec["raw_source"],
                    "section_kind_reason": "Named-person/place table; finer editorial scope retained in heading_raw.",
                    "verified_with_ocr_reader": [str(path) for path in files],
                    "excluded_neighbor": str(SOURCE_ROOT / "661f644d-637e-4d26-b06d-a692725ee9bb-473.txt"),
                    "excluded_neighbor_reason": "TABLE DES MATIÈRES contents page, not alphabetical index entries.",
                },
            }
        )

        node_by_label: dict[str, str] = {}
        for item in split_index_lines(collect_body_lines(files)):
            if item["kind"] == "node":
                label = item["text"]
                node_key = f"PO006:node:{node_number:04d}"
                node_number += 1
                node_by_label[label] = node_key
                payload["nodes"].append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": len(node_by_label),
                        "node_kind": "letter_group",
                        "label_raw": label,
                        "label_norm": normalize_text(label),
                        "label_sort": normalize_text(label),
                        "node_level": 1,
                        "confidence": 0.93,
                        "raw_json": {
                            "source_file": str(item["source_file"]),
                            "source": "Single-letter/group divider recovered from OCR body block.",
                        },
                    }
                )
                continue

            entry_text = item["text"]
            refs = extract_refs_from_entry(entry_text)
            if not refs:
                continue
            lemma = lemma_from_entry(entry_text)
            entry_key = f"PO006:entry:{entry_number:04d}"
            entry_number += 1
            page_hints = []
            page_hint_ints = []
            for ref in refs:
                value = ref["page_ref_int"]
                if value not in page_hint_ints:
                    page_hint_ints.append(value)
                    page_hints.append(str(value))
            source_file = str(item["source_file"])
            payload["entries"].append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": node_by_label.get(item.get("heading")),
                    "entry_order": entry_number - 1,
                    "entry_kind": "lemma",
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": normalize_text(lemma),
                    "lemma_sort": normalize_text(lemma),
                    "entry_raw": entry_text,
                    "context_raw": None,
                    "heading_letter": item.get("heading"),
                    "inferred_printed_page": page_hint_ints[0] if page_hint_ints else None,
                    "section_start_file": str(files[0]),
                    "editorial_anchor_file": source_file,
                    "target_file_best": None,
                    "confidence": 0.82,
                    "raw_json": {
                        "segment_source": entry_text,
                        "segment_source_file": source_file,
                        "source": "Added from missing early PO006 candidate section verified in OCR reader.",
                        "material_locator_status": "pending_helper",
                    },
                }
            )
            helper_request_entries.append(
                {
                    "entry_id": f"po006_{entry_key.rsplit(':', 1)[-1]}",
                    "lemma_raw": lemma,
                    "query_names": [lemma, entry_text],
                    "page_hints": page_hints,
                    "page_hint_ints": page_hint_ints,
                    "context_raw": entry_text,
                }
            )
            for order, ref in enumerate(refs, start=1):
                ref_payload = {
                    "entry_key": entry_key,
                    "ref_order": order,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": str(files[0]),
                    "editorial_anchor_file": source_file,
                    "confidence": 0.78,
                    "raw_json": {
                        "source": "Parsed from early PO006 index entry; target locator supplied by helper when available."
                    },
                }
                ref_payload.update(ref)
                new_refs.append(ref_payload)

    payload["sections"] = added_sections + payload["sections"]


def extract_page_hints(refs: list[dict]) -> tuple[list[str], list[int]]:
    ints: list[int] = []
    for ref in refs:
        value = ref.get("page_ref_int")
        if isinstance(value, int) and value > 0 and value not in ints:
            ints.append(value)
    return [str(value) for value in ints], ints


def summarize_helper(entry_output: dict | None) -> dict | None:
    if not entry_output:
        return None
    best = entry_output.get("best_candidate") or {}
    candidates = entry_output.get("candidates") or []
    summary = {
        "status": entry_output.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "best_candidate": best or None,
        "top_candidates": [],
    }
    for candidate in candidates[:5]:
        summary["top_candidates"].append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
            }
        )
    return summary


def rebuild_payload() -> None:
    payload = load_json(PAYLOAD_PATH)
    helper_request = load_json(HELPER_REQUEST_PATH)
    helper_output = load_json(HELPER_OUTPUT_PATH)

    entries = payload["entries"]
    refs = payload["refs"]

    entry_by_key = {entry["entry_key"]: deepcopy(entry) for entry in entries}
    request_by_entry_id = {item["entry_id"]: deepcopy(item) for item in helper_request["entries"]}
    output_by_entry_id = {item["entry_id"]: deepcopy(item) for item in helper_output["entries"]}
    refs_by_key: dict[str, list[dict]] = {}
    for ref in refs:
        refs_by_key.setdefault(ref["entry_key"], []).append(deepcopy(ref))

    merged_entry_data: dict[str, dict] = {}
    merged_helper_request: dict[str, dict] = {}

    for keeper_key, group in KEEPER_TO_GROUP.items():
        if keeper_key not in entry_by_key:
            continue
        available_group = [key for key in group if key in entry_by_key]
        if len(available_group) == 1:
            continue
        group_entries = [entry_by_key[key] for key in available_group]
        group_refs: list[dict] = []
        for key in available_group:
            group_refs.extend(refs_by_key.get(key, []))

        keeper = deepcopy(group_entries[0])
        for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            merged_value = join_fragments([entry.get(field) for entry in group_entries])
            if merged_value is not None:
                keeper[field] = merged_value

        if keeper_key in LEMMA_OVERRIDES:
            keeper["lemma_raw"] = LEMMA_OVERRIDES[keeper_key]
            keeper["lemma_display"] = LEMMA_OVERRIDES[keeper_key]

        if keeper.get("lemma_raw"):
            keeper["lemma_norm"] = normalize_text(keeper["lemma_raw"])
            keeper["lemma_sort"] = keeper["lemma_norm"]

        keeper["raw_json"] = deepcopy(keeper.get("raw_json", {}))
        keeper["raw_json"]["segment_source"] = keeper["entry_raw"]
        keeper["raw_json"]["merged_from_entry_keys"] = available_group
        keeper["raw_json"]["merge_reason"] = "ocr_linebreak_or_column_continuation"

        entry_id = f"po006_{keeper_key.rsplit(':', 1)[-1].split(':')[-1]}"
        request_parts = []
        request_page_strs: list[str] = []
        request_page_ints: list[int] = []
        for key in available_group:
            req = request_by_entry_id.get(f"po006_{key.rsplit(':', 1)[-1].split(':')[-1]}")
            if req:
                request_parts.append(req)
                for page in req.get("page_hints", []):
                    if page not in request_page_strs:
                        request_page_strs.append(page)
                for value in req.get("page_hint_ints", []):
                    if value not in request_page_ints:
                        request_page_ints.append(value)

        if not request_page_ints:
            request_page_strs, request_page_ints = extract_page_hints(group_refs)

        query_names: list[str] = []
        for candidate in [keeper.get("lemma_raw"), keeper.get("entry_raw")]:
            if candidate and candidate not in query_names:
                query_names.append(candidate)

        merged_helper_request[keeper_key] = {
            "entry_id": entry_id,
            "lemma_raw": keeper.get("lemma_raw"),
            "query_names": query_names,
            "page_hints": request_page_strs,
            "page_hint_ints": request_page_ints,
            "context_raw": keeper.get("entry_raw"),
        }
        merged_entry_data[keeper_key] = keeper

    new_entries: list[dict] = []
    for entry in entries:
        key = entry["entry_key"]
        if key in REMOVE_ENTRY_KEYS or key in MERGED_KEYS:
            continue
        if key in merged_entry_data:
            new_entries.append(merged_entry_data[key])
        else:
            new_entries.append(deepcopy(entry))

    keep_entry_keys = {entry["entry_key"] for entry in new_entries}

    new_refs: list[dict] = []
    for entry in new_entries:
        key = entry["entry_key"]
        if key in KEEPER_TO_GROUP:
            group = KEEPER_TO_GROUP[key]
            group_refs = []
            for source_key in group:
                group_refs.extend(deepcopy(refs_by_key.get(source_key, [])))
        else:
            group_refs = deepcopy(refs_by_key.get(key, []))

        dedupe_seen: set[tuple] = set()
        rewritten_refs: list[dict] = []
        for ref in group_refs:
            rewritten = deepcopy(ref)
            rewritten["entry_key"] = key
            dedupe_key = (
                rewritten.get("ref_kind"),
                rewritten.get("ref_raw"),
                rewritten.get("page_ref_raw"),
                rewritten.get("page_ref_int"),
                rewritten.get("line_ref_raw"),
                rewritten.get("range_start_raw"),
                rewritten.get("range_end_raw"),
                rewritten.get("target_file"),
            )
            if dedupe_key in dedupe_seen:
                continue
            dedupe_seen.add(dedupe_key)
            rewritten_refs.append(rewritten)

        for order, ref in enumerate(rewritten_refs, start=1):
            ref["ref_order"] = order
            new_refs.append(ref)

    helper_request_entries: list[dict] = []
    for entry in new_entries:
        key = entry["entry_key"]
        if key in merged_helper_request:
            helper_request_entries.append(merged_helper_request[key])
            continue
        entry_id = f"po006_{key.rsplit(':', 1)[-1].split(':')[-1]}"
        req = request_by_entry_id.get(entry_id)
        if req is None:
            page_hints, page_hint_ints = extract_page_hints([ref for ref in new_refs if ref["entry_key"] == key])
            req = {
                "entry_id": entry_id,
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [value for value in [entry.get("lemma_raw"), entry.get("entry_raw")] if value],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry.get("entry_raw"),
            }
        helper_request_entries.append(req)

    payload["entries"] = new_entries
    payload["refs"] = new_refs
    append_early_sections(payload, helper_request_entries, payload["refs"])

    for entry in payload["entries"]:
        if collapse_ws(entry.get("context_raw")) == collapse_ws(entry.get("entry_raw")):
            entry["context_raw"] = None

    helper_request["entries"] = helper_request_entries
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    dump_json(PAYLOAD_PATH, payload)
    dump_json(HELPER_REQUEST_PATH, helper_request)


def apply_helper_results() -> None:
    payload = load_json(PAYLOAD_PATH)
    helper_output = load_json(HELPER_OUTPUT_PATH)

    output_by_entry_id = {item["entry_id"]: item for item in helper_output["entries"]}
    helper_by_entry_key: dict[str, dict] = {}

    for entry in payload["entries"]:
        if entry["entry_key"] in LEMMA_OVERRIDES:
            entry["lemma_raw"] = LEMMA_OVERRIDES[entry["entry_key"]]
            entry["lemma_display"] = LEMMA_OVERRIDES[entry["entry_key"]]
            entry["lemma_norm"] = normalize_text(entry["lemma_raw"])
            entry["lemma_sort"] = entry["lemma_norm"]
        entry_id = f"po006_{entry['entry_key'].rsplit(':', 1)[-1].split(':')[-1]}"
        helper_item = output_by_entry_id.get(entry_id)
        if not helper_item:
            continue
        helper_summary = summarize_helper(helper_item)
        if helper_summary:
            entry.setdefault("raw_json", {})["helper"] = helper_summary
            best_candidate = helper_summary.get("best_candidate") or {}
            best_file = best_candidate.get("file")
            best_prob = best_candidate.get("probability")
            if best_file:
                helper_by_entry_key[entry["entry_key"]] = {
                    "file": best_file,
                    "probability": best_prob,
                    "summary": helper_summary,
                }
            if (entry["entry_key"] in KEEPER_TO_GROUP or not entry.get("target_file_best")) and best_file:
                entry["editorial_anchor_file"] = best_file
                entry["target_file_best"] = best_file
                if isinstance(best_prob, (int, float)):
                    entry["confidence"] = float(best_prob)

    for ref in payload["refs"]:
        helper = helper_by_entry_key.get(ref["entry_key"])
        if not helper or ref.get("target_file"):
            continue
        ref["target_file"] = helper["file"]
        ref["editorial_anchor_file"] = helper["file"]
        if isinstance(helper.get("probability"), (int, float)):
            ref["target_file_probability"] = float(helper["probability"])
            ref["confidence"] = min(0.95, max(float(ref.get("confidence") or 0.0), float(helper["probability"])))
        ref.setdefault("raw_json", {})["helper"] = {
            "status": helper["summary"].get("status"),
            "candidate_role": helper["summary"].get("candidate_role"),
            "reason_summary": helper["summary"].get("reason_summary"),
        }

    dump_json(PAYLOAD_PATH, payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-helper", action="store_true")
    args = parser.parse_args()
    if args.apply_helper:
        apply_helper_results()
    else:
        rebuild_payload()

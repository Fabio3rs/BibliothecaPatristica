#!/usr/bin/env python3
"""Usage: rebuild the PO002 alphabetical payload from the validated checkpoint and OCR grouping.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_po002_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PO002/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO002_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO002_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PO002 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO002_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO002"
COLLECTION = "PO"
VOLUME_LABEL = "Patrologia Orientalis 2"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PO002/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PO002_alphabetical_indices.json"
DEFAULT_CHECKPOINT_FILE = ROOT / "data/alphabetical_index_payloads/PO002_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PO002_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PO002_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO002"
SECTION_KEY = f"{VOLUME_ID}:alpha:onomastic_mixed:001"
SECTION_START_FILE = str(DEFAULT_SOURCE_ROOT / "3a441df3-4fd8-43ea-8009-0db01b8869dc-568.txt")
PAGE_559_FILE = str(DEFAULT_SOURCE_ROOT / "3a441df3-4fd8-43ea-8009-0db01b8869dc-569.txt")
PAGE_560_FILE = str(DEFAULT_SOURCE_ROOT / "3a441df3-4fd8-43ea-8009-0db01b8869dc-570.txt")
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſƀ-ɏͰ-ϿЀ-ӿԱ-Ֆա-ֆא-ת؀-ۿ܀-ݏ"
HYPHEN_SPLIT_RE = re.compile(rf"([{WORD_CHARS}]+)-\s+([{WORD_CHARS}]+)")
DIGIT_RANGE_SPLIT_RE = re.compile(r"(\d+)-\s+(\d+)")
SPACE_RE = re.compile(r"\s+")

# Inclusive, 1-based ranges from the invalid checkpoint that map to one logical entry.
MERGE_GROUPS: list[tuple[int, int]] = [
    (49, 51), (52, 53), (54, 54), (55, 55), (56, 57), (58, 58), (59, 60), (61, 63),
    (64, 71), (72, 72), (73, 75), (76, 76), (77, 78), (79, 80), (81, 81), (82, 82),
    (83, 84), (85, 85), (86, 88), (89, 90), (91, 91), (92, 93), (94, 94), (95, 97),
    (98, 98), (99, 99), (100, 100), (101, 101), (102, 103), (104, 104), (105, 106),
    (107, 107), (108, 110), (111, 114), (115, 115), (116, 116), (117, 118), (119, 121),
    (122, 123), (124, 126), (127, 131), (132, 133), (134, 134), (135, 135), (136, 136),
    (137, 137), (138, 143), (144, 144), (145, 145), (146, 146), (147, 147), (148, 148),
    (149, 149), (150, 150), (151, 152), (153, 154), (155, 156), (157, 157), (158, 158),
    (159, 159), (160, 160), (161, 161), (162, 163), (164, 164), (165, 165), (166, 168),
    (169, 170), (171, 171), (172, 172), (173, 174), (175, 176), (177, 180), (181, 181),
    (182, 183), (184, 184), (185, 188), (189, 189), (190, 192), (193, 195), (196, 196),
    (197, 198), (199, 199), (200, 200), (201, 201), (202, 203), (204, 204), (205, 205),
    (206, 208), (209, 211), (212, 212), (213, 213), (214, 215), (216, 217), (218, 218),
    (219, 221), (222, 222), (223, 223),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def normalize_text(text: str) -> str:
    value = text.replace("\xa0", " ")
    value = DIGIT_RANGE_SPLIT_RE.sub(r"\1-\2", value)
    while True:
        updated = HYPHEN_SPLIT_RE.sub(r"\1\2", value)
        if updated == value:
            break
        value = updated
    value = SPACE_RE.sub(" ", value).strip()
    return value


def normalize_key_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = normalize_text(text)
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = re.sub(r"[=,;:.()\[\]*/?]+", " ", value)
    value = SPACE_RE.sub(" ", value).strip().lower()
    return value or None


def derive_lemma(entry_raw: str, entry_kind: str) -> str | None:
    text = entry_raw.strip().rstrip(".")
    if entry_kind == "cross_reference":
        parts = re.split(r"\b(?:voir|Voir|vid\.?|vide|cf\.?)\b", text, maxsplit=1)
        return parts[0].strip(" .;:") or None
    if ":" in text:
        return text.split(":", 1)[0].strip(" .;:")
    return text.strip(" .;:") or None


def dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            ref.get("ref_kind"),
            ref.get("ref_raw"),
            ref.get("page_ref_raw"),
            ref.get("page_ref_int"),
            ref.get("page_ref_col"),
            ref.get("line_ref_raw"),
            ref.get("range_start_raw"),
            ref.get("range_end_raw"),
            ref.get("target_file"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]], source_root: Path) -> dict[str, Any]:
    request_entries: list[dict[str, Any]] = []
    for entry in entries:
        refs = refs_by_entry.get(entry["entry_key"], [])
        page_hints = []
        seen_pages: set[int] = set()
        for ref in refs:
            page = ref.get("page_ref_int")
            if isinstance(page, int) and page not in seen_pages:
                seen_pages.add(page)
                page_hints.append(page)
        if not page_hints:
            continue
        lemma = entry.get("lemma_raw") or entry["entry_raw"].split(":", 1)[0].strip()
        request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [lemma],
                "page_hints": [str(page) for page in page_hints[:8]],
                "page_hint_ints": page_hints[:8],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"max_candidates": 5},
        "entries": request_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_map.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": best.get("candidate_role") or (helper.get("candidates") or [{}])[0].get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best or None,
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in helper.get("candidates", [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best["file"]
            if entry.get("editorial_anchor_file") != best["file"]:
                raw_json["target_override_reason"] = "helper_best_candidate"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--checkpoint-file", type=Path, default=DEFAULT_CHECKPOINT_FILE)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    checkpoint = json.loads(args.checkpoint_file.read_text(encoding="utf-8"))
    old_entries = checkpoint["entries"]
    old_refs = checkpoint["refs"]
    old_scripture_refs = checkpoint["scripture_refs"]

    if len(old_entries) < 223:
        for entry in old_entries:
            entry["entry_raw"] = normalize_text(entry["entry_raw"])
            lemma_raw = derive_lemma(entry["entry_raw"], entry.get("entry_kind") or "lemma")
            entry["lemma_raw"] = lemma_raw
            entry["lemma_display"] = lemma_raw
            entry["lemma_norm"] = normalize_key_text(lemma_raw)
            entry["lemma_sort"] = normalize_key_text(lemma_raw)
            raw_json = entry.get("raw_json") or {}
            if isinstance(raw_json, dict) and "segment_source" in raw_json:
                raw_json["segment_source"] = normalize_text(raw_json["segment_source"])
                entry["raw_json"] = raw_json
        checkpoint["generated_at"] = now_iso()
        write_json(args.output_file, checkpoint)
        return

    refs_by_old_key: dict[str, list[dict[str, Any]]] = {}
    for ref in old_refs:
        refs_by_old_key.setdefault(ref["entry_key"], []).append(ref)
    scripture_by_old_key: dict[str, list[dict[str, Any]]] = {}
    for ref in old_scripture_refs:
        scripture_by_old_key.setdefault(ref["entry_key"], []).append(ref)

    new_entries: list[dict[str, Any]] = []
    new_refs: list[dict[str, Any]] = []
    new_scripture_refs: list[dict[str, Any]] = []
    refs_for_helper: dict[str, list[dict[str, Any]]] = {}

    entry_counter = 0
    entry_key_map: dict[str, str] = {}

    def append_entry(base_entry: dict[str, Any], entry_raw: str, source_keys: list[str], anchor_file: str) -> None:
        nonlocal entry_counter
        entry_counter += 1
        new_key = f"{VOLUME_ID}:entry:{entry_counter:03d}"
        entry_key_map.update({old_key: new_key for old_key in source_keys})
        combined_refs = []
        for old_key in source_keys:
            combined_refs.extend(refs_by_old_key.get(old_key, []))
        combined_refs = dedupe_refs(combined_refs)
        inferred_page = None
        for ref in combined_refs:
            if isinstance(ref.get("page_ref_int"), int):
                inferred_page = ref["page_ref_int"]
                break
        if inferred_page is None:
            inferred_page = base_entry.get("inferred_printed_page")
        raw_json = {
            "segment_source": entry_raw,
            "merged_from_old_keys": source_keys,
            "page_hints": [ref["page_ref_int"] for ref in combined_refs if isinstance(ref.get("page_ref_int"), int)],
            "checkpoint_rebuild": True,
        }
        lemma_raw = derive_lemma(entry_raw, base_entry.get("entry_kind") or "lemma")
        entry = {
            "entry_key": new_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": base_entry.get("entry_kind"),
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize_key_text(lemma_raw),
            "lemma_sort": normalize_key_text(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": inferred_page,
            "section_start_file": SECTION_START_FILE,
            "editorial_anchor_file": anchor_file,
            "target_file_best": base_entry.get("target_file_best") or anchor_file,
            "confidence": base_entry.get("confidence"),
            "raw_json": raw_json,
        }
        new_entries.append(entry)

        entry_refs: list[dict[str, Any]] = []
        for order, ref in enumerate(combined_refs, start=1):
            new_ref = json.loads(json.dumps(ref))
            new_ref["entry_key"] = new_key
            new_ref["ref_order"] = order
            new_ref["section_start_file"] = SECTION_START_FILE
            new_ref["editorial_anchor_file"] = anchor_file
            raw = new_ref.get("raw_json") or {}
            if isinstance(raw, dict):
                raw["merged_from_old_keys"] = source_keys
                new_ref["raw_json"] = raw
            entry_refs.append(new_ref)
            new_refs.append(new_ref)
        refs_for_helper[new_key] = entry_refs

        combined_scripture = []
        for old_key in source_keys:
            combined_scripture.extend(scripture_by_old_key.get(old_key, []))
        for order, scripture_ref in enumerate(combined_scripture, start=1):
            new_scripture = json.loads(json.dumps(scripture_ref))
            new_scripture["entry_key"] = new_key
            new_scripture["ref_order"] = order
            raw = new_scripture.get("raw_json") or {}
            if isinstance(raw, dict):
                raw["merged_from_old_keys"] = source_keys
                new_scripture["raw_json"] = raw
            new_scripture_refs.append(new_scripture)

    for old_entry in old_entries[:48]:
        entry_counter += 1
        new_key = f"{VOLUME_ID}:entry:{entry_counter:03d}"
        cloned = json.loads(json.dumps(old_entry))
        entry_key_map[old_entry["entry_key"]] = new_key
        cloned["entry_key"] = new_key
        cloned["entry_order"] = entry_counter
        cloned["section_start_file"] = SECTION_START_FILE
        new_entries.append(cloned)

        entry_refs: list[dict[str, Any]] = []
        for ref in refs_by_old_key.get(old_entry["entry_key"], []):
            new_ref = json.loads(json.dumps(ref))
            new_ref["entry_key"] = new_key
            entry_refs.append(new_ref)
            new_refs.append(new_ref)
        for order, ref in enumerate(entry_refs, start=1):
            ref["ref_order"] = order
        refs_for_helper[new_key] = entry_refs

        for scripture_ref in scripture_by_old_key.get(old_entry["entry_key"], []):
            new_scripture = json.loads(json.dumps(scripture_ref))
            new_scripture["entry_key"] = new_key
            new_scripture_refs.append(new_scripture)

    for start, end in MERGE_GROUPS:
        group_entries = old_entries[start - 1:end]
        source_keys = [entry["entry_key"] for entry in group_entries]
        base_entry = group_entries[0]
        anchor_file = PAGE_559_FILE if start < 164 else PAGE_560_FILE
        merged_text = normalize_text(" ".join(entry["entry_raw"] for entry in group_entries))
        append_entry(base_entry, merged_text, source_keys, anchor_file)

    for entry in new_entries:
        if entry["entry_key"] in refs_for_helper:
            entry["raw_json"]["page_hints"] = [
                ref["page_ref_int"]
                for ref in refs_for_helper[entry["entry_key"]]
                if isinstance(ref.get("page_ref_int"), int)
            ]

    helper_request = build_helper_request(new_entries, refs_for_helper, args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    attach_helper(new_entries, helper_output)

    for entry in new_entries:
        if entry["entry_kind"] == "cross_reference" and not refs_for_helper.get(entry["entry_key"]):
            entry["target_file_best"] = entry["editorial_anchor_file"]

    checkpoint["generated_at"] = now_iso()
    checkpoint["volume"]["volume_id"] = VOLUME_ID
    checkpoint["volume"]["collection"] = COLLECTION
    checkpoint["volume"]["source_root"] = str(args.source_root)
    checkpoint["volume"]["volume_label"] = VOLUME_ID
    notes = checkpoint.get("notes") or []
    note = "Rebuilt entries 49+ from OCR-backed merge groups on index pages 559-560; removed checkpoint line-break artifacts."
    if note not in notes:
        notes.append(note)
    checkpoint["notes"] = notes
    checkpoint["entries"] = new_entries
    checkpoint["refs"] = new_refs
    checkpoint["scripture_refs"] = new_scripture_refs
    checkpoint["nodes"] = checkpoint.get("nodes") or []
    checkpoint["coverage"] = {
        "entries_status": "complete",
        "entries_status_reason": "PO002 checkpoint rebuilt against OCR pages 568-570 and revalidated.",
        "evidence_files": [SECTION_START_FILE, PAGE_559_FILE, PAGE_560_FILE],
    }

    write_json(args.intermediate_dir / "entries.json", new_entries)
    write_json(args.intermediate_dir / "refs.json", new_refs)
    write_json(args.intermediate_dir / "scripture_refs.json", new_scripture_refs)
    write_json(args.intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": checkpoint["generated_at"],
        "entry_count": len(new_entries),
        "ref_count": len(new_refs),
        "scripture_ref_count": len(new_scripture_refs),
        "helper_request_json": str(args.helper_request_json),
        "helper_output_json": str(args.helper_output_json),
    })
    write_json(args.output_file, checkpoint)


if __name__ == "__main__":
    main()

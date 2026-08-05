#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_po007_alphabetical_payload.py
# Rebuilds the PO007 alphabetical-index payload from the verified OCR window,
# refreshes helper artifacts, writes intermediate fragments, and validates.

from __future__ import annotations

import json
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO007"
COLLECTION = "PO"
SOURCE_ROOT = ROOT / "teste/PO007/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PO007_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PO007_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PO007_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO007"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"

NAMES_098 = SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-098.txt"
NAMES_099 = SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-099.txt"
SCRIPTURE_100 = SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-100.txt"
ADDENDA_464 = SOURCE_ROOT / "dc5f1d69-b11b-43ce-ac3b-40d3ad0c1f77-464.txt"


SECTION_NAMES = f"{VOLUME_ID}:alpha:onomastic_mixed:001"
SECTION_SCRIPTURE = f"{VOLUME_ID}:alpha:scripture_index:002"
SECTION_ADDENDA = f"{VOLUME_ID}:alpha:editorial_closure:003"


PAGE98_GROUPS: list[tuple[str, list[str], str]] = [
    ("ܐ", ["ܐܒܪܗܡ", "ܐܕܡ", "ܐܝܒ", "ܐܝܣܚܩ", "ܐܠܝܐ", "ܐܠܥܙܐ", "ܐܣܛܦܢܘܣ", "ܐܣܬܪ", "ܐܦܪܝܡ ܡܠܦܐ", "ܬܘܪ̈ܝܐ"], "source line block 098, right column"),
    ("ܒ", ["ܒܝܬ ܡܕܝܝ̈ܐ"], "source line block 098, right column"),
    ("ܓ", ["ܓܒܪܝܠ"], "source line block 098, right column"),
    ("ܕ", ["ܕܢܫܐܝܠ"], "source line block 098, middle column"),
    ("ܗ", [], "empty printed letter heading in OCR 098"),
    ("ܘ", [], "empty printed letter heading in OCR 098"),
    ("ܙ", ["ܙܟܪܝܐ"], "source line block 098, left column"),
    ("ܚ", ["ܚܙܩܝܐ", "ܚܢ ܐܢܒܝܬܐ", "ܚܢܘܟ", "ܚܢܢܐ ܚܕܝܒܚܐ", "ܚܢܢܐ"], "source line block 098, left column"),
    ("ܛ", ["ܛܝܡܬܐܘܣ"], "letter heading inferred from the initial Syriac character; OCR gives the item but not a separate heading"),
    ("ܝ", ["ܝܘܚܢܢ", "ܝܘܚܢ ܐܣܟܘܠܝܐܼ"], "source line block 098, left column"),
    ("ܟ", [], "empty printed letter heading in OCR 098"),
    ("ܠ", ["ܠܘܐ ܐܘܢܓܠܣ"], "source line block 098, left column"),
    ("ܡ", ["ܡܘܢܐ", "ܡܢܫ̈ܢܝܐ", "ܡܨ̈ܝܐ", "ܡܪܝ ܡܓܕܝܬܐ", "ܡܕ̈ܩܝܘܢܝܐ"], "source line block 098, left column"),
    ("ܢ", ["ܢܣܛܘܪܝܣ", "ܢܨܪܬ ܕܓܝܠܐ", "ܢܬܢܐܝܠ"], "source line block 098, left column"),
    ("ܣ", ["ܣܕܘܡ̈ܝܐ", "ܒܒܢܓ", "ܒܘ"], "letter heading inferred before same block's final items; last two strings are low-confidence OCR fragments"),
]


ADDENDA_ITEMS = [
    ("Comme il a été dit (p. 396, 432), A ajoute des salām ou des qenē qui manquent dans les autres mss.", [396, 432]),
    ("1) Salām pour Abbā Nob (p. 396).", [396]),
    ("2) Salām pour Abbā Takla Adonāy (p. 396).", [396]),
    ("3) Au 25 Ḥamlē (p. 423). Ce salām précède la mention de Maryam p. 403, l. 15-16).", [423, 403]),
    ("4, 5) Deux salam ou qene (p. 432).", [432]),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_previous() -> dict[str, Any]:
    return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def node_key(label: str, order: int) -> str:
    safe = f"{order:03d}"
    return f"{VOLUME_ID}:node:names:{safe}"


def make_node(label: str, order: int, source_note: str, file_path: Path) -> dict[str, Any]:
    return {
        "node_key": node_key(label, order),
        "section_key": SECTION_NAMES,
        "parent_node_key": None,
        "node_order": order,
        "node_kind": "letter_group",
        "label_raw": label,
        "label_norm": label,
        "label_sort": label,
        "node_level": 1,
        "confidence": 0.92 if "inferred" not in source_note else 0.72,
        "raw_json": {
            "source_file": str(file_path),
            "source_note": source_note,
            "section_kind_reason": "Syriac letter grouping in TABLE DES NOMS PROPRES",
        },
    }


def make_name_entry(entry_no: int, label: str, raw: str, parent: str, file_path: Path, note: str) -> dict[str, Any]:
    confidence = 0.9
    if "low-confidence" in note:
        confidence = 0.55
    elif "inferred" in note:
        confidence = 0.74
    return {
        "entry_key": f"{VOLUME_ID}:entry:{entry_no:03d}",
        "section_key": SECTION_NAMES,
        "parent_node_key": parent,
        "entry_order": entry_no,
        "entry_kind": "lemma",
        "lemma_raw": raw,
        "lemma_display": raw,
        "lemma_norm": raw,
        "lemma_sort": raw,
        "entry_raw": raw,
        "context_raw": None,
        "heading_letter": label,
        "inferred_printed_page": 88 if file_path == NAMES_098 else 89,
        "section_start_file": str(NAMES_098),
        "editorial_anchor_file": str(file_path),
        "target_file_best": str(file_path),
        "confidence": confidence,
        "raw_json": {
            "source_file": str(file_path),
            "heading_letter": label,
            "note": note,
            "locator_note": "No page/line locator is printed for this name in the OCR line.",
        },
    }


SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def parse_page_hints(entry_raw: str) -> tuple[list[str], list[int]]:
    hints: list[str] = []
    ints: list[int] = []
    for match in re.finditer(r"(\d{1,3})([₀₁₂₃₄₅₆₇₈₉]+)?", entry_raw):
        raw = match.group(0)
        page = int(match.group(1))
        if page not in ints:
            ints.append(page)
            hints.append(raw)
    return hints, ints


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        if entry["section_key"] != SECTION_NAMES:
            continue
        hints, ints = parse_page_hints(entry["entry_raw"])
        if not ints:
            continue
        helper_entries.append(
            {
                "entry_id": f"po007_{entry['entry_key'].split(':')[-1]}",
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"], entry["entry_raw"]],
                "page_hints": [str(i) for i in ints],
                "page_hint_ints": ints,
                "context_raw": entry["entry_raw"],
                "payload_entry_key": entry["entry_key"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "candidate_window": 3,
            "notes": "Generated by build_po007_alphabetical_payload.py from verified TABLE DES NOMS PROPRES entries with material page/line locators.",
        },
        "entries": helper_entries,
    }


def run_helper() -> dict[str, Any]:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        cwd=ROOT,
        check=True,
    )
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def slim_helper(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates = []
    for cand in item.get("candidates", [])[:3]:
        candidates.append(
            {
                "rank": cand.get("rank"),
                "file": cand.get("file"),
                "file_seq": cand.get("file_seq"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:8]],
            }
        )
    return {
        "status": item.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "file_seq": best.get("file_seq"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        },
        "top_candidates": candidates,
    }


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_entry = {}
    for item in helper_output.get("entries", []):
        payload_key = item.get("request", {}).get("payload_entry_key") or item.get("payload_entry_key")
        if not payload_key:
            payload_key = f"{VOLUME_ID}:entry:{str(item.get('entry_id','')).split('_')[-1]}"
        by_entry[payload_key] = item

    for entry in entries:
        item = by_entry.get(entry["entry_key"])
        if not item:
            continue
        best = item.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best["file"]
        if best.get("probability") is not None:
            entry["confidence"] = min(float(best["probability"]), 0.94)
        entry.setdefault("raw_json", {})["helper"] = slim_helper(item)

    for ref in refs:
        item = by_entry.get(ref["entry_key"])
        if not item:
            continue
        best = item.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best["file"]
        if best.get("probability") is not None:
            ref["target_file_probability"] = best["probability"]
            ref["confidence"] = min(float(best["probability"]), ref.get("confidence") or 1.0)
        ref.setdefault("raw_json", {})["helper"] = slim_helper(item)


def ref_norm(ref: dict[str, Any]) -> str | None:
    book = ref.get("book_norm")
    chapter = ref.get("chapter_start")
    verse = ref.get("verse_start")
    if not book or not chapter:
        return None
    value = f"{book} {chapter}"
    if verse:
        value += f",{verse}"
    if ref.get("chapter_end") or ref.get("verse_end"):
        end_ch = ref.get("chapter_end") or chapter
        end_v = ref.get("verse_end")
        value += f"-{end_ch}"
        if end_v:
            value += f",{end_v}"
    return value


def material_file_for_page(page: int) -> str | None:
    matches = sorted(SOURCE_ROOT.glob(f"*-{page:03d}.txt"))
    return str(matches[0]) if matches else None


def make_addenda_refs(entry: dict[str, Any], pages: list[int]) -> list[dict[str, Any]]:
    out = []
    for idx, page in enumerate(pages, start=1):
        target = material_file_for_page(page)
        out.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": idx,
                "ref_kind": "editorial_page",
                "ref_raw": f"p. {page}",
                "page_ref_raw": f"p. {page}",
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": "l. 15-16" if page == 403 and "15-16" in entry["entry_raw"] else None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target,
                "target_file_probability": 0.82 if target else None,
                "section_start_file": str(ADDENDA_464),
                "editorial_anchor_file": str(ADDENDA_464),
                "confidence": 0.82 if target else 0.62,
                "raw_json": {
                    "source_file": str(ADDENDA_464),
                    "locator_resolution": "direct file suffix lookup inside PO007 source_root",
                },
            }
        )
    return out


def rebuild_payload() -> dict[str, Any]:
    previous = load_previous()
    old_entries = previous["entries"]
    old_refs = previous["refs"]
    old_scripture = previous["scripture_refs"]

    sections = deepcopy(previous["sections"])
    sections[0]["page_start"] = 88
    sections[0]["page_end"] = 89
    sections[0]["file_start"] = str(NAMES_098)
    sections[0]["file_end"] = str(NAMES_099)
    sections[0]["raw_json"] = {
        "source": "OCR files 098-099; printed pages 88-89 confirmed by TABLE DES MATIÈRES on OCR file 101",
        "section_type": "Syriac proper-name table",
        "candidate_files": [str(NAMES_098), str(NAMES_099)],
        "neighbor_checks": [str(SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-097.txt"), str(SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-101.txt")],
        "excluded_material": ["TABLE DES MATIÈRES on OCR file 101", "body text on OCR file 097"],
    }
    sections[2]["raw_json"] = {
        "source": "OCR page 464",
        "note": "Editorial ADDENDA page; serialized as editorial_closure with note entries and page locators.",
    }

    nodes = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    next_entry = 1

    node_order = 1
    node_for_label: dict[tuple[str, str], str] = {}
    for label, values, note in PAGE98_GROUPS:
        key = node_key(label, node_order)
        nodes.append(make_node(label, node_order, note, NAMES_098))
        node_for_label[("098", label)] = key
        node_order += 1
        for value in values:
            local_note = note
            if value in {"ܒܒܢܓ", "ܒܘ"}:
                local_note += "; low-confidence OCR fragment retained rather than omitted"
            entries.append(make_name_entry(next_entry, label, value, key, NAMES_098, local_note))
            next_entry += 1

    page99_labels = []
    for old in old_entries[:26]:
        if old["entry_order"] < 7:
            continue
        label = old.get("heading_letter")
        if label not in page99_labels:
            page99_labels.append(label)
    for label in page99_labels:
        key = node_key(label, node_order)
        nodes.append(make_node(label, node_order, "source block in OCR 099", NAMES_099))
        node_for_label[("099", label)] = key
        node_order += 1

    old_to_new: dict[str, str] = {}
    for old in old_entries[:26]:
        if old["entry_order"] < 7:
            continue
        new = deepcopy(old)
        old_key = old["entry_key"]
        new_key = f"{VOLUME_ID}:entry:{next_entry:03d}"
        old_to_new[old_key] = new_key
        new["entry_key"] = new_key
        new["entry_order"] = next_entry
        new["parent_node_key"] = node_for_label.get(("099", old.get("heading_letter")))
        new["context_raw"] = None
        new["section_start_file"] = str(NAMES_098)
        new["editorial_anchor_file"] = str(NAMES_099)
        new.setdefault("raw_json", {})["source_file"] = str(NAMES_099)
        new["raw_json"]["modeling_note"] = "Retained from prior checkpoint after OCR verification; parent_node_key now points to explicit Syriac letter node."
        entries.append(new)
        next_entry += 1

    for old_ref in old_refs:
        if old_ref["entry_key"] not in old_to_new:
            continue
        ref = deepcopy(old_ref)
        ref["entry_key"] = old_to_new[old_ref["entry_key"]]
        ref["context_raw"] = None
        ref["section_start_file"] = str(NAMES_098)
        ref["editorial_anchor_file"] = str(NAMES_099)
        refs.append(ref)

    for old in old_entries[26:]:
        new = deepcopy(old)
        old_key = old["entry_key"]
        new_key = f"{VOLUME_ID}:entry:{next_entry:03d}"
        old_to_new[old_key] = new_key
        new["entry_key"] = new_key
        new["entry_order"] = next_entry
        new["parent_node_key"] = None
        new["context_raw"] = None
        new["section_start_file"] = str(SCRIPTURE_100)
        new["editorial_anchor_file"] = str(SCRIPTURE_100)
        new["target_file_best"] = str(SCRIPTURE_100)
        new.setdefault("raw_json", {})["source_file"] = str(SCRIPTURE_100)
        entries.append(new)
        next_entry += 1

    for old_sr in old_scripture:
        sr = deepcopy(old_sr)
        sr["entry_key"] = old_to_new[old_sr["entry_key"]]
        sr["ref_norm"] = ref_norm(sr)
        raw = sr.setdefault("raw_json", {})
        entry_raw = next(e["entry_raw"] for e in entries if e["entry_key"] == sr["entry_key"])
        if entry_raw.lstrip().startswith("—") or re.match(r"^[IVXLCDM]+[,\\.]", entry_raw.strip()) or re.match(r"^\\d+[\\.]", entry_raw.strip()):
            raw["inherited_book"] = True
        scripture_refs.append(sr)

    for text, pages in ADDENDA_ITEMS:
        entry = {
            "entry_key": f"{VOLUME_ID}:entry:{next_entry:03d}",
            "section_key": SECTION_ADDENDA,
            "parent_node_key": None,
            "entry_order": next_entry,
            "entry_kind": "editorial_note",
            "lemma_raw": None,
            "lemma_display": None,
            "lemma_norm": None,
            "lemma_sort": None,
            "entry_raw": text,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": None,
            "section_start_file": str(ADDENDA_464),
            "editorial_anchor_file": str(ADDENDA_464),
            "target_file_best": str(ADDENDA_464),
            "confidence": 0.86,
            "raw_json": {
                "source_file": str(ADDENDA_464),
                "note": "Short editorial ADDENDA item extracted from OCR block; Ethiopic text blocks are not duplicated in entry_raw.",
            },
        }
        entries.append(entry)
        refs.extend(make_addenda_refs(entry, pages))
        next_entry += 1

    helper_request = build_helper_request(entries)
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output = run_helper()
    apply_helper(entries, refs, helper_output)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "Patrologia Orientalis volume with TABLE DES NOMS PROPRES and TABLE DES CITATIONS DE L'ÉCRITURE sections.",
                "TABLE DES NOMS PROPRES spans OCR files 098-099; printed pages 88-89 were confirmed from TABLE DES MATIÈRES on OCR file 101.",
                "Syriac letter headings are modeled as nodes; page 098 names without printed locators remain anchored to their index page.",
                "ADDENDA at OCR file 464 was recorded as editorial_closure with compact note entries and page locators.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Verified OCR pages 098-100 and neighboring files 097/101. Names, scripture citations, and compact ADDENDA notes were serialized; ambiguous Syriac fragments from page 098 are retained with low confidence instead of omitted.",
            "evidence_files": [str(NAMES_098), str(NAMES_099), str(SCRIPTURE_100), str(ADDENDA_464), str(SOURCE_ROOT / "b12002a8-e127-40c6-8775-42b18821d10d-101.txt")],
        },
        "notes": [
            "The prior checkpoint confused OCR footer/page signals on file 098; the section page range is corrected to printed pages 88-89 based on the contents table.",
            "The TABLE DES CITATIONS DE L'ÉCRITURE section uses inherited biblical book context only from explicit book headings or dash/continuation lines.",
            "No raw OCR page dumps are embedded in entry_raw or raw_json; long Ethiopic ADDENDA blocks are represented by compact editorial notes.",
        ],
    }

    return payload


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = rebuild_payload()

    for key in ["volume", "sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"]:
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "output_file": str(OUTPUT_FILE),
            "helper_request_json": str(HELPER_REQUEST_JSON),
            "helper_output_json": str(HELPER_OUTPUT_JSON),
            "counts": {k: len(payload[k]) for k in ["sections", "nodes", "entries", "refs", "scripture_refs"]},
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PO007 payload rebuilt and validated from verified OCR windows.",
            "completed": [
                "verified candidate sections and neighboring files",
                "re-modeled Syriac letter headings as nodes",
                "added compact ADDENDA entries",
                "rebuilt helper request and applied helper output",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Page 098 printed page is treated as 88 from the table of contents, despite OCR footer noise.",
                "Low-confidence Syriac fragments on page 098 were retained with explicit notes.",
            ],
        },
    )
    write_json(OUTPUT_FILE, payload)

    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(OUTPUT_FILE),
            "--validate-only",
            "--print-summary",
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()

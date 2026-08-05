#!/usr/bin/env python3
"""Usage: build the PG067 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg067_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG067/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG067_helper_output.json \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG067_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG067"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 67"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG067/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG067_alphabetical_indices.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG067_helper_output.json"

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
REF_RE = re.compile(r"\b(?:Socr\.|Soz\.|Soc\.|ibid\.|ib\.)\s*([0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*)?", re.IGNORECASE)
NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_ONLY_RE = re.compile(r"^\d{1,4}(?:\s+\d{1,4})?$")
CONTINUATION_HEADER_RE = re.compile(
    r"^\d{4}\s+(?:INDEX ANALYTICUS\.?|INDEX AD NOTAS VARIORUM\.?|ORDO RERUM)\s+\d{4}\s+",
    re.IGNORECASE,
)


SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS IN HISTORIAM ECCLESIASTICAM SOCRATIS ET SOZOMENI.",
        "heading_norm": "index analyticus in historiam ecclesiasticam socratis et sozomeni",
        "page_start": 1675,
        "page_end": 1690,
        "file_start_seq": 842,
        "file_end_seq": 849,
        "section_order": 1,
        "kind_reason": "Alphabetical analytical index of historical topics and names for Socrates and Sozomenus.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:002",
        "section_kind": "author_index",
        "heading_raw": "INDEX AUCTORUM.",
        "heading_norm": "index auctorum",
        "page_start": 1691,
        "page_end": 1692,
        "file_start_seq": 850,
        "file_end_seq": 850,
        "section_order": 2,
        "kind_reason": "Alphabetical index of authors and cited works headed INDEX AUCTORUM.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:003",
        "section_kind": "author_index",
        "heading_raw": "INDEX AUCTORUM. Qui citantur a Socrate ac Sozomeno.",
        "heading_norm": "index auctorum qui citantur a socrate ac sozomeno",
        "page_start": 1691,
        "page_end": 1692,
        "file_start_seq": 850,
        "file_end_seq": 850,
        "section_order": 3,
        "kind_reason": "Sub-index of authors and works cited by Socrates and Sozomenus, editorially distinct from the preceding author list.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:004",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX AD VALESII NOTAS.",
        "heading_norm": "index ad valesii notas",
        "page_start": 1695,
        "page_end": 1696,
        "file_start_seq": 851,
        "file_end_seq": 851,
        "section_order": 4,
        "kind_reason": "Alphabetical notes index attached to Valesius annotations; mixed names and analytic topics.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:005",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX AD NOTAS VARIORUM.",
        "heading_norm": "index ad notas variorum",
        "page_start": 1697,
        "page_end": 1698,
        "file_start_seq": 852,
        "file_end_seq": 852,
        "section_order": 5,
        "kind_reason": "Alphabetical notes index attached to the Variorum notes; editorially a separate notes index.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:006",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX AD VARIORUM NOTAS. Qui et tabulæ Chronologicæ usum præstat.",
        "heading_norm": "index ad variorum notas qui et tabulae chronologicae usum praestat",
        "page_start": 1699,
        "page_end": 1704,
        "file_start_seq": 853,
        "file_end_seq": 856,
        "section_order": 6,
        "kind_reason": "Second Variorum notes index, editorially distinct from the preceding notes index and used as a chronological table.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:007",
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "page_start": 1711,
        "page_end": 1720,
        "file_start_seq": 857,
        "file_end_seq": 866,
        "section_order": 7,
        "kind_reason": "Closing contents table for the tome, editorial closure rather than an index of lemmas.",
    },
]

SECTION_ORDER = {spec["section_key"]: spec["section_order"] for spec in SECTION_SPECS}
SECTION_LOOKUP = {spec["section_key"]: spec for spec in SECTION_SPECS}
SECTION_BY_SEQ: dict[int, str] = {}
for spec in SECTION_SPECS:
    for seq in range(spec["file_start_seq"], spec["file_end_seq"] + 1):
        SECTION_BY_SEQ[seq] = spec["section_key"]

HELPER_LEMMA_TO_ENTRY_ID = {
    "Athanasius quo anno ab exsilio fuerit revocatus": "pg067_athanasius_exsilio_044",
    "Constantinus Magnus imperare cœpit anno Christi 306": "pg067_constantinus_imp_006",
    "Alexander episcopus Alexandriæ": "pg067_alexander_episcopus_009",
    "Theodosius senior": "pg067_theodosius_senior_379",
    "Valens": "pg067_valens_372",
    "Zosimus": "pg067_zosimus_417",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])-\s+([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])", r"\1\2", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    cleaned = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def page_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=page_seq)


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for token in HEADER_PAGE_RE.findall(header):
            if token.startswith("0"):
                continue
            value = int(token)
            mapping.setdefault(value, str(path))
    return mapping


def load_helper_map(helper_output_json: Path) -> dict[str, dict[str, Any]]:
    if not helper_output_json.exists():
        return {}
    data = json.loads(helper_output_json.read_text(encoding="utf-8"))
    mapping: dict[str, dict[str, Any]] = {}
    for item in data.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        mapping[entry_id] = {
            "status": item.get("status"),
            "best_candidate": item.get("best_candidate") or {},
            "candidates": item.get("candidates") or [],
        }
    return mapping


def first_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for match in NUM_RE.finditer(text):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            numbers.append(value)
    return numbers


def infer_entry_refs(text: str) -> list[int]:
    refs: list[int] = []
    for match in REF_RE.finditer(text):
        raw_nums = match.group(1)
        if not raw_nums:
            continue
        for token in raw_nums.split(","):
            token = token.strip()
            if token.isdigit():
                refs.append(int(token))
    if not refs:
        refs = first_numbers(text)
    seen: set[int] = set()
    ordered: list[int] = []
    for num in refs:
        if num not in seen:
            seen.add(num)
            ordered.append(num)
    return ordered


def strip_page_header_continuation(text: str) -> str:
    return CONTINUATION_HEADER_RE.sub("", text).strip()


def repair_terminal_hyphen_entries(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired: list[dict[str, Any]] = []
    key_remap: dict[str, str | None] = {}
    i = 0
    while i < len(entries):
        entry = entries[i]
        text = normalize(entry.get("entry_raw")) or ""
        if text == "-":
            key_remap[entry["entry_key"]] = None
            i += 1
            continue
        if text.endswith("-") and i + 1 < len(entries):
            nxt = entries[i + 1]
            if nxt.get("section_key") == entry.get("section_key"):
                next_text = strip_page_header_continuation(normalize(nxt.get("entry_raw")) or "")
                merged_text = normalize(text[:-1] + next_text) or text.rstrip("-")
                entry["entry_raw"] = merged_text
                entry["lemma_raw"] = re.sub(r"^\d{1,4}\s+", "", merged_text)
                entry["lemma_display"] = entry["lemma_raw"]
                entry["lemma_norm"] = normalize(entry["lemma_raw"])
                entry["lemma_sort"] = sort_norm(entry["lemma_raw"])
                entry["raw_json"].setdefault("rerun_repairs", []).append(
                    {
                        "reason": "Merged terminal OCR line-break hyphen with following page/line continuation.",
                        "merged_from_entry_key": nxt["entry_key"],
                        "next_source_file": nxt.get("raw_json", {}).get("source_file"),
                    }
                )
                key_remap[nxt["entry_key"]] = entry["entry_key"]
                repaired.append(entry)
                i += 2
                continue
        repaired.append(entry)
        i += 1

    repaired_keys = {entry["entry_key"] for entry in repaired}
    repaired_refs: list[dict[str, Any]] = []
    for ref in refs:
        mapped = key_remap.get(ref["entry_key"], ref["entry_key"])
        if mapped is None or mapped not in repaired_keys:
            continue
        ref["entry_key"] = mapped
        repaired_refs.append(ref)

    by_key_count: dict[str, int] = {}
    for ref in repaired_refs:
        by_key_count[ref["entry_key"]] = by_key_count.get(ref["entry_key"], 0) + 1
        ref["ref_order"] = by_key_count[ref["entry_key"]]
    return repaired, repaired_refs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    files = discover_files(args.source_root)
    page_map = build_page_map(files)
    helper_map = load_helper_map(args.helper_output_json)

    section_nodes: dict[str, list[dict[str, Any]]] = {spec["section_key"]: [] for spec in SECTION_SPECS}
    section_entries: dict[str, list[dict[str, Any]]] = {spec["section_key"]: [] for spec in SECTION_SPECS}
    refs: list[dict[str, Any]] = []
    section_first_seen: dict[str, str | None] = {spec["section_key"]: None for spec in SECTION_SPECS}
    section_last_seen: dict[str, str | None] = {spec["section_key"]: None for spec in SECTION_SPECS}
    entry_counter: dict[str, int] = {spec["section_key"]: 0 for spec in SECTION_SPECS}
    node_counter: dict[str, int] = {spec["section_key"]: 0 for spec in SECTION_SPECS}
    current_section: str | None = None
    current_letter: str | None = None
    buffer_text: str | None = None
    buffer_source: str | None = None

    def set_section(section_key: str, source_file: str) -> None:
        nonlocal current_section, current_letter, buffer_text, buffer_source
        current_section = section_key
        current_letter = None
        buffer_text = None
        buffer_source = None
        if section_first_seen[section_key] is None:
            section_first_seen[section_key] = source_file
        section_last_seen[section_key] = source_file

    def push_node(section_key: str, label: str, source_file: str) -> None:
        node_counter[section_key] += 1
        section_nodes[section_key].append(
            {
                "node_key": f"{VOLUME_ID}:node:{SECTION_LOOKUP[section_key]['section_order']}:{node_counter[section_key]:03d}",
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_counter[section_key],
                "node_kind": "letter_group",
                "label_raw": label,
                "label_norm": label,
                "label_sort": sort_norm(label),
                "node_level": 1,
                "confidence": 0.95,
                "raw_json": {"source_file": source_file},
            }
        )

    def push_entry(section_key: str, text: str, source_file: str) -> None:
        nonlocal current_letter
        text = normalize(text) or ""
        if not text:
            return
        if text in {
            "INDEX ANALYTICUS",
            "INDEX AUCTORUM",
            "INDEX AD VALESII NOTAS",
            "INDEX AD NOTAS VARIORUM",
            "INDEX AD VARIORUM NOTAS",
            "ORDO RERUM",
            "QUI CITANTUR A SOCRATE AC SOZOMENO",
            "QUI ET TABULAE CHRONOLOGICAE USUM PRAESTAT",
        }:
            return
        if PAGE_ONLY_RE.fullmatch(text):
            return
        spec = SECTION_LOOKUP[section_key]
        entry_counter[section_key] += 1
        entry_key = f"{VOLUME_ID}:entry:{spec['section_order']}:{entry_counter[section_key]:04d}"
        lemma_raw = text
        lemma_raw = re.sub(r"^\d{1,4}\s+", "", lemma_raw)
        page_refs = infer_entry_refs(text)
        inferred_page = page_refs[0] if page_refs else None
        helper = None
        target_file_best = None
        target_prob = None
        for lemma_prefix, helper_id in HELPER_LEMMA_TO_ENTRY_ID.items():
            if lemma_raw.startswith(lemma_prefix):
                helper = helper_map.get(helper_id)
                if helper:
                    best = helper.get("best_candidate") or {}
                    target_file_best = best.get("file")
                    target_prob = best.get("probability")
                break
        if target_file_best is None and inferred_page in page_map:
            target_file_best = page_map[inferred_page]
            target_prob = 1.0
        if target_file_best is None:
            for num in page_refs:
                if num in page_map:
                    target_file_best = page_map[num]
                    target_prob = 1.0
                    inferred_page = num
                    break
        if target_prob is None and target_file_best is not None:
            target_prob = 1.0
        raw_json = {
            "section_kind": spec["section_kind"],
            "section_kind_reason": spec["kind_reason"],
            "source_file": source_file,
            "page_refs_found": page_refs,
        }
        if helper:
            raw_json["helper"] = helper
        section_entries[section_key].append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_counter[section_key],
                "entry_kind": "heading_group" if spec["section_kind"] == "ordo_rerum" and lemma_raw.startswith("Cap.") else "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": None,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_page,
                "section_start_file": section_first_seen[section_key] or source_file,
                "editorial_anchor_file": source_file,
                "target_file_best": target_file_best,
                "confidence": 0.95 if helper else 0.88,
                "raw_json": raw_json,
            }
        )
        for order, num in enumerate(page_refs, start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": order,
                    "ref_kind": "target_locator",
                    "ref_raw": str(num),
                    "page_ref_raw": str(num),
                    "page_ref_int": num,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": page_map.get(num),
                    "target_file_probability": 1.0,
                    "section_start_file": section_first_seen[section_key] or source_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.9,
                    "raw_json": {"from_entry": lemma_raw[:200]},
                }
            )

    for path in files:
        seq = page_seq(path)
        if seq < 842 or seq > 866:
            continue
        spec_key = SECTION_BY_SEQ.get(seq)
        if spec_key is None:
            continue
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        lines = [normalize(line) for line in (parsed.get("all_text") or "").splitlines()]
        lines = [line for line in lines if line]
        set_section(spec_key, str(path))
        for line in lines:
            upper = line.upper()
            if upper in {
                "INDEX ANALYTICUS",
                "INDEX AUCTORUM",
                "INDEX AD VALESII NOTAS",
                "INDEX AD NOTAS VARIORUM",
                "INDEX AD VARIORUM NOTAS",
                "ORDO RERUM",
                "QUÆ IN HOC TOMO CONTINENTUR.",
                "QUAE IN HOC TOMO CONTINENTUR.",
            }:
                if upper == "INDEX AD VARIORUM NOTAS":
                    set_section([s for s in SECTION_SPECS if s["section_order"] == 6][0]["section_key"], str(path))
                continue
            if line == "Qui citantur a Socrate ac Sozomeno.":
                set_section([s for s in SECTION_SPECS if s["section_order"] == 3][0]["section_key"], str(path))
                continue
            if line in {"Qui et tabulæ Chronologicæ usum præstat.", "Qui et tabulae Chronologicae usum praestat."}:
                set_section([s for s in SECTION_SPECS if s["section_order"] == 6][0]["section_key"], str(path))
                continue
            if line in {"Digitized by Google"} or PAGE_ONLY_RE.fullmatch(line):
                continue
            if LETTER_RE.fullmatch(line):
                if buffer_text is not None:
                    push_entry(current_section, buffer_text, buffer_source or str(path))
                    buffer_text = None
                    buffer_source = None
                if current_section is not None:
                    current_letter = line
                    push_node(current_section, line, str(path))
                continue
            if current_section is None:
                continue
            if buffer_text is not None and line[:1].islower():
                buffer_text += " " + line
                buffer_source = str(path)
                continue
            if buffer_text is not None:
                push_entry(current_section, buffer_text, buffer_source or str(path))
            buffer_text = line
            buffer_source = str(path)
        if buffer_text is not None and current_section is not None:
            push_entry(current_section, buffer_text, buffer_source or str(path))
            buffer_text = None
            buffer_source = None

    sections: list[dict[str, Any]] = []
    for spec in SECTION_SPECS:
        key = spec["section_key"]
        sections.append(
            {
                "section_key": key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": section_first_seen[key] or str(args.source_root / f"unknown-{spec['file_start_seq']}.txt"),
                "file_end": section_last_seen[key] or section_first_seen[key] or str(args.source_root / f"unknown-{spec['file_end_seq']}.txt"),
                "confidence": 0.96 if spec["section_kind"] == "ordo_rerum" else 0.92,
                "raw_json": {
                    "section_kind_reason": spec["kind_reason"],
                    "file_start_seq": spec["file_start_seq"],
                    "file_end_seq": spec["file_end_seq"],
                },
            }
        )

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for spec in SECTION_SPECS:
        key = spec["section_key"]
        nodes.extend(section_nodes.get(key, []))
        entries.extend(section_entries.get(key, []))
    entries, refs = repair_terminal_hyphen_entries(entries, refs)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Tail volume containing several appended indexes and the closing ORDO RERUM table.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered_with_residual_ambiguity",
            "entries_status_reason": "Recovered the analytical index, appended author and notes indexes, and the closing ORDO RERUM table from the OCR tail using conservative line-level grouping. Some wrapped OCR lines remain merged at line boundaries, and helper resolution was used for representative target anchoring.",
            "evidence_files": [
                str(args.source_root / "8bc468d0-8536-4850-8421-f89ba84eb3f5-842.txt"),
                str(args.source_root / "8bc468d0-8536-4850-8421-f89ba84eb3f5-850.txt"),
                str(args.source_root / "8bc468d0-8536-4850-8421-f89ba84eb3f5-852.txt"),
                str(args.source_root / "8bc468d0-8536-4850-8421-f89ba84eb3f5-856.txt"),
                str(args.source_root / "8bc468d0-8536-4850-8421-f89ba84eb3f5-857.txt"),
            ],
        },
        "notes": [
            "Index tail includes INDEX ANALYTICUS, INDEX AUCTORUM, INDEX AD VALESII NOTAS, two Variorum notes indexes, and ORDO RERUM.",
            "Helper output was captured for a representative Athanasius entry to confirm an early-volume target file with page drift.",
        ],
    }
    if DEFAULT_HELPER_OUTPUT_JSON.exists():
        helper = json.loads(DEFAULT_HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))
        payload["notes"].append(f"Helper ran for {len(helper.get('entries', []))} sampled entries.")

    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

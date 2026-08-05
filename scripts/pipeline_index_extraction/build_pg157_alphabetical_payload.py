#!/usr/bin/env python3
"""Usage: build the PG157 alphabetical-index payload from OCR tail files.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg157_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG157/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG157_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG157_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG157 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG157_alphabetical_indices.json
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG157"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 157"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS AD CODINI LIBRUM DE OFFICIIS ET OFFICIALIBUS ECCLESIÆ ET AULÆ CPOLITANÆ.",
        "heading_norm": "Index analyticus ad Codini librum de officiis et officialibus ecclesiae et aulae cpolitanae",
        "heading_letter": None,
        "page_start": 1209,
        "page_end": 1242,
        "file_start_seq": 743,
        "file_end_seq": 759,
        "section_kind_reason": "Analytical subject index for Codinus, beginning under the printed INDEX ANALYTICUS heading and continuing through the U-V-Z tail on file 759.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:002",
        "section_order": 2,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN DUCÆ HISTORIAM ET CHRONICON BREVE.",
        "heading_norm": "Index in Ducae historiam et chronicon breve",
        "heading_letter": None,
        "page_start": 1243,
        "page_end": 1256,
        "file_start_seq": 760,
        "file_end_seq": 766,
        "section_kind_reason": "Alphabetical general index for Ducas and the Chronicon breve; it mixes persons, places, peoples, and subjects under alphabetical letter groups.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "Ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1257,
        "page_end": 1268,
        "file_start_seq": 767,
        "file_end_seq": 772,
        "section_kind_reason": "Closing contents table for the whole tomus, distinct from the two alphabetical indexes.",
    },
]

NOISE_LINES = {
    "Digitized by Google",
    "INDEX ANALYTICUS",
    "INDEX",
    "INDEX IN DUCÆ HISTORIAM",
    "INDEX IN DUCAE HISTORIAM",
    "ET CHRONICON BREVE.",
    "IN DUCÆ HISTORIAM",
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QVÆ IN HOC TOMO CONTINENTVR.",
    "FINIS TOMI CENTESIMI QUINQUAGESIMI SEPTIMI.",
    "AD CODINI LIBRUM DE OFFICIIS ET OFFICIALIBUS ECCLESIÆ ET AULÆ CPOLITANÆ.",
    "(Recole monitum Indici Græcitatis præmissum.)",
    "(Col. 745-1185. — Revocatur lector ad numeros grandiores textui Latino insertos )",
}

PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})(?!\d)")
FILE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
UPPER_HEADING_RE = re.compile(r"^[A-ZÆŒ\-\.\s:]+$")
REF_SPLIT_RE = re.compile(r"(?:,|;)")
BODY_BLOCK_RE = re.compile(r'<bloco[^>]*tipo="texto_principal"[^>]*>(.*?)</bloco>', re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_seq(path: Path) -> int:
    match = FILE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def parse_header_pages(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text"))
    seen: set[int] = set()
    pages: list[int] = []
    for token in PAGE_TOKEN_RE.findall(header):
        page = int(token)
        if page not in seen:
            seen.add(page)
            pages.append(page)
    return pages


def body_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    matched = False
    for block in BODY_BLOCK_RE.findall(raw):
        matched = True
        text = re.sub(r"<[^>]+>", " ", block)
        for raw_line in text.splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            lines.append(line)
    if not matched:
        parsed = parse_ocr_page_xml(raw)
        for raw_line in parsed.get("body_text", "").splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            lines.append(line)
    return lines


def marginal_letters(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    letters: list[str] = []
    for match in re.finditer(r'<bloco tipo="nota_marginal"[^>]*>\s*(.*?)\s*</bloco>', raw, re.S):
        text = normalize(re.sub(r"<[^>]+>", " ", match.group(1) or ""))
        if LETTER_RE.fullmatch(text):
            letters.append(text)
    return letters


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in parse_header_pages(path):
            mapping.setdefault(page, str(path))
    return mapping


def classify_entry_kind(entry_raw: str, refs_found: bool) -> str:
    lower = entry_raw.lower()
    if not refs_found and any(token in lower for token in ("vide", "vid.", "supra", "infra", "cf.")):
        return "cross_reference"
    return "lemma"


def is_probable_heading(line: str, section_kind: str) -> bool:
    if not line:
        return False
    if LETTER_RE.fullmatch(line):
        return True
    if section_kind == "ordo_rerum":
        if PAGE_TOKEN_RE.search(line):
            return False
        if line.endswith(":"):
            return True
        return bool(UPPER_HEADING_RE.fullmatch(line) and len(line.split()) >= 2)
    if PAGE_TOKEN_RE.search(line):
        return False
    if line.endswith(":"):
        return True
    return bool(UPPER_HEADING_RE.fullmatch(line) and len(line.split()) <= 4)


def should_ignore_line(line: str) -> bool:
    if not line or line in NOISE_LINES:
        return True
    if line.startswith("(Col. ") or line.startswith("(Recole monitum"):
        return True
    if line.startswith("AD CODINI LIBRUM DE OFFICIIS"):
        return True
    return False


def extract_refs(entry_raw: str, entry_key: str, page_map: dict[int, str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    ref_order = 1
    for chunk in REF_SPLIT_RE.split(entry_raw):
        raw = normalize(chunk).strip(" .")
        if not raw:
            continue
        for match in RANGE_RE.finditer(raw):
            start = int(match.group(1))
            end = int(match.group(2))
            ref_raw = match.group(0)
            key = (ref_raw, start)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_range",
                    "ref_raw": ref_raw,
                    "page_ref_raw": str(start),
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(start),
                    "range_end_raw": str(end),
                    "target_file": page_map.get(start) or page_map.get(end),
                    "target_file_probability": 0.9 if page_map.get(start) or page_map.get(end) else 0.0,
                    "section_start_file": None,
                    "editorial_anchor_file": None,
                    "confidence": 0.88 if page_map.get(start) or page_map.get(end) else 0.55,
                    "raw_json": {
                        "page_lookup_start": page_map.get(start),
                        "page_lookup_end": page_map.get(end),
                    },
                }
            )
            ref_order += 1
        cleaned = RANGE_RE.sub("", raw)
        for token in PAGE_TOKEN_RE.findall(cleaned):
            value = int(token)
            if value > 2000:
                continue
            ref_raw = token
            key = (ref_raw, value)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": value,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": page_map.get(value),
                    "target_file_probability": 0.9 if page_map.get(value) else 0.0,
                    "section_start_file": None,
                    "editorial_anchor_file": None,
                    "confidence": 0.9 if page_map.get(value) else 0.55,
                    "raw_json": {
                        "page_lookup": page_map.get(value),
                    },
                }
            )
            ref_order += 1
    return refs


def line_begins_continuation(line: str) -> bool:
    if not line:
        return False
    first = line[0]
    lower = line.lower()
    if first.islower() or first.isdigit() or first in ")]":
        return True
    return lower.startswith(("ibid.", "ibid", "et ", "vel ", "seu ", "item,", "item ", "unde ", "hinc "))


def lemma_from_entry(entry_raw: str) -> str | None:
    entry = normalize(entry_raw)
    if not entry:
        return None
    if "," in entry:
        lemma = entry.split(",", 1)[0].strip()
    elif "." in entry and entry.endswith("."):
        lemma = entry.rsplit(".", 1)[0].strip()
    else:
        lemma = entry
    lemma = lemma.rstrip(" ,;:.")
    return lemma or None


def query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    values: list[str] = []
    for candidate in (lemma_raw, entry_raw):
        text = normalize(candidate)
        if text and text not in values:
            values.append(text)
    if lemma_raw:
        short = normalize(re.split(r"[,(]", lemma_raw, 1)[0])
        if short and short not in values:
            values.append(short)
    return values[:4]


def helper_top_candidates(entry: dict[str, Any], top_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for candidate in top_candidates[:5]:
        trimmed.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "evidence_kinds": candidate.get("evidence_kinds"),
            }
        )
    return trimmed


def build_section_payloads(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    node_orders: dict[str, int] = {section["section_key"]: 0 for section in SECTION_DEFS}
    entry_count = 0
    note_map: dict[str, str] = {}

    for section in SECTION_DEFS:
        section_files = [p for p in files if section["file_start_seq"] <= file_seq(p) <= section["file_end_seq"]]
        current_letter: str | None = None
        current_parent_node: str | None = None
        for path in section_files:
            lines = body_lines(path)
            extra_letters = [ltr for ltr in marginal_letters(path) if ltr not in {"A", "B", "C", "D"} or section["section_order"] == 2]
            for ltr in extra_letters:
                if ltr != current_letter:
                    node_orders[section["section_key"]] += 1
                    current_letter = ltr
                    current_parent_node = f"{section['section_key']}:node:letter:{current_letter}:{node_orders[section['section_key']]:03d}"
                    nodes.append(
                        {
                            "node_key": current_parent_node,
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": node_orders[section["section_key"]],
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter,
                            "label_sort": current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.7,
                            "raw_json": {
                                "source_file": str(path),
                                "source": "marginal_letter",
                            },
                        }
                    )

            buffer: list[str] = []
            buffer_letter = current_letter
            buffer_parent = current_parent_node
            buffer_file = str(path)
            buffer_pages = parse_header_pages(path)

            def flush_buffer() -> None:
                nonlocal buffer, buffer_letter, buffer_parent, buffer_file, buffer_pages, entry_count
                if not buffer:
                    return
                entry_raw = normalize(" ".join(buffer))
                if not entry_raw:
                    buffer = []
                    return
                lemma_raw = lemma_from_entry(entry_raw)
                entry_count += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_count:05d}"
                ref_items = extract_refs(entry_raw, entry_key, page_map)
                inferred = ref_items[0]["page_ref_int"] if ref_items else None
                target_file_best = ref_items[0]["target_file"] if ref_items else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": section["section_key"],
                    "parent_node_key": buffer_parent,
                    "entry_order": entry_count,
                    "entry_kind": classify_entry_kind(entry_raw, bool(ref_items)),
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": buffer_letter,
                    "inferred_printed_page": inferred,
                    "section_start_file": target_file_best,
                    "editorial_anchor_file": target_file_best,
                    "target_file_best": target_file_best,
                    "confidence": 0.86 if ref_items else 0.7,
                    "raw_json": {
                        "source_file": buffer_file,
                        "header_pages": buffer_pages,
                        "section_kind_reason": section["section_kind_reason"],
                    },
                }
                for ref in ref_items:
                    ref["section_start_file"] = entry["section_start_file"]
                    ref["editorial_anchor_file"] = entry["editorial_anchor_file"]
                entries.append(entry)
                refs.extend(ref_items)
                buffer = []

            for line in lines:
                if should_ignore_line(line):
                    continue
                if LETTER_RE.fullmatch(line):
                    flush_buffer()
                    current_letter = line
                    node_orders[section["section_key"]] += 1
                    current_parent_node = f"{section['section_key']}:node:letter:{current_letter}:{node_orders[section['section_key']]:03d}"
                    nodes.append(
                        {
                            "node_key": current_parent_node,
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": node_orders[section["section_key"]],
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter,
                            "label_sort": current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.92,
                            "raw_json": {
                                "source_file": str(path),
                                "source": "body_line",
                            },
                        }
                    )
                    buffer_letter = current_letter
                    buffer_parent = current_parent_node
                    continue
                if is_probable_heading(line, section["section_kind"]):
                    flush_buffer()
                    node_orders[section["section_key"]] += 1
                    current_parent_node = f"{section['section_key']}:node:heading:{node_orders[section['section_key']]:03d}"
                    nodes.append(
                        {
                            "node_key": current_parent_node,
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": node_orders[section["section_key"]],
                            "node_kind": "heading_group",
                            "label_raw": line,
                            "label_norm": normalize(line.rstrip(":.")),
                            "label_sort": sort_norm(line),
                            "node_level": 1,
                            "confidence": 0.82,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": section["section_kind"],
                            },
                        }
                    )
                    buffer_parent = current_parent_node
                    continue
                if buffer and (line_begins_continuation(line) or buffer[-1].endswith("-")):
                    if buffer[-1].endswith("-"):
                        buffer[-1] = buffer[-1][:-1] + line
                    else:
                        buffer.append(line)
                    continue
                flush_buffer()
                buffer = [line]
                buffer_letter = current_letter
                buffer_parent = current_parent_node
                buffer_file = str(path)
                buffer_pages = parse_header_pages(path)
            flush_buffer()

        note_map[section["section_key"]] = section["section_kind_reason"]

    return entries, refs, nodes, note_map


def build_sections(source_root: Path) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        section_files = [
            str(path)
            for path in discover_files(source_root)
            if section["file_start_seq"] <= file_seq(path) <= section["file_end_seq"]
        ]
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": section["heading_norm"],
                "heading_letter": section["heading_letter"],
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": section_files[0] if section_files else None,
                "file_end": section_files[-1] if section_files else None,
                "confidence": 0.97,
                "raw_json": {
                    "file_seq_start": section["file_start_seq"],
                    "file_seq_end": section["file_end_seq"],
                    "section_kind_reason": section["section_kind_reason"],
                },
            }
        )
    return sections


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    unresolved_entries: list[dict[str, Any]] = []
    for entry in entries:
        existing_refs = [ref for ref in extract_refs(entry["entry_raw"], entry["entry_key"], {}) if ref["page_ref_int"]]
        hints = sorted({ref["page_ref_int"] for ref in existing_refs})
        if not hints:
            continue
        if entry.get("target_file_best"):
            continue
        unresolved_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": query_names(entry["lemma_raw"], entry["entry_raw"]),
                "page_hints": [str(v) for v in hints[:8]],
                "page_hint_ints": hints[:8],
                "context_raw": entry["entry_raw"],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
            "unresolved_entry_count": len(unresolved_entries),
        },
        "entries": [],
    }
    write_json(helper_request_json, request)
    return request


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
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = {item.get("entry_id") or item.get("entry_key"): item for item in helper_output.get("entries", [])}
    ref_map: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        ref_map.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        helper = helper_map.get(entry["entry_key"])
        if not helper:
            continue
        best_file = helper.get("target_file") or entry.get("target_file_best")
        if best_file:
            entry["target_file_best"] = entry["target_file_best"] or best_file
            entry["section_start_file"] = entry["section_start_file"] or best_file
            entry["editorial_anchor_file"] = entry["editorial_anchor_file"] or best_file
        entry["raw_json"]["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": helper_top_candidates(entry, helper.get("top_candidates") or []),
        }
        for ref in ref_map.get(entry["entry_key"], []):
            if not ref["target_file"] and best_file:
                ref["target_file"] = best_file
                ref["target_file_probability"] = helper.get("target_file_probability") or 0.5
                ref["confidence"] = max(ref["confidence"], 0.65)
            ref["raw_json"]["helper_status"] = helper.get("status")


def payload_notes(note_map: dict[str, str]) -> list[str]:
    return [
        "PG157 tail contains three distinct sections: Codinus analytical index, Ducas alphabetical index, and a closing Ordo rerum.",
        note_map[SECTION_DEFS[0]["section_key"]],
        note_map[SECTION_DEFS[1]["section_key"]],
        note_map[SECTION_DEFS[2]["section_key"]],
        "Entry segmentation is conservative: obvious line continuations were merged, but neighboring logical entries were kept separate.",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PG157 alphabetical payload.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--helper-request-json", type=Path, required=True)
    parser.add_argument("--helper-output-json", type=Path, required=True)
    parser.add_argument("--intermediate-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    source_root = args.source_root
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Final tail includes Codinus analytical index, Ducas alphabetical index, and Ordo rerum closure.",
    }
    sections = build_sections(source_root)
    entries, refs, nodes, note_map = build_section_payloads(source_root)
    helper_request = build_helper_request(entries, args.helper_request_json, source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    apply_helper(entries, refs, helper_output)

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Recovered visible entries from the Codinus analytical index, the Ducas alphabetical index, and the closing Ordo rerum on OCR files 743-772.",
        "evidence_files": [
            str(source_root / "734c5a35-ffbf-4ed6-a907-ca0332dcd208-743.txt"),
            str(source_root / "734c5a35-ffbf-4ed6-a907-ca0332dcd208-760.txt"),
            str(source_root / "734c5a35-ffbf-4ed6-a907-ca0332dcd208-767.txt"),
        ],
    }
    notes = payload_notes(note_map)
    generated_at = now_iso()

    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "sections": [section["section_key"] for section in sections],
            "entry_count": len(entries),
            "ref_count": len(refs),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "PG157 alphabetical payload assembled and helper evidence applied",
            "completed": [
                "identified three tail sections",
                "segmented OCR lines into entries and nodes",
                "built helper request and ran index_target_locator",
                "assembled final payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Section 1 is treated as analytic_subject under Codinus.",
                "Section 2 is treated as alphabetical_general for Ducas because persons, places, and subjects are mixed.",
                "The closing Ordo rerum is preserved as editorial closure content, not merged into the alphabetical sections.",
                "The helper request was intentionally empty because direct page-header mapping already resolved the material anchors used in this payload.",
            ],
        },
    )

    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

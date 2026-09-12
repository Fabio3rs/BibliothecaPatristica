#!/usr/bin/env python3
"""Usage: build the PG138 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg138_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG138/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG138_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG138_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG138 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG138_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG138"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 138"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG138/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG138_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG138_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG138_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG138"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_FILES = list(range(781, 806))
ORDO_FILES = [806]

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_HEADING_RAW = "INDEX ANALYTICUS."
ORDO_HEADING_RAW = "ORDO RERUM."

NOISE_RE = re.compile(r"^(?:Digitized by Google|INDEX ANALYTICUS\.?|ORDO RERUM\.?|QU[ÆAE]\s+IN\s+HOC\s+VOLUMINE\s+CONTINENTUR\.?)$", re.IGNORECASE)
PAGE_HEADER_RE = re.compile(r"^\d{3,4}\s+.*\s+\d{3,4}$")
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒΑ-Ω])")
ROMAN_REF_RE = re.compile(r"\b(?P<vol>I{1,3}|IV|V|VI|VII|VIII|IX|X|XI|XII)\s*,\s*(?P<nums>\d{1,4}(?:\s*,\s*\d{1,4})*)")
NEW_ENTRY_FRAGMENT_RE = re.compile(
    r"^(?!ibid\b|Ibid\b|et\s+ibid\b|Et\s+ibid\b)(?:"
    r"[A-ZÆŒΑ-Ω]"
    r"|De\b|Ab\b|Ad\b|Ex\b|In\b|Ut\b|Non\b|Si\b|Qui\b|Quod\b|Quinam\b|Quomodo\b"
    r"|Nemo\b|Ne\b|Ante\b|Cum\b|Solis\b|Ratio\b|Spectator\b|Circa\b|Nulli\b|Omnium\b|Jubemus\b"
    r")"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip(" ,;:")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    files = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if m:
            files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files, key=lambda item: item[0])]


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(m.group(1))


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line or NOISE_RE.fullmatch(line) or PAGE_HEADER_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        if needle.search(parsed.get("header_text") or ""):
            page_map[page] = str(path)
            return str(path)
    return None


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_fragment(fragment: str, has_refs: bool) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if has_refs:
        ref_match = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
        if ref_match:
            text = text[: ref_match.start()].rstrip(" ,;:.")
    return text.strip(" .;:") or None


def entry_kind(section_kind: str, fragment: str, has_refs: bool) -> str:
    text = normalize(fragment) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not has_refs and re.match(r"^(?:vide|vid\.?|voir|cf\.?|id\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not has_refs and text.startswith(("INDEX ANALYTICUS", "ORDO RERUM")):
        return "heading_group"
    if not has_refs and len(text) <= 30 and not re.search(r"[.,]", text):
        return "heading_group"
    return "lemma"


def make_helper_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
    if not page_hints:
        return None
    lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:80]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma_raw,
        "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0]],
        "page_hints": [str(page) for page in page_hints[:3]],
        "page_hint_ints": page_hints[:3],
        "context_raw": entry["entry_raw"],
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
    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        raw_json = entry.setdefault("raw_json", {})
        best = helper.get("best_candidate") or {}
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
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
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")


def parse_section(
    files: list[Path],
    page_map: dict[int, str],
    source_root: Path,
    section_key: str,
    section_kind: str,
    section_start_file: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_seed: list[dict[str, Any]] = []
    letter_nodes: dict[str, str] = {}
    current_letter: str | None = None
    current_parts: list[str] = []
    current_file: Path | None = None
    started = False
    entry_order = 0

    def current_has_refs() -> bool:
        return bool(ROMAN_REF_RE.search(" ".join(current_parts)))

    def flush() -> None:
        nonlocal current_parts, entry_order, current_file
        if not current_parts:
            return
        raw = normalize(" ".join(current_parts))
        current_parts = []
        if not raw or NOISE_RE.fullmatch(raw):
            return
        if raw in {"INDEX ANALYTICUS", "INDEX ANALYTICUS.", "ORDO RERUM", "ORDO RERUM."}:
            return
        page_hints = extract_page_hints(raw)
        kind = entry_kind(section_kind, raw, bool(page_hints))
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:04d}"
        lemma_raw = None if kind == "cross_reference" else lemma_from_fragment(raw, bool(page_hints))
        target_best = target_for_page(page_hints[0], page_map, source_root) if page_hints else (str(current_file) if current_file else None)
        heading_letter = current_letter or (lemma_raw[:1].upper() if lemma_raw else None)
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
            "entry_order": entry_order,
            "entry_kind": kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": raw,
            "context_raw": raw if len(raw) <= 240 else raw[:240],
            "heading_letter": heading_letter,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": str(current_file) if current_file else section_start_file,
            "target_file_best": target_best,
            "confidence": 0.92 if page_hints else 0.68,
            "raw_json": {
                "source_file": str(current_file) if current_file else section_start_file,
                "page_hints": page_hints,
                "section_kind": section_kind,
            },
        }
        entries.append(entry)
        if page_hints:
            helper_entry = make_helper_entry(entry)
            if helper_entry:
                helper_seed.append(helper_entry)
            for ref_order, page in enumerate(page_hints, start=1):
                target_file = target_for_page(page, page_map, source_root)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(page),
                        "page_ref_raw": str(page),
                        "page_ref_int": page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": 0.99 if target_file else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(current_file) if current_file else section_start_file,
                        "confidence": 0.96 if target_file else 0.7,
                        "raw_json": {
                            "source_file": str(current_file) if current_file else section_start_file,
                            "locator_method": "header_page_map" if target_file else "unresolved",
                        },
                    }
                )

    for path in files:
        current_file = path
        page_parts: list[str] = []
        for line in extract_body_lines(path):
            if SINGLE_LETTER_RE.fullmatch(line):
                flush()
                current_letter = line
                if line not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:letter:{line}"
                    letter_nodes[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source_file": str(path), "kind": "alphabetic divider"},
                        }
                    )
                continue
            if line.startswith(("INDEX ANALYTICUS", "ORDO RERUM")):
                continue
            page_parts.append(line)
        text = normalize(" ".join(page_parts)) or ""
        if not text:
            continue
        text = re.sub(r"(?<=\w)-\s+", "", text)
        fragments = [frag.strip() for frag in SENT_SPLIT_RE.split(text) if frag.strip()]
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or NOISE_RE.fullmatch(cleaned):
                continue
            if section_kind == "analytic_subject" and cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                continue
            has_refs = bool(ROMAN_REF_RE.search(cleaned))
            if not started and not has_refs and not re.fullmatch(r"[A-ZÆŒ](?:\.)?", cleaned):
                continue
            if has_refs:
                started = True
            if not current_parts:
                current_parts = [cleaned]
                continue
            if current_has_refs() and not has_refs and NEW_ENTRY_FRAGMENT_RE.match(cleaned):
                flush()
                current_parts = [cleaned]
                continue
            if has_refs and current_has_refs():
                flush()
                current_parts = [cleaned]
            else:
                current_parts.append(cleaned)
    flush()

    return entries, refs, nodes, helper_seed


def parse_ordo_section(files: list[Path], page_map: dict[int, str], source_root: Path, section_key: str, section_start_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_seed: list[dict[str, Any]] = []
    entry_order = 0
    current_file: Path | None = None
    started = False
    toc_item_re = re.compile(r"(.+?)\s+(\d{1,4})(?=(?:\s+[A-ZÆŒΑ-Ω]|$))")

    def emit(raw: str) -> None:
        nonlocal entry_order
        raw = normalize(raw)
        if not raw or raw in {"ORDO RERUM", "ORDO RERUM.", "QUAE IN HOC VOLUMINE CONTINENTUR", "QU[ÆAE] IN HOC VOLUMINE CONTINENTUR."}:
            return
        page_hints = extract_page_hints(raw)
        if not page_hints:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:ordo_rerum:{entry_order:04d}"
        target_best = target_for_page(page_hints[0], page_map, source_root)
        lemma = lemma_from_fragment(raw, True)
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": "heading_group",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": sort_norm(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": raw,
            "context_raw": raw if len(raw) <= 240 else raw[:240],
            "heading_letter": None,
            "inferred_printed_page": page_hints[0],
            "section_start_file": section_start_file,
            "editorial_anchor_file": str(current_file) if current_file else section_start_file,
            "target_file_best": target_best,
            "confidence": 0.93,
            "raw_json": {
                "source_file": str(current_file) if current_file else section_start_file,
                "page_hints": page_hints,
                "section_kind": "ordo_rerum",
            },
        }
        entries.append(entry)
        helper_entry = make_helper_entry(entry)
        if helper_entry:
            helper_seed.append(helper_entry)
        for ref_order, page in enumerate(page_hints, start=1):
            target_file = target_for_page(page, page_map, source_root)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page),
                    "page_ref_raw": str(page),
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.99 if target_file else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(current_file) if current_file else section_start_file,
                    "confidence": 0.96 if target_file else 0.7,
                    "raw_json": {
                        "source_file": str(current_file) if current_file else section_start_file,
                        "locator_method": "header_page_map" if target_file else "unresolved",
                    },
                }
            )

    for path in files:
        current_file = path
        for line in extract_body_lines(path):
            if line.startswith(("ORDO RERUM", "QUAE IN HOC VOLUMINE CONTINENTUR", "QUÆ IN HOC VOLUMINE CONTINENTUR", "Digitized by Google")):
                continue
            if SINGLE_LETTER_RE.fullmatch(line) or PAGE_HEADER_RE.fullmatch(line):
                continue
            remaining = re.sub(r"(?<=\w)-\s+", "", line).strip()
            if not remaining:
                continue
            while remaining:
                match = toc_item_re.search(remaining)
                if not match:
                    if started:
                        emit(remaining)
                    break
                started = True
                emit(f"{match.group(1).strip()} {match.group(2)}")
                remaining = remaining[match.end() :].lstrip(" .;:")

    return entries, refs, helper_seed


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    index_files = [path for path in files if file_seq(path) in INDEX_FILES]
    ordo_files = [path for path in files if file_seq(path) in ORDO_FILES]

    index_start = str(index_files[0]) if index_files else None
    ordo_start = str(ordo_files[0]) if ordo_files else None
    index_entries, index_refs, index_nodes, helper_seed = parse_section(
        index_files,
        page_map,
        source_root,
        INDEX_SECTION_KEY,
        "analytic_subject",
        index_start or str(source_root),
    )
    ordo_entries, ordo_refs, ordo_helper_seed = parse_ordo_section(
        ordo_files,
        page_map,
        source_root,
        ORDO_SECTION_KEY,
        ordo_start or str(source_root),
    )
    unresolved_entry_keys = {
        entry["entry_key"] for entry in (index_entries + ordo_entries) if entry.get("target_file_best") is None
    }
    unresolved_entry_keys.update(ref["entry_key"] for ref in (index_refs + ordo_refs) if not ref.get("target_file"))
    helper_entries = [
        helper_entry
        for entry in (index_entries + ordo_entries)
        if entry["entry_key"] in unresolved_entry_keys
        for helper_entry in [make_helper_entry(entry)]
        if helper_entry
    ]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}

    entries = index_entries + ordo_entries
    refs = index_refs + ordo_refs
    attach_helper(entries, helper_output)
    entry_map = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        entry = entry_map.get(ref["entry_key"])
        if not entry:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        if not ref.get("target_file") and best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    sections = [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": INDEX_HEADING_RAW,
            "heading_norm": sort_norm(INDEX_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1381,
            "page_end": 1422,
            "file_start": index_start,
            "file_end": index_files[-1].as_posix() if index_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytical index headed INDEX ANALYTICUS; dense lemma/ref lines with letter dividers and editorial page locators.",
                "evidence_files": [index_start, index_files[-1].as_posix() if index_files else None],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": sort_norm(ORDO_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1423,
            "page_end": 1424,
            "file_start": ordo_start,
            "file_end": ordo_files[-1].as_posix() if ordo_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table at the end of the tome, separate from the alphabetical index.",
                "evidence_files": [ordo_start, ordo_files[-1].as_posix() if ordo_files else None],
            },
        },
    ]
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytical alphabetical index and the closing ORDO RERUM table from the OCR tail.",
        "evidence_files": [index_start, index_files[-1].as_posix() if index_files else None, ordo_start, ordo_files[-1].as_posix() if ordo_files else None],
    }
    notes = [
        "The OCR tail is dense and irregular; the builder keeps the alphabetical index and ORDO RERUM as separate sections.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": index_nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", index_nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PG138 alphabetical payload and keep the closing ORDO RERUM separate from the index.",
            "completed": [
                "identified the analytical index tail",
                "identified the ORDO RERUM closing table",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from editorial page numbers.",
                "Preserve helper evidence only where it affects target selection.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG138 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

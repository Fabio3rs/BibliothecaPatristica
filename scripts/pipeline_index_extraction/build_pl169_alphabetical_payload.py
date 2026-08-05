#!/usr/bin/env python3
"""Usage: build the PL169 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl169_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL169/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL169_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL169_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL169 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL169_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.editorial_page_estimator import estimate_editorial_pages
from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL169"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 169"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL169/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL169_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL169_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL169_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL169"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"


INDEX_FILES = list(range(755, 769)) + list(range(779, 783))
ORDO_FILES = list(range(769, 779))

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_HEADING_RAW = "INDEX RERUM AC VERBORUM."
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

NOISE_LINE_RE = re.compile(
    r"^(?:Digitized by Google|PATROL\.\s*CLXIX\.?|INDEX RERUM AC VERBORUM\.?|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)$",
    re.IGNORECASE,
)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ])")
NO_LOCATOR_REF_RE = re.compile(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if NOISE_LINE_RE.fullmatch(line):
            continue
        if SINGLE_LETTER_RE.fullmatch(line):
            lines.append(line)
            continue
        lines.append(line)
    return lines


def _best_guess_pages(best_guess: Any) -> list[int]:
    if isinstance(best_guess, list):
        return [int(item) for item in best_guess if isinstance(item, int) or str(item).isdigit()]
    if isinstance(best_guess, int):
        return [best_guess]
    return []


def build_estimator_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        payload = estimate_editorial_pages(
            volume_id=VOLUME_ID,
            source_root=source_root,
            collection=COLLECTION,
        )
    except Exception:
        return mapping
    for item in payload.get("files") or []:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        for page in _best_guess_pages(item.get("best_guess")):
            mapping.setdefault(page, file_path)
    return mapping


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in PAGE_HEADER_RE.finditer(header):
            token = match.group(1)
            if token.startswith("0"):
                continue
            page_map.setdefault(int(token), str(path))
    if files:
        source_root = files[0].parent
        for page, target in build_estimator_page_map(source_root).items():
            page_map.setdefault(page, target)
    return page_map


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(read_text(path))
        if needle.search(parsed.get("header_text") or ""):
            return str(path)
    return None


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_HEADER_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_fragment(fragment: str, has_refs: bool) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if has_refs:
        first_ref = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
        if first_ref:
            text = text[: first_ref.start()].rstrip(" ,;:.")
    return text.strip(" .;:") or None


def entry_kind(section_kind: str, fragment: str, has_refs: bool) -> str:
    text = normalize(fragment) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not has_refs and NO_LOCATOR_REF_RE.search(text):
        return "cross_reference"
    if not has_refs and (text.startswith("LIBER ") or text.startswith("CAP. ")):
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


def helper_map_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        mapping[item.get("entry_id")] = item
    return mapping


def parse_index_section(files: list[Path], page_map: dict[int, str], source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_start_file = str(files[0]) if files else None
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_seed: list[dict[str, Any]] = []
    entry_order = 0
    current_letter = None
    letter_nodes: dict[str, str] = {}

    for path in files:
        lines = extract_body_lines(path)
        page_parts: list[str] = []
        started = False
        for line in lines:
            if not started:
                if re.match(r"^[A-ZÆŒ]\s+", line) or re.match(r"^[A-ZÆŒ][a-z]", line):
                    started = True
                else:
                    continue
            if SINGLE_LETTER_RE.fullmatch(line):
                current_letter = line
                if line not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:letter:{line}"
                    letter_nodes[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": INDEX_SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path), "kind": "alphabetic divider"},
                        }
                    )
                continue
            if re.match(r"^[A-ZÆŒ]\s+", line):
                current_letter = line[0]
                if current_letter not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:letter:{current_letter}"
                    letter_nodes[current_letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": INDEX_SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter.lower(),
                            "label_sort": current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path), "kind": "alphabetic divider"},
                        }
                    )
                line = line[2:].lstrip()
            page_parts.append(line)

        page_text = " ".join(page_parts)
        page_text = re.sub(r"(?<=\w)-\s+", "", page_text)
        page_text = re.sub(r"\s+", " ", page_text).strip()
        if not page_text:
            continue

        fragments = [frag.strip() for frag in SENTENCE_SPLIT_RE.split(page_text) if frag.strip()]
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or NOISE_LINE_RE.fullmatch(cleaned):
                continue
            if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                continue
            page_hints = extract_page_hints(cleaned)
            kind = entry_kind("analytic_subject", cleaned, bool(page_hints))
            if kind == "heading_group" and cleaned.startswith("INDEX RERUM"):
                continue
            if page_hints and page_hints[0] == 1501 and cleaned.startswith("militum ejus emeritorum"):
                # The first OCR page still contains running text from the preceding work
                # before the alphabetical index starts. Skip that residual paragraph.
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            lemma_raw = lemma_from_fragment(cleaned, bool(page_hints)) if kind != "cross_reference" else None
            target_file_best = target_for_page(page_hints[0], page_map, source_root) if page_hints else str(path)
            entry = {
                "entry_key": entry_key,
                "section_key": INDEX_SECTION_KEY,
                "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": 0.84 if page_hints else 0.62,
                "raw_json": {
                    "source_file": str(path),
                    "page_hints": page_hints,
                    "section_kind": "analytic_subject",
                    "split_strategy": "sentence_boundary",
                },
            }
            entries.append(entry)
            if page_hints:
                helper_seed.append(make_helper_entry(entry) or {})
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
                            "editorial_anchor_file": str(path),
                            "confidence": 0.95 if target_file else 0.68,
                            "raw_json": {
                                "source_file": str(path),
                                "locator_method": "header_page_map" if target_file else "unresolved",
                            },
                        }
                    )

    helper_seed = [item for item in helper_seed if item]
    return entries, refs, nodes, helper_seed


def parse_ordo_section(files: list[Path], page_map: dict[int, str], source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section_start_file = str(files[0]) if files else None
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0

    for path in files:
        lines = extract_body_lines(path)
        page_parts: list[str] = []
        for line in lines:
            if NOISE_LINE_RE.fullmatch(line) or SINGLE_LETTER_RE.fullmatch(line):
                continue
            if line.startswith("ORDO RERUM") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                continue
            page_parts.append(line)

        page_text = " ".join(page_parts)
        page_text = re.sub(r"(?<=\w)-\s+", "", page_text)
        page_text = re.sub(r"\s+", " ", page_text).strip()
        if not page_text:
            continue

        fragments = re.findall(r"CAP\.\s*.*?(?=(?:\bCAP\.|\bLIBER\b|$))", page_text, flags=re.S)
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or NOISE_LINE_RE.fullmatch(cleaned):
                continue
            cap_pos = cleaned.find("CAP.")
            if cap_pos > 0:
                cleaned = cleaned[cap_pos:]
            page_hints = extract_page_hints(cleaned)
            if not page_hints:
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:ordo:{entry_order:04d}"
            lemma_raw = lemma_from_fragment(cleaned, True)
            target_file_best = target_for_page(page_hints[0], page_map, source_root)
            entry = {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": None,
                "inferred_printed_page": page_hints[0],
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": 0.9,
                "raw_json": {
                    "source_file": str(path),
                    "page_hints": page_hints,
                    "section_kind": "ordo_rerum",
                    "split_strategy": "toc_sentence_boundary",
                },
            }
            entries.append(entry)
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
                        "editorial_anchor_file": str(path),
                        "confidence": 0.96 if target_file else 0.7,
                        "raw_json": {
                            "source_file": str(path),
                            "locator_method": "header_page_map" if target_file else "unresolved",
                        },
                    }
                )

    return entries, refs


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": best if best else None,
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


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    index_files = [path for path in files if file_seq(path) in INDEX_FILES]
    ordo_files = [path for path in files if file_seq(path) in ORDO_FILES]

    index_entries, index_refs, nodes, helper_seed = parse_index_section(index_files, page_map, source_root)
    ordo_entries, ordo_refs = parse_ordo_section(ordo_files, page_map, source_root)

    helper_request_entries = [item for item in helper_seed if item]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_request_entries[:18],
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
        if best.get("file"):
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
            "page_start": 1501,
            "page_end": 1528,
            "file_start": str(index_files[0]) if index_files else None,
            "file_end": str(index_files[-1]) if index_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index headed INDEX RERUM AC VERBORUM; letter-group dividers A-Z and page-locator entries.",
                "evidence_files": [
                    str(index_files[0]) if index_files else None,
                    str(index_files[-1]) if index_files else None,
                ],
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
            "page_start": 1531,
            "page_end": 1548,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table for the tomus, separate from the alphabetical index.",
                "evidence_files": [
                    str(ordo_files[0]) if ordo_files else None,
                    str(ordo_files[-1]) if ordo_files else None,
                ],
            },
        },
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the alphabetical subject index and the closing ORDO RERUM contents table from the OCR tail. The two editorial structures are distinct and were serialized separately.",
        "evidence_files": [
            str(index_files[0]) if index_files else None,
            str(index_files[-1]) if index_files else None,
            str(ordo_files[0]) if ordo_files else None,
            str(ordo_files[-1]) if ordo_files else None,
        ],
    }
    notes = [
        "The OCR tail is irregular: the alphabetical index is interleaved in file sequence with the ORDO RERUM contents table, so file order is not the same as editorial order.",
        "Index entries were split conservatively on sentence boundaries and page locators were preserved literally.",
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
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
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
            "current_focus": "Finalize PL169 alphabetical payload and keep the alphabetical index separate from ORDO RERUM.",
            "completed": [
                "identified the alphabetical index files",
                "identified the ORDO RERUM contents-table files",
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
    ap = argparse.ArgumentParser(description="Build the PL169 alphabetical payload.")
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

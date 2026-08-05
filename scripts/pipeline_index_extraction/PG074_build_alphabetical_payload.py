#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG074_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG074/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG074_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG074_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG074 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG074_alphabetical_indices.json

Build the PG074 alphabetical-index payload from the OCR tail. The volume
contains one analytical subject index plus a closing ORDO RERUM table, and the
OCR file order is not aligned with the printed-page order.
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
VOLUME_ID = "PG074"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus LXXIV"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG074/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG074_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG074_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG074_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG074"
DEFAULT_TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

ANALYTICAL_FILE_START = 542
ANALYTICAL_FILE_END = 547
ORDO_FILE = 548

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET VERBORUM QUÆ IN HIS R. CYRILLI IN JOANNEM COMMENTARIIS NOTABILIA OCCURRUNT.",
        "heading_norm": "index rerum et verborum quae in his r. cyrilli in joannem commentariis notabilia occurrunt",
        "heading_letter": None,
        "page_start": 1033,
        "page_end": 1050,
        "file_start": ANALYTICAL_FILE_START,
        "file_end": ANALYTICAL_FILE_END,
        "section_kind_reason": "Analytical subject index of notable things and words in the Johannine commentaries, with letter-group dividers and page citations.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1031,
        "page_end": 1032,
        "file_start": ORDO_FILE,
        "file_end": ORDO_FILE,
        "section_kind_reason": "Closing editorial contents table for the tomus, distinct from the alphabetical index that precedes it in file order.",
    },
]

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SECTION_HEAD_RE = re.compile(
    r"^(?:INDEX RERUM ET VERBORUM QUÆ IN HIS R\. CYRILLI IN JOANNEM COMMENTARIIS NOTABILIA OCCURRUNT\.|INDEX ANALYTICUS\.?|ORDO RERUM(?:\s+QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QUÆ IN HOC TOMO CONTINENTUR\.)$",
    re.IGNORECASE,
)
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?!\d)")
SPACED_HEADER_NUMBER_RE = re.compile(r"(?:(?<=^)|(?<=\s))(\d{1,4})(?=$|\s)")
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=(?:[A-ZÆŒ]|[IVXLCDM]+\.) )")
SPACE_RE = re.compile(r"\s+")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S | re.I)
ATTR_RE = re.compile(r'tipo="([^"]+)"', re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = value.replace("\u00ad", "")
    value = SPACE_RE.sub(" ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = SPACE_RE.sub(" ", value).strip().lower()
    return value or None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            files.append((int(match.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(match.group(1))


def extract_pages(path: Path) -> dict[str, list[int]]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    numbers: list[int] = []
    for match in SPACED_HEADER_NUMBER_RE.finditer(parsed.get("header_text") or ""):
        value = int(match.group(1))
        if 0 < value < 10000 and value not in numbers:
            numbers.append(value)
    return {
        "header_numbers": numbers,
        "header_text": parsed.get("header_text") or "",
        "body_text": parsed.get("body_text") or "",
        "footer_text": parsed.get("footer_text") or "",
        "notes_text": parsed.get("notes_text") or "",
        "all_text": parsed.get("all_text") or "",
    }


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, str]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        kind_match = ATTR_RE.search(attrs)
        kind = kind_match.group(1).strip().lower() if kind_match else ""
        body = match.group("body") or ""
        body = re.sub(r"<[^>]+>", " ", body)
        body = body.replace("\xa0", " ")
        blocks.append((kind, body))
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = extract_pages(path)
        for number in parsed["header_numbers"]:
            page_map.setdefault(number, str(path))
    return page_map


def helper_query_names(lemma_raw: str) -> list[str]:
    variants = [
        lemma_raw,
        lemma_raw.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe"),
        lemma_raw.replace("—", " "),
        lemma_raw.replace("–", " "),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        value = normalize(item)
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped[:4]


def is_section_heading(text: str) -> bool:
    return bool(SECTION_HEAD_RE.fullmatch(text.strip()))


def is_single_letter(text: str) -> bool:
    return bool(LETTER_RE.fullmatch(text.strip()))


def infer_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^\d+\s*", "", text)
    if "," in text:
        text = text.split(",", 1)[0]
    elif "." in text and len(text.split(".", 1)[0].split()) <= 8:
        text = text.split(".", 1)[0]
    text = text.strip(" .;:")
    return text or None


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": match.group(0).strip(),
                "page_ref_raw": match.group(0).strip(),
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
            }
        )
    return refs


def find_best_page_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, float | None]:
    if page is None:
        return None, None
    if page in page_map:
        return page_map[page], 0.99
    if not page_map:
        return None, None
    best: tuple[int, int, str] | None = None
    for candidate_page, candidate_path in page_map.items():
        distance = abs(candidate_page - page)
        if best is None or (distance, candidate_page) < (best[0], best[1]):
            best = (distance, candidate_page, candidate_path)
    if best is None:
        return None, None
    if best[0] == 0:
        return best[2], 0.99
    if best[0] <= 3:
        return best[2], 0.72
    return best[2], max(0.35, 0.68 - min(best[0], 30) * 0.01)


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
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
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")


def summarize_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, dict[str, Any]]:
    request = read_json(helper_request_json, {})
    output = read_json(helper_output_json, {})
    request_entries = {item.get("entry_id"): item for item in request.get("entries", []) if item.get("entry_id")}
    summary: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []) or []:
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        candidates = item.get("candidates") or []
        best = candidates[0] if candidates else (item.get("best_candidate") or {})
        top_candidates = []
        for candidate in candidates[:3]:
            top_candidates.append(
                {
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "candidate_role": candidate.get("candidate_role"),
                    "reason_summary": candidate.get("reason_summary"),
                    "evidence_kinds": [
                        ev.get("kind")
                        for ev in candidate.get("evidence", [])
                        if isinstance(ev, dict) and ev.get("kind")
                    ],
                }
            )
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "inferred_printed_page": best.get("inferred_printed_page"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in best.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            },
            "top_candidates": top_candidates,
        }
    return summary


def choose_helper_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    patterns = [
        "Aaron aqua ablui jussus cum Levitis",
        "Cæsarem coeleste regnum sibi non arrogare",
        "Christus institutionis legis finis",
        "Joseph ab Arimathæa",
        "Pilati præfractum animum",
        "S CYRILLUS ALEXANDRINUS ARCHIEPISCOPUS",
    ]
    for pattern in patterns:
        for unit in units:
            if pattern.lower() in unit["entry_raw"].lower():
                picks.append(unit)
                break
    if len(picks) < 5:
        for unit in units:
            if unit not in picks and unit.get("page_refs"):
                picks.append(unit)
            if len(picks) >= 5:
                break
    return picks[:6]


def build_helper_request(units: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, unit in enumerate(choose_helper_units(units), start=1):
        page_refs = unit.get("page_refs") or []
        page_hints: list[str] = []
        page_hint_ints: list[int] = []
        for ref in page_refs[:2]:
            if ref["page_ref_int"] not in page_hint_ints:
                page_hint_ints.append(ref["page_ref_int"])
                page_hints.append(str(ref["page_ref_int"]))
        lemma_raw = unit["lemma_raw"] or unit["entry_raw"][:120]
        helper_entries.append(
            {
                "entry_id": f"pg074_helper_{idx:03d}",
                "lemma_raw": lemma_raw,
                "query_names": helper_query_names(lemma_raw),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": unit["entry_raw"][:240],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def collect_units(source_root: Path, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    files = discover_text_files(source_root)
    selected = [path for path in files if ANALYTICAL_FILE_START <= file_seq(path) <= ORDO_FILE]
    if not selected:
        raise SystemExit("No OCR files found in the requested PG074 extraction window.")

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    section_start_file_cache = {
        f"{VOLUME_ID}:alpha:analytic_subject:001": str(next(path for path in selected if file_seq(path) == ANALYTICAL_FILE_START)),
        f"{VOLUME_ID}:alpha:ordo_rerum:002": str(next(path for path in selected if file_seq(path) == ORDO_FILE)),
    }

    section_files = {
        f"{VOLUME_ID}:alpha:analytic_subject:001": [path for path in selected if ANALYTICAL_FILE_START <= file_seq(path) <= ANALYTICAL_FILE_END],
        f"{VOLUME_ID}:alpha:ordo_rerum:002": [path for path in selected if file_seq(path) == ORDO_FILE],
    }

    for section in SECTION_DEFS:
        files_for_section = section_files[section["section_key"]]
        if not files_for_section:
            continue
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
                "file_start": str(files_for_section[0]),
                "file_end": str(files_for_section[-1]),
                "confidence": 0.93 if section["section_kind"] != "ordo_rerum" else 0.98,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "source_files": [str(path) for path in files_for_section],
                    "printed_page_drift": True,
                },
            }
        )

    current_letter: str | None = None
    entry_order = 0
    node_order = 0

    def add_node(section_key: str, label_raw: str, source_file: str) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:04d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": label_raw,
                "label_norm": label_raw.lower(),
                "label_sort": label_raw.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_file": source_file},
            }
        )
        return node_key

    for section in SECTION_DEFS:
        files_for_section = section_files[section["section_key"]]
        current_letter = None
        for path in files_for_section:
            for block_index, (block_kind, block_text) in enumerate(extract_blocks(path), start=1):
                lines = [normalize(line) for line in block_text.splitlines()]
                lines = [line for line in lines if line and not FOOTER_RE.fullmatch(line)]
                if not lines:
                    continue
                if block_kind == "cabecalho":
                    continue
                if block_kind == "nota_marginal":
                    for line in lines:
                        if section["section_kind"] == "analytic_subject" and is_single_letter(line):
                            current_letter = line
                            add_node(section["section_key"], line, str(path))
                    continue
                if block_kind not in {"texto_principal", "outro"}:
                    continue

                if section["section_kind"] == "ordo_rerum":
                    block_units = lines
                else:
                    block_units = []
                    for paragraph in re.split(r"\n\s*\n", block_text.strip()):
                        paragraph_lines = [normalize(line) for line in paragraph.splitlines()]
                        paragraph_lines = [line for line in paragraph_lines if line and not FOOTER_RE.fullmatch(line)]
                        paragraph_text = " ".join(paragraph_lines).strip()
                        if paragraph_text:
                            block_units.append(paragraph_text)
                    if not block_units:
                        block_units = lines

                for unit_index, unit in enumerate(block_units, start=1):
                    if not unit or is_section_heading(unit) or FOOTER_RE.fullmatch(unit):
                        continue
                    if is_single_letter(unit):
                        current_letter = unit
                        add_node(section["section_key"], unit, str(path))
                        continue
                    if section["section_kind"] == "ordo_rerum" and unit.upper() in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
                        continue

                    entry_order += 1
                    entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
                    lemma_raw = infer_lemma(unit)
                    page_refs = extract_page_refs(unit)
                    inferred_page = page_refs[0]["page_ref_int"] if page_refs else None
                    target_file_best = str(path)

                    units.append(
                        {
                            "entry_key": entry_key,
                            "section_key": section["section_key"],
                            "parent_node_key": next(
                                (node["node_key"] for node in reversed(nodes) if node["label_raw"] == current_letter and node["section_key"] == section["section_key"]),
                                None,
                            ),
                            "entry_order": entry_order,
                            "entry_kind": "heading_group" if section["section_kind"] == "ordo_rerum" else "lemma",
                            "lemma_raw": lemma_raw,
                            "lemma_display": lemma_raw,
                            "lemma_norm": sort_norm(lemma_raw),
                            "lemma_sort": sort_norm(lemma_raw),
                            "entry_raw": unit,
                            "context_raw": None,
                            "heading_letter": current_letter,
                            "inferred_printed_page": inferred_page,
                            "section_start_file": section_start_file_cache[section["section_key"]],
                            "editorial_anchor_file": str(path),
                            "target_file_best": target_file_best,
                            "confidence": 0.84 if page_refs else 0.72,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": section["section_kind"],
                                "block_kind": block_kind,
                                "block_index": block_index,
                                "unit_index": unit_index,
                                "page_refs": page_refs,
                            },
                            "page_refs": page_refs,
                        }
                    )

    return sections, nodes, {"units": units, "selected_files": [str(path) for path in selected], "page_map": page_map}


def populate_helper_evidence(units: list[dict[str, Any]], helper_summary: dict[str, dict[str, Any]]) -> None:
    helper_patterns = {
        "pg074_helper_001": "Aaron aqua ablui jussus cum Levitis",
        "pg074_helper_002": "Cæsarem coeleste regnum sibi non arrogare",
        "pg074_helper_003": "Christus institutionis legis finis",
        "pg074_helper_004": "Joseph ab Arimathæa",
        "pg074_helper_005": "Pilati præfractum animum",
        "pg074_helper_006": "S CYRILLUS ALEXANDRINUS ARCHIEPISCOPUS",
    }
    for unit in units:
        for helper_id, needle in helper_patterns.items():
            if needle.lower() in unit["entry_raw"].lower() and helper_id in helper_summary:
                unit["raw_json"]["helper_entry_id"] = helper_id
                unit["raw_json"]["helper_summary"] = helper_summary[helper_id]


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_text_files(source_root)
    page_map = build_page_map(files)

    # Initial collection is done without helper evidence; helper request uses the
    # recovered units as the source of representative ambiguous cases.
    sections, nodes, meta = collect_units(source_root, page_map)
    helper_request = build_helper_request(meta["units"], source_root)
    write_json(helper_request_json, helper_request)
    run_helper(helper_request_json, helper_output_json)
    helper_summary = summarize_helper(helper_request_json, helper_output_json)
    populate_helper_evidence(meta["units"], helper_summary)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    for unit in meta["units"]:
        page_refs = unit.get("page_refs", [])
        entry = {
            "entry_key": unit["entry_key"],
            "section_key": unit["section_key"],
            "parent_node_key": unit["parent_node_key"],
            "entry_order": unit["entry_order"],
            "entry_kind": unit["entry_kind"],
            "lemma_raw": unit["lemma_raw"],
            "lemma_display": unit["lemma_display"],
            "lemma_norm": unit["lemma_norm"],
            "lemma_sort": unit["lemma_sort"],
            "entry_raw": unit["entry_raw"],
            "context_raw": unit["context_raw"],
            "heading_letter": unit["heading_letter"],
            "inferred_printed_page": unit["inferred_printed_page"],
            "section_start_file": unit["section_start_file"],
            "editorial_anchor_file": unit["editorial_anchor_file"],
            "target_file_best": unit["target_file_best"],
            "confidence": unit["confidence"],
            "raw_json": unit["raw_json"],
        }
        entries.append(entry)

        for ref_order, page_ref in enumerate(page_refs, start=1):
            target_file, target_prob = find_best_page_target(page_ref["page_ref_int"], page_map)
            refs.append(
                {
                    "entry_key": unit["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": page_ref["ref_kind"],
                    "ref_raw": page_ref["ref_raw"],
                    "page_ref_raw": page_ref["page_ref_raw"],
                    "page_ref_int": page_ref["page_ref_int"],
                    "page_ref_col": page_ref["page_ref_col"],
                    "line_ref_raw": page_ref["line_ref_raw"],
                    "range_start_raw": page_ref["range_start_raw"],
                    "range_end_raw": page_ref["range_end_raw"],
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": unit["section_start_file"],
                    "editorial_anchor_file": unit["editorial_anchor_file"],
                    "confidence": 0.96 if target_prob and target_prob >= 0.9 else 0.72,
                    "raw_json": {
                        "source_file": unit["editorial_anchor_file"],
                        "page_ref_resolution": "exact" if target_prob and target_prob >= 0.9 else "nearest_or_unresolved",
                    },
                }
            )

    if entries:
        notes = [
            "The analytical index and the closing ORDO RERUM table are serialized as distinct sections.",
            "OCR file sequence does not match printed-page order; refs were resolved against a volume-wide page map.",
            "Analytical entries preserve conservative paragraph-level grouping when the OCR packs several related clauses into one visible entry block.",
        ]
    else:
        notes = [
            "No recoverable entries were found in the extracted tail window.",
        ]

    coverage = {
        "entries_status": "partial_recovery" if entries else "unrecoverable_ocr",
        "entries_status_reason": (
            "Recovered the visible analytical index blocks and the closing ORDO RERUM table conservatively from OCR files 542-548; the OCR file order is shuffled against the printed-page order, and some embedded locators are noisy enough to warrant conservative grouping."
            if entries
            else "The tail window did not yield recoverable line items."
        ),
        "evidence_files": meta["selected_files"],
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "PG074 closes with an analytical subject index headed INDEX RERUM ET VERBORUM and a separate ORDO RERUM contents table.",
            "The OCR files are not in printed-page order; page numbers and file suffixes must be treated as independent numbering systems.",
        ],
    }

    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "source_root": str(source_root),
        "helper_request_json": str(helper_request_json),
        "helper_output_json": str(helper_output_json),
        "output_file": str(intermediate_dir.parent / f"{VOLUME_ID}_alphabetical_indices.json"),
    }

    write_json(intermediate_dir / "manifest.json", manifest)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        DEFAULT_TODO_JSON if intermediate_dir == DEFAULT_INTERMEDIATE_DIR else intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PG074 alphabetical payload and validate refs against the volume-wide page map.",
            "completed": [
                "OCR tail inspected",
                "section boundaries mapped",
                "helper request written and resolved",
                "payload fragments assembled",
            ],
            "pending": [
                "validate final JSON structure",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not conflate OCR file suffixes with printed page references.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PG074 alphabetical-index payload.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

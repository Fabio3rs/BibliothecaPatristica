#!/usr/bin/env python3
"""Usage: build the PG087.03 alphabetical payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/PG087_03_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG087.03/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG087.03_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG087.03_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG087.03 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG087.03_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG087.03"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 87.03"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG087.03_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG087.03_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG087.03_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG087.03"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

ANALYTIC_SECTION = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS",
    "heading_norm": "index analyticus",
    "heading_letter": None,
    "page_start": 4107,
    "page_end": 4170,
    "file_start": None,
    "file_end": None,
    "confidence": 0.95,
    "raw_json": {
        "section_kind_reason": (
            "Main alphabetical analytical index for Procopius' commentaries on the Octateuch and "
            "Isaiah, with letter dividers A-T and page-locator entries."
        ),
        "alternate_heading_raw": [
            "INDEX RERUM ET VERBORUM MEMORABILIUM",
            "INDEX ANALYTICUS.",
        ],
    },
}

ORDO_SECTION = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM",
    "heading_norm": "ordo rerum",
    "heading_letter": None,
    "page_start": 4171,
    "page_end": 4180,
    "file_start": None,
    "file_end": None,
    "confidence": 0.98,
    "raw_json": {
        "section_kind_reason": "Editorial closing contents table for the tome, distinct from the analytical index.",
        "alternate_heading_raw": "ORDO RERUM QUÆ IN HOC TOMO TRIPARTITO CONTINENTUR.",
    },
}

PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–]\s*(\d{1,4})(?:\s*([ab]))?")
PAGE_COL_RE = re.compile(r"(?<!\d)(\d{1,4})\s*([ab])\b")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
NO_LOCATOR_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?)\b", re.IGNORECASE)
NUMERIC_ONLY_RE = re.compile(r"^\d{1,4}$")
LETTER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]\.?$")
ROMAN_DIVIDER_RE = re.compile(r"^[IVXLCDM]+\.$", re.IGNORECASE)
STRUCTURAL_RE = re.compile(
    r"^(?:INDEX ANALYTICUS|INDEX RERUM ET VERBORUM MEMORABILIUM|QUÆ IN PROCOPII COMMENTARIIS IN OCTATEUCHUM EXPLICANTUR\.|"
    r"Revocatur lector ad numeros grandiori charactere in texto expressos\.|Revocatur lector ad paginas Editionis Parisiensis numeris crassioribus in texto Latino expressas\.|"
    r"ORDO RERUM|QUÆ IN HOC TOMO TRIPARTITO CONTINENTUR\.|Digitized by Google)$",
    re.IGNORECASE,
)
SINGLE_CAP_RE = re.compile(r"^[A-ZÆŒΑ-Ω]$")
BOUNDARY_RE = re.compile(r"(?<=[.;:])\s+(?=[A-ZÆŒΑ-Ω])")
ORDO_ENTRY_RE = re.compile(r"^(?P<label>[IVXLCDM]+\.)\s+[-—]\s+(?P<body>.+)$")
ORDO_FRAGMENT_SPLIT_RE = re.compile(r"(?=(?:[IVXLCDM]{1,3}\.?\s+[—-]))")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_files(source_root: Path) -> list[Path]:
    files = sorted(
        source_root.glob("*.txt"),
        key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)),
    )
    return files


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    raw_text = parsed.get("all_text") or ""
    lines: list[str] = []
    for raw in raw_text.splitlines():
        text = normalize(raw)
        if not text:
            continue
        if NUMERIC_ONLY_RE.fullmatch(text):
            continue
        if text == "Digitized by Google":
            continue
        lines.append(text)
    return lines


def extract_header_numbers(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text") or "") or ""
    numbers: list[int] = []
    for match in PAGE_RE.finditer(header):
        value = int(match.group(1))
        if 0 < value < 10000:
            numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for page in extract_header_numbers(path):
            page_map.setdefault(page, str(path))
    return page_map


def merge_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            merged.append(buffer.strip())
            buffer = ""

    for line in lines:
        if STRUCTURAL_RE.fullmatch(line):
            flush()
            merged.append(line)
            continue
        if LETTER_RE.fullmatch(line) or ROMAN_DIVIDER_RE.fullmatch(line) or SINGLE_CAP_RE.fullmatch(line):
            flush()
            merged.append(line.rstrip("."))
            continue
        if not buffer:
            buffer = line
            continue
        if buffer.endswith("-"):
            buffer = f"{buffer[:-1]}{line.lstrip()}"
            continue
        if not re.search(r"[.!?]$", buffer) and (
            line[:1].islower()
            or line.startswith(("—", "-", ",", ";", "ibid", "id.", "et "))
            or not PAGE_RE.search(line)
        ):
            buffer = f"{buffer} {line}"
            continue
        flush()
        buffer = line

    flush()
    return merged


def split_fragments(text: str) -> list[str]:
    cleaned = normalize(text) or ""
    if not cleaned:
        return []
    parts = [part.strip() for part in BOUNDARY_RE.split(cleaned) if part.strip()]
    return parts or [cleaned]


def infer_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if ORDO_ENTRY_RE.match(text):
        return text
    if PAGE_RE.search(text):
        text = text[: PAGE_RE.search(text).start()].strip()
    text = re.sub(r"\b(?:ibid\.?|id\.?)\.?$", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip(" ,;:").strip()
    return text or None


def infer_entry_kind(section_kind: str, entry_raw: str) -> str:
    text = normalize(entry_raw) or ""
    if NO_LOCATOR_RE.search(text) and not PAGE_RE.search(text):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        if text.startswith(("PARS ", "COMMENTARII", "EPISTOLAE", "FRAGMENTA", "TRIOLIUM", "COMMENTARIUS", "ORDO ", "FINIS", "SPURIA")):
            return "heading_group"
        if ORDO_ENTRY_RE.match(text):
            return "heading_group"
    if LETTER_RE.fullmatch(text) or ROMAN_DIVIDER_RE.fullmatch(text):
        return "heading_group"
    return "lemma"


def extract_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    text = normalize(entry_raw) or ""
    working = text

    for match in PAGE_RANGE_RE.finditer(working):
        start = int(match.group(1))
        end = int(match.group(2))
        raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": match.group(3),
                "line_ref_raw": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end),
            }
        )
        last_page = end
        working = working.replace(raw, " ", 1)

    for match in PAGE_COL_RE.finditer(working):
        page = int(match.group(1))
        raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": match.group(2),
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page
        working = working.replace(raw, " ", 1)

    for match in PAGE_RE.finditer(working):
        page = int(match.group(1))
        if page < 5 and not re.search(r"\b\d{3,4}\b", text):
            continue
        raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page

    if not refs and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref["ref_kind"],
            ref["page_ref_int"],
            ref["page_ref_col"],
            ref["range_start_raw"],
            ref["range_end_raw"],
            ref["ref_raw"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped, last_page


def build_section_meta(files: list[Path]) -> list[dict[str, Any]]:
    analytic_files = [p for p in files if 668 <= file_seq(p) <= 699]
    ordo_files = [p for p in files if 700 <= file_seq(p) <= 706]
    section1 = dict(ANALYTIC_SECTION)
    section1["file_start"] = str(analytic_files[0]) if analytic_files else None
    section1["file_end"] = str(analytic_files[-1]) if analytic_files else None
    section1["raw_json"] = {
        **section1["raw_json"],
        "evidence_files": [str(analytic_files[0]) if analytic_files else None, str(analytic_files[-1]) if analytic_files else None],
    }
    section2 = dict(ORDO_SECTION)
    section2["file_start"] = str(ordo_files[0]) if ordo_files else None
    section2["file_end"] = str(ordo_files[-1]) if ordo_files else None
    section2["raw_json"] = {
        **section2["raw_json"],
        "evidence_files": [str(ordo_files[0]) if ordo_files else None, str(ordo_files[-1]) if ordo_files else None],
    }
    return [section1, section2]


def page_to_section(file_seq_num: int) -> str:
    if 668 <= file_seq_num <= 699:
        return ANALYTIC_SECTION["section_key"]
    if 700 <= file_seq_num <= 706:
        return ORDO_SECTION["section_key"]
    raise ValueError(file_seq_num)


def page_to_heading_letter(line: str | None) -> str | None:
    if not line:
        return None
    cleaned = line.strip().strip(".")
    if SINGLE_CAP_RE.fullmatch(cleaned):
        return cleaned
    return None


def build_chunks(files: list[Path]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_section = ANALYTIC_SECTION["section_key"]
    current_letter: str | None = None
    current_file: Path | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines, current_file
        if not current_lines or current_file is None:
            current_lines = []
            return
        text = normalize(" ".join(current_lines)) or ""
        if text:
            chunks.append(
                {
                    "section_key": current_section,
                    "heading_letter": current_letter,
                    "source_file": str(current_file),
                    "text": text,
                }
            )
        current_lines = []

    for path in files:
        seq = file_seq(path)
        if seq < 668 or seq > 706:
            continue
        section_key = page_to_section(seq)
        if section_key != current_section:
            flush()
            current_section = section_key
            current_letter = None
        current_file = path
        for line in merge_lines(extract_lines(path)):
            if STRUCTURAL_RE.fullmatch(line):
                flush()
                continue
            if LETTER_RE.fullmatch(line):
                flush()
                current_letter = line.strip(".")
                continue
            if ORDO_ENTRY_RE.match(line):
                flush()
                current_lines.append(line)
                flush()
                continue
            current_lines.append(line)
        flush()
    return chunks


def helper_compact(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates: list[dict[str, Any]] = []
    for candidate in (helper_entry.get("candidates") or [])[:5]:
        candidates.append(
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
    return {
        "status": helper_entry.get("status"),
        "candidate_role": helper_entry.get("candidate_role"),
        "reason_summary": helper_entry.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper_entry.get("candidates") or []),
        "candidates": candidates,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_TARGET_LOCATOR),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def build_entries(files: list[Path], page_map: dict[int, str], helper_by_id: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    section_entry_counts = {ANALYTIC_SECTION["section_key"]: 0, ORDO_SECTION["section_key"]: 0}
    section_node_counts = {ANALYTIC_SECTION["section_key"]: 0, ORDO_SECTION["section_key"]: 0}
    letter_nodes_by_section: dict[tuple[str, str], str] = {}
    last_page_by_section: dict[str, int | None] = {ANALYTIC_SECTION["section_key"]: None, ORDO_SECTION["section_key"]: None}

    for chunk in build_chunks(files):
        section_key = chunk["section_key"]
        text = normalize(chunk["text"]) or ""
        if not text:
            continue
        fragments = split_fragments(text)
        if section_key == ORDO_SECTION["section_key"]:
            expanded: list[str] = []
            for fragment in fragments:
                pieces = [part.strip() for part in ORDO_FRAGMENT_SPLIT_RE.split(fragment) if part.strip()]
                if pieces:
                    expanded.extend(pieces)
                else:
                    expanded.append(fragment)
            fragments = expanded
        if not fragments:
            continue
        current_letter = chunk["heading_letter"]
        for fragment in fragments:
            fragment = normalize(fragment) or ""
            if not fragment:
                continue
            if section_key == ANALYTIC_SECTION["section_key"] and (
                "INDEX ANALYTICUS" in fragment
                or fragment in {"INDICES.", "INDICES"}
                or fragment.startswith("INDEX RERUM ET VERBORUM MEMORABILIUM")
            ):
                continue
            if section_key == ORDO_SECTION["section_key"] and (
                fragment.startswith("Parisiis.")
                or fragment == "MIGNE."
                or fragment.startswith("FINIS TOMI")
                or fragment.startswith("PATROL. GR. LXXXVII.")
            ):
                continue

            entry_kind = infer_entry_kind(section_key == ORDO_SECTION["section_key"] and "ordo_rerum" or "analytic_subject", fragment)
            lemma_raw = infer_lemma(fragment) if entry_kind != "cross_reference" else None
            refs_for_entry, last_page = extract_refs(fragment, last_page_by_section[section_key])
            if last_page is not None:
                last_page_by_section[section_key] = last_page

            if section_key == ANALYTIC_SECTION["section_key"] and not current_letter and lemma_raw:
                match = re.match(r"^([A-ZÆŒΑ-Ω])", lemma_raw)
                if match:
                    current_letter = match.group(1)

            node_key = None
            if section_key == ANALYTIC_SECTION["section_key"] and current_letter:
                node_lookup_key = (section_key, current_letter)
                node_key = letter_nodes_by_section.get(node_lookup_key)
                if node_key is None:
                    section_node_counts[section_key] += 1
                    node_key = f"{VOLUME_ID}:node:{section_node_counts[section_key]:03d}:{sort_norm(current_letter) or current_letter.lower()}"
                    letter_nodes_by_section[node_lookup_key] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": section_node_counts[section_key],
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter.lower(),
                            "label_sort": sort_norm(current_letter) or current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {
                                "source_file": chunk["source_file"],
                                "section_kind": "analytic_subject",
                            },
                        }
                    )

            section_entry_counts[section_key] += 1
            entry_order = section_entry_counts[section_key]
            entry_key = f"{section_key}:entry:{entry_order:06d}"
            page_hints = [ref["page_ref_int"] for ref in refs_for_entry if ref.get("page_ref_int") is not None]
            inferred_printed_page = page_hints[0] if page_hints else last_page_by_section.get(section_key)
            target_file_best = page_map.get(inferred_printed_page) if inferred_printed_page is not None else chunk["source_file"]
            if target_file_best is None:
                target_file_best = chunk["source_file"]

            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": fragment,
                "context_raw": None,
                "heading_letter": current_letter if section_key == ANALYTIC_SECTION["section_key"] else None,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": section_key == ANALYTIC_SECTION["section_key"] and ANALYTIC_SECTION["file_start"] or ORDO_SECTION["file_start"],
                "editorial_anchor_file": chunk["source_file"],
                "target_file_best": target_file_best,
                "confidence": 0.91 if refs_for_entry else (0.79 if entry_kind == "cross_reference" else 0.84),
                "raw_json": {
                    "source_file": chunk["source_file"],
                    "section_kind": "analytic_subject" if section_key == ANALYTIC_SECTION["section_key"] else "ordo_rerum",
                    "page_hints": page_hints,
                },
            }
            helper = helper_by_id.get(entry_key)
            if helper:
                entry["raw_json"]["helper"] = helper_compact(helper)
                best = helper.get("best_candidate") or {}
                if best.get("file"):
                    entry["target_file_best"] = best.get("file")
            entries.append(entry)

            for ref_order, ref in enumerate(refs_for_entry, start=1):
                ref_key = (
                    entry_key,
                    ref["ref_kind"],
                    ref["page_ref_int"],
                    ref["page_ref_col"],
                    ref["range_start_raw"],
                    ref["range_end_raw"],
                    ref["ref_raw"],
                )
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": page_map.get(ref["page_ref_int"]),
                        "target_file_probability": 0.98 if page_map.get(ref["page_ref_int"]) else None,
                        "section_start_file": entry["section_start_file"],
                        "editorial_anchor_file": chunk["source_file"],
                        "confidence": 0.92 if page_map.get(ref["page_ref_int"]) else 0.72,
                        "raw_json": {
                            "source_file": chunk["source_file"],
                            "section_kind": entry["raw_json"]["section_kind"],
                            "dedupe_key": ref_key,
                        },
                    }
                )

            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or fragment,
                    "query_names": [x for x in [lemma_raw, fragment] if x][:5],
                    "page_hints": [str(x) for x in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": fragment,
                }
            )

    # Deduplicate refs after helper enrichment.
    seen_refs: set[tuple[Any, ...]] = set()
    unique_refs: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            ref["entry_key"],
            ref["ref_kind"],
            ref["page_ref_int"],
            ref["page_ref_col"],
            ref["range_start_raw"],
            ref["range_end_raw"],
            ref["ref_raw"],
        )
        if key in seen_refs:
            continue
        seen_refs.add(key)
        unique_refs.append(ref)

    return entries, unique_refs, nodes, helper_entries


def compact_helper_output(helper_output: dict[str, Any]) -> dict[str, Any]:
    entries = helper_output.get("entries") or []
    mapped: dict[str, Any] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("entry_id"):
            mapped[item["entry_id"]] = item
    return mapped


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit(f"No OCR files found under {source_root}")
    page_map = build_page_map(files)
    sections = build_section_meta(files)
    helper_output = {}
    helper_by_id: dict[str, Any] = {}

    # Build a first-pass set of entries so we can generate the helper request.
    _, _, _, helper_entries = build_entries(files, page_map, helper_by_id={})
    helper_entries = [
        entry
        for entry in helper_entries
        if len(entry.get("page_hint_ints") or []) > 1
        or not (entry.get("page_hint_ints") or [])
        or any(token in (entry.get("context_raw") or "").lower() for token in ("ibid", "id.", "vide", "voir", "cf."))
    ]
    if not helper_entries:
        helper_entries = build_entries(files, page_map, helper_by_id={})[3][:40]
    elif len(helper_entries) > 240:
        helper_entries = helper_entries[:240]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_by_id = compact_helper_output(helper_output)

    entries, refs, nodes, _ = build_entries(files, page_map, helper_by_id)

    sections[0]["file_start"] = sections[0]["file_start"] or str(next((p for p in files if 668 <= file_seq(p) <= 699), files[0]))
    sections[0]["file_end"] = sections[0]["file_end"] or str(next((p for p in reversed(files) if 668 <= file_seq(p) <= 699), files[-1]))
    sections[1]["file_start"] = sections[1]["file_start"] or str(next((p for p in files if 700 <= file_seq(p) <= 706), files[0]))
    sections[1]["file_end"] = sections[1]["file_end"] or str(next((p for p in reversed(files) if 700 <= file_seq(p) <= 706), files[-1]))

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The volume ends with an ORDO RERUM contents table after the main analytical index.",
            "Printed page numbers in the OCR headers and cited page references inside entries are kept distinct from OCR file suffixes.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the main analytical index and closing ORDO RERUM block conservatively from the OCR tail, "
            "but some wrapped clauses and OCR noise remain."
        ),
        "evidence_files": [str(p) for p in files if 668 <= file_seq(p) <= 706],
    }
    notes = [
        "Analytical entries are grouped under letter nodes where the OCR provides explicit dividers.",
        "Entry segmentation is conservative around wrapped clauses and repeated page-locator clusters.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
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
    now = now_iso()
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now,
            "updated_at": now,
            "output_file": str(output_file),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
        },
    )
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now,
            "current_focus": "Validate helper-assisted PG087.03 alphabetical payload and keep OCR literals intact.",
            "completed": [
                "main analytical index detected",
                "closing ORDO RERUM detected",
                "helper request generated",
                "helper executed",
                "intermediate fragments written",
            ],
            "pending": [
                "review the final JSON for any malformed fragments",
            ],
            "blocked": [],
            "notes": [
                "Do not confuse OCR file suffixes with printed page anchors.",
                "Keep material locators separate from editorial section titles.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG087.03 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output_file.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()

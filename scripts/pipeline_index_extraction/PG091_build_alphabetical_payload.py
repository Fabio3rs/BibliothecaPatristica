#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG091_build_alphabetical_payload.py

Build the PG091 alphabetical payload from the OCR tail, write helper request
and output JSON files, persist per-volume checkpoints, and emit the canonical
final payload.
"""
from __future__ import annotations

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
VOLUME_ID = "PG091"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 91"
SOURCE_ROOT = ROOT / "teste/PG091/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG091_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG091_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG091_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG091"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

NOISE_LINES = {"Digitized by Google"}
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*([ab]))?(?:\s*[-–—]\s*(\d{1,4})(?:\s*([ab]))?)?(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
VIDE_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?)\b", re.IGNORECASE)
LETTER_RE = re.compile(r"^(?:[A-Z]|[ΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ])\.?$")
ANALYTIC_HEADING_RE = re.compile(r"INDEX ANALYTICUS(?:\.|$)", re.IGNORECASE)
ANALYTIC_SUBTITLE_RE = re.compile(r"RERUM ET VERBORUM MEMORABILIUM", re.IGNORECASE)
CROSSWALK_TITLE_RE = re.compile(r"INDICES AD AMBIGUORUM LIBRUM\.?", re.IGNORECASE)
CROSSWALK_INDEX_RE = re.compile(r"INDEX LOCORUM EX OPERIBUS SS\. PP\. GREGORII NAZIANZENI ET DIONYSII AREOPAGITÆ", re.IGNORECASE)
VERBORUM_HEADING_RE = re.compile(r"INDEX VERBORUM\.?", re.IGNORECASE)
ORDO_HEADING_RE = re.compile(r"ORDO RERUM(?:\.|$)", re.IGNORECASE)
AUTHOR_HEADING_RE = re.compile(r"^(GREGORIUS NAZIANZENUS|DIONYSIUS AREOPAGITA)\.?$", re.IGNORECASE)
SECTION_LINE_SKIP_RE = re.compile(r"^(?:Digitized by Google|\(Numeri respondent foliis libri Gudiani typis grandioribus expressis\.\)|Monitum ad locos communes\.|IN OPERIBUS S\. MAXIMI LAUDATORUM\.)$", re.IGNORECASE)
SPLIT_FRAGMENT_RE = re.compile(r"(?<=[.;])\s+(?=(?:[A-ZΑ-Ω\u0370-\u03FF\u1F00-\u1FFF]))")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def parse_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for source in (parsed["header_text"], parsed["body_text"], parsed["footer_text"], parsed["notes_text"]):
        for raw_line in source.splitlines():
            line = normalize_space(raw_line)
            if line and line not in NOISE_LINES:
                lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for line in parse_lines(path)[:8]:
            if VERBORUM_HEADING_RE.search(line) or ANALYTIC_HEADING_RE.search(line) or ORDO_HEADING_RE.search(line):
                break
            for match in PAGE_RE.finditer(line):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def is_letter_line(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line.strip()))


def section_kind_for_line(line: str, current_section: str | None) -> str | None:
    if ANALYTIC_HEADING_RE.search(line) and ANALYTIC_SUBTITLE_RE.search(line):
        return "analytic_subject"
    if ANALYTIC_HEADING_RE.search(line):
        return "analytic_subject"
    if CROSSWALK_TITLE_RE.search(line) or CROSSWALK_INDEX_RE.search(line):
        return "crosswalk_index"
    if VERBORUM_HEADING_RE.search(line):
        if current_section == "crosswalk_index":
            return None
        return "foreign_terms"
    if ORDO_HEADING_RE.search(line):
        return "ordo_rerum"
    return None


def section_key(section_kind: str) -> str:
    return f"{VOLUME_ID}:alpha:{section_kind}:{1 if section_kind == 'analytic_subject' else 2 if section_kind == 'crosswalk_index' else 3 if section_kind == 'foreign_terms' else 4:03d}"


def section_start_file(section_kind: str, files_by_num: dict[int, Path]) -> str | None:
    if section_kind == "analytic_subject":
        return str(files_by_num[814])
    if section_kind == "crosswalk_index":
        return str(files_by_num[821])
    if section_kind == "foreign_terms":
        return str(files_by_num[822])
    if section_kind == "ordo_rerum":
        return str(files_by_num[830])
    return None


def parse_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    order = 1

    if IBID_RE.fullmatch(text.strip().rstrip(".")) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": text.strip(),
                "page_ref_raw": text.strip(),
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)
        return refs, page_hints, last_page

    for match in PAGE_RE.finditer(text):
        page = int(match.group(1))
        col = match.group(2)
        end = match.group(3)
        end_col = match.group(4)
        raw = match.group(0).strip()
        if end is not None:
            refs.append(
                {
                    "ref_order": order,
                    "ref_kind": "editorial_range",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page,
                    "page_ref_col": col,
                    "line_ref_raw": None,
                    "range_start_raw": str(page) + (f" {col}" if col else ""),
                    "range_end_raw": str(int(end)) + (f" {end_col}" if end_col else ""),
                }
            )
        else:
            refs.append(
                {
                    "ref_order": order,
                    "ref_kind": "editorial_page",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page,
                    "page_ref_col": col,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
        page_hints.append(page)
        last_page = page
        order += 1
    if not refs and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "Ibid.",
                "page_ref_raw": "Ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)
    return refs, page_hints, last_page


def choose_entry_kind(section_kind: str, text: str, refs: list[dict[str, Any]]) -> str:
    if section_kind in {"ordo_rerum", "analytic_subject"} and not refs and not text:
        return "heading_group"
    if not refs and (VIDE_RE.search(text) or IBID_RE.search(text)):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        return "heading_group"
    if section_kind == "crosswalk_index":
        return "lemma"
    return "lemma"


def clean_lemma(text: str, refs: list[dict[str, Any]], section_kind: str) -> str:
    lemma = text
    for ref in refs:
        pos = lemma.find(ref["page_ref_raw"])
        if pos >= 0:
            lemma = lemma[:pos]
            break
    lemma = lemma.strip(" ,;:.")
    if section_kind == "crosswalk_index" and "—" in lemma:
        lemma = lemma.split("—", 1)[0].strip(" ,;:.")
    return lemma or text.strip(" ,;:.")


def unique_preserve_order(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normed = normalize_space(value)
        if normed and normed not in seen:
            result.append(normed)
            seen.add(normed)
    return result


def strip_section_prefix(text: str, section_kind: str, current_file_num: int) -> str:
    value = normalize_space(text)
    if section_kind == "analytic_subject":
        value = re.sub(r"^\d{3,4}\s+INDEX ANALYTICUS(?:\.|$)\s*\d{0,4}\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^INDEX ANALYTICUS(?:\.|$)\s*", "", value, flags=re.IGNORECASE)
    elif section_kind == "foreign_terms":
        value = re.sub(r"^\d{1,4}(?::\d{1,4})?\s+INDEX VERBORUM(?:\.|$)\s*\d{1,4}\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^INDEX VERBORUM(?:\.|$)\s*", "", value, flags=re.IGNORECASE)
    elif section_kind == "crosswalk_index":
        value = re.sub(r"^\d{1,4}\s+INDICES AD AMBIGUORUM LIBRUM(?:\.|$)\s*", "", value, flags=re.IGNORECASE)
    elif section_kind == "ordo_rerum":
        value = re.sub(r"^\d{1,4}\s+ORDO RERUM(?:\.|$)\s*", "", value, flags=re.IGNORECASE)
    if current_file_num in {814, 821, 822, 830} and not value:
        return ""
    return value


def split_entry_fragments(text: str, section_kind: str) -> list[str]:
    value = normalize_space(text)
    if not value:
        return []
    if section_kind in {"analytic_subject", "foreign_terms", "crosswalk_index", "ordo_rerum"}:
        parts = [part.strip() for part in SPLIT_FRAGMENT_RE.split(value) if part.strip()]
        if parts and re.fullmatch(r"[A-ZΑ-Ω]\.?", parts[0]):
            parts = parts[1:]
        return parts or [value]
    return [value]


def build_payload() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files = discover_text_files(SOURCE_ROOT)
    files_by_num = {file_num(path): path for path in files}
    page_map = build_page_map(files)

    section_plan = [
        {
            "section_key": section_key("analytic_subject"),
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS. RERUM ET VERBORUM MEMORABILIUM QUÆ IN HOCCE VOLUMINE CONTINENTUR.",
            "heading_norm": "index analyticus rerum et verborum memorabilium quae in hocce volumine continentur",
            "heading_letter": None,
            "page_start": 1503,
            "page_end": 1516,
            "file_start": str(files_by_num[814]),
            "file_end": str(files_by_num[820]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Analytical subject index with Latin lemmata and printed-page citations.",
                "evidence_files": [str(files_by_num[814]), str(files_by_num[820])],
            },
        },
        {
            "section_key": section_key("crosswalk_index"),
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "crosswalk_index",
            "heading_raw": "INDICES AD AMBIGUORUM LIBRUM. INDEX LOCORUM EX OPERIBUS SS. PP. GREGORII NAZIANZENI ET DIONYSII AREOPAGITÆ QUI VEL ILLUSTRANTUR VEL CITANTUR.",
            "heading_norm": "indices ad ambiguorum librum index locorum ex operibus ss pp gregorii nazianzeni et dionysii areopagitae qui vel illustrantur vel citantur",
            "heading_letter": None,
            "page_start": 1517,
            "page_end": 1518,
            "file_start": str(files_by_num[821]),
            "file_end": str(files_by_num[821]),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Crosswalk-style index of loci cited from Gregory Nazianzen and Dionysius Areopagita.",
                "evidence_files": [str(files_by_num[821])],
            },
        },
        {
            "section_key": section_key("foreign_terms"),
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "foreign_terms",
            "heading_raw": "INDEX VERBORUM.",
            "heading_norm": "index verborum",
            "heading_letter": None,
            "page_start": 1519,
            "page_end": 1530,
            "file_start": str(files_by_num[822]),
            "file_end": str(files_by_num[829]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical Greek vocabulary index with page citations.",
                "evidence_files": [str(files_by_num[822]), str(files_by_num[829])],
            },
        },
    ]

    sections = section_plan
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    entry_order = 0
    node_order = 0

    def emit_entry(text: str, source_file: str, section_kind: str, current_letter: str | None, current_node_key: str | None, last_page: int | None) -> int | None:
        nonlocal entry_order
        text = normalize_space(text)
        if not text or SECTION_LINE_SKIP_RE.search(text):
            return last_page
        if section_kind == "analytic_subject":
            if ANALYTIC_HEADING_RE.search(text) or ANALYTIC_SUBTITLE_RE.search(text) or text.startswith("(A col.") or text.startswith("Revocatur"):
                return last_page
        if section_kind == "foreign_terms":
            if VERBORUM_HEADING_RE.search(text) or text in {"A B C D", "A", "B", "C", "D", "E", "Z", "H", "I", "K", "L", "M", "N", "O", "P", "R", "S", "T", "U", "X", "Y", "Γ", "Δ", "Ε", "Ζ", "Η", "Θ", "Ι", "Κ", "Λ", "Μ", "Ν", "Ξ", "Ο", "Π", "Ρ", "Σ", "Τ", "Υ", "Φ", "Χ", "Ψ", "Ω"}:
                return last_page
        if section_kind == "crosswalk_index" and (CROSSWALK_TITLE_RE.search(text) or CROSSWALK_INDEX_RE.search(text) or AUTHOR_HEADING_RE.fullmatch(text.rstrip("."))):
            return last_page
        refs_local, page_hints, last_page_local = parse_refs(text, last_page)
        if last_page_local is not None:
            last_page = last_page_local
        lemma_raw = clean_lemma(text, refs_local, section_kind)
        if section_kind == "crosswalk_index" and lemma_raw.upper().startswith("INDEX VERBORUM"):
            return last_page
        if section_kind == "analytic_subject" and lemma_raw.upper().startswith("INDEX SCRIPTORUM"):
            return last_page
        if not lemma_raw:
            lemma_raw = text
        entry_kind = choose_entry_kind(section_kind, text, refs_local)
        inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else source_file
        if target_file_best is None:
            target_file_best = source_file
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
        raw_json = {
            "source_file": source_file,
            "section_kind": section_kind,
            "page_hints": page_hints,
        }
        if current_letter is not None and section_kind in {"analytic_subject", "foreign_terms"}:
            raw_json["letter_group"] = current_letter
        if section_kind == "crosswalk_index":
            raw_json["section_note"] = "Locus index under Ambiguorum Liber."
        entry = {
            "entry_key": entry_key,
            "section_key": section_key(section_kind),
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize_sort(lemma_raw),
            "lemma_sort": normalize_sort(lemma_raw),
            "entry_raw": text,
            "context_raw": text if len(text) < 500 else text[:500],
            "heading_letter": current_letter if section_kind in {"analytic_subject", "foreign_terms"} else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": section_start_file(section_kind, files_by_num),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.86 if refs_local else 0.74,
            "raw_json": raw_json,
        }
        entries.append(entry)
        if refs_local:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": unique_preserve_order([lemma_raw, text, lemma_raw.split(",", 1)[0]]),
                    "page_hints": [str(p) for p in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": text,
                }
            )
        for ref in refs_local:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref["ref_order"],
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": page_map.get(ref["page_ref_int"]),
                    "target_file_probability": 0.9 if page_map.get(ref["page_ref_int"]) else None,
                    "section_start_file": section_start_file(section_kind, files_by_num),
                    "editorial_anchor_file": source_file,
                    "confidence": 0.88 if page_map.get(ref["page_ref_int"]) else 0.7,
                    "raw_json": {},
                }
            )
        return last_page

    def emit_buffer(buffer: str, source_file: str, section_kind: str, current_letter: str | None, current_node_key: str | None, last_page: int | None) -> int | None:
        if not buffer:
            return last_page
        return emit_entry(buffer, source_file, section_kind, current_letter, current_node_key, last_page)

    # Section 1: analytic index
    analytic_started = False
    analytic_body_started = False
    current_letter: str | None = None
    current_node_key: str | None = None
    last_page: int | None = None
    buffer = ""
    buffer_file = ""
    for seq in range(814, 821):
        path = files_by_num[seq]
        for raw_line in parse_lines(path):
            if not analytic_started:
                if ANALYTIC_HEADING_RE.search(raw_line):
                    analytic_started = True
                continue
            line = strip_section_prefix(raw_line, "analytic_subject", seq)
            if not line:
                continue
            if ANALYTIC_SUBTITLE_RE.search(line) or line.startswith("(A col.") or line.startswith("Revocatur") or SECTION_LINE_SKIP_RE.search(line):
                continue
            if not analytic_body_started:
                if PAGE_RE.search(line) and not re.match(r"^[A-Z]\s+[IVXLC]+\s+Cor\.", line):
                    analytic_body_started = True
                else:
                    continue
            if is_letter_line(line):
                last_page = emit_buffer(buffer, buffer_file, "analytic_subject", current_letter, current_node_key, last_page)
                buffer = ""
                current_letter = line.strip(" .")
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key("analytic_subject"),
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter,
                        "label_sort": current_letter,
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path), "section_kind": "analytic_subject"},
                    }
                )
                continue
            if current_letter is None:
                first_alpha = next((ch for ch in line if ch.isalpha()), None)
                if first_alpha is None:
                    continue
                current_letter = first_alpha.upper()
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key("analytic_subject"),
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter,
                        "label_sort": current_letter,
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path), "section_kind": "analytic_subject"},
                    }
                )
            if buffer and not PAGE_RE.search(buffer) and (line[:1].islower() or line[:1].isdigit() or line.startswith((",", ".", ";", "—", "-", "·"))):
                buffer = f"{buffer} {line}"
                continue
            last_page = emit_buffer(buffer, buffer_file, "analytic_subject", current_letter, current_node_key, last_page)
            for fragment in split_entry_fragments(line, "analytic_subject"):
                last_page = emit_entry(fragment, str(path), "analytic_subject", current_letter, current_node_key, last_page)
            buffer = ""
            buffer_file = ""
    last_page = emit_buffer(buffer, buffer_file, "analytic_subject", current_letter, current_node_key, last_page)

    # Section 2: crosswalk index
    crosswalk_started = False
    current_letter = None
    current_node_key = None
    last_page = None
    buffer = ""
    buffer_file = ""
    path = files_by_num[821]
    for raw_line in parse_lines(path):
        if not crosswalk_started:
            if CROSSWALK_TITLE_RE.search(raw_line) or CROSSWALK_INDEX_RE.search(raw_line):
                crosswalk_started = True
            continue
        line = strip_section_prefix(raw_line, "crosswalk_index", 821)
        if not line:
            continue
        if AUTHOR_HEADING_RE.fullmatch(line.rstrip(".")):
            last_page = emit_buffer(buffer, buffer_file, "crosswalk_index", current_letter, current_node_key, last_page)
            buffer = ""
            node_order += 1
            current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_node_key,
                    "section_key": section_key("crosswalk_index"),
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "heading_group",
                    "label_raw": line.rstrip("."),
                    "label_norm": normalize_space(line.rstrip(".")).lower(),
                    "label_sort": normalize_sort(line.rstrip(".")),
                    "node_level": 1,
                    "confidence": 0.97,
                    "raw_json": {"source_file": str(path), "section_kind": "crosswalk_index"},
                }
            )
            continue
        if buffer and not PAGE_RE.search(buffer) and (line[:1].islower() or line[:1].isdigit() or line.startswith((",", ".", ";", "—", "-", "·"))):
            buffer = f"{buffer} {line}"
            continue
        last_page = emit_buffer(buffer, buffer_file, "crosswalk_index", current_letter, current_node_key, last_page)
        for fragment in split_entry_fragments(line, "crosswalk_index"):
            last_page = emit_entry(fragment, str(path), "crosswalk_index", current_letter, current_node_key, last_page)
        buffer = ""
        buffer_file = ""
    last_page = emit_buffer(buffer, buffer_file, "crosswalk_index", current_letter, current_node_key, last_page)

    # Section 3: Greek foreign terms index
    foreign_started = False
    current_letter = None
    current_node_key = None
    last_page = None
    buffer = ""
    buffer_file = ""
    for seq in range(822, 830):
        path = files_by_num[seq]
        for raw_line in parse_lines(path):
            if not foreign_started:
                if VERBORUM_HEADING_RE.search(raw_line):
                    foreign_started = True
                continue
            line = strip_section_prefix(raw_line, "foreign_terms", seq)
            if not line:
                continue
            if VERBORUM_HEADING_RE.search(line) or SECTION_LINE_SKIP_RE.search(line):
                continue
            if is_letter_line(line):
                last_page = emit_buffer(buffer, buffer_file, "foreign_terms", current_letter, current_node_key, last_page)
                buffer = ""
                current_letter = line.strip(" .")
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key("foreign_terms"),
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter,
                        "label_sort": current_letter,
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path), "section_kind": "foreign_terms"},
                    }
                )
                continue
            if buffer and not PAGE_RE.search(buffer) and (line[:1].islower() or line[:1].isdigit() or line.startswith((",", ".", ";", "—", "-", "·"))):
                buffer = f"{buffer} {line}"
                continue
            last_page = emit_buffer(buffer, buffer_file, "foreign_terms", current_letter, current_node_key, last_page)
            for fragment in split_entry_fragments(line, "foreign_terms"):
                last_page = emit_entry(fragment, str(path), "foreign_terms", current_letter, current_node_key, last_page)
            buffer = ""
            buffer_file = ""
    last_page = emit_buffer(buffer, buffer_file, "foreign_terms", current_letter, current_node_key, last_page)

    # Optional editorial-closure contents page.
    ordo_started = False
    current_letter = None
    current_node_key = None
    last_page = None
    buffer = ""
    buffer_file = ""
    for seq in range(830, 832):
        path = files_by_num[seq]
        for raw_line in parse_lines(path):
            if not ordo_started:
                if ORDO_HEADING_RE.search(raw_line):
                    ordo_started = True
                continue
            line = strip_section_prefix(raw_line, "ordo_rerum", seq)
            if not line:
                continue
            if ORDO_HEADING_RE.search(line) or SECTION_LINE_SKIP_RE.search(line):
                continue
            if buffer and not PAGE_RE.search(buffer) and (line[:1].islower() or line[:1].isdigit() or line.startswith((",", ".", ";", "—", "-", "·"))):
                buffer = f"{buffer} {line}"
                continue
            last_page = emit_buffer(buffer, buffer_file, "ordo_rerum", current_letter, current_node_key, last_page)
            for fragment in split_entry_fragments(line, "ordo_rerum"):
                last_page = emit_entry(fragment, str(path), "ordo_rerum", current_letter, current_node_key, last_page)
            buffer = ""
            buffer_file = ""
    last_page = emit_buffer(buffer, buffer_file, "ordo_rerum", current_letter, current_node_key, last_page)

    analytic_cut = next(
        (
            idx
            for idx, item in enumerate(entries)
            if item["section_key"] == section_key("analytic_subject") and item["lemma_raw"].startswith("Abrah")
        ),
        None,
    )
    if analytic_cut is not None and analytic_cut > 0:
        removed_entry_keys = {item["entry_key"] for item in entries[:analytic_cut] if item["section_key"] == section_key("analytic_subject")}
        entries = [item for item in entries if item["entry_key"] not in removed_entry_keys]
        refs = [item for item in refs if item["entry_key"] not in removed_entry_keys]

    if not helper_entries:
        helper_entries = [
            {
                "entry_id": item["entry_key"],
                "lemma_raw": item["lemma_raw"],
                "query_names": unique_preserve_order([item["lemma_raw"], item["entry_raw"]]),
                "page_hints": [str(item["inferred_printed_page"])] if item["inferred_printed_page"] else [],
                "page_hint_ints": [item["inferred_printed_page"]] if item["inferred_printed_page"] else [],
                "context_raw": item["entry_raw"],
            }
            for item in entries[:8]
        ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Alphabetical, locorum, and foreign-term index lines were recovered from the OCR tail.",
            "evidence_files": [str(files_by_num[814]), str(files_by_num[821]), str(files_by_num[822]), str(files_by_num[831])],
        },
        "notes": [
            "PG091 contains an analytical subject index, a locorum crosswalk for Ambiguorum Liber, and a Greek foreign-terms index in the tail.",
            "The final ORDO RERUM contents page is structurally present in the OCR tail and is expanded conservatively as editorial-closure heading groups.",
        ],
    }, helper_entries


def run_helper() -> dict[str, Any]:
    if not HELPER_REQUEST_JSON.exists():
        return {}
    cmd = [
        sys.executable,
        str(SCRIPT_TARGET_LOCATOR),
        "--input",
        str(HELPER_REQUEST_JSON),
        "--output",
        str(HELPER_OUTPUT_JSON),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(HELPER_OUTPUT_JSON, {})


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_entry = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        top_candidates = helper.get("top_candidates") or helper.get("candidates") or []
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": top_candidates[:3],
        }
        if helper.get("best_candidate"):
            raw_json["helper"]["best_candidate"] = helper["best_candidate"]
        if isinstance(top_candidates, list) and top_candidates:
            best = top_candidates[0]
            if isinstance(best, dict) and best.get("file"):
                entry["target_file_best"] = best["file"]


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build and validate PG091 alphabetical payload",
        "completed": [
            "read OCR tail and confirmed analytical, crosswalk, and foreign-terms sections",
            "checked helper contract and section taxonomy",
        ],
        "pending": [
            "run helper on sampled ambiguous entries",
            "write final payload and validate schema fields",
        ],
        "blocked": [],
        "notes": [
            "Treat INDEX SCRIPTORUM as a structural note rather than a standalone lemma section.",
            "The tail ORDO RERUM page is editorial closure and is not expanded in this pass.",
        ],
    }
    write_json(TODO_JSON, todo)

    payload, helper_entries = build_payload()
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries[:12],
    }
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output = run_helper()
    if helper_output:
        attach_helper(payload["entries"], helper_output)
        for ref in payload["refs"]:
            entry = next((item for item in payload["entries"] if item["entry_key"] == ref["entry_key"]), None)
            if not entry:
                continue
            helper = (entry.get("raw_json") or {}).get("helper")
            if helper and helper.get("top_candidates"):
                best = helper["top_candidates"][0]
                if isinstance(best, dict) and best.get("file"):
                    ref["target_file"] = best["file"]
                    ref["target_file_probability"] = best.get("probability")

    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(INTERMEDIATE_DIR / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"], "updated_at": payload["generated_at"]})

    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo["updated_at"] = now_iso()
    todo["completed"].append("final payload written")
    todo["pending"] = []
    write_json(TODO_JSON, todo)


if __name__ == "__main__":
    main()

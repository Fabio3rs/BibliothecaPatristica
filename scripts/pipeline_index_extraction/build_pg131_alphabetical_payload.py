#!/usr/bin/env python3
"""Usage: build the PG131 alphabetical payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pg131_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG131/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG131_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG131_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG131 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG131_alphabetical_indices.json
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
VOLUME_ID = "PG131"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 131"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG131/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG131_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG131_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG131_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG131"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:author_index:002"
SECTION_3_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:003"

SECTION_1_HEADING = "INDEX RERUM MEMORABILIUM."
SECTION_2_HEADING = "INDEX AUCTORUM QUI IN NOTIS LAUDANTUR."
SECTION_3_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

SECTION_1_REASON = (
    "Alphabetical subject index for the Alexiad tail, with Latin and Greek letter dividers, "
    "page-locator entries, and a running title that alternates between INDEX RERUM MEMORABILIUM "
    "and INDICES IN ALEXIADEM."
)
SECTION_2_REASON = (
    "Alphabetical author/work index for the notes to Anna Comnena; the OCR surfaces a long title "
    "('INDEX AUCTORUM QUI PASSIM IN NOTIS AD ANNAM COMNENAM LAUDANTUR ET ILLUSTRANTUR') and the "
    "shorter printed heading 'INDEX AUCTORUM QUI IN NOTIS LAUDANTUR.'"
)
SECTION_3_REASON = (
    "Editorial closing contents table headed ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR., distinct from "
    "the alphabetical indexes."
)

SECTION_SWITCH_RE = re.compile(
    r"^(?:INDEX AUCTORUM(?:\s+QUI\s+IN\s+NOTIS\s+LAUDANTUR\.)?|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?)$",
    re.IGNORECASE,
)
TITLE_NOISE_RE = re.compile(
    r"^(?:INDICES IN ALEXIADEM\.?|INDEX RERUM MEMORABILIUM\.?|INDEX RERUM\.?|INDEX AUCTORUM\.?|"
    r"INDEX AUCTORUM QUI PASSIM IN NOTIS AD ANNAM COMNENAM LAUDANTUR ET ILLUSTRANTUR\.?|"
    r"ORDO RERUM QU[ÆAE] IN HOC TOMO CONTINENTUR\.?|ORDO RERUM\.?)$",
    re.IGNORECASE,
)
LETTER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]\.?$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–]\s*(\d{1,4})(?:\s*([ab]))?")
PAGE_COL_RE = re.compile(r"(?<!\d)(\d{1,4})\s*([ab])\b")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.)\b", re.IGNORECASE)
NO_LOCATOR_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?)\b", re.IGNORECASE)
BOUNDARY_RE = re.compile(
    r"(?<!\b[A-Z])(?<=[.;])\s+(?=(?:[A-ZÆŒΑ-Ω]))|(?<=\d)\s+(?=[A-ZÆŒΑ-Ω][a-z])"
)
LEADING_EDITORIAL_REPLACEMENTS = [
    re.compile(r"^INDICES IN ALEXIADEM ANNAE COMMENAE\.\s*Revocatur Lector ad numeros crassiores in textu expressos, qui paginas editionis Regiæ repræsentant\.\s*", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s+INDICES IN ALEXIADEM\.\s+\d{3,4}\s*", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s+INDEX RERUM(?: MEMORABILIUM)?\.\s+\d{3,4}\s*", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s+INDEX GEOGRAPHICUS\.\s+\d{3,4}\s+INDEX GEOGRAPHICUS AD ANN[ÆAE]\s+ALEXIADEM\.\s*", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s+INDEX GR[ÆAE]CARUM VOCUM IN NOTIS\.\s+\d{3,4}\s*", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s+INDEX AUCTORUM(?:\s+QUI\s+PASSIM\s+IN\s+NOTIS\s+AD\s+ANNAM\s+COMNENAM\s+LAUDANTUR\s+ET\s+ILLUSTRANTUR\.)?\s+\d{3,4}\s*", re.IGNORECASE),
    re.compile(r"^INDEX AUCTORUM(?:\s+QUI\s+IN\s+NOTIS\s+LAUDANTUR\.)?\s*", re.IGNORECASE),
    re.compile(r"^INDEX AUCTORUM QUI PASSIM IN NOTIS AD ANNAM COMNENAM LAUDANTUR ET ILLUSTRANTUR\.\s*Vide numeros grandiores in Notis expressos\.\s*", re.IGNORECASE),
    re.compile(r"^INDEX GEOGRAPHICUS(?:\s+AD\s+ANN[ÆAE]\s+ALEXIADEM\.)?\s*", re.IGNORECASE),
    re.compile(r"^INDEX GR[ÆAE]CARUM VOCUM IN NOTIS\.?\s*", re.IGNORECASE),
    re.compile(r"^INDICES IN ALEXIADEM\.\s*", re.IGNORECASE),
    re.compile(r"^INDEX RERUM(?: MEMORABILIUM)?\.\s*", re.IGNORECASE),
    re.compile(r"^ORDO RERUM QU[ÆAE] IN HOC TOMO CONTINENTUR\.\s*", re.IGNORECASE),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
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


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_files(source_root: Path) -> list[Path]:
    selected: list[Path] = []
    for path in sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1))):
        seq = int(re.search(r"-(\d+)\.txt$", path.name).group(1))
        if 668 <= seq <= 696:
            selected.append(path)
    return selected


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_page_text(path: Path) -> dict[str, str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "header_text": normalize(parsed.get("header_text") or "") or "",
        "body_text": normalize(parsed.get("body_text") or "") or "",
        "footer_text": normalize(parsed.get("footer_text") or "") or "",
        "notes_text": normalize(parsed.get("notes_text") or "") or "",
        "all_text": normalize(parsed.get("all_text") or "") or "",
    }


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        text = normalize(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        page_text = extract_page_text(path)
        for blob in (page_text["header_text"], page_text["body_text"][:160]):
            for match in PAGE_RE.finditer(blob):
                value = int(match.group(1))
                if 0 < value < 10000 and value not in mapping:
                    mapping[value] = str(path)
    return mapping


def target_for_page(page: int | None, page_map: dict[int, str], source_root: Path) -> str | None:
    if page is None:
        return None
    return page_map.get(page)


def query_names(lemma_raw: str, entry_raw: str) -> list[str]:
    candidates = [normalize(lemma_raw), normalize(entry_raw).split(",", 1)[0], normalize(entry_raw).split(";", 1)[0]]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = normalize(item)
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:5]


def infer_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if NO_LOCATOR_RE.search(text):
        return text.split("Vide", 1)[0].split("vide", 1)[0].strip(" .;:") or None
    if "," in text:
        lemma = text.split(",", 1)[0]
    else:
        lemma = text
    lemma = re.sub(r"\s+\d{1,4}(?:\s*[-–]\s*\d{1,4})?(?:\s*[ab])?(?:\s*(?:seqq\.|seq\.))?\s*$", "", lemma, flags=re.IGNORECASE)
    return lemma.strip(" .;:") or None


def infer_entry_kind(entry_raw: str) -> str:
    text = normalize(entry_raw) or ""
    if NO_LOCATOR_RE.search(text):
        return "cross_reference"
    if re.match(r"^(?:Cap\.|Caput|Titulus|LIBER)\b", text, flags=re.IGNORECASE):
        return "heading_group"
    return "lemma"


def extract_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    text = normalize(entry_raw) or ""
    working = text

    if IBID_RE.search(working) and last_page is not None:
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
        working = IBID_RE.sub(" ", working)

    for match in PAGE_RANGE_RE.finditer(working):
        start = int(match.group(1))
        end = int(match.group(2))
        col = match.group(3)
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": col,
                "line_ref_raw": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end),
            }
        )
        last_page = end
        working = working.replace(ref_raw, " ", 1)

    for match in PAGE_COL_RE.finditer(working):
        page = int(match.group(1))
        col = match.group(2)
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page
        working = working.replace(ref_raw, " ", 1)

    for match in PAGE_RE.finditer(working):
        page = int(match.group(1))
        if page < 5 and not re.search(r"\b\d{3,4}\b", text):
            continue
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page

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


def split_fragments(text: str) -> list[str]:
    cleaned = normalize(text) or ""
    if not cleaned:
        return []
    parts = [part.strip() for part in BOUNDARY_RE.split(cleaned) if part.strip()]
    return parts or [cleaned]


def strip_leading_editorial(text: str) -> str:
    cleaned = normalize(text) or ""
    changed = True
    while changed and cleaned:
        changed = False
        for pattern in LEADING_EDITORIAL_REPLACEMENTS:
            new_value = pattern.sub("", cleaned, count=1)
            if new_value != cleaned:
                cleaned = normalize(new_value) or ""
                changed = True
                break
    return cleaned


def inferred_letter_from_entry(lemma_raw: str | None) -> str | None:
    if not lemma_raw:
        return None
    for ch in lemma_raw:
        if ch.isalpha():
            return ch.upper()
    return None


def section_meta(files: list[Path]) -> list[dict[str, Any]]:
    section1_files = [p for p in files if 668 <= file_seq(p) <= 692]
    section2_files = [p for p in files if 692 <= file_seq(p) <= 695]
    section3_files = [p for p in files if 696 <= file_seq(p) <= 696]
    return [
        {
            "section_key": SECTION_1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_1_HEADING,
            "heading_norm": sort_norm(SECTION_1_HEADING),
            "heading_letter": None,
            "page_start": 1253,
            "page_end": 1301,
            "file_start": str(section1_files[0]) if section1_files else None,
            "file_end": str(section1_files[-1]) if section1_files else None,
            "confidence": 0.94,
            "raw_json": {
                "section_kind_reason": SECTION_1_REASON,
                "evidence_files": [str(section1_files[0]) if section1_files else None, str(section1_files[-1]) if section1_files else None],
                "alternate_heading_raw": "INDICES IN ALEXIADEM.",
            },
        },
        {
            "section_key": SECTION_2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "author_index",
            "heading_raw": SECTION_2_HEADING,
            "heading_norm": sort_norm(SECTION_2_HEADING),
            "heading_letter": None,
            "page_start": 1302,
            "page_end": 1306,
            "file_start": str(section2_files[0]) if section2_files else None,
            "file_end": str(section2_files[-1]) if section2_files else None,
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": SECTION_2_REASON,
                "evidence_files": [str(section2_files[0]) if section2_files else None, str(section2_files[-1]) if section2_files else None],
                "alternate_heading_raw": "INDEX AUCTORUM QUI PASSIM IN NOTIS AD ANNAM COMNENAM LAUDANTUR ET ILLUSTRANTUR.",
            },
        },
        {
            "section_key": SECTION_3_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_3_HEADING,
            "heading_norm": sort_norm(SECTION_3_HEADING),
            "heading_letter": None,
            "page_start": 1307,
            "page_end": 1308,
            "file_start": str(section3_files[0]) if section3_files else None,
            "file_end": str(section3_files[-1]) if section3_files else None,
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": SECTION_3_REASON,
                "evidence_files": [str(section3_files[0]) if section3_files else None, str(section3_files[-1]) if section3_files else None],
            },
        },
    ]


def parse_ocr_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in extract_lines(path):
        text = normalize(raw)
        if not text or FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_chunks(files: list[Path]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_section = SECTION_1_KEY
    current_letter: str | None = None
    current_file: Path | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines, current_file
        if not current_lines or current_file is None:
            current_lines = []
            return
        text = " ".join(current_lines)
        text = re.sub(r"\s+", " ", text).strip()
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
        current_file = path
        for line in parse_ocr_lines(path):
            if SECTION_SWITCH_RE.fullmatch(line):
                flush()
                if re.match(r"^INDEX AUCTORUM", line, flags=re.IGNORECASE):
                    current_section = SECTION_2_KEY
                    current_letter = None
                elif re.match(r"^ORDO RERUM", line, flags=re.IGNORECASE):
                    current_section = SECTION_3_KEY
                    current_letter = None
                continue
            if TITLE_NOISE_RE.fullmatch(line):
                continue
            if LETTER_RE.fullmatch(line) and len(line.strip(".").strip()) == 1:
                flush()
                current_letter = line.strip(".").strip()
                continue
            if current_section == SECTION_3_KEY:
                # Keep ORDO RERUM lines out of the alphabetical chunks.
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


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit(f"No OCR files found under {source_root}")

    page_map = build_page_map(files)
    sections = section_meta(files)
    chunks = build_chunks(files)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_request_entries: list[dict[str, Any]] = []
    section_entry_counts = {SECTION_1_KEY: 0, SECTION_2_KEY: 0, SECTION_3_KEY: 0}
    section_node_counts = {SECTION_1_KEY: 0, SECTION_2_KEY: 0, SECTION_3_KEY: 0}
    section_start_file = {
        SECTION_1_KEY: sections[0]["file_start"],
        SECTION_2_KEY: sections[1]["file_start"],
        SECTION_3_KEY: sections[2]["file_start"],
    }
    letter_nodes_by_section: dict[tuple[str, str], str] = {}
    last_page_by_section_letter: dict[tuple[str, str | None], int | None] = {}
    carry_by_section_letter: dict[tuple[str, str | None], str] = {}
    helper_by_id: dict[str, Any] = {}

    for chunk_index, chunk in enumerate(chunks, start=1):
        section_key = chunk["section_key"]
        letter = chunk["heading_letter"]
        source_file = chunk["source_file"]
        key = (section_key, letter)
        text = strip_leading_editorial(chunk["text"])
        if carry_by_section_letter.get(key):
            text = carry_by_section_letter[key] + " " + text
        fragments = split_fragments(text)
        if not fragments:
            continue

        carry_by_section_letter[key] = ""
        should_carry = False
        if text and not re.search(r"[.!?]\s*$", text):
            should_carry = True

        for frag_index, fragment in enumerate(fragments, start=1):
            is_last = frag_index == len(fragments)
            fragment_text = normalize(fragment) or ""
            if not fragment_text:
                continue
            if is_last and should_carry and not re.search(r"[.!?]\s*$", fragment_text):
                carry_by_section_letter[key] = fragment_text
                continue

            fragment_letter = letter or inferred_letter_from_entry(infer_lemma(fragment_text))
            node_key = None
            if fragment_letter:
                node_lookup_key = (section_key, fragment_letter)
                node_key = letter_nodes_by_section.get(node_lookup_key)
                if node_key is None:
                    section_node_counts[section_key] += 1
                    node_key = f"{VOLUME_ID}:node:{section_node_counts[section_key]:03d}:{sort_norm(fragment_letter) or fragment_letter.lower()}"
                    letter_nodes_by_section[node_lookup_key] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": section_node_counts[section_key],
                            "node_kind": "letter_group",
                            "label_raw": fragment_letter,
                            "label_norm": fragment_letter.lower(),
                            "label_sort": sort_norm(fragment_letter) or fragment_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {
                                "source_file": source_file,
                                "section_kind": next(sec["section_kind"] for sec in sections if sec["section_key"] == section_key),
                            },
                        }
                    )

            entry_order = section_entry_counts[section_key] + 1
            section_entry_counts[section_key] = entry_order
            entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
            lemma_raw = infer_lemma(fragment_text)
            entry_kind = infer_entry_kind(fragment_text)
            refs_for_entry, last_page = extract_refs(fragment_text, last_page_by_section_letter.get(key))
            if last_page is not None:
                last_page_by_section_letter[key] = last_page
            page_hints = [ref["page_ref_int"] for ref in refs_for_entry if ref.get("page_ref_int") is not None]
            inferred_printed_page = page_hints[0] if page_hints else None
            target_file_best = None
            if page_hints:
                for page in page_hints:
                    target_file_best = target_for_page(page, page_map, source_root)
                    if target_file_best:
                        break
            if target_file_best is None:
                target_file_best = source_file

            entry_parent_node = letter_nodes_by_section.get(key)
            entry_payload = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": fragment_text,
                "context_raw": None,
                "heading_letter": fragment_letter,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": section_start_file[section_key],
                "editorial_anchor_file": source_file,
                "target_file_best": target_file_best,
                "confidence": 0.88 if page_hints else 0.72,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": next(sec["section_kind"] for sec in sections if sec["section_key"] == section_key),
                    "section_kind_reason": next(sec["raw_json"]["section_kind_reason"] for sec in sections if sec["section_key"] == section_key),
                    "page_hints": page_hints,
                    "chunk_index": chunk_index,
                },
            }
            entries.append(entry_payload)

            if page_hints:
                needs_helper = (
                    len(page_hints) > 1
                    or target_file_best is None
                    or NO_LOCATOR_RE.search(fragment_text)
                    or re.search(r"\b(?:et seq\.|seqq\.|seq\.)\b", fragment_text, re.IGNORECASE)
                )
                if needs_helper and len(helper_request_entries) < 60:
                    helper_request_entries.append(
                        {
                            "entry_id": entry_key,
                            "lemma_raw": lemma_raw or fragment_text,
                            "query_names": query_names(lemma_raw or fragment_text, fragment_text),
                            "page_hints": [str(page) for page in page_hints[:3]],
                            "page_hint_ints": page_hints[:3],
                            "context_raw": fragment_text[:180],
                        }
                    )

            for ref_order, ref in enumerate(refs_for_entry, start=1):
                target_file = target_for_page(ref["page_ref_int"], page_map, source_root)
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
                        "target_file": target_file,
                        "target_file_probability": 0.98 if target_file else None,
                        "section_start_file": section_start_file[section_key],
                        "editorial_anchor_file": source_file,
                        "confidence": 0.9 if target_file else 0.68,
                        "raw_json": {
                            "source_file": source_file,
                            "locator_method": "header_page_map" if target_file else "unresolved",
                        },
                    }
                )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    write_json(helper_request_json, helper_request)
    if helper_request_entries:
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
        helper_output = read_json(helper_output_json, {"status": "empty", "entries": []})
    else:
        helper_output = {"status": "empty", "entries": []}
        write_json(helper_output_json, helper_output)

    for item in helper_output.get("entries", []):
        if isinstance(item, dict) and item.get("entry_id"):
            helper_by_id[item["entry_id"]] = item

    entry_map = {entry["entry_key"]: entry for entry in entries}
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        compact = helper_compact(helper)
        entry.setdefault("raw_json", {})["helper"] = compact
        best = (helper.get("best_candidate") or {})
        if best.get("file"):
            if not entry.get("target_file_best") or entry.get("target_file_best") == entry.get("editorial_anchor_file"):
                entry["target_file_best"] = best.get("file")
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        entry = entry_map.get(ref["entry_key"])
        if not entry:
            continue
        helper = helper_by_id.get(ref["entry_key"])
        if helper:
            best = (helper.get("best_candidate") or {})
            if best.get("file") and ref["target_file"] is None:
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": (
            "Recovered the two alphabetical index sections plus the closing ORDO RERUM from the OCR tail; "
            "the final section is editorial and intentionally left without alphabetical entries."
        ),
        "evidence_files": [sections[0]["file_start"], sections[1]["file_start"], sections[2]["file_start"]],
    }

    notes = [
        "The OCR tail contains a section transition inside file 692: the subject index gives way to the author index in the same source file.",
        "Letter-group nodes were created from isolated A-Z and Greek divider lines; entries inherit the most recent divider in reading order.",
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
            "current_focus": "Finalize PG131 alphabetical payload and verify section boundaries around file 692.",
            "completed": [
                "identified subject index section",
                "identified author index section",
                "identified ORDO RERUM closing table",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page numbers and cited references.",
                "Preserve helper evidence only where it affects target selection.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG131 alphabetical payload.")
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

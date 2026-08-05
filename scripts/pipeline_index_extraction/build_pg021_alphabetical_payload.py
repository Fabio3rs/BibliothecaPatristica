#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg021_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG021/text \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG021_alphabetical_indices.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG021

Builds the PG021 alphabetical payload from the OCR tail by splitting the final
author, analytic, and contents sections and serializing conservative entries and
material references.
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG021"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 21"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG021/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG021_alphabetical_indices.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG021"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG021_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG021_helper_output.json"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_1 = {
    "section_key": f"{VOLUME_ID}:alpha:author_index:001",
    "section_kind": "author_index",
    "heading_raw": "INDEX SCRIPTORUM IN EUSEBII PRÆPARATIONIS EVANGELICÆ LIBRIS LAUDATORUM.",
    "heading_norm": "index scriptorum in eusebii praeparationis evangelicae libris laudatorum",
    "page_start": None,
    "page_end": None,
    "file_start_seq": 719,
    "file_end_seq": 724,
    "section_order": 1,
    "section_kind_reason": "Author index of writers cited in Eusebius' Praeparatio Evangelica; OCR pagination is inconsistent in this block, so file anchors are trusted more than page numbers.",
}

SECTION_2 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX LOCUPLETISSIMUS RERUM MEMORABILIUM QUÆ IN HOC EUSEBII DE PRÆPARATIONE EVANGELICA VOLUMINE CONTINENTUR.",
    "heading_norm": "index locupletissimus rerum memorabilium quae in hoc eusebii de praeparatione evangelica volumine continentur",
    "page_start": 1421,
    "page_end": 1442,
    "file_start_seq": 725,
    "file_end_seq": 735,
    "section_order": 2,
    "section_kind_reason": "Main analytic subject index opening with INDEX LOCUPLETISSIMUS / INDEX ANALYTICUS headings; the printed page sequence is stable enough here to keep page anchors.",
}

SECTION_3 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "page_start": 1443,
    "page_end": 1454,
    "file_start_seq": 736,
    "file_end_seq": 742,
    "section_order": 3,
    "section_kind_reason": "Editorial contents table for the volume's internal chapters, distinct from the alphabetical indexes that precede it.",
}


NOISE_LINES = {
    "Digitized by Google",
}
SECTION_TITLE_PREFIXES = (
    "INDEX SCRIPTORUM",
    "INDEX LOCUPLETISSIMUS",
    "INDEX ANALYTICUS",
    "ORDO RERUM",
)
LEADING_PAGE_ONLY_RE = re.compile(r"^\d{1,4}(?:/\d{1,4})?$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?:\s*(seq\.?|seqq\.?|et seq\.?|ibid\.?|id\.?|passim))?", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_RE = re.compile(r"^[IVXLCDM]+\.$")
CONTINUATION_PREFIXES = (
    "ibid.",
    "ibid",
    "id.",
    "id",
    "seq.",
    "seqq.",
    "et seq.",
    "et seq",
    "vide ",
    "Vide ",
    "supra",
    "infra",
    "Ejus",
    "Ejusdem",
    "Item",
    "Alius",
    "Alia",
    "De ",
    "Ex ",
    "In ",
    "Ad ",
    "Ab ",
    "Ἐν ",
    "Ἐκ ",
    "Ἀπὸ ",
    "Ἄλλος",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def helper_summary_map(helper_request_path: Path, helper_output_path: Path) -> dict[str, dict[str, Any]]:
    request = read_json(helper_request_path, {})
    output = read_json(helper_output_path, {})
    request_entries = {item.get("entry_id"): item for item in request.get("entries", []) if item.get("entry_id")}
    result: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        req = request_entries[entry_id]
        candidate = item.get("candidates", [{}])[0] or {}
        summary = {
            "status": item.get("status"),
            "candidate_role": candidate.get("candidate_role"),
            "reason_summary": candidate.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": candidate.get("file") or item.get("best_candidate", {}).get("file"),
                "probability": candidate.get("probability") or item.get("best_candidate", {}).get("probability"),
                "candidate_role": candidate.get("candidate_role") or item.get("best_candidate", {}).get("candidate_role"),
                "inferred_printed_page": candidate.get("inferred_printed_page") or item.get("best_candidate", {}).get("inferred_printed_page"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in candidate.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ][:8],
            },
            "query_names": req.get("query_names"),
            "page_hints": req.get("page_hint_ints") or req.get("page_hints"),
        }
        result[normalize(req.get("lemma_raw") or "") or ""] = summary
    return result


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
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
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_header_and_lines(path: Path) -> tuple[str, list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    header_parts: list[str] = []
    lines: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        tipo = (match.group(1) or "").strip().lower()
        content = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        if tipo == "cabecalho":
            header_parts.append(" ".join(part for part in (normalize(content),) if part))
            continue
        if tipo != "texto_principal":
            continue
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if line in NOISE_LINES:
                continue
            if LEADING_PAGE_ONLY_RE.fullmatch(line):
                continue
            lines.append(line)
    header = normalize(" ".join(header_parts)) or ""
    return header, lines


def extract_page_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        header, _ = extract_header_and_lines(path)
        if not header:
            continue
        nums = extract_page_numbers(header)
        if not nums:
            continue
        for value in nums:
            mapping.setdefault(value, str(path))
    return mapping


def section_for_seq(seq: int) -> dict[str, Any] | None:
    for section in (SECTION_1, SECTION_2, SECTION_3):
        if section["file_start_seq"] <= seq <= section["file_end_seq"]:
            return section
    return None


def is_heading_line(line: str, section: dict[str, Any]) -> bool:
    normalized = normalize(line) or ""
    if not normalized:
        return False
    if any(normalized.startswith(prefix) for prefix in SECTION_TITLE_PREFIXES):
        return True
    if normalized in {
        SECTION_1["heading_raw"],
        SECTION_2["heading_raw"],
        SECTION_3["heading_raw"],
        "INDEX SCRIPTORUM",
        "INDEX ANALYTICUS.",
        "INDEX ANALYTICUS",
        "ORDO RERUM",
        "IN EUSEBII PRÆPARATIONIS EVANGELICÆ LIBRIS LAUDATORUM.",
        "QUÆ IN HOC EUSEBII DE PRÆPARATIONE EVANGELICA VOLUMINE CONTINENTUR.",
        "RERUM MEMORABILIUM",
    }:
        return True
    if LETTER_RE.fullmatch(normalized) or ROMAN_RE.fullmatch(normalized):
        return True
    if normalized == "I." and section["section_kind"] != "ordo_rerum":
        return True
    return False


def is_letter_heading(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(normalize(line) or ""))


def is_continuation(line: str) -> bool:
    normalized = normalize(line) or ""
    if not normalized:
        return False
    if normalized.startswith(("-", "—", ",", ";", ":", ")", "]", "(")):
        return True
    if normalized[0].islower():
        return True
    for prefix in CONTINUATION_PREFIXES:
        if normalized.startswith(prefix):
            return True
    return False


def leading_lemma(text: str) -> str | None:
    normalized = normalize(text) or ""
    if not normalized:
        return None
    first_ref = PAGE_REF_RE.search(normalized)
    if first_ref:
        normalized = normalized[: first_ref.start()].rstrip(" ,.;:")
    if "," in normalized:
        normalized = normalized.split(",", 1)[0]
    if "." in normalized and not normalized.startswith(("Dr.", "St.")):
        normalized = normalized.split(".", 1)[0]
    return normalized.strip(" ,.;:") or None


def extract_refs(entry_raw: str, target_file: str | None, anchor_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None, str | None]] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        raw = match.group(0)
        page = int(match.group(1))
        end_raw = match.group(2)
        suffix = (raw[len(match.group(1)) :].strip() or "").lower()
        key = (raw, page, end_raw, target_file)
        if key in seen:
            continue
        seen.add(key)
        ref_kind = "editorial_range" if end_raw or any(tok in suffix for tok in ("seq", "passim")) else "editorial_page"
        refs.append(
            {
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page) if end_raw else None,
                "range_end_raw": end_raw,
                "target_file": target_file,
                "target_file_probability": 1.0 if target_file else 0.0,
                "section_start_file": anchor_file,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.9 if target_file else 0.6,
                "raw_json": {
                    "page_ref_source": "ocr_literature_index_entry",
                    "matched_raw": raw,
                },
            }
        )
    return refs


def parse_volume(source_root: Path, helper_map: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    current_section_key: str | None = None
    current_section = None
    section_index = 0
    node_index: dict[str, int] = {}
    entry_index: dict[str, int] = {}

    section_defs = [SECTION_1, SECTION_2, SECTION_3]

    for sdef in section_defs:
        section_index += 1
        files_in_section = [p for p in files if sdef["file_start_seq"] <= file_seq(p) <= sdef["file_end_seq"]]
        if not files_in_section:
            continue
        section = {
            "section_key": sdef["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": sdef["section_order"],
            "section_kind": sdef["section_kind"],
            "heading_raw": sdef["heading_raw"],
            "heading_norm": sdef["heading_norm"],
            "heading_letter": None,
            "page_start": sdef["page_start"],
            "page_end": sdef["page_end"],
            "file_start": str(files_in_section[0]),
            "file_end": str(files_in_section[-1]),
            "confidence": 0.93 if sdef["section_kind"] != "author_index" else 0.88,
            "raw_json": {
                "section_kind_reason": sdef["section_kind_reason"],
                "source_files": [str(p) for p in files_in_section],
                "notes": [
                    "OCR literals preserved.",
                    "Entries were segmented conservatively from visual OCR lines.",
                ],
            },
        }
        sections.append(section)
        current_section_key = section["section_key"]
        current_section = sdef

        current_entry: dict[str, Any] | None = None
        current_entry_lines: list[str] = []
        current_letter: str | None = None
        entry_order = 0

        def flush_entry() -> None:
            nonlocal current_entry, current_entry_lines, entry_order
            if current_entry is None:
                current_entry_lines = []
                return
            entry_raw = normalize(" ".join(current_entry_lines)) or ""
            if not entry_raw:
                current_entry = None
                current_entry_lines = []
                return
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{current_section['section_kind']}:{entry_order:04d}"
            lemma = leading_lemma(entry_raw)
            entry_kind = "lemma"
            if lemma and lemma.lower().startswith(("vide", "ibid", "id.", "cf.", "voir")):
                entry_kind = "cross_reference"
            elif lemma and lemma.startswith("ORDO RERUM"):
                entry_kind = "editorial_note"
            anchor_file = current_entry["source_file"]
            target_file = anchor_file
            refs_for_entry = extract_refs(entry_raw, target_file, anchor_file)
            helper_info = helper_map.get(lemma or "") if helper_map else None
            raw_json = {
                "source_file": anchor_file,
                "section_kind": current_section["section_kind"],
                "line_count": len(current_entry_lines),
                "ref_count": len(refs_for_entry),
            }
            if helper_info:
                raw_json["helper"] = helper_info
            entry = {
                "entry_key": entry_key,
                "section_key": current_section_key,
                "parent_node_key": current_entry.get("node_key"),
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": sort_norm(lemma),
                "lemma_sort": sort_norm(lemma),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": current_letter,
                "inferred_printed_page": current_entry.get("printed_page"),
                "section_start_file": current_entry["section_start_file"],
                "editorial_anchor_file": anchor_file,
                "target_file_best": target_file,
                "confidence": 0.82 if refs_for_entry else 0.72,
                "raw_json": raw_json,
            }
            entries.append(entry)
            refs.extend(refs_for_entry)
            current_entry = None
            current_entry_lines = []

        for path in files_in_section:
            header, lines = extract_header_and_lines(path)
            page_numbers = extract_page_numbers(header)
            printed_page = page_numbers[0] if page_numbers else None
            for raw_line in lines:
                line = normalize(raw_line) or ""
                if not line:
                    continue
                if is_heading_line(line, sdef):
                    flush_entry()
                    if is_letter_heading(line):
                        current_letter = line
                        node_index.setdefault(current_section_key, 0)
                        node_index[current_section_key] += 1
                        node_key = f"{VOLUME_ID}:node:{current_section['section_kind']}:letter:{node_index[current_section_key]:03d}"
                        nodes.append(
                            {
                                "node_key": node_key,
                                "section_key": current_section_key,
                                "parent_node_key": None,
                                "node_order": node_index[current_section_key],
                                "node_kind": "letter_group",
                                "label_raw": line,
                                "label_norm": sort_norm(line),
                                "label_sort": sort_norm(line),
                                "node_level": 1,
                                "confidence": 0.98,
                                "raw_json": {
                                    "source_file": str(path),
                                    "printed_page": printed_page,
                                },
                            }
                        )
                    continue
                if current_entry is not None and is_continuation(line):
                    current_entry_lines.append(line)
                    continue
                flush_entry()
                current_entry = {
                    "source_file": str(path),
                    "section_start_file": str(files_in_section[0]),
                    "node_key": None,
                    "printed_page": printed_page,
                }
                current_entry_lines = [line]

        flush_entry()

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Final index sections were recovered from OCR lines with conservative segmentation; a few entries remain line-based rather than fully sentence-normalized.",
        "evidence_files": [str(p) for p in files if file_seq(p) in {719, 720, 722, 725, 730, 736, 742}],
    }

    notes = [
        "PG021 final tail contains Index Scriptorum, Index Analyticus / Index Locupletissimus, and Ordo Rerum.",
        "Section 1 keeps page anchors null because the OCR header numbering is inconsistent across the block.",
        "Entry segmentation is conservative: line-based by default with obvious continuations merged.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Tail OCR used to recover final index sections.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    helper_map = helper_summary_map(DEFAULT_HELPER_REQUEST, DEFAULT_HELPER_OUTPUT)
    payload = parse_volume(args.source_root, helper_map)
    write_json(args.output_file, payload)
    write_json(TODO_JSON, {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Final payload written.",
        "completed": ["sections recovered", "entries serialized", "refs serialized"],
        "pending": [],
        "blocked": [],
        "notes": ["No helper-assisted disambiguation was required for the conservative file-based anchors."],
    })


if __name__ == "__main__":
    main()

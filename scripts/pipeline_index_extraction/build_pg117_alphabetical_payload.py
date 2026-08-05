#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg117_alphabetical_payload.py

Build the PG117 alphabetical payload from the OCR tail, write helper checkpoints,
and assemble the canonical JSON payload for data/alphabetical_index_payloads/PG117_alphabetical_indices.json.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG117"
COLLECTION = "PG"
SOURCE_ROOT = ROOT / "teste/PG117/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG117_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG117_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG117_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG117"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
MANIFEST_JSON = INTERMEDIATE_DIR / "manifest.json"
VOLUME_JSON = INTERMEDIATE_DIR / "volume.json"
SECTIONS_JSON = INTERMEDIATE_DIR / "sections.json"
NODES_JSON = INTERMEDIATE_DIR / "nodes.json"
ENTRIES_JSON = INTERMEDIATE_DIR / "entries.json"
REFS_JSON = INTERMEDIATE_DIR / "refs.json"
SCRIPTURE_REFS_JSON = INTERMEDIATE_DIR / "scripture_refs.json"
COVERAGE_JSON = INTERMEDIATE_DIR / "coverage.json"
NOTES_JSON = INTERMEDIATE_DIR / "notes.json"

AUTHOR_SEQ_START = 667
AUTHOR_SEQ_END = 767
ORDO_SEQ_START = 805
ORDO_SEQ_END = 807

NOISE_LINES = {
    "Digitized by Google",
}

HEADER_LINE_RE = re.compile(
    r"^(?:\d{1,4}\s+)?(?:INDEX SCRIPTORUM(?:\s+QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR\.)?"
    r"|ORDO RERUM(?:\s+QUÆ IN HOC TOMO CONTINENTUR\.)?|QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR\.)$",
    flags=re.I,
)
PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
LETTER_ONLY_RE = re.compile(r"^[A-D]$")
LEADING_GREEK_LETTER_RE = re.compile(r"^[Α-ΩΆΈΉΊΌΎΏ]")
LEADING_LATIN_LETTER_RE = re.compile(r"^[A-ZÆŒ]")
REFERENCE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_key(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def discover_files(seqs: list[int]) -> list[Path]:
    files: list[Path] = []
    for seq in seqs:
        matches = sorted(SOURCE_ROOT.glob(f"*-{seq:03d}.txt"))
        if not matches:
            continue
        files.append(matches[0])
    return files


def extract_text_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block_type, block_text in re.findall(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        if block_type != "texto_principal":
            continue
        for raw_line in block_text.splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            lines.append(line)
    return lines


def extract_lemma_raw(line: str) -> str | None:
    line = normalize(line)
    if not line:
        return None
    lowered = line.casefold()
    if lowered.startswith(("vid.", "vide ", "voir ", "cf.", "cf ")):
        return None
    if lowered.startswith("v. "):
        return None
    if line.startswith(("Ejus ", "Supra ", "Infra ")):
        return None
    if line.startswith(("C ", "D ", "B ", "A ")) and len(line) > 2 and line[2].isupper():
        line = line[2:].lstrip()
    cut_positions = [pos for pos in (line.find("."), line.find(","), line.find(";"), line.find(":")) if pos > 0]
    if cut_positions:
        line = line[: min(cut_positions)]
    return normalize(line) or None


def extract_ordo_lemma_raw(line: str) -> str | None:
    line = normalize(line)
    if not line:
        return None
    digit_pos = re.search(r"\d", line)
    if digit_pos:
        line = line[: digit_pos.start()].rstrip(" .;,:")
    return normalize(line) or None


def classify_entry_kind(line: str) -> str:
    lowered = line.casefold()
    if lowered.startswith(("vid.", "vide ", "voir ", "v. ")) or re.search(r"\bv\.\s", line):
        return "cross_reference"
    if lowered.startswith(("cf.", "cf ")):
        return "cross_reference"
    if line.startswith(("Ejus ", "Supra ", "Infra ")):
        return "cross_reference"
    return "lemma"


def line_starts_new_entry(line: str) -> bool:
    if not line:
        return False
    if LETTER_ONLY_RE.fullmatch(line):
        return False
    if PAGE_ONLY_RE.fullmatch(line):
        return False
    if HEADER_LINE_RE.fullmatch(line):
        return False
    first = line[0]
    if first.isdigit():
        return False
    if first.isupper() or LEADING_GREEK_LETTER_RE.match(line):
        return True
    if first in "ἈἉἘἙἸἩἜἌἍἎἏἪἬἮἨἼἾἺἽἻἊἋ":
        return True
    return False


def build_request() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "pg117_index_scriptorum_heading",
                "lemma_raw": "INDEX SCRIPTORUM",
                "query_names": [
                    "INDEX SCRIPTORUM",
                    "QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR",
                ],
                "page_hints": ["1215", "1216"],
                "page_hint_ints": [1215, 1216],
                "context_raw": "1215 INDEX SCRIPTORUM 1216",
            },
            {
                "entry_id": "pg117_abaris_entry",
                "lemma_raw": "Abaris",
                "query_names": [
                    "Abaris",
                    "Abaris, Scytha, Seuthae filius",
                ],
                "page_hints": ["1215"],
                "page_hint_ints": [1215],
                "context_raw": "Abaris, Scytha, Seuthæ filius, χρησμούς; scripsit σκυθικούς, oracula Scythica.",
            },
            {
                "entry_id": "pg117_ordo_rerum_heading",
                "lemma_raw": "ORDO RERUM",
                "query_names": [
                    "ORDO RERUM",
                    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
                ],
                "page_hints": ["1491", "1492"],
                "page_hint_ints": [1491, 1492],
                "context_raw": "1491 ORDO RERUM 1492",
            },
        ],
    }


def summarize_helper_output() -> dict[str, dict[str, Any]]:
    request = read_json(HELPER_REQUEST_JSON, {})
    output = read_json(HELPER_OUTPUT_JSON, {})
    request_entries = {item.get("entry_id"): item for item in request.get("entries", [])}
    summary: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        candidate = (item.get("candidates") or [{}])[0] or {}
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": candidate.get("candidate_role"),
            "reason_summary": candidate.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": candidate.get("file") or item.get("best_candidate", {}).get("file"),
                "probability": candidate.get("probability") or item.get("best_candidate", {}).get("probability"),
                "candidate_role": candidate.get("candidate_role") or item.get("best_candidate", {}).get("candidate_role"),
            },
            "query_names": request_entries[entry_id].get("query_names"),
            "page_hints": request_entries[entry_id].get("page_hint_ints") or request_entries[entry_id].get("page_hints"),
        }
    return summary


def parse_section_entries(files: list[Path], section_key: str, section_start_file: str, editorial_anchor_file: str, helper_summary: dict[str, Any], *, section_kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    node_refs: list[dict[str, Any]] = []
    current_lines: list[str] = []
    current_start_file: str | None = None

    def flush() -> None:
        nonlocal entry_order, current_lines, current_start_file
        if not current_lines:
            return
        entry_order += 1
        entry_raw = normalize(" ".join(current_lines))
        if section_kind == "ordo_rerum":
            lemma_raw = extract_ordo_lemma_raw(entry_raw)
            entry_kind = "lemma"
        else:
            lemma_raw = extract_lemma_raw(entry_raw)
            entry_kind = classify_entry_kind(entry_raw)
        inferred_page = None
        if section_kind == "ordo_rerum":
            page_numbers = [int(m.group(1)) for m in REFERENCE_NUMBER_RE.finditer(entry_raw)]
            inferred_page = page_numbers[0] if page_numbers else None
        entry = {
            "entry_key": f"{VOLUME_ID}:entry:{section_key}:{entry_order:04d}",
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize(lemma_raw).casefold() if lemma_raw else None,
            "lemma_sort": sort_key(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": (lemma_raw[:1].upper() if lemma_raw else None),
            "inferred_printed_page": inferred_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": current_start_file,
            "confidence": 0.84 if section_kind == "ordo_rerum" else 0.78,
            "raw_json": {
                "source_file": current_start_file,
                "section_kind": section_kind,
                "helper": helper_summary if entry_order <= 3 else None,
            },
        }
        if section_kind == "ordo_rerum":
            page_tokens = [m.group(1) for m in REFERENCE_NUMBER_RE.finditer(entry_raw)]
            for ref_order, token in enumerate(page_tokens, start=1):
                refs.append(
                    {
                        "entry_key": entry["entry_key"],
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": token,
                        "page_ref_raw": token,
                        "page_ref_int": int(token),
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": current_start_file,
                        "target_file_probability": None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": editorial_anchor_file,
                        "confidence": 0.95,
                        "raw_json": {
                            "parsed_from": "contents_table_page_number",
                        },
                    }
                )
        entries.append(entry)
        current_lines = []
        current_start_file = None

    for path in files:
        current_start_file = None
        for line in extract_text_lines(path):
            if HEADER_LINE_RE.fullmatch(line):
                flush()
                continue
            if PAGE_ONLY_RE.fullmatch(line):
                flush()
                continue
            if LETTER_ONLY_RE.fullmatch(line):
                flush()
                continue
            if line.startswith(("SUIDAS.", "DE QUIBUS SUIDAS.", "INDEX SCRIPTORUM", "ORDO RERUM")):
                flush()
                continue
            if section_kind == "ordo_rerum" and current_lines:
                prev = current_lines[-1]
                if not re.search(r"\d", prev) and not prev.rstrip().endswith((".", ":", ";")) and line and (line[0].isupper() or LEADING_GREEK_LETTER_RE.match(line)):
                    current_lines.append(line)
                    continue
            if line_starts_new_entry(line):
                flush()
                if line.startswith(("C ", "D ", "B ", "A ")) and len(line) > 2 and line[2].isupper():
                    line = line[2:].lstrip()
                current_lines = [line]
                current_start_file = str(path)
                continue
            if not current_lines:
                current_lines = [line]
                current_start_file = str(path)
                continue
            current_lines.append(line)
        flush()

    for item in entries:
        item["raw_json"]["helper_summary"] = helper_summary.get("pg117_index_scriptorum_heading") if section_kind == "author_index" else helper_summary.get("pg117_ordo_rerum_heading")
    return entries, refs


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)

    request = build_request()
    write_json(HELPER_REQUEST_JSON, request)

    helper_summary = summarize_helper_output()

    author_files = discover_files(list(range(AUTHOR_SEQ_START, AUTHOR_SEQ_END + 1, 2)))
    ordo_files = discover_files([ORDO_SEQ_START, ORDO_SEQ_END])
    if not author_files:
        raise SystemExit("No author_index OCR files found for PG117")
    if not ordo_files:
        raise SystemExit("No ordo_rerum OCR files found for PG117")

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Patrologiae Graecae Tomus CXVII",
        "notes": "PG117 contains the Suidas material: an author index headed INDEX SCRIPTORUM and a closing ORDO RERUM table.",
    }

    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:author_index:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": "INDEX SCRIPTORUM. QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR.",
            "heading_norm": "index scriptorum quorum suidas notitiam tradit per ordinem litterarum auctior et locupletior",
            "heading_letter": None,
            "page_start": 1215,
            "page_end": 1416,
            "file_start": str(author_files[0]),
            "file_end": str(author_files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical author index of writers cited by Suidas, in Latin and Greek columns; the printed structure is not a contents table.",
                "helper_summary": helper_summary.get("pg117_index_scriptorum_heading"),
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1491,
            "page_end": 1496,
            "file_start": str(ordo_files[0]),
            "file_end": str(ordo_files[-1]),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing editorial contents table for the tome, distinct from the alphabetical author index.",
                "helper_summary": helper_summary.get("pg117_ordo_rerum_heading"),
            },
        },
    ]

    nodes = [
        {
            "node_key": f"{VOLUME_ID}:node:author_index:title",
            "section_key": sections[0]["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR.",
            "label_norm": "quorum suidas notitiam tradit per ordinem litterarum auctior et locupletior",
            "label_sort": sort_key("QUORUM SUIDAS NOTITIAM TRADIT PER ORDINEM LITTERARUM AUCTIOR ET LOCUPLETIOR."),
            "node_level": 1,
            "confidence": 0.96,
            "raw_json": {
                "role": "section_subtitle",
                "helper_summary": helper_summary.get("pg117_index_scriptorum_heading"),
            },
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo_rerum:title",
            "section_key": sections[1]["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "label_norm": "ordo rerum quae in hoc tomo continentur",
            "label_sort": sort_key("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."),
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {
                "role": "section_title",
                "helper_summary": helper_summary.get("pg117_ordo_rerum_heading"),
            },
        },
    ]

    author_entries, author_refs = parse_section_entries(
        author_files,
        sections[0]["section_key"],
        str(author_files[0]),
        str(author_files[0]),
        helper_summary,
        section_kind="author_index",
    )
    ordo_entries, ordo_refs = parse_section_entries(
        ordo_files,
        sections[1]["section_key"],
        str(ordo_files[0]),
        str(ordo_files[0]),
        helper_summary,
        section_kind="ordo_rerum",
    )

    entries = author_entries + ordo_entries
    refs = author_refs + ordo_refs
    scripture_refs: list[dict[str, Any]] = []

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the PG117 author index headed INDEX SCRIPTORUM and the closing ORDO RERUM table from OCR files 667-767 and 805-807 using conservative line-based segmentation.",
        "evidence_files": [
            str(author_files[0]),
            str(author_files[min(7, len(author_files) - 1)]),
            str(author_files[-1]),
            str(ordo_files[0]),
            str(ordo_files[-1]),
        ],
    }

    notes = [
        "PG117 indexes are mixed Latin/Greek author listings followed by a closing ORDO RERUM table.",
        "The helper was used only to anchor the section heading and the closing contents table; entry segmentation came from direct OCR line inspection.",
        "Material page references are concentrated in ORDO RERUM; the author index entries are preserved as OCR-verbatim lexicon lines without forcing synthetic page refs.",
    ]

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build the PG117 alphabetical payload from OCR and preserve the Suidas author index and ORDO RERUM as separate sections.",
        "completed": [
            "Reviewed index-related OCR files in the PG117 tail",
            "Created helper request for the section heading and a representative lemma",
            "Prepared conservative line-based segmentation for the author index and ORDO RERUM",
        ],
        "pending": [
            "Run helper and capture helper output",
            "Assemble final payload and validate JSON structure",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals in entry_raw and lemma_raw.",
            "Do not normalize editorial page numbers out of ORDO RERUM refs.",
        ],
    }

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
    }

    write_json(TODO_JSON, todo)
    write_json(MANIFEST_JSON, manifest)
    write_json(VOLUME_JSON, volume)
    write_json(SECTIONS_JSON, sections)
    write_json(NODES_JSON, nodes)
    write_json(ENTRIES_JSON, entries)
    write_json(REFS_JSON, refs)
    write_json(SCRIPTURE_REFS_JSON, scripture_refs)
    write_json(COVERAGE_JSON, coverage)
    write_json(NOTES_JSON, notes)

    payload = {
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
    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

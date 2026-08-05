#!/usr/bin/env python3
"""Usage: build the PL074 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl074_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL074/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL074_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL074_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL074 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL074_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


SECTION_DEFS = [
    {
        "section_key": "PL074:alpha:scripture_index:001",
        "section_order": 1,
        "section_kind": "scripture_index",
        "heading_raw": "INDEX SACRÆ SCRIPTURÆ.",
        "file_start": 640,
        "file_end": 645,
        "page_start": None,
        "page_end": None,
        "notes": "Biblical citation index at the opening of the tail window.",
    },
    {
        "section_key": "PL074:alpha:analytic_subject:002",
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM.",
        "file_start": 646,
        "file_end": 683,
        "page_start": None,
        "page_end": None,
        "notes": "Main subject index with letters A-Z and dense subject lines.",
    },
    {
        "section_key": "PL074:alpha:onomastic_person:003",
        "section_order": 3,
        "section_kind": "onomastic_person",
        "heading_raw": "INDEX NOMINUM PROPRIORUM VIRORUM AC MULIERUM.",
        "file_start": 684,
        "file_end": 688,
        "page_start": None,
        "page_end": None,
        "notes": "Proper-name index for persons.",
    },
    {
        "section_key": "PL074:alpha:onomastic_place:004",
        "section_order": 4,
        "section_kind": "onomastic_place",
        "heading_raw": "INDEX CHOROGRAPHICUS IN VITAS PATRUM.",
        "file_start": 689,
        "file_end": 691,
        "page_start": None,
        "page_end": None,
        "notes": "Geographic/place-name index.",
    },
    {
        "section_key": "PL074:alpha:analytic_subject:005",
        "section_order": 5,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX CONCIONATORIUS.",
        "file_start": 692,
        "file_end": 709,
        "page_start": None,
        "page_end": None,
        "notes": "Homiletic/thematic index block with liturgical Sunday rubrics and prolegomena/præludia notes.",
    },
    {
        "section_key": "PL074:alpha:author_index:006",
        "section_order": 6,
        "section_kind": "author_index",
        "heading_raw": "INDEX AUCTORUM CITATORUM IN PROLEGOMENIS.",
        "file_start": 710,
        "file_end": 713,
        "page_start": None,
        "page_end": None,
        "notes": "Index of cited authors in the prolegomena; the tail continues into the closing page before Ordo Rerum.",
    },
    {
        "section_key": "PL074:alpha:ordo_rerum:007",
        "section_order": 7,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "file_start": 713,
        "file_end": 715,
        "page_start": None,
        "page_end": None,
        "notes": "Closing table of contents for the volume.",
    },
]

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
PAGE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?(?!\d)")
ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADLINE_RE = re.compile(
    r"^(INDEX(?:\s+RERUM|(?:\s+NOMINUM\s+PROPRIORUM\s+VIRORUM\s+AC\s+MULIERUM)|(?:\s+CHOROGRAPHICUS.*)|(?:\s+CONCIONATORIUS)|(?:\s+AUCTORUM\s+CITATORUM.*)|(?:\s+IN\s+PROLEGOMENIS.*)|(?:\s+SCRIPTURÆ\s+SACRÆ)|(?:\s+SACRÆ\s+SCRIPTURÆ))|ORDO\s+RERUM|INDICES\s+IN\s+VITAS\s+PATRUM|DOMINICA\s+[IVXLCDM]+.*|EPIST\.)",
    re.IGNORECASE,
)
BOOK_HEAD_RE = re.compile(r"^[A-ZÆŒ][A-ZÆŒ\s\.\-']{2,}$")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def is_in_section(path: Path, section: dict[str, Any]) -> bool:
    num = file_num(path)
    return section["file_start"] <= num <= section["file_end"]


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def clean_line(text: str) -> str:
    text = norm(text) or ""
    text = text.replace("\u00a0", " ")
    return text


def is_single_letter(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def is_section_heading(line: str) -> bool:
    return bool(HEADLINE_RE.search(line))


def parse_page_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last = last_page

    for match in PAGE_NUM_RE.finditer(text):
        start = int(match.group(1))
        end = match.group(2)
        ref_raw = match.group(0).strip()
        if end is not None:
            refs.append(
                {
                    "ref_kind": "editorial_range",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(start),
                    "range_end_raw": str(int(end)),
                }
            )
        else:
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
        current_last = start

    if not refs and IBID_RE.search(text) and current_last is not None:
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": current_last,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )

    return refs, current_last


def split_entry_text(line: str) -> list[str]:
    text = line.strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.;])\s+(?=[A-ZÆŒ])", text) if part.strip()]
    if len(parts) == 1:
        return parts
    return parts


def infer_lemma(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^\d+\s*", "", text)
    text = re.sub(r"^[A-ZÆŒ]\s+", "", text)
    if "," in text:
        text = text.split(",", 1)[0]
    if "." in text and not text.startswith("DOMINICA"):
        candidate = text.split(".", 1)[0]
        if len(candidate) > 2:
            text = candidate
    text = text.strip(" .;:")
    return text or None


def current_entry_kind(section_kind: str, entry_raw: str) -> str:
    if section_kind == "scripture_index":
        return "scripture_citation"
    if section_kind == "ordo_rerum":
        return "heading_group"
    if re.match(r"^(?:Vide|Vid\.|Voir|v\.)\b", entry_raw, re.IGNORECASE):
        return "cross_reference"
    if entry_raw.startswith("DOMINICA "):
        return "heading_group"
    return "lemma"


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": "PL074",
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl074_scripture_genesis_ii_7",
                "lemma_raw": "Genesis II, 7",
                "query_names": ["Inflavit in eum spiraculum vitæ", "Genesis II, 7"],
                "page_hints": ["259"],
                "page_hint_ints": [259],
                "context_raw": "GENESIS. II, 7. Inflavit in eum spiraculum vitæ, 259.",
            },
            {
                "entry_id": "pl074_index_rerum_abstinentia",
                "lemma_raw": "Abstinentia a potu",
                "query_names": ["Abstinentia a potu", "abstinentia vini consulitur", "abstinentia monachis necessaria"],
                "page_hints": ["542", "572"],
                "page_hint_ints": [542, 572],
                "context_raw": "Abstinentia a potu, 542, 572; a vino, 870, 870; a pane, 761, 770.",
            },
            {
                "entry_id": "pl074_index_nominum_maria_aegyptiaca",
                "lemma_raw": "Maria Ægyptiaca",
                "query_names": ["Maria Ægyptiaca", "Mariam Ægyptiacam", "Maria peccatrix"],
                "page_hints": ["381", "388"],
                "page_hint_ints": [381, 388],
                "context_raw": "Maria Ægyptiaca, 381. — Maria peccatrix, 882.",
            },
            {
                "entry_id": "pl074_index_chorographicus_aegyptus",
                "lemma_raw": "Aegyptus",
                "query_names": ["Aegyptus", "Aegyptii", "Aegyptiorum", "Aegypti monasteria"],
                "page_hints": ["36", "876"],
                "page_hint_ints": [36, 876],
                "context_raw": "Aegyptus, 27, 28, 35, 36, 41, 48, 50.",
            },
            {
                "entry_id": "pl074_index_auctorum_palladius",
                "lemma_raw": "Palladius",
                "query_names": ["Palladius", "Palladii", "Palladius Galata"],
                "page_hints": ["423", "646", "702"],
                "page_hint_ints": [423, 646, 702],
                "context_raw": "Palladius, xii, xv, xxviii, xxxiv, xli, 31, 32, 37, 50, 61, 65, 66, 67, 68, 69.",
            },
        ],
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any] | None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json)


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    sections = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    section_start_file_cache: dict[str, str] = {}

    for section in SECTION_DEFS:
        start_file = next(path for path in files if file_num(path) == section["file_start"])
        end_file = next(path for path in files if file_num(path) == section["file_end"])
        section_start_file_cache[section["section_key"]] = str(start_file)
        section_payload = {
            "section_key": section["section_key"],
            "volume_id": "PL074",
            "work_key": None,
            "section_order": section["section_order"],
            "section_kind": section["section_kind"],
            "heading_raw": section["heading_raw"],
            "heading_norm": section["heading_raw"].rstrip(".").lower(),
            "heading_letter": None,
            "page_start": section["page_start"],
            "page_end": section["page_end"],
            "file_start": str(start_file),
            "file_end": str(end_file),
            "confidence": 0.93 if section["section_kind"] != "ordo_rerum" else 0.98,
            "raw_json": {
                "section_kind_reason": section["notes"],
                "source_files": [str(path) for path in files if is_in_section(path, section)],
            },
        }
        if helper_output is not None:
            section_payload["raw_json"]["helper_locator_status"] = helper_output.get("status")
        sections.append(section_payload)

    current_section_idx = 0
    current_letter: str | None = None
    current_book: str | None = None
    last_page: int | None = None

    def add_node(section_key: str, label_raw: str, node_kind: str, node_level: int, source_file: str, parent_key: str | None = None) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"PL074:node:{node_order:06d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent_key,
                "node_order": node_order,
                "node_kind": node_kind,
                "label_raw": label_raw,
                "label_norm": label_raw.lower(),
                "label_sort": label_raw.lower(),
                "node_level": node_level,
                "confidence": 0.96,
                "raw_json": {"source_file": source_file},
            }
        )
        return node_key

    def add_entry(section: dict[str, Any], source_file: str, line: str) -> None:
        nonlocal entry_order, last_page, current_letter, current_book
        raw_line = clean_line(line)
        if not raw_line:
            return
        if is_section_heading(raw_line) or raw_line.startswith("Digitized by Google"):
            return
        if is_single_letter(raw_line):
            return
        for segment in split_entry_text(raw_line):
            if not segment:
                continue
            if is_section_heading(segment):
                continue
            if segment in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Z"}:
                continue
            entry_order += 1
            entry_key = f"PL074:entry:{entry_order:06d}"
            entry_kind = current_entry_kind(section["section_kind"], segment)
            lemma_raw = infer_lemma(segment)
            page_refs, last_page_after = parse_page_refs(segment, last_page)
            last_page = last_page_after
            entry = {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": segment,
                "context_raw": segment,
                "heading_letter": current_letter,
                "inferred_printed_page": None,
                "section_start_file": section_start_file_cache[section["section_key"]],
                "editorial_anchor_file": source_file,
                "target_file_best": source_file,
                "confidence": 0.78 if entry_kind == "lemma" else 0.72,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section["section_kind"],
                },
            }
            if helper_output is not None and helper_output.get("entries"):
                entry["raw_json"]["helper_status"] = helper_output.get("status")
            entries.append(entry)

            for page_ref in page_refs:
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": len([r for r in refs if r["entry_key"] == entry_key]) + 1,
                        "ref_kind": page_ref["ref_kind"],
                        "ref_raw": page_ref["ref_raw"],
                        "page_ref_raw": page_ref["page_ref_raw"],
                        "page_ref_int": page_ref["page_ref_int"],
                        "page_ref_col": page_ref["page_ref_col"],
                        "line_ref_raw": page_ref["line_ref_raw"],
                        "range_start_raw": page_ref["range_start_raw"],
                        "range_end_raw": page_ref["range_end_raw"],
                        "target_file": None,
                        "target_file_probability": None,
                        "section_start_file": section_start_file_cache[section["section_key"]],
                        "editorial_anchor_file": source_file,
                        "confidence": 0.66,
                        "raw_json": {"source_file": source_file, "section_kind": section["section_kind"]},
                    }
                )

            if section["section_kind"] == "scripture_index":
                m = re.match(r"^(?P<chapter>[IVXLCDM]+|[0-9]+)\s*,\s*(?P<verse1>\d+)(?:\s*,\s*(?P<verse2>\d+))?\.\s*(?P<rest>.*)$", segment)
                if m and current_book:
                    chapter_raw = m.group("chapter")
                    chapter = int(chapter_raw) if chapter_raw.isdigit() else None
                    verse1 = int(m.group("verse1"))
                    verse2 = int(m.group("verse2")) if m.group("verse2") else None
                    scripture_refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": 1,
                            "ref_role": "citation",
                            "ref_raw": segment,
                            "book_raw": current_book,
                            "book_norm": current_book.lower(),
                            "chapter_start": chapter,
                            "verse_start": verse1,
                            "chapter_end": chapter,
                            "verse_end": verse2,
                            "is_range": 1 if verse2 is not None else 0,
                            "confidence": 0.73,
                            "raw_json": {"source_file": source_file},
                        }
                    )

    for section in SECTION_DEFS:
        section_files = [path for path in files if is_in_section(path, section)]
        for path in section_files:
            lines = extract_lines(path)
            for line in lines:
                cleaned = clean_line(line)
                if not cleaned:
                    continue
                if cleaned.startswith("Digitized by Google"):
                    continue
                if is_single_letter(cleaned):
                    current_letter = cleaned
                    add_node(section["section_key"], cleaned, "letter_group", 1, str(path))
                    continue
                if cleaned.startswith("DOMINICA "):
                    add_node(section["section_key"], cleaned.split(".")[0], "heading_group", 1, str(path))
                if section["section_kind"] == "scripture_index" and BOOK_HEAD_RE.fullmatch(cleaned) and len(cleaned) < 40:
                    current_book = cleaned.rstrip(".")
                    add_node(section["section_key"], current_book, "heading_group", 1, str(path))
                    continue
                if is_section_heading(cleaned):
                    continue
                add_entry(section, str(path), cleaned)

    volume = {
        "volume_id": "PL074",
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina, volume 74",
        "notes": "Recovered the scripture, subject, onomastic, chorographic, homiletic, author, and closing contents indexes from the OCR tail.",
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the visible index blocks conservatively from the OCR tail. The sections are mixed in several files, the OCR is noisy in the analytic and concionatorius blocks, and some cited pages remain unresolved because they require body-page lookup outside the indexed tail window.",
        "evidence_files": [
            str(next(path for path in files if file_num(path) == num))
            for num in [640, 646, 684, 689, 692, 710, 713, 715]
        ],
    }

    notes = [
        "Section 1 is the scripture index at the opening of the tail window.",
        "Section 2 is the main subject index (INDEX RERUM).",
        "Section 3 is the onomastic index for persons.",
        "Section 4 is the chorographic / place-name index.",
        "Section 5 is the homiletic/analytic concionatorius block with prolegomena/præludia material.",
        "Section 6 is the cited-author index in the prolegomena.",
        "Section 7 is the closing ORDO RERUM block.",
        "Material references were kept conservative; many `target_file` values remain null because the cited body pages are not in the OCR tail itself.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": "PL074",
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": "/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL074_alphabetical_indices.json",
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": "PL074",
            "updated_at": now_iso(),
            "current_focus": "Validate the conservative PL074 alphabetical payload and confirm section boundaries.",
            "completed": [
                "section boundaries mapped from OCR headings",
                "helper request written",
                "payload fragments assembled",
            ],
            "pending": [
                "validate output JSON",
                "inspect any import-time schema errors",
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
        "generated_at": now_iso(),
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
    ap = argparse.ArgumentParser(description="Build PL074 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

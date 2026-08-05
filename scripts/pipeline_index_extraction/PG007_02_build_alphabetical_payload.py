#!/usr/bin/env python3
"""Usage: build the PG007.02 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG007_02_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG007.02/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG007.02_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG007.02_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG007.02 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG007.02_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.editorial_page_estimator import estimate_editorial_pages
from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG007.02"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus VII, pars II"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_HEADING_1 = "INDEX RERUM ET SENTENTIARUM"
SECTION_HEADING_2 = "INDEX RERUM PRÆCIPUARUM QUÆ IN NOTIS VARIORUM INVENIUNTUR"
SECTION_HEADING_3 = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR"
SECTION_HEADING_4 = "FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI"
SECTION_HEADING_5 = "INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET SENTENTIARUM.",
        "heading_norm": "index rerum et sententiarum",
        "heading_letter": None,
        "page_start": 1901,
        "page_end": 1988,
        "file_start_seq": 411,
        "file_end_seq": 454,
        "section_kind_reason": "Main analytical index of things and sayings.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM PRÆCIPUARUM QUÆ IN NOTIS VARIORUM INVENIUNTUR.",
        "heading_norm": "index rerum praecipuarum quae in notis variorum inveniuntur",
        "heading_letter": None,
        "page_start": 1989,
        "page_end": 2004,
        "file_start_seq": 454,
        "file_end_seq": 462,
        "section_kind_reason": "Analytical index of items found in the various notes.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 2005,
        "page_end": 2014,
        "file_start_seq": 463,
        "file_end_seq": 467,
        "section_kind_reason": "Table of contents for the volume.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:editorial_closure:004",
        "section_order": 4,
        "section_kind": "editorial_closure",
        "heading_raw": "ADDENDA / FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI.",
        "heading_norm": "addenda fragmenta deperditorum operum s irenaei",
        "heading_letter": None,
        "page_start": 2015,
        "page_end": 2016,
        "file_start_seq": 468,
        "file_end_seq": 468,
        "section_kind_reason": "Editorial closure material and addenda adjacent to the index tail.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:005",
        "section_order": 5,
        "section_kind": "author_index",
        "heading_raw": "INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO.",
        "heading_norm": "index scriptorum et virorum illustrium qui memorantur ab irenaeo",
        "heading_letter": None,
        "page_start": 2017,
        "page_end": 2020,
        "file_start_seq": 469,
        "file_end_seq": 470,
        "section_kind_reason": "Author index of writers and notable men cited by Irenaeus.",
    },
]

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ARABIC_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à)\s*(\d{1,4}))?")
ROMAN_REF_RE = re.compile(r"(?<![A-ZÆŒ])\b([IVXLCDM]{1,8})\b")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SECTION_RE = re.compile(
    r"^(?:INDEX RERUM ET SENTENTIARUM|INDEX RERUM PRÆCIPUARUM|INDEX RERUM QUÆ IN NOTIS VARIORUM INVENIUNTUR|ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR|INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO|ADDENDA\.?|FRAGMENTA DEPERDITORUM OPERUM S\. IRENÆI\.?|APPENDIX ad Irenæi libros contra hæreses, continens Gnosticorum quorum meminit S\. martyr fragmenta\.?)$",
    re.IGNORECASE,
)
NOISE_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


@dataclass
class OpenEntry:
    section_key: str
    parent_node_key: str | None
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str | None
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str | None
    editorial_anchor_file: str | None
    target_file_best: str | None
    confidence: float
    raw_json: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = LINEBREAK_HYPHEN_RE.sub(r"\1\2", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            continue
        files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(m.group(1))


def page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in HEADER_PAGE_RE.finditer(header):
            mapping.setdefault(int(match.group(1)), str(path))
    if files:
        for page, target in build_estimator_page_map(files[0].parent).items():
            mapping.setdefault(page, target)
    return mapping


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


def build_estimator_pages_by_seq(source_root: Path) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    try:
        payload = estimate_editorial_pages(
            volume_id=VOLUME_ID,
            source_root=source_root,
            collection=COLLECTION,
        )
    except Exception:
        return out
    for item in payload.get("files") or []:
        seq = item.get("file_seq")
        if seq is None:
            continue
        pages = _best_guess_pages(item.get("best_guess"))
        if pages:
            out[int(seq)] = pages
    return out


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def write_todo(intermediate_dir: Path, current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": current_focus,
        "completed": completed,
        "pending": pending,
        "blocked": blocked,
        "notes": notes,
    }
    write_json(intermediate_dir / "todo.json", todo)


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
        check=True,
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip():
        print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr)
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def parse_header_pages(header_text: str | None) -> list[int]:
    if not header_text:
        return []
    pages: list[int] = []
    for token in HEADER_PAGE_RE.findall(header_text):
        page = int(token)
        if page not in pages:
            pages.append(page)
    return pages


def clean_lines(body_text: str | None) -> list[str]:
    if not body_text:
        return []
    lines: list[str] = []
    for raw in body_text.splitlines():
        line = normalize(raw)
        if not line or NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def is_section_heading(line: str) -> bool:
    return bool(SECTION_RE.match(line)) or line in {
        "INDEX RERUM PRÆCIPUARUM",
        "QUÆ IN NOTIS VARIORUM INVENIUNTUR.",
        "QUÆ IN HOC TOMO CONTINENTUR.",
        "ADDENDA.",
        "FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI.",
        "INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO.",
    }


def current_section_for_file(seq: int) -> int | None:
    if 411 <= seq <= 454:
        return 1
    if 455 <= seq <= 462:
        return 2
    if 463 <= seq <= 467:
        return 3
    if seq == 468:
        return 4
    if 469 <= seq <= 470:
        return 5
    return None


def new_entry(
    section_key: str,
    parent_node_key: str | None,
    entry_order: int,
    entry_kind: str,
    entry_raw: str,
    current_file: str,
    inferred_printed_page: int | None,
    section_start_file: str,
    heading_letter: str | None,
    confidence: float,
) -> OpenEntry:
    text = normalize(entry_raw) or ""
    lemma_raw: str | None = None
    if entry_kind == "heading_group":
        lemma_raw = text.split(" — ", 1)[0].strip(" .;:")
        lemma_raw = lemma_raw.split(".", 1)[0].strip(" .;:") if text.startswith("CAP.") else lemma_raw
    elif entry_kind == "cross_reference":
        lemma_raw = text.split(",", 1)[0].strip(" .;:")
    else:
        if "," in text:
            lemma_raw = text.split(",", 1)[0].strip(" .;:")
        elif "." in text and not text.upper().startswith(("CAP.", "ART.", "LIBER ", "PRÆFATIO")):
            lemma_raw = text.split(".", 1)[0].strip(" .;:")
        else:
            lemma_raw = text.strip(" .;:")
    return OpenEntry(
        section_key=section_key,
        parent_node_key=parent_node_key,
        entry_order=entry_order,
        entry_kind=entry_kind,
        lemma_raw=lemma_raw or None,
        lemma_display=lemma_raw or None,
        lemma_norm=normalize(lemma_raw) if lemma_raw else None,
        lemma_sort=sort_norm(lemma_raw),
        entry_raw=text,
        context_raw=None,
        heading_letter=heading_letter,
        inferred_printed_page=inferred_printed_page,
        section_start_file=section_start_file,
        editorial_anchor_file=current_file,
        target_file_best=current_file,
        confidence=confidence,
        raw_json={},
    )


def classify_entry_kind(section_kind: str, text: str) -> str:
    upper = text.upper()
    if section_kind == "editorial_closure":
        return "editorial_note"
    if section_kind == "ordo_rerum":
        return "heading_group"
    if upper.startswith(("VID.", "VIDE ", "VOIR ", "V. ", "CF. ", "ID. ")):
        return "cross_reference"
    if upper.startswith(("CAP.", "ART.", "PRÆFATIO", "PRATFATIO", "DISSERTATIO", "LIBER ")):
        return "heading_group"
    return "lemma"


def extract_refs(text: str, page_lookup: dict[int, str], section_kind: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str | None]] = set()

    if section_kind == "ordo_rerum":
        m = re.search(r"(\d{1,4})\s*$", text)
        if m:
            page = int(m.group(1))
            key = ("editorial_page", page, None)
            if key not in seen:
                seen.add(key)
                refs.append(
                    {
                        "ref_kind": "editorial_page",
                        "ref_raw": m.group(1),
                        "page_ref_raw": m.group(1),
                        "page_ref_int": page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": page_lookup.get(page),
                        "target_file_probability": 0.99 if page in page_lookup else None,
                        "section_start_file": None,
                        "editorial_anchor_file": None,
                        "confidence": 0.95 if page in page_lookup else 0.7,
                        "raw_json": {"reason": "trailing page number in Ordo Rerum line"},
                    }
                )
        return refs

    for match in ARABIC_REF_RE.finditer(text):
        raw = match.group(0).strip()
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = ("editorial_range" if end is not None else "editorial_page", start, str(end) if end is not None else None)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
                "target_file": page_lookup.get(start),
                "target_file_probability": 0.99 if start in page_lookup else None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.92 if start in page_lookup else 0.72,
                "raw_json": {},
            }
        )

    if not refs and section_kind == "author_index":
        for match in ROMAN_REF_RE.finditer(text):
            raw = match.group(1)
            key = ("parallel_locator", None, raw)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref_kind": "parallel_locator",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": None,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": None,
                    "editorial_anchor_file": None,
                    "confidence": 0.6,
                    "raw_json": {"reason": "roman locator or book/chapter style citation in author index"},
                }
            )
    return refs


def build_helper_request(source_root: Path) -> dict[str, Any]:
    estimator_by_seq = build_estimator_pages_by_seq(source_root)

    def merged_page_hints(file_seq_hint: int, *base_pages: int) -> list[int]:
        hints: list[int] = []
        for page in base_pages:
            if page not in hints:
                hints.append(page)
        for page in estimator_by_seq.get(file_seq_hint, []):
            if page not in hints:
                hints.append(page)
        return hints[:4]

    section_001_hints = merged_page_hints(411, 1901)
    section_002_hints = merged_page_hints(454, 1989)
    section_005_hints = merged_page_hints(469, 2017)
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": f"{VOLUME_ID.lower().replace('.', '_')}_section_001",
                "lemma_raw": "INDEX RERUM ET SENTENTIARUM",
                "query_names": ["INDEX RERUM ET SENTENTIARUM", "AABON", "ABEL", "ABRAHAM"],
                "page_hints": [str(page) for page in section_001_hints],
                "page_hint_ints": section_001_hints,
                "context_raw": "1901 INDEX RERUM ET SENTENTIARUM. 1902",
            },
            {
                "entry_id": f"{VOLUME_ID.lower().replace('.', '_')}_section_002",
                "lemma_raw": "INDEX RERUM PRÆCIPUARUM QUÆ IN NOTIS VARIORUM INVENIUNTUR",
                "query_names": [
                    "INDEX RERUM PRÆCIPUARUM",
                    "QUÆ IN NOTIS VARIORUM INVENIUNTUR",
                    "Victor papa",
                    "Eucharistia sacramentum"
                ],
                "page_hints": [str(page) for page in section_002_hints],
                "page_hint_ints": section_002_hints,
                "context_raw": "1989 QUÆ IN NOTIS VARIORUM INVENIUNTUR. 1990",
            },
            {
                "entry_id": f"{VOLUME_ID.lower().replace('.', '_')}_section_005",
                "lemma_raw": "INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO",
                "query_names": [
                    "INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO",
                    "Anonymus",
                    "Presbyteri",
                    "Irenæus"
                ],
                "page_hints": [str(page) for page in section_005_hints],
                "page_hint_ints": section_005_hints,
                "context_raw": "2017 INDEX SCRIPTORUM. 2018",
            },
        ],
    }


def build_sections(helper_result: dict[str, Any], seq_to_path: dict[int, str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    helper_summary = helper_result if isinstance(helper_result, dict) else {}
    for spec in SECTION_DEFS:
        section = {
            "section_key": spec["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": spec["section_order"],
            "section_kind": spec["section_kind"],
            "heading_raw": spec["heading_raw"],
            "heading_norm": spec["heading_norm"],
            "heading_letter": spec["heading_letter"],
            "page_start": spec["page_start"],
            "page_end": spec["page_end"],
            "file_start": seq_to_path.get(spec["file_start_seq"]),
            "file_end": seq_to_path.get(spec["file_end_seq"]),
            "confidence": 0.9 if spec["section_kind"] != "editorial_closure" else 0.75,
            "raw_json": {
                "section_kind_reason": spec["section_kind_reason"],
                "helper": helper_summary.get("entries", []),
            },
        }
        sections.append(section)
    return sections


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root: Path = args.source_root
    helper_request_json: Path = args.helper_request_json
    helper_output_json: Path = args.helper_output_json
    intermediate_dir: Path = args.intermediate_dir
    output_file: Path = args.output_file

    files = discover_files(source_root)
    relevant_files = [p for p in files if 411 <= file_seq(p) <= 470]
    pmap = page_map(files)

    write_todo(
        intermediate_dir,
        current_focus="Build PG007.02 alphabetical payload from OCR tail",
        completed=[
            "repository docs checked",
            "OCR tail inspected",
            "section boundaries identified",
        ],
        pending=[
            "run locator helper",
            "assemble entries and refs",
            "write final payload",
        ],
        blocked=[],
        notes=[
            "Section split uses OCR headings at 454, 463, 468, and 469.",
            "Helper is used only to confirm section anchors; OCR drives the payload.",
        ],
    )

    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_result = run_helper(helper_request_json, helper_output_json)

    seq_to_path = {file_seq(p): str(p) for p in relevant_files}
    sections = build_sections(helper_result, seq_to_path)
    section_by_index = {1: SECTION_DEFS[0], 2: SECTION_DEFS[1], 3: SECTION_DEFS[2], 4: SECTION_DEFS[3], 5: SECTION_DEFS[4]}
    section_lookup = {spec["section_key"]: spec for spec in SECTION_DEFS}

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    current_node_key: str | None = None
    current_section_idx = 1
    current_section_key = SECTION_DEFS[0]["section_key"]
    current_section_kind = SECTION_DEFS[0]["section_kind"]
    current_entry: OpenEntry | None = None
    entry_order_by_section: dict[str, int] = {spec["section_key"]: 0 for spec in SECTION_DEFS}
    node_order_by_section: dict[str, int] = {spec["section_key"]: 0 for spec in SECTION_DEFS}
    current_page_header: int | None = None

    def finalize_entry() -> None:
        nonlocal current_entry
        if current_entry is None:
            return
        payload = current_entry.__dict__.copy()
        payload["raw_json"] = {
            **payload["raw_json"],
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "source_file": payload["editorial_anchor_file"],
        }
        entries.append(payload)
        current_entry = None

    def switch_section(idx: int) -> None:
        nonlocal current_section_idx, current_section_key, current_section_kind, current_node_key
        if idx == current_section_idx:
            return
        finalize_entry()
        current_section_idx = idx
        current_section_key = section_by_index[idx]["section_key"]
        current_section_kind = section_by_index[idx]["section_kind"]
        current_node_key = None

    def maybe_new_node(section_key: str, label: str) -> str:
        node_order_by_section[section_key] += 1
        node_key = f"{VOLUME_ID}:node:{section_key.split(':')[-1]}:{node_order_by_section[section_key]:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order_by_section[section_key],
                "node_kind": "letter_group",
                "label_raw": label,
                "label_norm": normalize(label),
                "label_sort": sort_norm(label),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"reason": "single-letter alphabetic divider"},
            }
        )
        return node_key

    for path in relevant_files:
        seq = file_seq(path)
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header_text = normalize(parsed.get("header_text") or "") or ""
        body_lines = clean_lines(parsed.get("body_text") or "")
        pages = parse_header_pages(header_text)
        if pages:
            current_page_header = pages[0]

        file_section_idx = current_section_idx
        if seq <= 454:
            file_section_idx = 1
        elif 455 <= seq <= 462:
            file_section_idx = 2
        elif 463 <= seq <= 467:
            file_section_idx = 3
        elif seq == 468:
            file_section_idx = 3
        elif 469 <= seq <= 470:
            file_section_idx = 5

        if current_section_idx != file_section_idx:
            switch_section(file_section_idx)

        for line in body_lines:
            line_norm = normalize(line) or ""
            upper = line_norm.upper()

            if seq == 454 and upper.startswith("INDEX RERUM PRÆCIPUARUM"):
                finalize_entry()
                switch_section(2)
                continue
            if seq == 463 and (upper.startswith("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR") or upper == "ORDO RERUM"):
                finalize_entry()
                switch_section(3)
                continue
            if seq == 468 and (upper.startswith("FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI") or upper.startswith("ADDENDA")):
                finalize_entry()
                switch_section(4)
                continue
            if seq == 469 and upper.startswith("INDEX SCRIPTORUM ET VIRORUM ILLUSTRIUM QUI MEMORANTUR AB IRENÆO"):
                finalize_entry()
                switch_section(5)
                continue

            if LETTER_RE.fullmatch(line_norm) and current_section_kind in {"analytic_subject", "author_index"}:
                finalize_entry()
                current_node_key = maybe_new_node(current_section_key, line_norm)
                continue

            if current_entry is not None and re.match(r"^[a-zæœ(,.;:\-]", line_norm):
                current_entry.entry_raw = normalize(f"{current_entry.entry_raw} {line_norm}") or current_entry.entry_raw
                continue

            if current_entry is not None and line_norm.startswith(("ibid.", "ibid", "id.", "et ", "quod ", "quæ ", "qui ", "qua ", "quo ", "quia ")):
                current_entry.entry_raw = normalize(f"{current_entry.entry_raw} {line_norm}") or current_entry.entry_raw
                continue

            if is_section_heading(line_norm):
                continue

            finalize_entry()
            entry_order_by_section[current_section_key] += 1
            entry_kind = classify_entry_kind(current_section_kind, line_norm)
            current_entry = new_entry(
                section_key=current_section_key,
                parent_node_key=current_node_key,
                entry_order=entry_order_by_section[current_section_key],
                entry_kind=entry_kind,
                entry_raw=line_norm,
                current_file=str(path),
                inferred_printed_page=current_page_header,
                section_start_file=str(relevant_files[0]),
                heading_letter=current_node_key.split(":")[-1] if current_node_key else None,
                confidence=0.9 if entry_kind != "editorial_note" else 0.72,
            )

        # Allow a carry-over entry to continue on the next file when the next
        # OCR line starts with lowercase continuation.
        continue

    finalize_entry()

    section_start_paths = {spec["section_key"]: seq_to_path.get(spec["file_start_seq"]) for spec in SECTION_DEFS}
    for entry in entries:
        entry["section_start_file"] = section_start_paths.get(entry["section_key"])
        entry["raw_json"]["section_kind_reason"] = section_lookup[entry["section_key"]]["section_kind_reason"]
        entry["raw_json"]["helper"] = {
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
        }
        entry_refs = extract_refs(entry["entry_raw"], pmap, section_lookup[entry["section_key"]]["section_kind"])
        if current_entry is not None and False:
            pass
        for ref_order, ref in enumerate(entry_refs, start=1):
            ref["entry_key"] = f"{entry['section_key']}:{entry['entry_order']:05d}"
            ref["ref_order"] = ref_order
            ref["section_start_file"] = entry["section_start_file"]
            ref["editorial_anchor_file"] = entry["editorial_anchor_file"]
            ref["raw_json"] = {**ref.get("raw_json", {}), "source_entry": entry["entry_raw"][:160]}
            refs.append(ref)

    # Re-key entries for canonical payload and add section-level notes.
    for entry in entries:
        entry["entry_key"] = f"{entry['section_key']}:{entry['entry_order']:05d}"
        entry["raw_json"].setdefault("current_page_header", entry["inferred_printed_page"])
        entry["raw_json"].setdefault("section_kind", section_lookup[entry["section_key"]]["section_kind"])

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "OCR tail parsed into sectioned index entries with conservative line grouping.",
        "evidence_files": [str(p) for p in relevant_files if file_seq(p) in {411, 454, 463, 468, 469, 470}],
    }

    notes = [
        "Section 1 spans files 411-454 and is split at the mid-page heading in file 454.",
        "Section 2 spans files 454-462; Section 3 spans files 463-467.",
        "File 468 contains closing editorial matter and addenda; files 469-470 contain INDEX SCRIPTORUM.",
        "OCR line grouping is conservative; continuation lines beginning in lowercase are attached to the prior entry.",
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    write_json(output_file, payload)
    write_todo(
        intermediate_dir,
        current_focus="Payload written; awaiting validation check",
        completed=[
            "helper request built and executed",
            "sections assembled",
            "entries serialized",
            "refs serialized",
            "payload written",
        ],
        pending=[],
        blocked=[],
        notes=[
            "Helper was used for section anchor confirmation only.",
            "A future pass can tighten lemma segmentation inside long analytical entries if needed.",
        ],
    )


if __name__ == "__main__":
    main()

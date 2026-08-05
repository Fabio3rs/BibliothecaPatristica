#!/usr/bin/env python3
"""Usage: build the PG104 alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/pg104_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG104/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG104_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG104_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG104 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG104_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG104"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 104"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG104/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG104_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG104_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG104_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG104"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET VERBORUM MEMORABILIUM QUÆ IN PHOTII BIBLIOTHECA REPERIUNTUR.",
        "heading_norm": "index rerum et verborum memorabilium quae in photii bibliotheca reperiuntur",
        "heading_letter": "I",
        "file_start": 777,
        "file_end": 805,
        "page_start": 1461,
        "page_end": 1516,
        "section_kind_reason": "Main analytical index of memorable matters and words in Photius' Bibliotheca, arranged alphabetically with letter dividers and material page citations.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS RERUM NOTABILIORUM QUÆ IN SYNTAGMATE INVENIUNTUR.",
        "heading_norm": "index analyticus rerum notabilium quae in syntagmate inveniuntur",
        "heading_letter": "II",
        "file_start": 806,
        "file_end": 809,
        "page_start": 1517,
        "page_end": 1524,
        "section_kind_reason": "Second analytical index for notable matters in the Syntagma, editorially distinct but using the same alphabetical subject layout.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "file_start": 810,
        "file_end": 814,
        "page_start": 1525,
        "page_end": 1534,
        "section_kind_reason": "Editorial closing contents table, separate from the alphabetical indices and preserving the chapter/contents structure of the volume tail.",
    },
]

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$|^[A-ZÆŒ]\.$")
SECTION_TITLE_RE = re.compile(
    r"^(?:INDICES?\s+ANALYTICI\.?|INDEX\s+RERUM\s+ET\s+VERBORUM\s+MEMORABILIUM|INDEX\s+ANALYTICUS(?:\s+RERUM\s+NOTABILIORUM\s+QUÆ\s+IN\s+SYNTAGMATE\s+INVENIUNTUR\.)?|ORDO\s+RERUM(?:\s+QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?)$",
    re.IGNORECASE,
)
ENTRY_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=(?:[A-ZÆŒ]|[Α-Ω]|[α-ω]|v\.\s))")
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–]\s*(\d{1,4})(?:\s*([ab]))?")
PAGE_COL_RE = re.compile(r"(?<!\d)(\d{1,4})\s*([ab])\b")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.)\b", re.IGNORECASE)
V_REF_RE = re.compile(r"^(?:v\.|vid\.|vide|voir|cf\.)\b", re.IGNORECASE)
LEADING_PAGE_ONLY_RE = re.compile(r"^\d{3,4}(?:\s+\d{3,4})?$")
CAP_RE = re.compile(r"^(?:Cap\.|Caput|TITULUS|Titulus)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
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


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


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
    for raw in parsed["all_text"].splitlines():
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
        header = page_text["header_text"]
        body = page_text["body_text"]
        for blob in (header, body[:140]):
            for match in PAGE_RE.finditer(blob):
                value = int(match.group(1))
                if 0 < value < 10000 and value not in mapping:
                    mapping[value] = str(path)
    return mapping


def target_for_page(page: int | None, page_map: dict[int, str], source_root: Path) -> str | None:
    if page is None:
        return None
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        page_text = extract_page_text(path)
        if needle.search(page_text["header_text"]):
            return str(path)
    return None


def split_entry_text(line: str) -> list[str]:
    text = normalize(line) or ""
    if not text:
        return []
    parts = [part.strip() for part in ENTRY_SPLIT_RE.split(text) if part.strip()]
    return parts or [text]


def infer_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if text.startswith("TITULUS ") or text.startswith("Cap.") or text.startswith("Caput "):
        return text
    if V_REF_RE.match(text):
        return None
    if "," in text:
        text = text.split(",", 1)[0]
    text = re.sub(r"\s+\d{1,4}(?:\s*[-–]\s*\d{1,4})?(?:\s*[ab])?(?:\s*(?:seqq\.|seq\.))?\s*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" .;:")
    return text or None


def infer_entry_kind(section_kind: str, entry_raw: str) -> str:
    text = normalize(entry_raw) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if V_REF_RE.match(text) or re.search(r"\b(?:v\.|vid\.|vide|voir|cf\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    if CAP_RE.match(text) or text.startswith("TITULUS ") or text.startswith("Caput "):
        return "heading_group"
    if text.startswith("N ") and len(text) <= 4:
        return "heading_group"
    return "lemma"


def extract_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    text = normalize(entry_raw) or ""
    if not text:
        return refs, last_page

    working = text
    if IBID_RE.search(working):
        if last_page is not None:
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


def body_lines_for_section(files: list[Path], section: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for path in files:
        seq = file_seq(path)
        if seq < section["file_start"] or seq > section["file_end"]:
            continue
        for line in extract_lines(path):
            rows.append((str(path), line))
    return rows


def build_section_entries(
    section: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
    entry_start: int,
    node_start: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    last_page: int | None = None
    entry_order = entry_start
    node_order = node_start
    helper_limit = 2 if section["section_kind"] != "ordo_rerum" else 1
    helper_count = 0

    def add_letter_node(letter: str, source_file: str) -> str:
        nonlocal node_order
        if current_letter == letter:
            return f"{VOLUME_ID}:node:{node_order:06d}"
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.97,
                "raw_json": {"source_file": source_file, "section_kind": section["section_kind"]},
            }
        )
        return node_key

    for source_file, line in body_lines_for_section(files, section):
        cleaned = normalize(line) or ""
        if not cleaned:
            continue
        if SECTION_TITLE_RE.fullmatch(cleaned):
            continue
        if FOOTER_RE.fullmatch(cleaned):
            continue
        if section["section_kind"] != "ordo_rerum" and (cleaned == "I" or cleaned == "II" or LETTER_RE.fullmatch(cleaned.rstrip("."))):
            letter = cleaned.rstrip(".")
            if letter != current_letter:
                current_letter = letter
                add_letter_node(letter, source_file)
            continue
        if section["section_kind"] == "ordo_rerum" and cleaned.startswith("ORDO RERUM"):
            continue

        fragments = split_entry_text(cleaned)
        if section["section_kind"] == "ordo_rerum" and not fragments:
            fragments = [cleaned]

        for fragment in fragments:
            seg = normalize(fragment) or ""
            if not seg:
                continue
            if SECTION_TITLE_RE.fullmatch(seg) or FOOTER_RE.fullmatch(seg):
                continue
            if section["section_kind"] != "ordo_rerum" and LETTER_RE.fullmatch(seg.rstrip(".")):
                letter = seg.rstrip(".")
                if letter != current_letter:
                    current_letter = letter
                    add_letter_node(letter, source_file)
                continue

            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
            refs_for_entry, last_page = extract_refs(seg, last_page)
            page_hints = [ref["page_ref_int"] for ref in refs_for_entry if ref.get("page_ref_int")]
            inferred_printed_page = page_hints[0] if page_hints else None
            entry_kind = infer_entry_kind(section["section_kind"], seg)
            lemma_raw = infer_lemma(seg)
            if lemma_raw is None and section["section_kind"] == "ordo_rerum":
                lemma_raw = seg

            anchor_file = source_file
            target_file_best = target_for_page(inferred_printed_page, page_map, Path(section["source_root"])) if inferred_printed_page else source_file

            entry_payload = {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": seg,
                "context_raw": seg if len(seg) < 220 else seg[:220],
                "heading_letter": current_letter if section["section_kind"] != "ordo_rerum" else None,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": str(files[0]) if files else None,
                "editorial_anchor_file": anchor_file,
                "target_file_best": target_file_best,
                "confidence": 0.82 if section["section_kind"] != "ordo_rerum" else 0.91,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section["section_kind"],
                    "section_kind_reason": section["section_kind_reason"],
                    "page_hints": page_hints,
                },
            }
            if entry_kind == "cross_reference":
                entry_payload["confidence"] = 0.72
            if refs_for_entry and helper_count < helper_limit:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or seg,
                        "query_names": [q for q in [lemma_raw, seg.split(",")[0].strip()] if q],
                        "page_hints": [str(page) for page in page_hints[:3]],
                        "page_hint_ints": page_hints[:3],
                        "context_raw": seg[:160],
                    }
                )
                helper_count += 1
            for ref_idx, ref in enumerate(refs_for_entry, start=1):
                ref_payload = {
                    "entry_key": entry_key,
                    "ref_order": ref_idx,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": target_for_page(ref["page_ref_int"], page_map, Path(section["source_root"])),
                    "target_file_probability": 0.97 if target_for_page(ref["page_ref_int"], page_map, Path(section["source_root"])) else None,
                    "section_start_file": str(files[0]) if files else None,
                    "editorial_anchor_file": anchor_file,
                    "confidence": 0.88 if section["section_kind"] != "ordo_rerum" else 0.92,
                    "raw_json": {
                        "source_file": source_file,
                        "section_kind": section["section_kind"],
                    },
                }
                refs.append(ref_payload)
            entries.append(entry_payload)

    return entries, nodes, refs, entry_order, node_order, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    try:
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
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        helper_output_json.write_text(
            json.dumps(
                {
                    "volume_id": VOLUME_ID,
                    "source_root": str(helper_request_json.parent),
                    "status": "timeout",
                    "entries": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return read_json(helper_output_json, {})
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def apply_helper_evidence(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    entry_map = {entry["entry_key"]: entry for entry in entries}
    for entry_id, helper in by_id.items():
        entry = entry_map.get(entry_id)
        if not entry:
            continue
        best = helper.get("best_candidate") or {}
        entry["raw_json"]["helper"] = {
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
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")
        for ref in refs:
            if ref["entry_key"] != entry_id:
                continue
            if best.get("file"):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")
                ref.setdefault("raw_json", {})["helper_best_file"] = best.get("file")
                ref["raw_json"]["helper_best_probability"] = best.get("probability")


def build_helper_request(source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    sections = []
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0

    for section in SECTION_DEFS:
        section["source_root"] = str(source_root)
        section_entries, section_nodes, section_refs, entry_order, node_order, section_helper_entries = build_section_entries(
            section,
            files,
            page_map,
            entry_order,
            node_order,
        )
        entries.extend(section_entries)
        nodes.extend(section_nodes)
        refs.extend(section_refs)
        helper_entries.extend(section_helper_entries)
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
                "file_start": str(next(p for p in files if file_seq(p) == section["file_start"])) if files else None,
                "file_end": str(next(p for p in files if file_seq(p) == section["file_end"])) if files else None,
                "confidence": 0.95 if section["section_kind"] != "ordo_rerum" else 0.98,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "evidence_files": [
                        str(next(p for p in files if file_seq(p) == section["file_start"])) if files else None,
                        str(next(p for p in files if file_seq(p) == section["file_end"])) if files else None,
                    ],
                },
            }
        )

    helper_request = build_helper_request(source_root, helper_entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_entries else {"status": "empty", "entries": []}
    apply_helper_evidence(entries, refs, helper_output)

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the two analytical index blocks and the closing ORDO RERUM from the OCR tail, with page-locators resolved from local page headers and helper corroboration preserved where available.",
        "evidence_files": [
            str(files[0]) if files else None,
            str(files[-1]) if files else None,
        ],
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    notes = [
        "PG104 contains two distinct INDICES ANALYTICI blocks followed by an ORDO RERUM closing table.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
        "OCR file suffixes were treated separately from printed page numbers when resolving target files.",
    ]
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
            "current_focus": "Finalize PG104 analytical index payload and validate section boundaries, page locators, and ORDO RERUM closure entries.",
            "completed": [
                "confirmed two analytical index sections",
                "confirmed closing ORDO RERUM block",
                "built and ran helper request",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page numbers.",
                "Preserve remissions such as vid. and ibid. at entry level when they do not add a real locator.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG104 alphabetical payload.")
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

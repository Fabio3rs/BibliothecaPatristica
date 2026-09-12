#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg106_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG106/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG106_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG106_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG106 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG106_alphabetical_indices.json

Builds the PG106 alphabetical-index payload from the OCR tail, including the
Arethas index, the shorter "INDEX RERUM ET SENTENTIARUM" block, and the final
ORDO RERUM contents table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG106"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 106"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG106/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG106_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG106_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG106_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG106"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM.",
        "heading_norm": "index rerum",
        "heading_letter": None,
        "page_start": 1395,
        "page_end": 1410,
        "file_start_seq": 707,
        "file_end_seq": 714,
        "section_kind_reason": "Alphabetical analytical index headed INDEX RERUM in the Arethas commentary window; letter groups A-Z are present in the OCR tail.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:002",
        "section_order": 2,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX RERUM ET SENTENTIARUM.",
        "heading_norm": "index rerum et sententiarum",
        "heading_letter": None,
        "page_start": 1411,
        "page_end": 1412,
        "file_start_seq": 715,
        "file_end_seq": 715,
        "section_kind_reason": "Short alphabetical subject/sentence index that follows the Arethas index and precedes the editorial ORDO RERUM block.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "heading_letter": None,
        "page_start": 1413,
        "page_end": 1424,
        "file_start_seq": 716,
        "file_end_seq": 725,
        "section_kind_reason": "Closing contents block for the tome; editorial table of contents distinct from the alphabetical indexes.",
    },
]

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?!\d)")
LETTER_SPLIT_RE = re.compile(r"(?<=\d\.)\s+(?=[A-ZÆŒΑ-Ω])")
REMISSION_SPLIT_RE = re.compile(r"\b(?:ibid\.?|id\.?|cf\.?|vide|vid\.?)\s+(?=[A-ZÆŒΑ-Ω])", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    return SPACE_RE.sub(" ", text).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = SPACE_RE.sub(" ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    blocks: list[dict[str, Any]] = []
    for key in ("header_text", "body_text", "footer_text"):
        text = normalize(parsed.get(key) or "")
        if not text:
            continue
        blocks.append({"kind": key, "text": text})
    return blocks


def extract_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for block in extract_blocks(path):
        if block["kind"] == "footer_text" and FOOTER_RE.fullmatch(block["text"]):
            continue
        for raw_line in block["text"].splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def page_number_from_header(text: str) -> tuple[int | None, int | None]:
    nums = [int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text)]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], None
    return nums[0], nums[-1]


def section_for_seq(seq: int) -> dict[str, Any]:
    if 707 <= seq <= 714:
        return SECTION_SPECS[0]
    if seq == 715:
        return SECTION_SPECS[1]
    return SECTION_SPECS[2]


def split_fragments(line: str) -> list[str]:
    parts = [line]
    for splitter in (LETTER_SPLIT_RE, REMISSION_SPLIT_RE):
        new_parts: list[str] = []
        for part in parts:
            if splitter is REMISSION_SPLIT_RE:
                new_parts.extend([p.strip() for p in splitter.split(part) if p.strip()])
            else:
                new_parts.extend([p.strip() for p in splitter.split(part) if p.strip()])
        parts = new_parts
    return [part.strip(" \t;") for part in parts if part.strip(" \t;")]


def lemma_from_fragment(fragment: str, section_kind: str) -> str | None:
    text = fragment.strip()
    if not text:
        return None
    if section_kind == "ordo_rerum":
        text = re.sub(r"\s+\d{1,4}(?:\s*[-–]\s*\d{1,4})?\s*$", "", text).strip(" ,;:.")
        return text or None
    m = PAGE_RE.search(text)
    if m:
        text = text[: m.start()].rstrip(" ,;:.")
    return text.strip(" ,;:.") or None


def refs_from_fragment(fragment: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    found = False
    for m in PAGE_RE.finditer(fragment):
        found = True
        start_raw = m.group(1)
        end_raw = m.group(2)
        ref_raw = m.group(0).strip(" ,;:.")
        if start_raw.lower() == "ibid":
            page_int = last_page
        else:
            page_int = int(start_raw)
        refs.append(
            {
                "ref_order": len(refs) + 1,
                "ref_kind": "editorial_range" if end_raw else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": start_raw if end_raw else None,
                "range_end_raw": end_raw,
                "confidence": 0.9,
            }
        )
        if page_int is not None:
            last_page = page_int
    if not found and re.search(r"\b(?:ibid\.?|id\.?|vid\.?|vide|voir|cf\.)\b", fragment, re.IGNORECASE):
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "unresolved",
                "ref_raw": fragment.strip(),
                "page_ref_raw": None,
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "confidence": 0.35,
            }
        )
    return refs, last_page


def build_helper_request(entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(helper_request_json), "--output", str(helper_output_json), "--pretty"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {"entries": []})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[entry_id] = item
    return mapping


def make_entry(
    *,
    section: dict[str, Any],
    entry_order: int,
    entry_kind: str,
    lemma_raw: str,
    entry_raw: str,
    section_start_file: str,
    editorial_anchor_file: str,
    target_file_best: str,
    heading_letter: str | None,
    inferred_printed_page: int,
    raw_json: dict[str, Any],
    parent_node_key: str | None = None,
) -> dict[str, Any]:
    lemma_display = lemma_raw
    return {
        "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
        "section_key": section["section_key"],
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_display,
        "lemma_norm": sort_norm(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": entry_raw,
        "context_raw": entry_raw if len(entry_raw) < 220 else entry_raw[:220].rstrip() + "...",
        "heading_letter": heading_letter,
        "inferred_printed_page": inferred_printed_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_file_best,
        "confidence": 0.85 if section["section_kind"] == "ordo_rerum" else 0.82,
        "raw_json": raw_json,
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    sections = [
        {
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
            "file_start": str(source_root / f"*{spec['file_start_seq']:03d}.txt"),
            "file_end": str(source_root / f"*{spec['file_end_seq']:03d}.txt"),
            "confidence": 0.95 if spec["section_kind"] != "ordo_rerum" else 0.94,
            "raw_json": {
                "section_kind_reason": spec["section_kind_reason"],
                "file_start_seq": spec["file_start_seq"],
                "file_end_seq": spec["file_end_seq"],
            },
        }
        for spec in SECTION_SPECS
    ]

    section_files = {
        1: [path for path in files if 707 <= file_seq(path) <= 714],
        2: [path for path in files if file_seq(path) == 715],
        3: [path for path in files if 716 <= file_seq(path) <= 725],
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    helper_entries: list[dict[str, Any]] = []
    entry_cursor = 1
    node_cursor = 1

    for section_idx, section in enumerate(sections, start=1):
        current_section = SECTION_SPECS[section_idx - 1]
        start_file = section_files[section_idx][0]
        end_file = section_files[section_idx][-1]
        section["file_start"] = str(start_file)
        section["file_end"] = str(end_file)

        current_letter: str | None = None
        current_node_key: str | None = None
        last_page: int | None = current_section["page_start"]
        current_heading: str | None = None

        if current_section["section_kind"] == "ordo_rerum":
            major_nodes = []
        else:
            major_nodes = None

        for path in section_files[section_idx]:
            lines = extract_lines(path)
            header_text = ""
            for block in extract_blocks(path):
                if block["kind"] == "header_text":
                    header_text = block["text"]
                    break
            header_start, header_end = page_number_from_header(header_text)
            if header_start is not None and current_section["page_start"] is None:
                current_section["page_start"] = header_start
            if header_start is not None:
                last_page = header_start

            for line in lines:
                if FOOTER_RE.fullmatch(line):
                    continue
                if section_idx in (1, 2) and LETTER_RE.fullmatch(line):
                    current_letter = line
                    current_node_key = f"{VOLUME_ID}:node:{node_cursor:04d}"
                    node_cursor += 1
                    nodes.append(
                        {
                            "node_key": current_node_key,
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source_file": str(path), "note": "letter divider"},
                        }
                    )
                    continue
                if section_idx == 3 and normalize(line).endswith(".") and not PAGE_RE.search(line):
                    if re.match(r"^(JOSEPPUS|NICEPHORUS PHILOSOPHUS|ANDREAS CÆSAREÆ CAPPADOCIÆ ARCHIEPISCOPUS|JOANNES GEOMETRA|COSMAS VESTITOR|LEO PATRICIUS|ATHANASIUS CORINTHIORUM EPISCOPUS|OPUSCULA GRÆCA INCERTÆ ÆTATIS)\.?", line, re.IGNORECASE):
                        current_heading = line.strip()
                        current_node_key = f"{VOLUME_ID}:node:{node_cursor:04d}"
                        node_cursor += 1
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": section["section_key"],
                                "parent_node_key": None,
                                "node_order": len(nodes) + 1,
                                "node_kind": "heading_group",
                                "label_raw": line.strip(),
                                "label_norm": sort_norm(line),
                                "label_sort": sort_norm(line),
                                "node_level": 1,
                                "confidence": 0.95,
                                "raw_json": {"source_file": str(path), "note": "major ordo heading"},
                            }
                        )
                        continue

                for fragment in split_fragments(line):
                    if not fragment:
                        continue
                    if section_idx == 3 and not PAGE_RE.search(fragment):
                        if re.match(r"^(Notitia|Commentarius|Oratio|Proœmium|Præfatio|Caput|Cap\.|Eordium|Appendix|Sermo|Hymni|Epigrammata|Elogium|Carmina|I\.|II\.|III\.|IV\.|V\.|VI\.|VII\.|VIII\.|IX\.|X\.|XI\.|XII\.|XIII\.|XIV\.|XV\.|XVI\.|XVII\.|XVIII\.|XIX\.|XX\.|XXI\.|XXII\.|XXIII\.|XXIV\.|XXV\.|XXVI\.|XXVII\.|XXVIII\.|XXIX\.|XXX\.|XXXI\.|XXXII\.|XXXIII\.|XXXIV\.|XXXV\.|XXXVI\.|XXXVII\.|XXXVIII\.|XXXIX\.|XL\.|XLI\.|XLII\.|XLIII\.|XLIV\.|XLV\.|XLVI\.|XLVII\.|XLVIII\.|XLIX\.|L\.|LI\.|LII\.|LIII\.|LIV\.|LV\.|LVI\.|LVII\.|LVIII\.|LIX\.|LX\.|LXI\.|LXII\.|LXIII\.|LXIV\.|LXV\.|LXVI\.|LXVII\.|LXVIII\.|LXIX\.|LXX\.|LXXI\.|LXXII\.|LXXIII\.|LXXIV\.|LXXV\.|LXXVI\.|LXXVII\.|LXXVIII\.|LXXIX\.|LXXX\.|LXXXI\.|LXXXII\.|LXXXIII\.|LXXXIV\.|LXXXV\.|LXXXVI\.|LXXXVII\.|LXXXVIII\.|LXXXIX\.|XC\.|XCI\.|XCII\.|XCIII\.|XCIV\.|XCV\.|XCVI\.|XCVII\.|XCVIII\.|XCIX\.|C\.|CI\.|CII\.|CIII\.|CIV\.|CV\.|CVI\.|CVII\.|CVIII\.|CIX\.|CX\.|CXI\.|CXII\.|CXIII\.|CXIV\.|CXV\.|CXVI\.|CXVII\.|CXVIII\.|CXIX\.|CXX\.|CXXI\.|CXXII\.|CXXIII\.|CXXIV\.|CXXV\.|CXXVI\.|CXXVII\.|CXXVIII\.|CXXIX\.|CXXX\.|CXXXI\.|CXXXII\.|CXXXIII\.|CXXXIV\.|CXXXV\.|CXXXVI\.|CXXXVII\.|CXXXVIII\.|CXXXIX\.|CXL\.|CXLI\.|CXLII\.|CXLIII\.|CXLIV\.|CXLV\.|CXLVI\.|CXLVII\.|CXLVIII\.|CXLIX\.|CL\.|CLI\.|CLII\.|CLIII\.|CLIV\.|CLV\.|CLVI\.|CLVII\.|CLVIII\.|CLIX\.|CLX\.|CLXI\.|CLXII\.|CLXIII\.|CLXIV\.|CLXV\.|CLXVI\.|CLXVII\.|CLXVIII\.|CLXIX\.|CLXX\.|CLXXI\.|CLXXII\.|CLXXIII\.|CLXXIV\.|CLXXV\.|CLXXVI\.|CLXXVII\.|CLXXVIII\.|CLXXIX\.|CLXXX\.|CLXXXI\.|CLXXXII\.|CLXXXIII\.|CLXXXIV\.|CLXXXV\.|CLXXXVI\.|CLXXXVII\.|CLXXXVIII\.|CLXXXIX\.|CXC\.|CXCI\.|CXCII\.|CXCIII\.|CXCIV\.|CXCV\.|CXCVI\.|CXCVII\.|CXCVIII\.|CXCIX\.|CC\.|CCI\.|CCII\.|CCIII\.|CCIV\.|CCV\.|CCVI\.|CCVII\.|CCVIII\.|CCIX\.|CCX\.|CCXI\.|CCXII\.|CCXIII\.|CCXIV\.|CCXV\.|CCXVI\.|CCXVII\.|CCXVIII\.|CCXIX\.|CCXX\.|CCXXI\.|CCXXII\.|CCXXIII\.|CCXXIV\.|CCXXV\.|CCXXVI\.|CCXXVII\.|CCXXVIII\.|CCXXIX\.|CCXXX\.|CCXXXI\.|CCXXXII\.|CCXXXIII\.|CCXXXIV\.|CCXXXV\.|CCXXXVI\.|CCXXXVII\.|CCXXXVIII\.|CCXXXIX\.|CCXL\.|CCXLI\.|CCXLII\.|CCXLIII\.|CCXLIV\.|CCXLV\.|CCXLVI\.|CCXLVII\.|CCXLVIII\.|CCXLIX\.|CCL\.|CCLI\.|CCLII\.|CCLIII\.|CCLIV\.|CCLV\.|CCLVI\.|CCLVII\.|CCLVIII\.|CCLIX\.|CCLX\.|CCLXI\.|CCLXII\.|CCLXIII\.|CCLXIV\.|CCLXV\.|CCLXVI\.|CCLXVII\.|CCLXVIII\.|CCLXIX\.|CCLXX\.|CCLXXI\.|CCLXXII\.|CCLXXIII\.|CCLXXIV\.|CCLXXV\.|CCLXXVI\.|CCLXXVII\.|CCLXXVIII\.|CCLXXIX\.|CCLXXX\.|CCLXXXI\.|CCLXXXII\.|CCLXXXIII\.|CCLXXXIV\.|CCLXXXV\.|CCLXXXVI\.|CCLXXXVII\.|CCLXXXVIII\.|CCLXXXIX\.|CCXC\.|CCXCI\.|CCXCII\.|CCXCIII\.|CCXCIV\.|CCXCV\.|CCXCVI\.|CCXCVII\.|CCXCVIII\.|CCXCIX\.|CCC\.)", fragment):
                            pass
                        entry_order = entry_cursor
                        entry_cursor += 1
                        lemma_raw = fragment.strip().rstrip(" ,;:.")
                        entry = make_entry(
                            section=section,
                            entry_order=entry_order,
                            entry_kind="heading_group",
                            lemma_raw=lemma_raw,
                            entry_raw=fragment,
                            section_start_file=str(start_file),
                            editorial_anchor_file=str(path),
                            target_file_best=str(path),
                            heading_letter=None,
                            inferred_printed_page=section["page_start"],
                            raw_json={
                                "source_file": str(path),
                                "section_kind": section["section_kind"],
                                "note": "contents-table line item",
                            },
                            parent_node_key=current_node_key,
                        )
                        entries.append(entry)
                        helper_entries.append(
                            {
                                "entry_id": f"{VOLUME_ID.lower()}_{entry_order:04d}",
                                "lemma_raw": lemma_raw,
                                "query_names": [lemma_raw, fragment],
                                "page_hints": [str(section["page_start"])],
                                "page_hint_ints": [section["page_start"]],
                                "context_raw": fragment,
                            }
                        )
                        continue

                    lemma_raw = lemma_from_fragment(fragment, section["section_kind"]) or fragment.strip()
                    refs_for_fragment, last_page = refs_from_fragment(fragment, last_page)
                    entry_kind = "lemma"
                    if section["section_kind"] == "ordo_rerum":
                        entry_kind = "heading_group"
                    elif not refs_for_fragment and re.search(r"\b(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", fragment, re.IGNORECASE):
                        entry_kind = "cross_reference"
                    entry_order = entry_cursor
                    entry_cursor += 1
                    entry = make_entry(
                        section=section,
                        entry_order=entry_order,
                        entry_kind=entry_kind,
                        lemma_raw=lemma_raw,
                        entry_raw=fragment,
                        section_start_file=str(start_file),
                        editorial_anchor_file=str(path),
                        target_file_best=str(path),
                        heading_letter=current_letter,
                        inferred_printed_page=section["page_start"],
                        raw_json={
                            "source_file": str(path),
                            "section_kind": section["section_kind"],
                            "fragment_count": 1,
                            "last_page_seen": last_page,
                        },
                        parent_node_key=current_node_key if section["section_kind"] == "ordo_rerum" else None,
                    )
                    entries.append(entry)
                    for ref in refs_for_fragment:
                        ref["entry_key"] = entry["entry_key"]
                        ref["section_start_file"] = str(start_file)
                        ref["editorial_anchor_file"] = str(path)
                        ref["target_file"] = str(path)
                        ref["target_file_probability"] = 0.84
                        refs.append(ref)
                    if len(entries) <= 12:
                        helper_entries.append(
                            {
                                "entry_id": f"{VOLUME_ID.lower()}_{entry_order:04d}",
                                "lemma_raw": lemma_raw,
                                "query_names": [lemma_raw, fragment],
                                "page_hints": [str(section["page_start"])],
                                "page_hint_ints": [section["page_start"]],
                                "context_raw": fragment,
                            }
                        )

    helper_request = build_helper_request(helper_entries[:60], source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"entries": []}
    helper_entries_map = helper_map(helper_output)

    for entry in entries:
        helper_id = entry.get("raw_json", {}).get("helper_entry_id")
        if helper_id and helper_id in helper_entries_map:
            entry["raw_json"]["helper"] = helper_entries_map[helper_id]
        elif helper_entries_map:
            # Best-effort association by order for the first few entries only.
            idx = int(entry["entry_key"].split(":")[-1])
            helper_id = f"{VOLUME_ID.lower()}_{idx:04d}"
            if helper_id in helper_entries_map:
                entry["raw_json"]["helper"] = helper_entries_map[helper_id]

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PG106 alphabetical payload from OCR tail",
        "completed": [
            "OCR tail inspected for the analytical and ordo sections",
            "helper request written",
            "index target locator executed",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The output intentionally separates the Arethas index, the INDEX RERUM ET SENTENTIARUM block, and the final ORDO RERUM table.",
            "OCR line splitting is conservative; sentence-like fragments that share a physical line may remain grouped in entry_raw.",
        ],
    }
    write_json(TODO_JSON, todo)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Main analytical index in the Arethas commentary window.",
                "Short INDEX RERUM ET SENTENTIARUM block after the first index.",
                "Final ORDO RERUM contents table is serialized separately.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "OCR tail contains recoverable alphabetical and contents-table line items; line splitting is conservative and preserves the literal OCR fragments.",
            "evidence_files": [str(path) for path in files if 707 <= file_seq(path) <= 725],
        },
        "notes": [
            "The helper request was built from representative line items and the helper was run locally for target anchoring.",
            "Letter-group nodes are preserved for the analytical indexes; the ORDO RERUM section uses heading-group nodes for major contents headings.",
        ],
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG106 alphabetical payload from OCR tail sections.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

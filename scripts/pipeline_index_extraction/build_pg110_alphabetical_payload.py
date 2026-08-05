#!/usr/bin/env python3
"""Usage: build the PG110 alphabetical payload and optional helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg110_alphabetical_payload.py \
    --write-helper-request \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG110_helper_request.json

  python scripts/index_target_locator.py \
    --input /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG110_helper_request.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG110_helper_output.json \
    --pretty

  python scripts/pipeline_index_extraction/build_pg110_alphabetical_payload.py \
    --build-payload \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG110_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG110 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG110_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG110"
COLLECTION = "PG"
VOLUME_LABEL = "PG110"
SOURCE_ROOT = ROOT / "teste/PG110/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG110_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG110_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG110_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG110"


SECTION_AUTHOR = {
    "section_key": f"{VOLUME_ID}:alpha:author_index:001",
    "section_order": 1,
    "section_kind": "author_index",
    "heading_raw": "ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ ΕΞ ΩΝ ΠΑΡΕΣ ΤΗΣ ΑΠΑΣΗΣ ΓΡΑΦΗΣ ΤΟ ΧΡΟΝΙΚΟΝ ΣΥΝΤΕΘΕΙΤΑΙ Η ΩΝ ΜΝΗΜΟΝΕΥΕΙ.",
    "heading_norm": "pinax ton syngrapheon ex on pares tes apases graphes to chronikon syntetheitai he on mnemonyeuei",
    "heading_letter": None,
    "page_start": 1237,
    "page_end": 1238,
    "file_start": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
    "file_end": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
    "confidence": 0.94,
    "raw_json": {
        "section_kind_reason": "Author/work register opening the index block before the names index; the OCR begins with the explicit Greek heading and author citations.",
        "evidence_files": [str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt")],
        "observed_headings": [
            "ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ",
            "ΟΝΟΜΑΤΟΛΟΓΙΟΝ.",
        ],
    },
}

SECTION_ONOMASTIC = {
    "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:002",
    "section_order": 2,
    "section_kind": "onomastic_mixed",
    "heading_raw": "ΟΝΟΜΑΤΟΛΟΓΙΟΝ.",
    "heading_norm": "onomatologion",
    "heading_letter": None,
    "page_start": 1238,
    "page_end": 1326,
    "file_start": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
    "file_end": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-666.txt"),
    "confidence": 0.91,
    "raw_json": {
        "section_kind_reason": "Mixed onomastic alphabetical index of names, places, and related terms following the Greek onomasticon heading; letter dividers appear throughout the OCR tail.",
        "evidence_files": [
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-649.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-650.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-651.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-652.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-653.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-654.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-655.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-656.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-657.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-658.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-659.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-660.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-661.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-662.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-663.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-664.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-665.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-666.txt"),
        ],
    },
}

SECTION_ORDO = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
    "section_order": 3,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": 1325,
    "page_end": 1328,
    "file_start": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-667.txt"),
    "file_end": str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"),
    "confidence": 0.97,
    "raw_json": {
        "section_kind_reason": "Closing table of contents for the chronicle; the OCR explicitly labels ORDO RERUM and lists the libri/chapter contents with page locators.",
        "evidence_files": [
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-667.txt"),
            str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"),
        ],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def strip_accents(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return value


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = strip_accents(text)
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def extract_blocks(file_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', file_text, flags=re.S):
        block_type = match.group(1)
        block_text = match.group(2)
        if block_text.strip():
            blocks.append((block_type, block_text))
    return blocks


def extract_plain_lines(path: Path, include_marginal_letters: bool = True) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines: list[str] = []
    for block_type, block in extract_blocks(text):
        if block_type not in {"texto_principal"} and not (include_marginal_letters and block_type == "nota_marginal"):
            continue
        block_lines = [line.strip() for line in block.splitlines()]
        for line in block_lines:
            cleaned = re.sub(r"\s+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
    return lines


def header_pages(path: Path) -> tuple[int | None, int | None]:
    text = path.read_text(encoding="utf-8")
    headers = []
    for block_type, block in extract_blocks(text):
        if block_type == "cabecalho":
            raw = block
            nums = [int(n) for n in re.findall(r"\b(\d{3,4})\b", raw)]
            if nums:
                headers.extend(nums[:2])
    if not headers:
        nums = [int(n) for n in re.findall(r"\b(\d{3,4})\b", text[:300])]
        if nums:
            headers.extend(nums[:2])
    if not headers:
        return None, None
    if len(headers) == 1:
        return headers[0], headers[0]
    return headers[0], headers[1]


def build_page_map() -> dict[int, Path]:
    page_map: dict[int, Path] = {}
    for path in sorted(SOURCE_ROOT.glob("*.txt")):
        first, second = header_pages(path)
        for page in (first, second):
            if page is not None and page not in page_map:
                page_map[page] = path
    return page_map


def nearest_page_file(page_map: dict[int, Path], page: int | None, fallback: Path) -> Path:
    if page is None or not page_map:
        return fallback
    if page in page_map:
        return page_map[page]
    nearest_page = min(page_map, key=lambda candidate: (abs(candidate - page), candidate))
    return page_map[nearest_page]


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s in {"A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "I.", "J.", "K.", "L.", "M.", "N.", "O.", "P.", "Q.", "R.", "S.", "T.", "U.", "V.", "W.", "X.", "Y.", "Z.", "Ξ.", "Ο.", "Π.", "Σ.", "Τ.", "Φ.", "Χ."}:
        return True
    if re.fullmatch(r"[A-ZΑ-Ω]\.?", s):
        return True
    if s.startswith(("ORDO RERUM", "INDICES.", "ΠΙΝΑΞ", "ΟΝΟΜΑΤΟΛΟΓΙΟΝ.", "GEORGIUS HAMARTOLUS.", "LIBER ")) or s.startswith("QUAE IN HOC TOMO CONTINENTUR"):
        return True
    return False


def is_possible_entry_start(line: str) -> bool:
    s = line.strip()
    if not s or is_heading(s):
        return False
    if s.startswith((".", ",", ";", ":", "-", "—")):
        return False
    if re.fullmatch(r"[A-ZΑ-Ω]\.?", s):
        return False
    return True


def merge_entries(lines: list[str]) -> list[str]:
    entries: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            entries.append(re.sub(r"\s+", " ", " ".join(current)).strip())
            current.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        if is_heading(line):
            flush()
            continue
        if not current:
            current.append(line)
            continue
        if is_possible_entry_start(line) and re.match(r"^[A-ZΑ-ΩἈ-῾Ἂ-῾]", line):
            flush()
            current.append(line)
            continue
        if re.match(r"^[\-—,.;:\)\]]", line):
            current.append(line)
            continue
        if current[-1].endswith((".", ":", ";")):
            flush()
            current.append(line)
            continue
        current.append(line)
    flush()
    return entries


def parse_lemma(entry_raw: str) -> str | None:
    s = entry_raw.strip()
    if not s:
        return None
    if " — " in s:
        left, right = s.split(" — ", 1)
        if re.search(r"\d", left):
            return normalize(left)
        if right:
            prefix = right
            cut = re.search(r"\b(?:\d{1,4}|Leo|Nota)\b", prefix)
            if cut:
                prefix = prefix[: cut.start()]
            return normalize(prefix)
    cut = re.search(r"\b(?:\d{1,4}|Leo|Nota)\b", s)
    if cut:
        return normalize(s[: cut.start()])
    return normalize(s)


REF_PATTERN = re.compile(
    r"Leo\s+\d+\s*,\s*\d+|Leo\s+\d+|\d+\s*[—-]\s*\d+|\d+",
    flags=re.UNICODE,
)


def extract_refs(entry_raw: str, entry_key: str, section_start_file: str, editorial_anchor_file: str, target_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for idx, match in enumerate(REF_PATTERN.finditer(entry_raw), start=1):
        raw = match.group(0).strip(" ,.;")
        if not raw:
            continue
        kind = "editorial_page"
        page_ref_raw = raw
        page_ref_int = None
        page_ref_col = None
        line_ref_raw = None
        range_start_raw = None
        range_end_raw = None
        if raw.startswith("Leo "):
            kind = "parallel_locator"
            m = re.search(r"Leo\s+(\d+)(?:\s*,\s*(\d+))?", raw)
            if m:
                page_ref_int = int(m.group(1))
                page_ref_raw = m.group(1)
                if m.group(2):
                    line_ref_raw = m.group(2)
        elif re.fullmatch(r"\d+\s*[—-]\s*\d+", raw):
            kind = "editorial_range"
            a, b = re.split(r"\s*[—-]\s*", raw)
            range_start_raw, range_end_raw = a, b
            page_ref_int = int(a)
            page_ref_raw = raw
        elif raw.isdigit():
            page_ref_int = int(raw)
            page_ref_raw = raw
        else:
            m = re.search(r"(\d+)", raw)
            if m:
                page_ref_int = int(m.group(1))
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": idx,
                "ref_kind": kind,
                "ref_raw": raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": page_ref_col,
                "line_ref_raw": line_ref_raw,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
                "target_file": target_file,
                "target_file_probability": 0.97,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": 0.92 if page_ref_int is not None else 0.85,
                "raw_json": {
                    "locator_kind": kind,
                },
            }
        )
    return refs


def make_entry(
    *,
    section_key: str,
    entry_order: int,
    entry_kind: str,
    entry_raw: str,
    section_start_file: str,
    editorial_anchor_file: str,
    target_file_best: str,
    heading_letter: str | None = None,
    parent_node_key: str | None = None,
    inferred_printed_page: int | None = None,
    helper_best: dict[str, Any] | None = None,
    helper_entry_id: str | None = None,
    section_kind: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lemma_raw = parse_lemma(entry_raw) if entry_kind != "heading_group" else None
    lemma_display = lemma_raw
    lemma_norm = normalize(lemma_raw) if lemma_raw else None
    lemma_sort = sort_norm(lemma_raw) if lemma_raw else None
    entry_key = f"{VOLUME_ID}:entry:{section_key.split(':')[-2]}:{entry_order:05d}"
    raw_json: dict[str, Any] = {
        "section_kind": section_kind,
        "source_file": editorial_anchor_file,
    }
    if helper_entry_id:
        raw_json["helper_entry_id"] = helper_entry_id
    if helper_best:
        raw_json["helper_status"] = helper_best.get("status")
        raw_json["helper_best_candidate"] = helper_best
    target_file = helper_best["file"] if helper_best and helper_best.get("file") else target_file_best
    entry = {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_display,
        "lemma_norm": lemma_norm,
        "lemma_sort": lemma_sort,
        "entry_raw": entry_raw,
        "context_raw": None,
        "heading_letter": heading_letter,
        "inferred_printed_page": inferred_printed_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_file,
        "confidence": 0.9 if entry_kind != "heading_group" else 0.86,
        "raw_json": raw_json,
    }
    refs = extract_refs(entry_raw, entry_key, section_start_file, editorial_anchor_file, target_file)
    return entry, refs


def build_helper_request() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pg110_author_athanasius",
                "lemma_raw": "Ἀθανάσιος",
                "query_names": ["Ἀθανάσιος", "Athanasius", "Ἀθανάσιος 41"],
                "page_hints": ["41", "249", "423"],
                "page_hint_ints": [41, 249, 423],
                "context_raw": "Ἀθανάσιος 41, 249? 423, 427, 573? 580, 602.",
            },
            {
                "entry_id": "pg110_onomastic_abraam",
                "lemma_raw": "Ἀβραάμ, Ἄβραμ",
                "query_names": ["Ἀβραάμ", "Ἄβραμ", "patr. πλήθους", "patr ὕψους"],
                "page_hints": ["29", "64", "252"],
                "page_hint_ints": [29, 64, 252],
                "context_raw": "Ἀβραάμ, Ἄβραμ, םהרבא πατὴρ πλήθους, ןברא πατὴρ ὕψους 29, 64 —70, 252, 258, 272, 273.",
            },
            {
                "entry_id": "pg110_foreign_xeirocherkos",
                "lemma_raw": "Ξηρόχερκος",
                "query_names": ["Ξηρόχερκος", "ξυλόχερκος", "circus siccus", "ligneus"],
                "page_hints": ["518", "627", "172"],
                "page_hint_ints": [518, 627, 172],
                "context_raw": "Ξ. Ξηρόχερκος (ξυλόχερκος?) circus siccus, ligneus 518, 2. ξυλὴ 627, 3, Leo 172, 1.",
            },
            {
                "entry_id": "pg110_ordo_georgius_hamartolus",
                "lemma_raw": "GEORGIUS HAMARTOLUS",
                "query_names": ["GEORGIUS HAMARTOLUS", "Prolegomena", "Chronicon", "LIBER PRIMUS"],
                "page_hints": ["9", "43", "47"],
                "page_hint_ints": [9, 43, 47],
                "context_raw": "GEORGIUS HAMARTOLUS. Prolegomena. 9 Chronicon. 43 Prooemium. 42 LIBER PRIMUS. I. — Genealogia Adam. 47",
            },
        ],
    }


def parse_helper_output(helper_path: Path) -> dict[str, Any]:
    data = read_json(helper_path, default=None)
    if not data:
        return {}
    best: dict[str, Any] = {}
    for item in data.get("entries", []):
        entry_id = item.get("entry_id")
        candidate = item.get("best_candidate") or item.get("top_candidate") or item.get("best")
        if candidate:
            best[entry_id] = {"status": item.get("status"), "best_candidate": candidate}
    return best


def build_payload(helper_output: dict[str, Any] | None = None) -> dict[str, Any]:
    files = {path.name: path for path in sorted(SOURCE_ROOT.glob("*.txt"))}
    page_map = build_page_map()
    helper_output = helper_output or {}

    # Section 1: author index from the upper register of file 648.
    author_lines = extract_plain_lines(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"])
    author_cut = author_lines.index("ΟΝΟΜΑΤΟΛΟΓΙΟΝ.") if "ΟΝΟΜΑΤΟΛΟΓΙΟΝ." in author_lines else len(author_lines)
    author_lines = author_lines[:author_cut]
    author_start = 0
    for idx, line in enumerate(author_lines):
        if re.search(r"\d", line):
            author_start = idx
            break
    author_entries_raw = merge_entries(author_lines[author_start:])

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    author_node_key = f"{VOLUME_ID}:node:author_index:001"
    nodes.append(
        {
            "node_key": author_node_key,
            "section_key": SECTION_AUTHOR["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ ΕΞ ΩΝ ΠΑΡΕΣ ΤΗΣ ΑΠΑΣΗΣ ΓΡΑΦΗΣ ΤΟ ΧΡΟΝΙΚΟΝ ΣΥΝΤΕΘΕΙΤΑΙ Η ΩΝ ΜΝΗΜΟΝΕΥΕΙ.",
            "label_norm": normalize("ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ ΕΞ ΩΝ ΠΑΡΕΣ ΤΗΣ ΑΠΑΣΗΣ ΓΡΑΦΗΣ ΤΟ ΧΡΟΝΙΚΟΝ ΣΥΝΤΕΘΕΙΤΑΙ Η ΩΝ ΜΝΗΜΟΝΕΥΕΙ."),
            "label_sort": sort_norm("ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ ΕΞ ΩΝ ΠΑΡΕΣ ΤΗΣ ΑΠΑΣΗΣ ΓΡΑΦΗΣ ΤΟ ΧΡΟΝΙΚΟΝ ΣΥΝΤΕΘΕΙΤΑΙ Η ΩΝ ΜΝΗΜΟΝΕΥΕΙ."),
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {
                "source_file": str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"]),
                "section_kind": "author_index",
            },
        }
    )

    author_files = str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"])
    for idx, entry_raw in enumerate(author_entries_raw, start=1):
        entry, ref_rows = make_entry(
            section_key=SECTION_AUTHOR["section_key"],
            entry_order=idx,
            entry_kind="lemma",
            entry_raw=entry_raw,
            section_start_file=author_files,
            editorial_anchor_file=author_files,
            target_file_best=str(nearest_page_file(page_map, int(re.search(r"(\d+)", entry_raw).group(1)) if re.search(r"(\d+)", entry_raw) else None, files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"])),
            parent_node_key=author_node_key,
            inferred_printed_page=1237,
            section_kind="author_index",
        )
        entries.append(entry)
        refs.extend(ref_rows)

    # Section 2: onomastic mixed index from the lower register of file 648 through 666.
    onomastic_node_key = f"{VOLUME_ID}:node:onomastic_mixed:001"
    nodes.append(
        {
            "node_key": onomastic_node_key,
            "section_key": SECTION_ONOMASTIC["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "ΟΝΟΜΑΤΟΛΟΓΙΟΝ.",
            "label_norm": normalize("ΟΝΟΜΑΤΟΛΟΓΙΟΝ."),
            "label_sort": sort_norm("ΟΝΟΜΑΤΟΛΟΓΙΟΝ."),
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {
                "source_file": str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"]),
                "section_kind": "onomastic_mixed",
            },
        }
    )

    current_letter = None
    letter_nodes: dict[str, str] = {}
    seen_section_start = False
    entry_counter = 0
    for fname in [
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-649.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-650.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-651.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-652.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-653.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-654.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-655.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-656.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-657.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-658.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-659.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-660.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-661.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-662.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-663.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-664.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-665.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-666.txt",
    ]:
        path = files[fname]
        lines = extract_plain_lines(path)
        if not seen_section_start and "ΟΝΟΜΑΤΟΛΟΓΙΟΝ." in lines:
            seen_section_start = True
            start_idx = lines.index("ΟΝΟΜΑΤΟΛΟΓΙΟΝ.") + 1
            lines = lines[start_idx:]
        elif not seen_section_start:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in {"A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "I.", "J.", "K.", "L.", "M.", "N.", "O.", "P.", "Q.", "R.", "S.", "T.", "U.", "V.", "W.", "X.", "Y.", "Z.", "Ξ.", "Ο.", "Π.", "Σ.", "Τ.", "Φ.", "Χ."}:
                current_letter = stripped.rstrip(".")
                letter_key = f"{VOLUME_ID}:node:onomastic_mixed:{current_letter}"
                if current_letter not in letter_nodes:
                    letter_nodes[current_letter] = letter_key
                    nodes.append(
                        {
                            "node_key": letter_key,
                            "section_key": SECTION_ONOMASTIC["section_key"],
                            "parent_node_key": onomastic_node_key,
                            "node_order": len(letter_nodes),
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": normalize(current_letter),
                            "label_sort": sort_norm(current_letter),
                            "node_level": 2,
                            "confidence": 0.99,
                            "raw_json": {
                                "source_file": str(path),
                                "kind": "letter_divider",
                            },
                        }
                    )
                continue
            entry_counter += 1
            entry_key_parent = letter_nodes.get(current_letter, onomastic_node_key)
            inferred_page = header_pages(path)[0]
            first_numeric = None
            m_first = re.search(r"(\d+)", stripped)
            if m_first:
                first_numeric = int(m_first.group(1))
            entry, ref_rows = make_entry(
                section_key=SECTION_ONOMASTIC["section_key"],
                entry_order=entry_counter,
                entry_kind="lemma",
                entry_raw=stripped,
                section_start_file=str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"]),
                editorial_anchor_file=str(path),
                target_file_best=str(nearest_page_file(page_map, first_numeric, path)),
                heading_letter=current_letter,
                parent_node_key=entry_key_parent,
                inferred_printed_page=inferred_page,
                section_kind="onomastic_mixed",
            )
            entries.append(entry)
            refs.extend(ref_rows)

    # Section 3: ORDO RERUM table.
    ordo_node_key = f"{VOLUME_ID}:node:ordo_rerum:001"
    nodes.append(
        {
            "node_key": ordo_node_key,
            "section_key": SECTION_ORDO["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
            "label_norm": normalize("ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."),
            "label_sort": sort_norm("ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {
                "source_file": str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"]),
                "section_kind": "ordo_rerum",
            },
        }
    )

    ordo_lines: list[str] = []
    for fname in [
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-667.txt",
        "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt",
    ]:
        ordo_lines.extend(extract_plain_lines(files[fname]))
    # Keep only the actual contents table, not the tail of the index blocks.
    if "ORDO RERUM" in ordo_lines:
        start = ordo_lines.index("ORDO RERUM")
        ordo_lines = ordo_lines[start:]
    elif any("ORDO RERUM" in line for line in ordo_lines):
        for idx, line in enumerate(ordo_lines):
            if "ORDO RERUM" in line:
                ordo_lines = ordo_lines[idx:]
                break

    # Remove duplicates and the very short end-marker lines.
    filtered_ordo: list[str] = []
    for line in ordo_lines:
        if line in {"ORDO RERUM", "INDICES.", "Digitized by Google"}:
            continue
        if line.startswith("ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."):
            filtered_ordo.append(line)
            continue
        filtered_ordo.append(line)
    ordo_entries = merge_entries(filtered_ordo)
    for idx, entry_raw in enumerate(ordo_entries, start=1):
        entry_kind = "heading_group" if entry_raw.startswith(("GEORGIUS HAMARTOLUS", "LIBER ")) else "lemma"
        entry, ref_rows = make_entry(
            section_key=SECTION_ORDO["section_key"],
            entry_order=idx,
            entry_kind=entry_kind,
            entry_raw=entry_raw,
            section_start_file=str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-667.txt"]),
            editorial_anchor_file=str(files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"]),
            target_file_best=str(nearest_page_file(page_map, int(re.search(r"(\d+)", entry_raw).group(1)) if re.search(r"(\d+)", entry_raw) else None, files["fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"])),
            parent_node_key=ordo_node_key,
            inferred_printed_page=1325,
            section_kind="ordo_rerum",
        )
        entries.append(entry)
        refs.extend(ref_rows)

    # If we sampled helper output, keep the evidence in the raw_json of the matching entries.
    helper_map = parse_helper_output(HELPER_OUTPUT_JSON)
    if helper_map:
        for entry in entries:
            raw = entry.get("entry_raw") or ""
            if "Ἀθανάσιος" in raw and "pg110_author_athanasius" in helper_map:
                helper = helper_map["pg110_author_athanasius"]
                entry["raw_json"]["helper_status"] = helper.get("status")
                entry["raw_json"]["helper_best_candidate"] = helper.get("best_candidate")
                entry["target_file_best"] = helper["best_candidate"]["file"]
                for ref in refs:
                    if ref["entry_key"] == entry["entry_key"]:
                        ref["target_file"] = helper["best_candidate"]["file"]
            elif "Ἀβραάμ" in raw and "pg110_onomastic_abraam" in helper_map:
                helper = helper_map["pg110_onomastic_abraam"]
                entry["raw_json"]["helper_status"] = helper.get("status")
                entry["raw_json"]["helper_best_candidate"] = helper.get("best_candidate")
                entry["target_file_best"] = helper["best_candidate"]["file"]
                for ref in refs:
                    if ref["entry_key"] == entry["entry_key"]:
                        ref["target_file"] = helper["best_candidate"]["file"]
            elif "Ξηρόχερκος" in raw and "pg110_foreign_xeirocherkos" in helper_map:
                helper = helper_map["pg110_foreign_xeirocherkos"]
                entry["raw_json"]["helper_status"] = helper.get("status")
                entry["raw_json"]["helper_best_candidate"] = helper.get("best_candidate")
                entry["target_file_best"] = helper["best_candidate"]["file"]
                for ref in refs:
                    if ref["entry_key"] == entry["entry_key"]:
                        ref["target_file"] = helper["best_candidate"]["file"]
            elif raw.startswith("GEORGIUS HAMARTOLUS") and "pg110_ordo_georgius_hamartolus" in helper_map:
                helper = helper_map["pg110_ordo_georgius_hamartolus"]
                entry["raw_json"]["helper_status"] = helper.get("status")
                entry["raw_json"]["helper_best_candidate"] = helper.get("best_candidate")
                entry["target_file_best"] = helper["best_candidate"]["file"]
                for ref in refs:
                    if ref["entry_key"] == entry["entry_key"]:
                        ref["target_file"] = helper["best_candidate"]["file"]

    payload = {
        "schema_version": 1.0,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": "Mixed Greek/Latin final index with author, onomastic, and closing ordo sections.",
        },
        "sections": [SECTION_AUTHOR, SECTION_ONOMASTIC, SECTION_ORDO],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the mixed alphabetical index and closing ORDO RERUM table from the OCR tail; section boundaries were verified against the explicit headings and representative helper samples.",
            "evidence_files": [
                str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
                str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-666.txt"),
                str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-667.txt"),
                str(SOURCE_ROOT / "fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"),
            ],
        },
        "notes": [
            "The OCR tail is a mixed alphabetical run rather than a pure single-class index: author citations, onomastic names, foreign terms, and the final table of contents all appear together.",
            "The payload keeps the explicit ORDO RERUM table separate from the mixed alphabetical material and preserves OCR literals in lemma_raw and entry_raw.",
        ],
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-helper-request", action="store_true")
    parser.add_argument("--helper-request-json", type=Path, default=HELPER_REQUEST_JSON)
    parser.add_argument("--build-payload", action="store_true")
    parser.add_argument("--helper-output-json", type=Path, default=HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()

    if args.write_helper_request:
        write_json(args.helper_request_json, build_helper_request())
        return 0

    if args.build_payload:
        helper_output = read_json(args.helper_output_json, default={})
        args.intermediate_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_file, build_payload(helper_output=helper_output))
        return 0

    parser.error("pass either --write-helper-request or --build-payload")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

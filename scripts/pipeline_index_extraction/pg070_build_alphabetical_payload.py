#!/usr/bin/env python3
"""Usage: build the PG070 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/pg070_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG070/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG070_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG070_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG070 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG070_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG070"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 70"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG070/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG070_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG070_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG070_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG070"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

ANALYTIC_SECTION = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS RERUM ET VERBORUM QUÆ IN COMMENTARIO IN ISAIAM INVENIUNTUR.",
    "heading_norm": "index analyticus rerum et verborum quae in commentario in isaiam inveniuntur",
    "heading_letter": None,
    "page_start": 1465,
    "page_end": 1478,
    "file_start": None,
    "file_end": None,
    "confidence": 0.96,
    "raw_json": {
        "section_kind_reason": "Alphabetical analytical index headed INDEX ANALYTICUS for the commentary on Isaiah.",
        "file_start_seq": 736,
        "file_end_seq": 743,
    },
}

ORDO_SECTION = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": 1479,
    "page_end": 1480,
    "file_start": None,
    "file_end": None,
    "confidence": 0.98,
    "raw_json": {
        "section_kind_reason": "Closing contents table for the tome; editorial closure distinct from the alphabetical index.",
        "file_start_seq": 744,
        "file_end_seq": 744,
    },
}

SECTIONS = [ANALYTIC_SECTION, ORDO_SECTION]

HELPER_SAMPLES = [
    {
        "entry_id": "pg070_abelis_sanguis_295",
        "lemma_raw": "Abelis sanguis licet voce carens ad Dominum clamavit",
        "query_names": [
            "Abelis sanguis licet voce carens ad Dominum clamavit",
            "Abelis sanguis",
            "295",
        ],
        "page_hints": ["295"],
        "page_hint_ints": [295],
        "context_raw": "Abelis sanguis licet voce carens ad Dominum clamavit, 295.",
    },
    {
        "entry_id": "pg070_gentes_ecclesiam_36_378",
        "lemma_raw": "Gentes ad Ecclesiam sola Dei gratia vocatae",
        "query_names": [
            "Gentes ad Ecclesiam sola Dei gratia vocatae",
            "In locum Israelis assumptae",
            "Gentium populus sanctificatus est per fidem",
        ],
        "page_hints": ["36", "378", "560"],
        "page_hint_ints": [36, 378, 560],
        "context_raw": "Gentes ad Ecclesiam sola Dei gratia vocatae, 36, 378. In locum Israelis assumptae, 560.",
    },
    {
        "entry_id": "pg070_christus_imago_142_613_854",
        "lemma_raw": "Christus imago et tanquam facies est Dei Patris",
        "query_names": [
            "Christus imago et tanquam facies est Dei Patris",
            "Dextera Dei est",
            "Fons omnis boni",
            "Vita est et vitæ auctor",
        ],
        "page_hints": ["142", "613", "854"],
        "page_hint_ints": [142, 613, 854],
        "context_raw": "Christus imago et tanquam facies est Dei Patris, 142, 613, 854. Dextera Dei est, 140, 590, 913.",
    },
    {
        "entry_id": "pg070_sion_ecclesia_348",
        "lemma_raw": "Sion, id est Jeruſalem",
        "query_names": [
            "Sion, id est Jeruſalem",
            "Sion intelligibilis Ecclesia",
            "Ecclesia civitas Dei",
        ],
        "page_hints": ["348", "281", "372"],
        "page_hint_ints": [348, 281, 372],
        "context_raw": "Sion, id est Jeruſalem, 348. Sion intelligibilis Ecclesia, 51, 77, 205, 213, 397, 440, 412, 454, 475, 869, 912.",
    },
    {
        "entry_id": "pg070_zorobabel_205_440",
        "lemma_raw": "Zorobabel, typus Christi fuit",
        "query_names": [
            "Zorobabel, typus Christi fuit",
            "Israelem a captivitate redux it",
            "captivitate liberavit",
        ],
        "page_hints": ["205", "440"],
        "page_hint_ints": [205, 440],
        "context_raw": "Zorobabel, typus Christi fuit, 205. Israelem a captivitate redux it, 205, 440.",
    },
]

TITLE_PATTERNS = [
    r"^INDEX ANALYTICUS(?: RERUM ET VERBORUM QUÆ IN COMMENTARIO IN ISAIAM INVENIUNTUR\.)?$",
    r"^ORDO RERUM$",
    r"^QUÆ IN HOC TOMO CONTINENTUR\.?$",
    r"^QUAE IN HOC TOMO CONTINENTUR\.?$",
    r"^Digitized by Google$",
]
TITLE_RE = re.compile("|".join(TITLE_PATTERNS), re.IGNORECASE)
LEADING_PAGE_RE = re.compile(r"^(\d{1,4})\.\s+(.*)$")
START_MARKER_RE = re.compile(r"^([A-ZÆŒ])\.?\s+([A-ZÆŒ][^\s]*)")
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒ]\.?$")
NUM_ONLY_RE = re.compile(r"^\d{1,4}\.?$")
NUM_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])")
SPACE_RE = re.compile(r"\s+")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    text = text.replace("ſ", "s")
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


def page_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=page_seq)


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header):
            if token.startswith("0"):
                continue
            mapping.setdefault(int(token), str(path))
    return mapping


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def build_helper_request() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(DEFAULT_SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": HELPER_SAMPLES,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_output_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(helper_request_json, build_helper_request())
    subprocess.run(
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
    )
    return load_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        mapping[entry_id] = item
    return mapping


def attach_helper(raw_json: dict[str, Any], lemma_raw: str, helper_by_id: dict[str, dict[str, Any]]) -> None:
    if lemma_raw.startswith("Abelis sanguis licet voce carens ad Dominum clamavit"):
        raw_json["helper"] = helper_by_id.get("pg070_abelis_sanguis_295")
    elif lemma_raw.startswith("Gentes ad Ecclesiam sola Dei gratia vocatae"):
        raw_json["helper"] = helper_by_id.get("pg070_gentes_ecclesiam_36_378")
    elif lemma_raw.startswith("Christus imago et tanquam facies est Dei Patris"):
        raw_json["helper"] = helper_by_id.get("pg070_christus_imago_142_613_854")
    elif lemma_raw.startswith("Sion, id est Jeru"):
        raw_json["helper"] = helper_by_id.get("pg070_sion_ecclesia_348")
    elif lemma_raw.startswith("Zorobabel, typus Christi fuit"):
        raw_json["helper"] = helper_by_id.get("pg070_zorobabel_205_440")


def parse_page_fragments(
    path: Path,
    *,
    section_kind: str,
    section_first_file: str,
    page_map: dict[int, str],
    helper_by_id: dict[str, dict[str, Any]],
    entry_counter: dict[str, int],
    section_entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    section_key: str,
    source_page_hint: int | None = None,
) -> None:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines = [normalize(line) for line in (parsed.get("body_text") or "").splitlines()]
    lines = [line for line in lines if line and not TITLE_RE.fullmatch(line)]

    pending_continuation_ref: int | None = None
    last_entry_key: str | None = None

    for line in lines:
        if line == "Digitized by Google":
            continue
        if LETTER_RE.fullmatch(line):
            continue
        if TITLE_RE.fullmatch(line):
            continue
        m = LEADING_PAGE_RE.match(line)
        if m:
            pending_continuation_ref = int(m.group(1))
            line = m.group(2).strip()
        m = START_MARKER_RE.match(line)
        if m and m.group(2)[0].upper() == m.group(1):
            line = line[m.end(1) + 1 :].strip()
        parts = SENTENCE_SPLIT_RE.split(line)
        for part in parts:
            part = part.strip()
            if not part or TITLE_RE.fullmatch(part) or LETTER_ONLY_RE.fullmatch(part) or NUM_ONLY_RE.fullmatch(part):
                continue
            if part == "E." or part == "M." or part == "N." or part == "O." or part == "P." or part == "R." or part == "U." or part == "V." or part == "Z.":
                continue
            if pending_continuation_ref is not None and last_entry_key is not None:
                for entry in section_entries:
                    if entry["entry_key"] == last_entry_key:
                        entry.setdefault("raw_json", {}).setdefault("continuation_refs", []).append(pending_continuation_ref)
                        ref_order = len([r for r in refs if r["entry_key"] == last_entry_key]) + 1
                        refs.append(
                            {
                                "entry_key": last_entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "target_locator",
                                "ref_raw": str(pending_continuation_ref),
                                "page_ref_raw": str(pending_continuation_ref),
                                "page_ref_int": pending_continuation_ref,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": page_map.get(pending_continuation_ref),
                                "target_file_probability": 0.5,
                                "section_start_file": section_first_file,
                                "editorial_anchor_file": str(path),
                                "confidence": 0.5,
                                "raw_json": {"continuation_from": str(path)},
                            }
                        )
                        break
                pending_continuation_ref = None

            lemma_raw = part
            lemma_raw = re.sub(r"^(?:\d{1,4}\s+)+", "", lemma_raw).strip()
            if not lemma_raw:
                continue
            if lemma_raw in {"INDEX ANALYTICUS.", "ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
                continue
            if lemma_raw.startswith("Digitized by Google"):
                continue
            if NUM_ONLY_RE.fullmatch(lemma_raw):
                continue

            entry_counter[section_key] += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter[section_key]:04d}"
            page_refs: list[int] = []
            ref_records: list[dict[str, Any]] = []
            for match in NUM_RANGE_RE.finditer(lemma_raw):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else None
                raw_suffix = ""
                tail = lemma_raw[match.end() : match.end() + 10]
                if tail.lstrip().startswith("et seq"):
                    raw_suffix = " et seq."
                elif tail.lstrip().startswith("seq"):
                    raw_suffix = " seq."
                elif tail.lstrip().startswith("seqq"):
                    raw_suffix = " seqq."
                ref_raw = match.group(0) + raw_suffix
                if start not in page_refs:
                    page_refs.append(start)
                ref_records.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": len(ref_records) + 1,
                        "ref_kind": "target_locator",
                        "ref_raw": ref_raw,
                        "page_ref_raw": match.group(0),
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(start),
                        "range_end_raw": str(end) if end is not None else None,
                        "target_file": page_map.get(start),
                        "target_file_probability": 1.0 if page_map.get(start) else None,
                        "section_start_file": section_first_file,
                        "editorial_anchor_file": str(path),
                        "confidence": 0.9 if page_map.get(start) else 0.5,
                        "raw_json": {"source_file": str(path), "page_refs_found": page_refs[:]},
                    }
                )

            if not ref_records and page_refs:
                pass

            inferred_page = page_refs[0] if page_refs else None
            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_counter[section_key],
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": lemma_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": inferred_page,
                "section_start_file": section_first_file,
                "editorial_anchor_file": str(path),
                "target_file_best": page_map.get(inferred_page) if inferred_page else None,
                "confidence": 0.9 if page_refs else 0.78,
                "raw_json": {
                    "source_file": str(path),
                    "section_kind": section_kind,
                    "page_refs_found": page_refs,
                },
            }
            attach_helper(entry["raw_json"], lemma_raw, helper_by_id)
            section_entries.append(entry)
            refs.extend(ref_records)
            last_entry_key = entry_key


def build_analytic_section(
    source_root: Path,
    page_map: dict[int, str],
    helper_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = discover_files(source_root)
    entry_counter = {ANALYTIC_SECTION["section_key"]: 0}
    section_entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    files_in_scope = [p for p in files if 736 <= page_seq(p) <= 743]
    section_first_file = str(files_in_scope[0]) if files_in_scope else str(source_root / "unknown-736.txt")

    for path in files_in_scope:
        parse_page_fragments(
            path,
            section_kind=ANALYTIC_SECTION["section_kind"],
            section_first_file=section_first_file,
            page_map=page_map,
            helper_by_id=helper_by_id,
            entry_counter=entry_counter,
            section_entries=section_entries,
            refs=refs,
            section_key=ANALYTIC_SECTION["section_key"],
        )

    nodes: list[dict[str, Any]] = []
    seen_letters: set[str] = set()
    node_order = 0
    for entry in section_entries:
        lemma = normalize(entry["lemma_raw"])
        if not lemma:
            continue
        first = next((ch for ch in lemma if ch.isalpha()), "")
        if not first:
            continue
        letter = first.upper()
        entry["heading_letter"] = letter
        if letter not in seen_letters:
            seen_letters.add(letter)
            node_order += 1
            nodes.append(
                {
                    "node_key": f"{VOLUME_ID}:node:alpha:{node_order:03d}",
                    "section_key": ANALYTIC_SECTION["section_key"],
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter,
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.96,
                    "raw_json": {"generated_from": "entry_initials"},
                }
            )

    return nodes, section_entries, refs


def build_ordo_section(
    source_root: Path,
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    path = source_root / f"{next(p.name for p in discover_files(source_root) if page_seq(p) == 744)}"
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines = [normalize(line) for line in (parsed.get("body_text") or "").splitlines()]
    text = " ".join(line for line in lines if line and line != "Digitized by Google")
    text = re.sub(r"\\s+", " ", text).strip()

    nodes: list[dict[str, Any]] = [
        {
            "node_key": f"{VOLUME_ID}:node:ordo:001",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "S. CYRILLUS, ALEXANDRIÆ ARCHIEPISCOPUS.",
            "label_norm": "S. CYRILLUS, ALEXANDRIÆ ARCHIEPISCOPUS.",
            "label_sort": "s cyrillus alexandriae archiepiscopus",
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:002",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:001",
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "COMMENTARIUS IN ISAIAM PROPHETAM.",
            "label_norm": "COMMENTARIUS IN ISAIAM PROPHETAM.",
            "label_sort": "commentarius in isaiam prophetam",
            "node_level": 2,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:003",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 3,
            "node_kind": "ordinal_group",
            "label_raw": "LIBER PRIMUS.",
            "label_norm": "LIBER PRIMUS.",
            "label_sort": "liber primus",
            "node_level": 3,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:004",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 4,
            "node_kind": "ordinal_group",
            "label_raw": "LIBER SECUNDUS.",
            "label_norm": "LIBER SECUNDUS.",
            "label_sort": "liber secundus",
            "node_level": 3,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:005",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 5,
            "node_kind": "ordinal_group",
            "label_raw": "LIBER TERTIUS.",
            "label_norm": "LIBER TERTIUS.",
            "label_sort": "liber tertius",
            "node_level": 3,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:006",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 6,
            "node_kind": "ordinal_group",
            "label_raw": "LIBER QUARTUS.",
            "label_norm": "LIBER QUARTUS.",
            "label_sort": "liber quartus",
            "node_level": 3,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:007",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 7,
            "node_kind": "ordinal_group",
            "label_raw": "LIBER QUINTUS.",
            "label_norm": "LIBER QUINTUS.",
            "label_sort": "liber quintus",
            "node_level": 3,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
        {
            "node_key": f"{VOLUME_ID}:node:ordo:008",
            "section_key": ORDO_SECTION["section_key"],
            "parent_node_key": f"{VOLUME_ID}:node:ordo:002",
            "node_order": 8,
            "node_kind": "rubric_group",
            "label_raw": "FRAGMENTA EX CATENIS, ab editore Patrologiæ addita.",
            "label_norm": "FRAGMENTA EX CATENIS, ab editore Patrologiæ addita.",
            "label_sort": "fragmenta ex catenis ab editore patrologiae addita",
            "node_level": 2,
            "confidence": 0.98,
            "raw_json": {"source_file": str(path)},
        },
    ]

    spec_defs = [
        ("LIBER PRIMUS.", f"{VOLUME_ID}:node:ordo:003", [
            ("Proœmium.", 9),
            ("Oratio I.", 14),
            ("Oratio II.", 66),
            ("Oratio III.", 110),
            ("Oratio IV.", 158),
            ("Oratio V.", 206),
            ("Oratio VI.", 258),
        ]),
        ("LIBER SECUNDUS.", f"{VOLUME_ID}:node:ordo:004", [
            ("Tomus I.", 503),
            ("Tomus II.", 546),
            ("Tomus III.", 595),
            ("Tomus IV.", 450),
            ("Tomus V.", 502),
        ]),
        ("LIBER TERTIUS.", f"{VOLUME_ID}:node:ordo:005", [
            ("Tomus I.", 555),
            ("Tomus II.", 623),
            ("Tomus III.", 687),
            ("Tomus IV.", 755),
            ("Tomus V.", 811),
        ]),
        ("LIBER QUARTUS.", f"{VOLUME_ID}:node:ordo:006", [
            ("Oratio I.", 858),
            ("Oratio II.", 918),
            ("Oratio III.", 976),
            ("Oratio IV.", 1034),
            ("Oratio V.", 1091),
        ]),
        ("LIBER QUINTUS.", f"{VOLUME_ID}:node:ordo:007", [
            ("Tomus I.", 1145),
            ("Tomus II.", 1191),
            ("Tomus III.", 1238),
            ("Tomus IV.", 1287),
            ("Tomus V.", 1343),
            ("Tomus VI.", 1399),
        ]),
        ("FRAGMENTA EX CATENIS, ab editore Patrologiæ addita.", f"{VOLUME_ID}:node:ordo:008", [
            ("In Jeremiam.", 1451),
            ("In librum Baruch.", 1457),
            ("In Ezechielem.", 1458),
            ("In Danielem.", 1462),
        ]),
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_counter = 0
    for heading, parent_node_key, items in spec_defs:
        for lemma_raw, page_num in items:
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:ordo:{entry_counter:03d}"
            target = page_map.get(page_num)
            entry = {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION["section_key"],
                "parent_node_key": parent_node_key,
                "entry_order": entry_counter,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw.rstrip("."),
                "lemma_display": lemma_raw.rstrip("."),
                "lemma_norm": normalize(lemma_raw.rstrip(".")),
                "lemma_sort": sort_norm(lemma_raw.rstrip(".")),
                "entry_raw": f"{lemma_raw} {page_num}",
                "context_raw": None,
                "heading_letter": next((ch.upper() for ch in lemma_raw if ch.isalpha()), None),
                "inferred_printed_page": page_num,
                "section_start_file": str(path),
                "editorial_anchor_file": str(path),
                "target_file_best": target,
                "confidence": 0.96 if target else 0.86,
                "raw_json": {"source_file": str(path), "parent_heading": heading},
            }
            entries.append(entry)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_num),
                    "page_ref_raw": str(page_num),
                    "page_ref_int": page_num,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target,
                    "target_file_probability": 1.0 if target else None,
                    "section_start_file": str(path),
                    "editorial_anchor_file": str(path),
                    "confidence": 0.95 if target else 0.8,
                    "raw_json": {"source_file": str(path), "parent_heading": heading},
                }
            )

    return nodes, entries, refs


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    analytic_files = [p for p in files if 736 <= page_seq(p) <= 743]
    ordo_files = [p for p in files if page_seq(p) == 744]
    analytic_start = str(analytic_files[0]) if analytic_files else str(DEFAULT_SOURCE_ROOT / "unknown-736.txt")
    analytic_end = str(analytic_files[-1]) if analytic_files else analytic_start
    ordo_file = str(ordo_files[0]) if ordo_files else str(DEFAULT_SOURCE_ROOT / "unknown-744.txt")
    sections = [json.loads(json.dumps(ANALYTIC_SECTION)), json.loads(json.dumps(ORDO_SECTION))]
    sections[0]["file_start"] = analytic_start
    sections[0]["file_end"] = analytic_end
    sections[1]["file_start"] = ordo_file
    sections[1]["file_end"] = ordo_file
    return sections


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_files(args.source_root)
    page_map = build_page_map(files)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_id = helper_map(helper_output)

    analytic_nodes, analytic_entries, analytic_refs = build_analytic_section(args.source_root, page_map, helper_by_id)
    ordo_nodes, ordo_entries, ordo_refs = build_ordo_section(args.source_root, page_map)

    nodes = analytic_nodes + ordo_nodes
    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs

    sections = build_sections(files)
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Volume with INDEX ANALYTICUS and closing ORDO RERUM.",
    }
    coverage = {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": "Recovered the analytical index and closing ORDO RERUM table from the OCR tail using conservative sentence-level grouping and manual ORDO parsing.",
        "evidence_files": [
            str(args.source_root / "0c99bc89-da02-4617-891e-f88dca2a26ef-736.txt"),
            str(args.source_root / "0c99bc89-da02-4617-891e-f88dca2a26ef-739.txt"),
            str(args.source_root / "0c99bc89-da02-4617-891e-f88dca2a26ef-743.txt"),
            str(args.source_root / "0c99bc89-da02-4617-891e-f88dca2a26ef-744.txt"),
        ],
    }
    notes = [
        "Analytical index spans printed pages 1465-1478; ORDO RERUM covers 1479-1480.",
        "Helper was sampled on a few representative entries to stabilize target-file anchoring for page-drift-heavy lemmas.",
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

    # Persist intermediate fragments for reruns.
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "manifest.json",
        {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"], "updated_at": payload["generated_at"]},
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PG070 alphabetical payload assembly",
            "completed": [
                "helper request built and resolved",
                "analytic index segmented",
                "ordo rerum parsed",
                "final payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals untouched.",
                "Use the helper only as locator support, not as editorial authority.",
            ],
        },
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

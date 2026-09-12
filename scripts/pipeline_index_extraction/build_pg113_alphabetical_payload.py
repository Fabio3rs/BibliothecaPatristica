#!/usr/bin/env python3
"""Usage: build the PG113 alphabetical payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pg113_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG113/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG113_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG113_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG113 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG113_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import estimate_editorial_pages


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG113"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 113"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG113/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG113_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG113_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG113_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG113"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:analytic_subject:002"
SECTION_3_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:003"

SECTION_1_HEADING = "INDEX RERUM MEMORABILIUM"
SECTION_2_HEADING = "INDEX RERUM ET VERBORUM IN EXCERPTIS DE LEGATIONIBUS PRÆCIPUE MEMORABILIUM."
SECTION_3_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

SECTION_2_START_RE = re.compile(r"^INDEX RERUM ET VERBORUM IN EXCERPTIS DE LEGATIONIBUS PRÆCIPUE MEMORABILIUM\.?$", re.IGNORECASE)
SECTION_3_START_RE = re.compile(r"^ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)?$", re.IGNORECASE)
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LOWERCASE_START_RE = re.compile(r"^[a-zà-ÿ\u0370-\u03ff\u1f00-\u1fff]")
NOISE_LINE_RE = re.compile(
    r"^(?:INDICES\.|INDEX RERUM(?: MEMORABILIUM)?|INDEX IN LIBROS DE THEMATIBUS ET DE ADM\. IMP\.|"
    r"INDEX RERUM ET VERBORUM IN EXCERPTIS DE LEGATIONIBUS PRÆCIPUE MEMORABILIUM\.?|"
    r"IN EXCERPTA DE LEGATIONIBUS\.?|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)?|"
    r"QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?|FINIS TOMI CENTESIMI DECIMI TERTII\.|"
    r"Digitized by Google)$",
    re.IGNORECASE,
)
NO_LOCATOR_REF_RE = re.compile(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", re.IGNORECASE)
CONTINUATION_STARTERS = (
    "Ejus ",
    "Eorum ",
    "Eadem ",
    "Ea ",
    "Iidem ",
    "Item ",
    "Apud ",
    "Cum ",
    "Contra ",
    "Sed ",
    "Hanc ",
    "Hic ",
    "Huic ",
    "Quæ ",
    "Quibus ",
    "Quod ",
    "Quando ",
    "Postea ",
    "Filiam ",
    "Bellum ",
    "Insulis ",
    "Scythas ",
    "Præfugii ",
    "In ",
    "Et ",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


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


def discover_volume_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def discover_index_files(source_root: Path) -> list[Path]:
    files = discover_volume_files(source_root)
    return [path for path in files if 644 <= file_seq(path) <= 666]


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def parse_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw.startswith("<pagina"):
        return [("texto_principal", raw)]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return [("texto_principal", raw)]
    blocks: list[tuple[str, str]] = []
    for bloco in root.findall("bloco"):
        block_type = (bloco.attrib.get("tipo") or "").strip().lower()
        content = "".join(bloco.itertext())
        content = normalize(content) or ""
        if content:
            blocks.append((block_type, content))
    return blocks


def block_lines(block_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in block_text.splitlines():
        line = normalize(raw_line)
        if line:
            lines.append(line)
    return lines


def should_continue_line(previous: str, current: str, block_type: str) -> bool:
    if block_type == "cabecalho":
        return False
    if not previous:
        return False
    if previous.endswith("-"):
        return True
    if SINGLE_LETTER_RE.fullmatch(current):
        return False
    if LOWERCASE_START_RE.match(current):
        return True
    if current.startswith(CONTINUATION_STARTERS):
        return True
    previous_hints = extract_page_hints(previous)
    current_hints = extract_page_hints(current)
    if not current_hints:
        return True
    if not previous_hints and current_hints:
        return True
    return False


def extract_logical_lines(path: Path) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    for block_type, content in parse_blocks(path):
        pending: str | None = None
        for line in block_lines(content):
            if pending is None:
                pending = line
                continue
            if should_continue_line(pending, line, block_type):
                pending = pending[:-1] + line.lstrip() if pending.endswith("-") else f"{pending} {line}"
                continue
            ordered.append((block_type, pending))
            pending = line
        if pending:
            ordered.append((block_type, pending))
    return ordered


def page_ref_int(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 < value < 10000 else None


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_HEADER_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if 0 < value < 10000 and value not in seen:
            hints.append(value)
            seen.add(value)
    return hints


def lemma_from_line(line: str, page_hints: list[int], entry_kind: str) -> str | None:
    cleaned = normalize(line) or ""
    if not cleaned:
        return None
    if entry_kind == "cross_reference":
        match = re.match(r"^(?P<lemma>.*?)(?:\s+V\.\s+.*)?$", cleaned)
        if match:
            lemma = normalize(match.group("lemma"))
            if lemma:
                return lemma.strip(" .;:")
    if page_hints:
        first_ref = re.search(r"(?<!\d)(\d{1,4})(?!\d)", cleaned)
        if first_ref:
            lemma = cleaned[: first_ref.start()].strip(" .;:")
            return lemma or None
    return cleaned.strip(" .;:") or None


def entry_kind(line: str, page_hints: list[int]) -> str:
    cleaned = normalize(line) or ""
    if page_hints:
        return "lemma"
    if NO_LOCATOR_REF_RE.search(cleaned) or re.search(r"\bV\.\s+[A-ZÆŒ]", cleaned):
        return "cross_reference"
    if cleaned.startswith("Cap.") or cleaned.startswith("Titulus ") or cleaned.startswith("LIBER "):
        return "heading_group"
    return "lemma" if len(cleaned) > 2 else "editorial_note"


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_volume_files(source_root):
        raw = path.read_text(encoding="utf-8", errors="replace")
        for block_type, content in parse_blocks(path):
            if block_type != "cabecalho":
                continue
            if needle.search(content):
                return str(path)
        if needle.search(raw):
            return str(path)
    return None


def build_estimator_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        payload = estimate_editorial_pages(volume_id=VOLUME_ID, source_root=source_root, collection=COLLECTION)
    except Exception:
        return mapping
    for item in payload.get("files") or []:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        best_guess = item.get("best_guess")
        if isinstance(best_guess, list):
            guesses = [int(x) for x in best_guess if str(x).isdigit()]
        elif isinstance(best_guess, int):
            guesses = [best_guess]
        else:
            guesses = []
        for page in guesses:
            mapping.setdefault(page, file_path)
    return mapping


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for block_type, content in parse_blocks(path):
            if block_type != "cabecalho":
                continue
            for match in PAGE_HEADER_RE.finditer(content):
                token = match.group(1)
                if token.startswith("0"):
                    continue
                page_map.setdefault(int(token), str(path))
    if files:
        source_root = files[0].parent
        for page, target in build_estimator_page_map(source_root).items():
            page_map.setdefault(page, target)
    return page_map


def helper_seed_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
    if not page_hints:
        return None
    if entry.get("target_file_best") is not None:
        return None
    lemma_raw = entry.get("lemma_raw") or entry.get("entry_raw") or ""
    query_names = [lemma_raw]
    if lemma_raw:
        query_names.append(lemma_raw.split(",", 1)[0])
    query_names = [q for q in dict.fromkeys(q.strip() for q in query_names if q and q.strip())]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma_raw,
        "query_names": query_names,
        "page_hints": [str(page) for page in page_hints[:3]],
        "page_hint_ints": page_hints[:3],
        "context_raw": entry["entry_raw"],
    }


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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {"status": "empty", "entries": []})


def helper_map_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        if isinstance(item, dict):
            mapping[item.get("entry_id")] = item
    return mapping


def create_node(nodes: list[dict[str, Any]], section_key: str, letter: str, source_file: str) -> str:
    node_key = f"{VOLUME_ID}:node:{section_key.split(':')[-2]}:letter:{letter}"
    if any(node.get("node_key") == node_key for node in nodes):
        return node_key
    nodes.append(
        {
            "node_key": node_key,
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": len([n for n in nodes if n["section_key"] == section_key]) + 1,
            "node_kind": "letter_group",
            "label_raw": letter,
            "label_norm": letter.lower(),
            "label_sort": letter.lower(),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_file": source_file, "kind": "alphabetic divider"},
        }
    )
    return node_key


def parse_volume(
    source_root: Path,
    index_files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_request_entries: list[dict[str, Any]] = []

    current_section_key = SECTION_1_KEY
    section_start_by_key: dict[str, str] = {}
    current_letter: str | None = None
    current_node_key: str | None = None
    entry_order_by_section = {SECTION_1_KEY: 0, SECTION_2_KEY: 0, SECTION_3_KEY: 0}
    global_entry_order = 0

    def switch_section(section_key: str, source_file: str) -> None:
        nonlocal current_section_key, current_letter, current_node_key
        if current_section_key != section_key:
            current_section_key = section_key
            section_start_by_key[section_key] = source_file
            current_letter = None
            current_node_key = None
        section_start_by_key.setdefault(section_key, source_file)

    for path in index_files:
        for block_type, line in extract_logical_lines(path):
            text = normalize(line) or ""
            if not text or NOISE_LINE_RE.fullmatch(text):
                continue
            simplified = re.sub(r"^\d+\s*|\s*\d+$", "", text).strip()
            if SECTION_2_START_RE.fullmatch(simplified):
                switch_section(SECTION_2_KEY, str(path))
                continue
            if SECTION_3_START_RE.fullmatch(simplified):
                switch_section(SECTION_3_KEY, str(path))
                continue
            if block_type == "cabecalho":
                continue
            if text in {"INDEX RERUM", "INDEX IN LIBROS DE THEMATIBUS ET DE ADM. IMP.", "IN EXCERPTA DE LEGATIONIBUS.", "ORDO RERUM"}:
                continue
            if text == "A" or text == "B" or text == "C" or text == "D" or text == "E" or text == "F" or text == "G" or text == "H" or text == "I" or text == "J" or text == "K" or text == "L" or text == "M" or text == "N" or text == "O" or text == "P" or text == "Q" or text == "R" or text == "S" or text == "T" or text == "U" or text == "V" or text == "W" or text == "X" or text == "Y" or text == "Z" or text in {"Æ", "Œ"}:
                current_letter = text
                current_node_key = create_node(nodes, current_section_key, text, str(path))
                continue

            page_hints = extract_page_hints(text)
            kind = entry_kind(text, page_hints)
            lemma_raw = lemma_from_line(text, page_hints, kind)
            heading_letter = current_letter
            if heading_letter is None and lemma_raw:
                first = lemma_raw[0].upper()
                if first.isalpha() or first in {"Æ", "Œ"}:
                    heading_letter = first
                    current_letter = first
                    current_node_key = create_node(nodes, current_section_key, first, str(path))
            entry_order_by_section[current_section_key] += 1
            entry_order = entry_order_by_section[current_section_key]
            global_entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{global_entry_order:04d}"
            target_best = target_for_page(page_hints[0], page_map, source_root) if page_hints else str(path)
            section_start_file = section_start_by_key.get(current_section_key, str(path))

            entry = {
                "entry_key": entry_key,
                "section_key": current_section_key,
                "parent_node_key": current_node_key,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": None,
                "heading_letter": heading_letter,
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_best,
                "confidence": 0.88 if page_hints else 0.68,
                "raw_json": {
                    "source_file": str(path),
                    "block_type": block_type,
                    "page_hints": page_hints,
                    "section_kind": "ordo_rerum" if current_section_key == SECTION_3_KEY else "analytic_subject",
                    "section_transition_file": str(path) if current_section_key in {SECTION_2_KEY, SECTION_3_KEY} else None,
                },
            }
            entries.append(entry)
            seed = helper_seed_entry(entry)
            if seed:
                helper_request_entries.append(seed)

            for ref_order, page in enumerate(page_hints, start=1):
                target_file = target_for_page(page, page_map, source_root)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(page),
                        "page_ref_raw": str(page),
                        "page_ref_int": page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": 0.99 if target_file else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(path),
                        "confidence": 0.95 if target_file else 0.7,
                        "raw_json": {
                            "source_file": str(path),
                            "locator_method": "header_page_map" if target_file else "unresolved",
                        },
                    }
                )

    return entries, refs, nodes, helper_request_entries


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = helper_map_by_id(helper_output)
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
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
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")
        if helper.get("status"):
            raw_json["helper_status"] = helper.get("status")


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    section1_files = [p for p in files if 644 <= file_seq(p) <= 654]
    section2_files = [p for p in files if 654 <= file_seq(p) <= 664]
    ordo_files = [p for p in files if 665 <= file_seq(p) <= 666]
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
            "page_start": 1197,
            "page_end": 1219,
            "file_start": str(section1_files[0]) if section1_files else None,
            "file_end": str(section1_files[-1]) if section1_files else None,
            "confidence": 0.9,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index for the De thematibus / De administrando imperio material; the page 645 header repeats the title as INDEX IN LIBROS DE THEMATIBUS ET DE ADM. IMP.",
                "evidence_files": [str(section1_files[0]) if section1_files else None, str(section1_files[-1]) if section1_files else None],
            },
        },
        {
            "section_key": SECTION_2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_2_HEADING,
            "heading_norm": sort_norm(SECTION_2_HEADING),
            "heading_letter": None,
            "page_start": 1217,
            "page_end": 1236,
            "file_start": str(section2_files[0]) if section2_files else None,
            "file_end": str(section2_files[-1]) if section2_files else None,
            "confidence": 0.9,
            "raw_json": {
                "section_kind_reason": "Second alphabetical subject index headed INDEX RERUM ET VERBORUM IN EXCERPTIS DE LEGATIONIBUS PRÆCIPUE MEMORABILIUM.",
                "evidence_files": [str(section2_files[0]) if section2_files else None, str(section2_files[-1]) if section2_files else None],
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
            "page_start": 1239,
            "page_end": 1240,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial ORDO RERUM contents table at the end of the tome.",
                "evidence_files": [str(ordo_files[0]) if ordo_files else None, str(ordo_files[-1]) if ordo_files else None],
            },
        },
    ]


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    volume_files = discover_volume_files(source_root)
    index_files = discover_index_files(source_root)
    page_map = build_page_map(volume_files)
    entries, refs, nodes, helper_request_entries = parse_volume(source_root, index_files, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request_entries else {"status": "empty", "entries": []}
    attach_helper(entries, helper_output)

    entry_map = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        entry = entry_map.get(ref["entry_key"])
        if not entry:
            continue
        best = (entry.get("raw_json") or {}).get("helper", {}).get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    sections = build_sections(index_files)
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the two alphabetical index sections plus the closing ORDO RERUM from the OCR tail, preserving the section transition inside file 654.",
        "evidence_files": [
            sections[0]["file_start"],
            sections[0]["file_end"],
            sections[1]["file_start"],
            sections[1]["file_end"],
            sections[2]["file_start"],
            sections[2]["file_end"],
        ],
    }
    notes = [
        "The OCR tail contains a section transition inside file 654: the first index ends there and the EXCERPTA DE LEGATIONIBUS index starts in the same OCR file before continuing through file 664.",
        "Letter-group nodes were created conservatively from the first explicit letter line or from the first lemma on each new letter group.",
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
            "output_file": str(output_file),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PG113 alphabetical payload and keep the two index sections separate from ORDO RERUM.",
            "completed": [
                "identified the main alphabetical index section",
                "identified the EXCERPTA DE LEGATIONIBUS alphabetical index section",
                "identified the closing ORDO RERUM contents table",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from editorial page numbers.",
                "Preserve helper evidence only where it affects target selection.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG113 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    payload = build_payload(
        args.source_root,
        args.helper_request_json,
        args.helper_output_json,
        args.intermediate_dir,
        args.output_file,
    )
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL171_build_alphabetical_payload.py

Builds the PL171 alphabetical-index payload from the OCR tail, writes the
helper request/output, persists intermediate checkpoints, and emits the final
canonical JSON payload.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL171"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 171"
SOURCE_ROOT = ROOT / "teste/PL171/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL171_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL171_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL171_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL171"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

ANALYTIC_FILE_START = 894
ANALYTIC_FILE_END = 910
ORDO_FILE_START = 911
ORDO_FILE_END = 917

SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ])")
PAGE_RE = re.compile(r"\b\d{1,4}\b")
NUMBER_HEADER_RE = re.compile(r"^\s*(\d{3,4})\b")
NOISE_LINES = {"Digitized by Google"}
SECTION_SKIP_RE = re.compile(r"^(?:INDEX ANALYTICUS|ORDO RERUM(?: QUÆ IN HOC TOMO CONTINENTUR\.)?|QUÆ IN HOC TOMO CONTINENTUR\.?)$", re.I)
MACRO_HEADING_RE = re.compile(
    r"^(?:MARBODUS REDONENSIS EPISCOPUS\.|EPISTOLÆ\.|DIPLOMATA A VENERABILI HILDEBERTO CONCESSA\.|"
    r"SERMONES HILDEBERTI(?:\s+\d+)?\.?|SERMONES DE TEMPORE\.|SERMONES DE DIVERSIS\.|"
    r"OPUSCULA VENERABILIS HILDEBERTI\.?|LIBER DE GEMMIS\.|DIPLOMATA MARBODI\.?)$",
    re.I,
)
LETTER_RE = re.compile(r"^[A-Z]$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_page_files(source_root: Path, start_seq: int, end_seq: int) -> list[Path]:
    files = []
    for seq in range(start_seq, end_seq + 1):
        matches = sorted(source_root.glob(f"*-{seq:03d}.txt"))
        if not matches:
            raise FileNotFoundError(f"Missing OCR file for sequence {seq:03d}")
        files.append(matches[0])
    return files


def parse_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for source in (parsed["header_text"], parsed["body_text"], parsed["footer_text"], parsed["notes_text"]):
        for raw_line in source.splitlines():
            line = normalize_space(raw_line)
            if line and line not in NOISE_LINES:
                lines.append(line)
    return lines


def strip_page_suffix(text: str) -> str:
    return re.sub(r"(?:\s+)?\d{1,4}\.?$", "", text).strip()


def first_page_hint(text: str) -> int | None:
    m = PAGE_RE.search(text)
    return int(m.group(0)) if m else None


def page_hints(text: str) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for match in PAGE_RE.finditer(text):
        value = int(match.group(0))
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def lemma_from_chunk(chunk: str, section_kind: str) -> str:
    text = normalize_space(chunk)
    if not text:
        return text
    if section_kind == "analytic_subject":
        if "," in text:
            return text.split(",", 1)[0].strip(" .;:")
        if "—" in text:
            return text.split("—", 1)[0].strip(" .;:")
        if "." in text and not LETTER_RE.fullmatch(text):
            return text.split(".", 1)[0].strip(" .;:")
        return text.strip(" .;:")
    return strip_page_suffix(text).strip(" .;:")


def entry_kind_for(chunk: str, section_kind: str) -> str:
    if re.match(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", chunk, re.I):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        return "heading_group"
    return "lemma"


def build_helper_query_names(lemma_raw: str, chunk: str) -> list[str]:
    values = [normalize_space(lemma_raw), normalize_space(chunk)]
    stripped = re.sub(r"\([^)]*\)", "", normalize_space(lemma_raw))
    if stripped and stripped != lemma_raw:
        values.append(stripped)
    if " — " in chunk:
        values.append(normalize_space(chunk.split(" — ", 1)[1]))
    if " - " in chunk:
        values.append(normalize_space(chunk.split(" - ", 1)[1]))
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normed = normalize_space(value)
        if normed and normed not in seen:
            out.append(normed)
            seen.add(normed)
    return out


def build_sections(analytic_files: list[Path], ordo_files: list[Path]) -> list[dict[str, Any]]:
    analytic_start = analytic_files[0]
    analytic_end = analytic_files[-1]
    ordo_start = ordo_files[0]
    ordo_end = ordo_files[-1]
    return [
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS.",
            "heading_norm": "index analyticus",
            "heading_letter": None,
            "page_start": 1735,
            "page_end": 1818,
            "file_start": str(analytic_start),
            "file_end": str(analytic_end),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Analytical alphabetical subject index with letter dividers and material page loci.",
                "evidence_files": [str(analytic_start), str(analytic_end)],
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
            "page_start": 1819,
            "page_end": 4832,
            "file_start": str(ordo_start),
            "file_end": str(ordo_end),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing editorial contents table, including Hildebert and Marbodus contents, distinct from the alphabetical index.",
                "evidence_files": [str(ordo_start), str(ordo_end)],
            },
        },
    ]


def build_nodes_and_entries(files: list[Path], section_key: str, section_kind: str, section_start_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    current_letter: str | None = None
    current_parent_node_key: str | None = None
    current_macro_node_key: str | None = None
    node_order = 0
    entry_order = 0

    def ensure_letter_node(letter: str, source_file: str) -> str:
        nonlocal node_order
        if letter not in nodes:
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes[letter] = {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_file": source_file, "role": "alphabetic divider"},
            }
        return nodes[letter]["node_key"]

    def ensure_macro_node(label: str, source_file: str, parent_key: str | None = None) -> str:
        nonlocal node_order
        key = f"{label}|{parent_key or ''}"
        if key not in nodes:
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes[key] = {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent_key,
                "node_order": node_order,
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize_sort(label),
                "label_sort": normalize_sort(label),
                "node_level": 1 if parent_key is None else 2,
                "confidence": 0.95,
                "raw_json": {"source_file": source_file, "role": "macro_heading"},
            }
        return nodes[key]["node_key"]

    def emit_entry(chunk: str, source_file: str) -> None:
        nonlocal entry_order, current_parent_node_key, current_macro_node_key
        chunk = normalize_space(chunk)
        if not chunk:
            return
        if section_kind == "analytic_subject":
            if LETTER_RE.fullmatch(chunk):
                current_parent_node_key = ensure_letter_node(chunk, source_file)
                return
            if SECTION_SKIP_RE.fullmatch(chunk):
                return
            if chunk.upper().startswith("INDEX ANALYTICUS"):
                return
        else:
            if SECTION_SKIP_RE.fullmatch(chunk):
                return
            if MACRO_HEADING_RE.fullmatch(chunk):
                current_macro_node_key = ensure_macro_node(strip_page_suffix(chunk), source_file, current_macro_node_key)
                current_parent_node_key = current_macro_node_key
                return
            if chunk.isupper() and len(chunk.split()) <= 6 and not PAGE_RE.search(chunk):
                current_macro_node_key = ensure_macro_node(chunk, source_file, current_macro_node_key)
                current_parent_node_key = current_macro_node_key
                return

        entry_order += 1
        lemma_raw = lemma_from_chunk(chunk, section_kind)
        entry_kind = entry_kind_for(chunk, section_kind)
        hints = page_hints(chunk)
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        inferred = first_page_hint(chunk)
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": current_parent_node_key if section_kind == "analytic_subject" else current_macro_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize_sort(lemma_raw),
            "lemma_sort": normalize_sort(lemma_raw),
            "entry_raw": chunk,
            "context_raw": chunk,
            "heading_letter": None,
            "inferred_printed_page": inferred,
            "section_start_file": section_start_file,
            "editorial_anchor_file": source_file,
            "target_file_best": None,
            "confidence": 0.58 if section_kind == "analytic_subject" else 0.62,
            "raw_json": {
                "source_file": source_file,
                "section_kind": section_kind,
                "page_hints": hints,
                "entry_kind_reason": "subject-index lemma" if section_kind == "analytic_subject" else "closing-table heading",
            },
        }
        if section_kind == "analytic_subject" and current_parent_node_key:
            for letter, node in nodes.items():
                if node["node_key"] == current_parent_node_key:
                    entry["heading_letter"] = node["label_raw"]
                    break
        if len(hints) > 1:
            entry["raw_json"]["page_hints"] = hints
        entries.append(entry)
        if hints:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": build_helper_query_names(lemma_raw, chunk),
                    "page_hints": [str(h) for h in hints],
                    "page_hint_ints": hints,
                    "context_raw": chunk,
                }
            )
        for idx, hint in enumerate(hints, start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(hint),
                    "page_ref_raw": str(hint),
                    "page_ref_int": hint,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.45,
                    "raw_json": {"source_file": source_file, "section_kind": section_kind},
                }
            )

    for path in files:
        lines = parse_lines(path)
        for raw_line in lines:
            line = normalize_space(raw_line)
            if not line or line in NOISE_LINES:
                continue
            if SECTION_SKIP_RE.fullmatch(line):
                continue
            if section_kind == "analytic_subject" and LETTER_RE.fullmatch(line):
                current_parent_node_key = ensure_letter_node(line, str(path))
                continue
            if re.fullmatch(r"\d{4}", line):
                continue
            if re.match(r"^\d{3,4}\s+(?:INDEX ANALYTICUS|ORDO RERUM|QUÆ IN HOC TOMO CONTINENTUR)", line, re.I):
                continue
            emit_entry(line, str(path))

    return list(nodes.values()), entries, refs, helper_entries


def attach_helper_results(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    results = {item["entry_id"]: item for item in helper_output.get("entries", [])}
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)

    for entry in entries:
        result = results.get(entry["entry_key"])
        if not result:
            continue
        entry.setdefault("raw_json", {})["helper"] = result
        best = result.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            entry["confidence"] = min(0.99, max(entry["confidence"], 0.72 if result.get("status") == "resolved" else 0.62))
            for ref in refs_by_entry.get(entry["entry_key"], []):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")
                ref["confidence"] = min(0.99, max(ref["confidence"], 0.7 if result.get("status") == "resolved" else 0.55))
                ref.setdefault("raw_json", {})["helper"] = result
        else:
            entry.setdefault("raw_json", {})["helper"] = result


def build_todo(volume_id: str) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "updated_at": now_iso(),
        "current_focus": "Resolve PL171 final analytical and ordo rerum index payload",
        "completed": [
            "OCR tail inspected",
            "section boundaries identified",
        ],
        "pending": [
            "run target locator helper",
            "assemble final payload",
            "validate schema and write output",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals and page hints separate from OCR file suffixes.",
            "The analytical section and the closing ordo rerum block are both present in the tail.",
        ],
    }


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(TODO_JSON, build_todo(VOLUME_ID))

    analytic_files = extract_page_files(SOURCE_ROOT, ANALYTIC_FILE_START, ANALYTIC_FILE_END)
    ordo_files = extract_page_files(SOURCE_ROOT, ORDO_FILE_START, ORDO_FILE_END)

    sections = build_sections(analytic_files, ordo_files)

    analytic_nodes, analytic_entries, analytic_refs, analytic_helper_entries = build_nodes_and_entries(
        analytic_files,
        sections[0]["section_key"],
        sections[0]["section_kind"],
        str(analytic_files[0]),
    )
    ordo_nodes, ordo_entries, ordo_refs, ordo_helper_entries = build_nodes_and_entries(
        ordo_files,
        sections[1]["section_key"],
        sections[1]["section_kind"],
        str(ordo_files[0]),
    )

    nodes = analytic_nodes + ordo_nodes
    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": analytic_helper_entries + ordo_helper_entries,
    }
    write_json(HELPER_REQUEST_JSON, helper_request)

    helper_output = {"entries": []}
    if HELPER_OUTPUT_JSON.exists():
        helper_output = read_json(HELPER_OUTPUT_JSON, default={"entries": []})

    attach_helper_results(entries, refs, helper_output)

    notes = [
        "PL171 tail contains both INDEX ANALYTICUS and ORDO RERUM material.",
        "Helper results were attached where a target file could be resolved; unresolved entries remain explicit.",
    ]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": "Tail OCR contains an analytic alphabetical index followed by a closing ordo rerum table.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "The tail OCR was readable enough to recover the analytic index and closing contents table as structured entries.",
            "evidence_files": [str(analytic_files[0]), str(analytic_files[-1]), str(ordo_files[0]), str(ordo_files[-1])],
        },
        "notes": notes,
    }

    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(INTERMEDIATE_DIR / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": now_iso(), "files": [str(p) for p in analytic_files + ordo_files]})

    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl178_alphabetical_payload.py

Build the PL178 alphabetical-index payload, helper request/output, and
intermediate JSON fragments from the OCR tail.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL178"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 178"
SOURCE_ROOT = ROOT / "teste/PL178/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL178_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL178_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL178_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL178"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:author_index:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_HEADING_RAW = "INDEX AUCTORUM."
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

NOISE_LINES = {
    "Digitized by Google",
    "INDEX AUCTORUM",
    "QUI IN OPERIBUS ABÆLARDI CITANTUR.",
    "(Revocatur Lector ad numerales notas columnarum nostræ editionis.)",
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
}

SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4}\??)(?!\d)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ])")
SECTION_SPLIT_RE = re.compile(r"\bORDO RERUM\b")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.S | re.I)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=page_seq)


def extract_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        if (attrs.get("tipo") or "").strip().lower() not in {"cabecalho", "texto_principal"}:
            continue
        content = match.group("content") or ""
        content = TAG_RE.sub(" ", content)
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if line in NOISE_LINES:
                continue
            lines.append(line)
    return lines


def extract_page_refs(text: str) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in PAGE_REF_RE.finditer(text):
        raw = match.group(1)
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        value = int(digits)
        key = (raw, value)
        if key in seen:
            continue
        seen.add(key)
        refs.append((raw, value))
    return refs


def first_ref_cut(fragment: str) -> str:
    match = PAGE_REF_RE.search(fragment)
    if not match:
        return fragment.strip(" .;:,-")
    return fragment[: match.start()].strip(" .;:,-")


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = (parsed.get("header_text") or "").splitlines()
        if not header:
            continue
        first_line = normalize(header[0]) or ""
        for raw, value in extract_page_refs(first_line):
            if value not in mapping:
                mapping[value] = str(path)
    return mapping


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        body = parsed.get("body_text") or ""
        if needle.search(header) or needle.search(body):
            return str(path)
    return None


def lemma_from_fragment(fragment: str) -> str | None:
    value = first_ref_cut(fragment)
    value = value.strip(" .;:")
    return value or None


def is_heading_like(fragment: str) -> bool:
    value = fragment.strip()
    if not value:
        return False
    if value.endswith(":"):
        return True
    if len(value) <= 40 and value.upper() == value and any(ch.isalpha() for ch in value):
        return True
    if value.startswith("ORDO RERUM") or value.startswith("PETRI ABELARDI OPERUM"):
        return True
    return False


def helper_entry_payload(entry: dict[str, Any]) -> dict[str, Any]:
    page_refs = (entry.get("raw_json") or {}).get("page_refs") or []
    page_hints = [ref["int"] for ref in page_refs if ref.get("int") is not None]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
        "query_names": [
            entry["lemma_raw"] or entry["entry_raw"][:80],
            entry["entry_raw"].split("—", 1)[-1].strip(),
        ],
        "page_hints": [str(value) for value in page_hints[:4]],
        "page_hint_ints": page_hints[:4],
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
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        mapping[item.get("entry_id")] = item
    return mapping


def add_letter_node(nodes: list[dict[str, Any]], letter_nodes: dict[str, str], letter: str, source_file: str, section_key: str) -> str:
    node_key = letter_nodes.get(letter)
    if node_key:
        return node_key
    node_key = f"{VOLUME_ID}:node:letter:{letter}"
    letter_nodes[letter] = node_key
    nodes.append(
        {
            "node_key": node_key,
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": len(nodes) + 1,
            "node_kind": "letter_group",
            "label_raw": letter,
            "label_norm": letter.lower(),
            "label_sort": letter.lower(),
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {"source_file": source_file, "kind": "alphabetic divider"},
        }
    )
    return node_key


def parse_author_index(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_start_file = str(files[0]) if files else None
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    letter_nodes: dict[str, str] = {}
    entry_order = 0
    started = False

    for path in files:
        lines = extract_lines(path)
        current_letter: str | None = None
        block_lines: list[str] = []
        block_source_file = str(path)

        def flush_block() -> None:
            nonlocal entry_order, block_lines, current_letter
            if not block_lines:
                return
            block_text = " ".join(block_lines)
            block_text = re.sub(r"(?<=\w)-\s+", "", block_text)
            block_text = re.sub(r"\s+", " ", block_text).strip()
            if not block_text:
                block_lines = []
                return
            fragments = [frag.strip() for frag in SENTENCE_SPLIT_RE.split(block_text) if frag.strip()]
            for fragment in fragments:
                cleaned = normalize(fragment) or ""
                if not cleaned or cleaned in NOISE_LINES:
                    continue
                page_refs = extract_page_refs(cleaned)
                if not page_refs and not is_heading_like(cleaned):
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                lemma_raw = lemma_from_fragment(cleaned) if page_refs else cleaned.strip(" .;:")
                heading_letter = current_letter or (lemma_raw[:1].upper() if lemma_raw else None)
                if heading_letter and len(heading_letter) == 1 and heading_letter.isalpha():
                    parent_node_key = add_letter_node(nodes, letter_nodes, heading_letter, block_source_file, INDEX_SECTION_KEY)
                else:
                    parent_node_key = None
                target_file_best = target_for_page(page_refs[0][1], page_map, SOURCE_ROOT) if page_refs else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": INDEX_SECTION_KEY,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group" if not page_refs else "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": cleaned,
                    "context_raw": cleaned,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": page_refs[0][1] if page_refs else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": block_source_file,
                    "target_file_best": target_file_best,
                    "confidence": 0.93 if page_refs else 0.72,
                    "raw_json": {
                        "source_file": block_source_file,
                        "page_refs": [{"raw": raw, "int": value} for raw, value in page_refs],
                        "section_kind": "author_index",
                        "split_strategy": "sentence_boundary",
                    },
                }
                entries.append(entry)
                for ref_order, (raw, value) in enumerate(page_refs, start=1):
                    target_file = target_for_page(value, page_map, SOURCE_ROOT)
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": raw,
                            "page_ref_raw": raw,
                            "page_ref_int": value,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": 0.98 if target_file else None,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": block_source_file,
                            "confidence": 0.94 if target_file else 0.69,
                            "raw_json": {
                                "source_file": block_source_file,
                                "locator_method": "header_map" if target_file else "unresolved",
                            },
                        }
                    )
            block_lines = []

        for line in lines:
            if not started:
                if line.startswith("QUI IN OPERIBUS") or line == "INDEX AUCTORUM." or line == "INDEX AUCTORUM":
                    started = True
                continue
            if SECTION_SPLIT_RE.search(line):
                flush_block()
                return entries, refs, nodes
            if line in NOISE_LINES:
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                flush_block()
                current_letter = line
                add_letter_node(nodes, letter_nodes, line, block_source_file, INDEX_SECTION_KEY)
                continue
            if re.fullmatch(r"[A-ZÆŒ]\s+.*", line) and len(line) <= 6:
                flush_block()
                current_letter = line[0]
                add_letter_node(nodes, letter_nodes, current_letter, block_source_file, INDEX_SECTION_KEY)
                line = line[2:].strip()
                if not line:
                    continue
            if current_letter is None:
                # The page heading and the first divider establish the active letter.
                continue
            block_lines.append(line)
        flush_block()

    return entries, refs, nodes


def parse_ordo(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section_start_file = str(files[0]) if files else None
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    started = False

    for path in files:
        lines = extract_lines(path)
        page_parts: list[str] = []
        for line in lines:
            if not started:
                if line == "PETRUS ABAELARDUS:":
                    started = True
                continue
            if line in NOISE_LINES:
                continue
            if line.startswith("Digitized by Google"):
                continue
            if line.startswith("ORDO RERUM") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                continue
            page_parts.append(line)

        if not page_parts:
            continue
        page_text = " ".join(page_parts)
        page_text = re.sub(r"(?<=\w)-\s+", "", page_text)
        page_text = re.sub(r"\s+", " ", page_text).strip()
        if not page_text:
            continue

        fragments = [frag.strip() for frag in SENTENCE_SPLIT_RE.split(page_text) if frag.strip()]
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or cleaned in NOISE_LINES:
                continue
            page_refs = extract_page_refs(cleaned)
            if not page_refs and not is_heading_like(cleaned):
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:ordo:{entry_order:04d}"
            lemma_raw = lemma_from_fragment(cleaned) if page_refs else cleaned.strip(" .;:")
            target_file_best = target_for_page(page_refs[0][1], page_map, SOURCE_ROOT) if page_refs else None
            entry = {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": "heading_group" if not page_refs else "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
                "inferred_printed_page": page_refs[0][1] if page_refs else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": 0.91 if page_refs else 0.74,
                "raw_json": {
                    "source_file": str(path),
                    "page_refs": [{"raw": raw, "int": value} for raw, value in page_refs],
                    "section_kind": "ordo_rerum",
                    "split_strategy": "sentence_boundary",
                },
            }
            entries.append(entry)
            for ref_order, (raw, value) in enumerate(page_refs, start=1):
                target_file = target_for_page(value, page_map, SOURCE_ROOT)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": raw,
                        "page_ref_raw": raw,
                        "page_ref_int": value,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": 0.98 if target_file else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(path),
                        "confidence": 0.94 if target_file else 0.69,
                        "raw_json": {
                            "source_file": str(path),
                            "locator_method": "header_map" if target_file else "unresolved",
                        },
                    }
                )

    return entries, refs


def build_helper_request(entries: list[dict[str, Any]], ordo_entries: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for entry in entries:
        if len(selected) >= 4:
            break
        if entry.get("entry_kind") != "lemma":
            continue
        if not ((entry.get("raw_json") or {}).get("page_refs")):
            continue
        selected.append(helper_entry_payload(entry))
    for entry in ordo_entries:
        if len(selected) >= 6:
            break
        if entry.get("entry_kind") != "lemma":
            continue
        if not ((entry.get("raw_json") or {}).get("page_refs")):
            continue
        selected.append(helper_entry_payload(entry))
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": selected,
    }


def attach_helper_summary(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = helper_map_by_id(helper_output)
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        candidates = helper.get("candidates") or []
        entry.setdefault("raw_json", {})
        entry["raw_json"]["helper"] = {
            "status": helper.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                }
                for cand in candidates[:3]
            ],
        }
        if best.get("file") and not entry.get("target_file_best"):
            entry["target_file_best"] = best.get("file")


def section_bounds() -> tuple[list[Path], list[Path]]:
    files = discover_files(SOURCE_ROOT)
    index_files = [p for p in files if 947 <= page_seq(p) <= 950]
    ordo_files = [p for p in files if 950 <= page_seq(p) <= 953]
    return index_files, ordo_files


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    index_files, ordo_files = section_bounds()
    all_files = discover_files(SOURCE_ROOT)
    page_map = build_page_map(all_files)

    index_entries, index_refs, index_nodes = parse_author_index(index_files, page_map)
    ordo_entries, ordo_refs = parse_ordo(ordo_files, page_map)

    helper_request = build_helper_request(index_entries, ordo_entries)
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output = run_helper(HELPER_REQUEST_JSON, HELPER_OUTPUT_JSON)

    # Preserve representative helper evidence in the final payload.
    attach_helper_summary(index_entries, helper_output)
    attach_helper_summary(ordo_entries, helper_output)

    sections = [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": INDEX_HEADING_RAW,
            "heading_norm": "index auctorum",
            "heading_letter": None,
            "page_start": 1881,
            "page_end": 1884,
            "file_start": str(index_files[0]) if index_files else None,
            "file_end": str(index_files[-1]) if index_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Alphabetical index of authors cited in Abælardus; letter dividers A-V and dense editorial page references.",
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1885,
            "page_end": 1892,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial table of contents / ordo rerum at the end of the tomus, separate from the alphabetical author index.",
            },
        },
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the closing INDEX AUCTORUM and ORDO RERUM material from OCR files 947-953, preserving OCR literals and unresolved page targets where the helper or page-map evidence stayed weak.",
        "evidence_files": [str(p) for p in index_files + ordo_files],
    }

    notes = [
        "PL178 combines the author index and a final ordo rerum table of contents in the closing OCR window.",
        "Question-marked or obviously garbled page tokens were preserved literally in `ref_raw` and `page_ref_raw`.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "Alphabetical author index plus closing ordo rerum table parsed from the final OCR spreads.",
        ],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": index_nodes,
        "entries": index_entries + ordo_entries,
        "refs": index_refs + ordo_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    # Basic consistency checks.
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    section_keys = {section["section_key"] for section in payload["sections"]}
    assert entry_keys
    assert section_keys == {INDEX_SECTION_KEY, ORDO_SECTION_KEY}
    for node in payload["nodes"]:
        assert node["section_key"] in section_keys
    for ref in payload["refs"]:
        assert ref["entry_key"] in entry_keys

    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", index_nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "source_root": str(SOURCE_ROOT),
            "sections": [section["section_key"] for section in sections],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "helper_request_json": str(HELPER_REQUEST_JSON),
            "helper_output_json": str(HELPER_OUTPUT_JSON),
            "written_file": str(OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalized PL178 alphabetical index payload.",
            "completed": [
                "indexed the author index from the final OCR window",
                "parsed the closing ordo rerum table of contents",
                "ran the index_target_locator helper on representative entries",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Preserve literal OCR page tokens even when they appear malformed.",
            ],
        },
    )

    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL205 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl205_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL205/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL205_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL205_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL205 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL205_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL205"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 205"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL205/text"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_INDEX_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_ORDO_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"
SECTION_INDEX_START = 508
SECTION_INDEX_END = 522
SECTION_ORDO_START = 522
SECTION_ORDO_END = 525

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
TYPE_RE = re.compile(r'tipo="([^"]+)"')
HEADER_RE = re.compile(r"(?P<page>\d{1,4})\s+(.+?)\s+(?P<page2>\d{1,4})$")
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|\.|,|;|:|-+)$", re.IGNORECASE)
ORDO_RE = re.compile(r"^ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)?$", re.IGNORECASE)
HEADLINE_RE = re.compile(r"^(?:INDEX IN PETRI CANTORIS|VERBUM ABBREVIATUM\.?|Revocetur Lector ea numerales notas crassioribus typis textui insertas\.)$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def iter_blocks(path: Path) -> list[dict[str, str]]:
    raw = read_text(path)
    blocks: list[dict[str, str]] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = TYPE_RE.search(attrs)
        block_type = (tipo_m.group(1).strip().lower() if tipo_m else "").strip()
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        blocks.append({"type": block_type, "text": body})
    return blocks


def parse_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = normalize(raw_line)
        if not line or NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def header_numbers(path: Path) -> list[int]:
    nums: list[int] = []
    for block in iter_blocks(path):
        if block["type"] != "cabecalho":
            continue
        for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", block["text"]):
            nums.append(int(num))
    return nums


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in discover_files(source_root):
        for num in header_numbers(path):
            page_map.setdefault(num, path.as_posix())
    return page_map


def target_for_page(page: int, page_map: dict[int, str]) -> str | None:
    return page_map.get(page)


def page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", text):
        value = int(num)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def is_entry_heading(text: str) -> bool:
    if HEADLINE_RE.fullmatch(text):
        return True
    if SINGLE_LETTER_RE.fullmatch(text):
        return True
    if text.endswith(".") and not page_hints(text) and len(text.split()) <= 8 and re.search(r"[A-Za-zÆŒæœ]", text):
        return True
    return False


def entry_kind(mode: str, text: str, hints: list[int]) -> str:
    if mode == "ordo":
        return "heading_group"
    if not hints and re.search(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?|ibid\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not hints and is_entry_heading(text):
        return "heading_group"
    if not hints and text.lower().startswith("revocetur lector"):
        return "editorial_note"
    return "lemma" if hints else "editorial_note"


def lemma_from_text(text: str, kind: str) -> str | None:
    if kind in {"cross_reference", "editorial_note"}:
        return None
    value = normalize(text)
    if not value:
        return None
    return value.strip(" ,;:")


def helper_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_int = entry.get("inferred_printed_page")
    if page_int is None:
        return None
    lemma = entry.get("lemma_raw") or entry.get("entry_raw") or ""
    lemma = normalize(lemma)
    query_names = [lemma] if lemma else []
    if lemma:
        first = lemma.split(",", 1)[0].strip()
        if first and first not in query_names:
            query_names.append(first)
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma,
        "query_names": query_names[:4],
        "page_hints": [str(page_int)],
        "page_hint_ints": [page_int],
        "context_raw": entry.get("entry_raw") or "",
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


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            out[entry_id] = item
    return out


def split_section_files(files: list[Path]) -> tuple[list[Path], list[Path]]:
    index_files = [p for p in files if SECTION_INDEX_START <= file_seq(p) <= SECTION_INDEX_END]
    ordo_files = [p for p in files if SECTION_ORDO_START <= file_seq(p) <= SECTION_ORDO_END]
    if not index_files:
        raise SystemExit("No PL205 tail files found for the analytical index section.")
    if not ordo_files:
        raise SystemExit("No PL205 tail files found for the ORDO RERUM section.")
    return index_files, ordo_files


def build_section_one(index_files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_seed: list[dict[str, Any]] = []
    notes: list[str] = []
    letter_nodes: dict[str, str] = {}
    current_letter: str | None = None
    entry_order = 0
    section_start_file = index_files[0].as_posix()
    section_end_file = index_files[-1].as_posix()
    mode = "index"

    def ensure_letter_node(letter: str, source_file: str) -> str:
        if letter in letter_nodes:
            return letter_nodes[letter]
        node_key = f"{VOLUME_ID}:node:letter:{letter}"
        letter_nodes[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_INDEX_KEY,
                "parent_node_key": None,
                "node_order": len(nodes) + 1,
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

    for path in index_files:
        blocks = iter_blocks(path)
        for block in blocks:
            lines = parse_lines(block["text"])
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if ORDO_RE.fullmatch(line) or line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                    mode = "ordo"
                    continue
                if line in {"INDEX IN PETRI CANTORIS", "VERBUM ABBREVIATUM."}:
                    continue
                if line == "Revocetur Lector ea numerales notas crassioribus typis textui insertas.":
                    entry_order += 1
                    entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                    entries.append(
                        {
                            "entry_key": entry_key,
                            "section_key": SECTION_INDEX_KEY,
                            "parent_node_key": None,
                            "entry_order": entry_order,
                            "entry_kind": "editorial_note",
                            "lemma_raw": None,
                            "lemma_display": None,
                            "lemma_norm": None,
                            "lemma_sort": None,
                            "entry_raw": line,
                            "context_raw": line,
                            "heading_letter": None,
                            "inferred_printed_page": None,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": path.as_posix(),
                            "target_file_best": None,
                            "confidence": 0.84,
                            "raw_json": {
                                "source_file": path.as_posix(),
                                "section_kind": "analytic_subject",
                                "entry_kind_reason": "editorial instruction in the section header",
                            },
                        }
                    )
                    continue
                if SINGLE_LETTER_RE.fullmatch(line):
                    current_letter = line
                    ensure_letter_node(line, path.as_posix())
                    continue
                if mode == "ordo":
                    continue

                cleaned = normalize(line)
                if not cleaned:
                    continue
                hints = page_hints(cleaned)
                kind = entry_kind("index", cleaned, hints)
                if kind == "heading_group" and not hints and cleaned in {"A", "B", "C", "D"}:
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                lemma_raw = lemma_from_text(cleaned, kind)
                lemma_display = lemma_raw
                target_best = target_for_page(hints[0], page_map) if hints else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_INDEX_KEY,
                    "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                    "entry_order": entry_order,
                    "entry_kind": kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_display,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": cleaned,
                    "context_raw": cleaned,
                    "heading_letter": current_letter,
                    "inferred_printed_page": hints[0] if hints else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": path.as_posix(),
                    "target_file_best": target_best,
                    "confidence": 0.88 if hints else 0.66,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "page_hints": hints,
                        "section_kind": "analytic_subject",
                        "split_strategy": "line_based",
                    },
                }
                entries.append(entry)
                helper_seed_item = helper_entry(entry)
                if helper_seed_item:
                    helper_seed.append(helper_seed_item)
                for ref_order, page in enumerate(hints, start=1):
                    target_file = target_for_page(page, page_map)
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
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": 0.93 if target_file else 0.64,
                            "raw_json": {
                                "source_file": path.as_posix(),
                                "locator_method": "header_page_map" if target_file else "unresolved",
                            },
                        }
                    )

    if not entries:
        notes.append("No index entries could be recovered from the subject-index section.")
    return entries, refs, nodes, {"entries": helper_seed}, notes


def build_section_two(ordo_files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    notes: list[str] = []
    entry_order = 0
    section_start_file = ordo_files[0].as_posix()
    in_ordo = False

    for path in ordo_files:
        blocks = iter_blocks(path)
        for block in blocks:
            lines = parse_lines(block["text"])
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if not in_ordo:
                    if ORDO_RE.fullmatch(line) or line == "ORDO RERUM" or line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                        in_ordo = True
                    continue
                if line in {"Digitized by Google", "ORDO RERUM"}:
                    continue
                hints = page_hints(line)
                if not hints and is_entry_heading(line):
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:ordo:{entry_order:04d}"
                lemma_raw = lemma_from_text(line, "lemma") if hints else lemma_from_text(line, "heading_group")
                target_best = target_for_page(hints[0], page_map) if hints else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_ORDO_KEY,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group" if not hints else "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line,
                    "heading_letter": None,
                    "inferred_printed_page": hints[0] if hints else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": path.as_posix(),
                    "target_file_best": target_best,
                    "confidence": 0.9 if hints else 0.72,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "page_hints": hints,
                        "section_kind": "ordo_rerum",
                        "split_strategy": "line_based",
                    },
                }
                entries.append(entry)
                for ref_order, page in enumerate(hints, start=1):
                    target_file = target_for_page(page, page_map)
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
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": 0.96 if target_file else 0.68,
                            "raw_json": {
                                "source_file": path.as_posix(),
                                "locator_method": "header_page_map" if target_file else "unresolved",
                            },
                        }
                    )

    return entries, refs, notes


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = helper_map(helper_output)
    for entry in entries:
        helper_id = entry["entry_key"]
        helper_row = helper_by_id.get(helper_id, {})
        raw_json = entry.setdefault("raw_json", {})
        if helper_row:
            best = helper_row.get("best_candidate") or {}
            candidates = helper_row.get("candidates") or []
            raw_json["helper"] = {
                "status": helper_row.get("status"),
                "candidate_role": helper_row.get("candidate_role"),
                "reason_summary": helper_row.get("reason_summary"),
                "best_candidate": best or None,
                "top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                    }
                    for cand in candidates[:5]
                ],
            }
            if best.get("file"):
                entry["target_file_best"] = best.get("file")
                raw_json["helper_best_file"] = best.get("file")
                raw_json["helper_best_probability"] = best.get("probability")


def build_sections(index_files: list[Path], ordo_files: list[Path]) -> list[dict[str, Any]]:
    index_start_numbers = []
    index_end_numbers = []
    for path in index_files:
        nums = header_numbers(path)
        if nums:
            index_start_numbers.append(min(nums))
            index_end_numbers.append(max(nums))
    ordo_start_numbers = []
    ordo_end_numbers = []
    for path in ordo_files:
        nums = header_numbers(path)
        if nums:
            ordo_start_numbers.append(min(nums))
            ordo_end_numbers.append(max(nums))
    return [
        {
            "section_key": SECTION_INDEX_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX IN PETRI CANTORIS / VERBUM ABBREVIATUM.",
            "heading_norm": sort_norm("INDEX IN PETRI CANTORIS / VERBUM ABBREVIATUM."),
            "heading_letter": None,
            "page_start": min(index_start_numbers) if index_start_numbers else None,
            "page_end": max(index_end_numbers) if index_end_numbers else None,
            "file_start": index_files[0].as_posix(),
            "file_end": index_files[-1].as_posix(),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index headed INDEX IN PETRI CANTORIS / VERBUM ABBREVIATUM with letter dividers and page-bearing entries.",
                "evidence_files": [index_files[0].as_posix(), index_files[-1].as_posix()],
            },
        },
        {
            "section_key": SECTION_ORDO_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": sort_norm("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."),
            "heading_letter": None,
            "page_start": min(ordo_start_numbers) if ordo_start_numbers else None,
            "page_end": max(ordo_end_numbers) if ordo_end_numbers else None,
            "file_start": ordo_files[0].as_posix(),
            "file_end": ordo_files[-1].as_posix(),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table at the end of the tome.",
                "evidence_files": [ordo_files[0].as_posix(), ordo_files[-1].as_posix()],
            },
        },
    ]


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(source_root)
    index_files, ordo_files = split_section_files(files)

    index_entries, index_refs, index_nodes, helper_request_fragment, index_notes = build_section_one(index_files, page_map)
    ordo_entries, ordo_refs, ordo_notes = build_section_two(ordo_files, page_map)

    entries = index_entries + ordo_entries
    refs = index_refs + ordo_refs

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_fragment["entries"],
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    attach_helper(entries, helper_output)

    entry_by_key = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        entry = entry_by_key.get(ref["entry_key"])
        if not entry:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    sections = build_sections(index_files, ordo_files)
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytical subject index headed INDEX IN PETRI CANTORIS and the closing ORDO RERUM contents table from the OCR tail.",
        "evidence_files": [index_files[0].as_posix(), index_files[-1].as_posix(), ordo_files[0].as_posix(), ordo_files[-1].as_posix()],
    }
    notes = [
        "The subject index begins at OCR file 508 with an editorial note and continues through the W/Z tail before the ORDO RERUM heading in file 522.",
        "The closing ORDO RERUM section is editorial contents material distinct from the subject index.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]
    notes.extend(index_notes)
    notes.extend(ordo_notes)

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": index_nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL205 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "helper_request_json": args.helper_request_json.as_posix(),
            "helper_output_json": args.helper_output_json.as_posix(),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Validate PL205 index payload and preserve OCR literals.",
            "completed": [
                "index and ORDO RERUM sections identified",
                "helper request generated and helper executed",
                "intermediate fragments written",
            ],
            "pending": [
                "review any low-confidence OCR page anchors",
                "validate final payload structure",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page references.",
                "Page drift and OCR corruption are preserved literally in the refs.",
            ],
        },
    )

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output_file.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

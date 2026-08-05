#!/usr/bin/env python3
"""Usage: build the PL103 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl103_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL103/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL103_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL103_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL103 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL103_alphabetical_indices.json
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


VOLUME_ID = "PL103"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 103"

INDEX_FILES = [720, 722, 724, 726, 728]
ORDO_FILES = [729, 732, 734, 736, 738]

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LETTER_RANGE_RE = re.compile(r"^[A-ZÆŒ]\s*[-–]\s*[A-ZÆŒ]$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à)\s*(\d{1,4}))?(?=[\s\.,;:\)\]]|$)")
ENTRY_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒΑ-Ω])|(?<=\d)\s+(?=[A-ZÆŒΑ-Ω])")
NOISE_RE = re.compile(r"^(?:Digitized by Google|THIS VOLUME DOES NOT CIRCULATE OUTSIDE THE LIBRARY\.)$", re.IGNORECASE)
INDEX_HEADER_RE = re.compile(r"INDEX RERUM ET VERBORUM", re.IGNORECASE)
ORDO_HEADER_RE = re.compile(r"ORDO RERUM", re.IGNORECASE)
INDEX_PREFACE_RE = re.compile(r"In hoc Indice revocatur Lector ad numeros columnarum nostrarum\.?", re.IGNORECASE)
STOP_INDEX_RE = re.compile(
    r"INDEX RERUM ET VERBORUM\s+QU[ÆAE]\s+IN\s+SANCTI\s+BENEDICTI\s+ANIANENSIS\s+CONCORDIA\s+REGULARUM\s+CONTINENTUR\.?",
    re.IGNORECASE,
)
BARE_REMISSION_RE = re.compile(r"^(?:Vid\.?|Vide|Voir|v\.|cf\.|id\.?)$", re.IGNORECASE)
BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(xml_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for block_type, content in BLOCK_RE.findall(xml_text):
        lines = []
        for raw_line in content.splitlines():
            text = normalize(raw_line)
            if text:
                lines.append(text)
        if lines:
            blocks.append((block_type, "\n".join(lines)))
    return blocks


def build_left_page_map(files: list[Path]) -> list[tuple[int, str]]:
    page_map: list[tuple[int, str]] = []
    for path in files:
        header_lines: list[str] = []
        for block_type, content in extract_blocks(path.read_text(encoding="utf-8", errors="replace")):
            if block_type == "cabecalho":
                header_lines = content.splitlines()
                break
        left_page: int | None = None
        for line in header_lines:
            nums = [int(match) for match in re.findall(r"\b(\d{1,4})\b", line)]
            if nums:
                left_page = nums[0]
                break
        if left_page is not None and 9 <= left_page <= 2000:
            page_map.append((left_page, str(path)))
    page_map.sort()
    return page_map


def resolve_page_to_file(page_map: list[tuple[int, str]], page_ref_int: int | None) -> tuple[str | None, float | None, int | None]:
    if page_ref_int is None or not page_map:
        return None, None, None
    left_page = None
    file_path = None
    for candidate_left, candidate_path in page_map:
        if candidate_left <= page_ref_int:
            left_page = candidate_left
            file_path = candidate_path
        else:
            break
    if file_path is None:
        return None, None, None
    distance = page_ref_int - left_page if left_page is not None else None
    probability = 0.93 if distance == 0 else 0.87 if distance == 1 else 0.72
    return file_path, probability, left_page


def is_single_letter(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def is_letter_range(line: str) -> bool:
    return bool(LETTER_RANGE_RE.fullmatch(line))


def clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        text = normalize(raw)
        if not text or NOISE_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def join_hyphenated(text: str) -> str:
    return re.sub(r"(\w)-\s+(?=\w)", r"\1", text)


def split_segments(text: str) -> list[str]:
    parts = [part.strip() for part in ENTRY_SPLIT_RE.split(text) if part and part.strip()]
    merged: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if BARE_REMISSION_RE.fullmatch(part) and merged:
            merged[-1] = f"{merged[-1]} {part}"
            i += 1
            continue
        merged.append(part)
        i += 1
    return merged


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        if start < 9:
            continue
        end = match.group(2)
        raw = match.group(0).strip()
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(int(end)) if end is not None else None,
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
            }
        )
    return refs


def derive_lemma_raw(text: str) -> str | None:
    stripped = normalize(text) or ""
    if not stripped:
        return None
    if PAGE_REF_RE.search(stripped):
        lemma = stripped[: PAGE_REF_RE.search(stripped).start()].strip()
    else:
        lemma = stripped
    lemma = lemma.strip(" ,;:.")
    if not lemma:
        return None
    return lemma


def derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    base = re.sub(r"\s*\(.*?\)\s*$", "", lemma_raw).strip()
    for item in (base, lemma_raw, context_raw.split(".", 1)[0].strip()):
        item = normalize(item) or ""
        if item and item not in candidates:
            candidates.append(item)
    return candidates[:4]


def build_helper_entry(volume_id: str, entry_id: str, lemma_raw: str, context_raw: str, page_hints: list[int]) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "lemma_raw": lemma_raw,
        "query_names": derive_query_names(lemma_raw, context_raw),
        "page_hints": [str(hint) for hint in page_hints],
        "page_hint_ints": page_hints,
        "context_raw": context_raw,
    }


def page_sort_key(item: tuple[int, Path, dict[str, Any]]) -> tuple[int, int]:
    return item[0], item[0]


def parse_index_section(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    node_order = 0
    entry_order = 0
    current_letter: str | None = None
    current_node_key: str | None = None
    capture = False
    saw_index_preface = False
    started_entries = False

    def ensure_letter_node(letter: str, source_file: Path) -> None:
        nonlocal node_order, current_node_key
        if current_letter == letter and current_node_key is not None:
            return
        node_order += 1
        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        nodes.append(
            {
                "node_key": current_node_key,
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_file": str(source_file), "section_kind": "analytic_subject"},
            }
        )

    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        lines = clean_lines(parsed.get("all_text", ""))
        page_lines: list[str] = []
        for raw_line in lines:
            line = normalize(raw_line) or ""
            if not line:
                continue
            if STOP_INDEX_RE.search(line):
                break
            if not capture:
                if INDEX_HEADER_RE.search(line):
                    capture = True
                continue
            if INDEX_PREFACE_RE.search(line):
                saw_index_preface = True
                continue
            if not saw_index_preface:
                continue
            if line == "A" and current_letter is None:
                started_entries = True
                ensure_letter_node("A", path)
                current_letter = "A"
                continue
            if is_single_letter(line):
                if line == "ORDO RERUM":
                    break
                started_entries = True
                ensure_letter_node(line, path)
                current_letter = line
                continue
            if is_letter_range(line):
                started_entries = True
                next_letter = line.split("-", 1)[0].strip()
                ensure_letter_node(next_letter, path)
                current_letter = next_letter
                continue
            if line in {"Digitized by Google"}:
                continue
            if not started_entries and re.match(r"^[A-ZÆŒ]\s+[A-ZÆŒ]", line):
                marker = line[0]
                started_entries = True
                ensure_letter_node(marker, path)
                current_letter = marker
                line = line[2:].lstrip()
            if not started_entries:
                continue
            if line.startswith("A ") and current_letter is None:
                ensure_letter_node("A", path)
                current_letter = "A"
                line = line[2:].lstrip()
            page_lines.append(line)

        if page_lines:
            evidence_files.append(str(path))
            joined = join_hyphenated(" ".join(page_lines))
            for segment in split_segments(joined):
                segment = segment.strip()
                if not segment:
                    continue
                if INDEX_HEADER_RE.search(segment) or ORDO_HEADER_RE.search(segment):
                    continue
                lemma_raw = derive_lemma_raw(segment)
                if not lemma_raw:
                    continue
                entry_kind = "cross_reference" if BARE_REMISSION_RE.fullmatch(lemma_raw) else "lemma"
                page_refs = extract_page_refs(segment)
                if not page_refs and not PAGE_REF_RE.search(segment):
                    entry_kind = "cross_reference"
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                helper_entry_id = f"pl103_idx_{entry_order:04d}"
                inferred_page = page_refs[0]["page_ref_int"] if page_refs else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                    "parent_node_key": current_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": None if entry_kind == "cross_reference" else lemma_raw,
                    "lemma_display": None if entry_kind == "cross_reference" else lemma_raw,
                    "lemma_norm": None if entry_kind == "cross_reference" else lemma_raw.lower(),
                    "lemma_sort": None if entry_kind == "cross_reference" else sort_norm(lemma_raw),
                    "entry_raw": segment,
                    "context_raw": segment,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(files[0]),
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.78 if entry_kind == "lemma" else 0.68,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": "analytic_subject",
                        "line_count_hint": len(page_lines),
                        "helper_entry_id": helper_entry_id,
                    },
                }
                entries.append(entry)
                helper_entries.append(
                    build_helper_entry(
                        VOLUME_ID,
                        helper_entry_id,
                        lemma_raw,
                        segment,
                        [ref["page_ref_int"] for ref in page_refs] or ([] if inferred_page is None else [inferred_page]),
                    )
                )
                for ref_order, ref in enumerate(page_refs, start=1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": ref["page_ref_col"],
                            "line_ref_raw": ref["line_ref_raw"],
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": None,
                            "target_file_probability": None,
                            "section_start_file": str(files[0]),
                            "editorial_anchor_file": str(path),
                            "confidence": 0.58 if ref["ref_kind"] == "editorial_page" else 0.55,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": "analytic_subject",
                                "helper_entry_id": helper_entry_id,
                            },
                        }
                    )

    return nodes, entries, refs, helper_entries, evidence_files


def parse_ordo_section(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"
    first_file = str(files[0])
    buffered_line: str | None = None

    def flush_buffer(path: Path) -> None:
        nonlocal buffered_line, entry_order
        if not buffered_line:
            return
        line = buffered_line
        buffered_line = None
        entry_order += 1
        page_refs = extract_page_refs(line)
        lemma_raw = derive_lemma_raw(line)
        helper_entry_id = f"pl103_ordo_{entry_order:04d}"
        entry_kind = "lemma" if page_refs else "heading_group"
        entry_key = f"{VOLUME_ID}:entry:ordo:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_raw.lower() if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": line,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_refs[0]["page_ref_int"] if page_refs else None,
            "section_start_file": first_file,
            "editorial_anchor_file": str(path),
            "target_file_best": str(path),
            "confidence": 0.82 if page_refs else 0.74,
            "raw_json": {
                "source_file": str(path),
                "section_kind": "ordo_rerum",
                "helper_entry_id": helper_entry_id,
            },
        }
        entries.append(entry)
        if page_refs:
            helper_entries.append(
                build_helper_entry(
                    VOLUME_ID,
                    helper_entry_id,
                    lemma_raw or line,
                    line,
                    [ref["page_ref_int"] for ref in page_refs],
                )
            )
        for ref_order, ref in enumerate(page_refs, start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": first_file,
                    "editorial_anchor_file": str(path),
                    "confidence": 0.6 if ref["ref_kind"] == "editorial_page" else 0.56,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": "ordo_rerum",
                        "helper_entry_id": helper_entry_id,
                    },
                }
            )

    for path in files:
        xml_text = path.read_text(encoding="utf-8", errors="replace")
        blocks = extract_blocks(xml_text)
        start_at = 0
        if file_num(path) == 729:
            for idx, (block_type, content) in enumerate(blocks):
                if block_type == "cabecalho" and content == "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR.":
                    start_at = idx + 1
                    break
            else:
                continue
        else:
            for idx, (block_type, _) in enumerate(blocks):
                if block_type == "cabecalho":
                    start_at = idx + 1
                    break
        for block_type, content in blocks[start_at:]:
            if block_type == "cabecalho" and INDEX_HEADER_RE.search(content):
                break
            if block_type != "texto_principal":
                continue
            for raw_line in content.splitlines():
                line = normalize(raw_line) or ""
                if not line or line in {"Digitized by Google", "Z", "Ζ"}:
                    continue
                if INDEX_HEADER_RE.search(line):
                    buffered_line = None
                    return entries, refs, helper_entries
                if ORDO_HEADER_RE.search(line):
                    continue
                if buffered_line and (line[:1].islower() or line.startswith("§ ") or line.startswith("— ")):
                    buffered_line = f"{buffered_line} {line}".strip()
                    continue
                if buffered_line:
                    flush_buffer(path)
                buffered_line = line

    if files:
        flush_buffer(files[-1])
    return entries, refs, helper_entries


def parse_index_z_tail(path: Path, start_node_order: int, start_entry_order: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files = [str(path)]
    node_key = f"{VOLUME_ID}:node:{start_node_order + 1:03d}"
    nodes.append(
        {
            "node_key": node_key,
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "parent_node_key": None,
            "node_order": start_node_order + 1,
            "node_kind": "letter_group",
            "label_raw": "Z",
            "label_norm": "z",
            "label_sort": "z",
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {"source_file": str(path), "section_kind": "analytic_subject"},
        }
    )
    blocks = extract_blocks(path.read_text(encoding="utf-8", errors="replace"))
    capture_blocks: list[str] = []
    for block_type, content in blocks:
        if block_type == "cabecalho" and content == "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR.":
            break
        if block_type == "texto_principal":
            capture_blocks.append(content)
    current_entry_order = start_entry_order
    for content in capture_blocks:
        for raw_line in content.splitlines():
            line = normalize(raw_line) or ""
            if not line or line in {"Z", "Ζ"}:
                continue
            if line.startswith("Z "):
                line = line[2:].lstrip()
            if line.startswith("Ζ "):
                line = line[2:].lstrip()
            current_entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{current_entry_order:04d}"
            helper_entry_id = f"pl103_idx_{current_entry_order:04d}"
            page_refs = extract_page_refs(line)
            lemma_raw = derive_lemma_raw(line)
            entry_kind = "lemma" if page_refs else "cross_reference"
            entry = {
                "entry_key": entry_key,
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                "parent_node_key": node_key,
                "entry_order": current_entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw if entry_kind == "lemma" else None,
                "lemma_display": lemma_raw if entry_kind == "lemma" else None,
                "lemma_norm": lemma_raw.lower() if entry_kind == "lemma" and lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw) if entry_kind == "lemma" else None,
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": "Z",
                "inferred_printed_page": page_refs[0]["page_ref_int"] if page_refs else None,
                "section_start_file": str(path),
                "editorial_anchor_file": str(path),
                "target_file_best": str(path),
                "confidence": 0.78 if entry_kind == "lemma" else 0.68,
                "raw_json": {
                    "source_file": str(path),
                    "section_kind": "analytic_subject",
                    "helper_entry_id": helper_entry_id,
                    "special_tail_file": True,
                },
            }
            entries.append(entry)
            if page_refs:
                helper_entries.append(
                    build_helper_entry(
                        VOLUME_ID,
                        helper_entry_id,
                        lemma_raw or line,
                        line,
                        [ref["page_ref_int"] for ref in page_refs],
                    )
                )
            for ref_order, ref in enumerate(page_refs, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": None,
                        "target_file_probability": None,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.58 if ref["ref_kind"] == "editorial_page" else 0.55,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": "analytic_subject",
                            "helper_entry_id": helper_entry_id,
                            "special_tail_file": True,
                        },
                    }
                )
    return nodes, entries, refs, helper_entries, evidence_files


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
    return read_json(helper_output_json, {})


def summarize_helper_output(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        entry_id = item.get("entry_id")
        if not isinstance(entry_id, str):
            continue
        best = item.get("best_candidate") or {}
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best,
            "candidates": item.get("candidates") or [],
        }
    return summary


def _candidate_matches_page(candidate: dict[str, Any], page_ref_int: int | None) -> tuple[int, int]:
    if page_ref_int is None:
        return (0, 0)
    inferred = candidate.get("inferred_printed_page")
    if inferred == page_ref_int:
        return (3, 0)
    if isinstance(inferred, int) and abs(inferred - page_ref_int) == 1:
        return (2, -abs(inferred - page_ref_int))
    evidence = candidate.get("evidence") or []
    for item in evidence:
        raw = str(item.get("raw") or "")
        if str(page_ref_int) in raw:
            return (2, 0)
    return (1, -abs(inferred - page_ref_int)) if isinstance(inferred, int) else (0, 0)


def resolve_ref_candidate(helper: dict[str, Any], page_ref_int: int | None) -> dict[str, Any] | None:
    candidates = helper.get("candidates") or []
    if not candidates:
        best = helper.get("best_candidate") or {}
        return best or None
    ranked = sorted(
        candidates,
        key=lambda cand: (
            _candidate_matches_page(cand, page_ref_int)[0],
            _candidate_matches_page(cand, page_ref_int)[1],
            float(cand.get("probability") or 0.0),
            float(cand.get("score") or 0.0),
        ),
        reverse=True,
    )
    chosen = ranked[0]
    if page_ref_int is not None and _candidate_matches_page(chosen, page_ref_int)[0] == 0:
        best = helper.get("best_candidate") or {}
        return best or chosen
    return chosen


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_summary: dict[str, dict[str, Any]]) -> None:
    helper_by_entry_key: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_id = entry["raw_json"].get("helper_entry_id")
        if not isinstance(entry_id, str):
            continue
        helper = helper_summary.get(entry_id)
        if not helper:
            continue
        helper_by_entry_key[entry["entry_key"]] = helper
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best["file"]
            if entry["inferred_printed_page"] is None and best.get("inferred_printed_page") is not None:
                entry["inferred_printed_page"] = best.get("inferred_printed_page")
        entry["raw_json"]["helper"] = helper
        entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))
    for ref in refs:
        helper = helper_by_entry_key.get(ref["entry_key"])
        if not helper:
            continue
        chosen = resolve_ref_candidate(helper, ref.get("page_ref_int"))
        if chosen and chosen.get("file"):
            ref["target_file"] = chosen["file"]
            ref["target_file_probability"] = float(chosen.get("probability") or 0.0)
            ref["confidence"] = max(ref["confidence"], min(0.99, float(chosen.get("probability") or 0.0)))
            ref["raw_json"]["helper_resolution"] = {
                "status": helper.get("status"),
                "reason_summary": chosen.get("reason_summary") or helper.get("reason_summary"),
                "candidate_role": chosen.get("candidate_role"),
                "inferred_printed_page": chosen.get("inferred_printed_page"),
                "file": chosen.get("file"),
                "probability": chosen.get("probability"),
            }


def apply_page_map(entries: list[dict[str, Any]], refs: list[dict[str, Any]], page_map: list[tuple[int, str]]) -> None:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
        target_file, probability, left_page = resolve_page_to_file(page_map, ref.get("page_ref_int"))
        if not target_file:
            continue
        ref["target_file"] = target_file
        ref["target_file_probability"] = probability
        ref["confidence"] = max(ref["confidence"], float(probability or 0.0))
        ref["raw_json"]["page_map_resolution"] = {
            "page_ref_int": ref.get("page_ref_int"),
            "matched_left_page": left_page,
            "file": target_file,
            "probability": probability,
        }
    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"]) or []
        if not entry_refs:
            continue
        first_resolved = next((ref for ref in entry_refs if ref.get("target_file")), None)
        if first_resolved:
            entry["target_file_best"] = first_resolved["target_file"]
            entry["confidence"] = max(entry["confidence"], float(first_resolved.get("target_file_probability") or 0.0))


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
    *,
    skip_helper: bool = False,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    index_files = [path for path in files if file_num(path) in INDEX_FILES]
    ordo_files = [path for path in files if file_num(path) in ORDO_FILES]
    page_map = build_left_page_map(files)

    nodes, entries, refs, helper_entries, evidence_files = parse_index_section(index_files)
    z_tail_file = next(path for path in files if file_num(path) == 729)
    z_nodes, z_entries, z_refs, z_helper_entries, z_evidence = parse_index_z_tail(
        z_tail_file,
        start_node_order=len(nodes),
        start_entry_order=len(entries),
    )
    nodes.extend(z_nodes)
    entries.extend(z_entries)
    refs.extend(z_refs)
    helper_entries.extend(z_helper_entries)
    evidence_files.extend(z_evidence)
    ordo_entries, ordo_refs, ordo_helper_entries = parse_ordo_section(ordo_files)
    entries.extend(ordo_entries)
    refs.extend(ordo_refs)
    helper_entries.extend(ordo_helper_entries)

    helper_output: dict[str, Any] = {}
    if not skip_helper:
        helper_request = {
            "volume_id": VOLUME_ID,
            "source_root": str(source_root),
            "options": {"top_k": 5, "adjacency_window": 2},
            "entries": helper_entries,
        }
        write_json(helper_request_json, helper_request)
        helper_output = run_helper(helper_request_json, helper_output_json)
        helper_summary = summarize_helper_output(helper_output)
        apply_helper(entries, refs, helper_summary)
    apply_page_map(entries, refs, page_map)

    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM ET VERBORUM AD SANCTI BENEDICTI ANIANENSIS CONCORDIAM REGULARUM.",
            "heading_norm": "index rerum et verborum ad sancti benedicti anianensis concordiam regularum",
            "heading_letter": None,
            "page_start": 1439,
            "page_end": 1458,
            "file_start": str(index_files[0]),
            "file_end": str(z_tail_file),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index in the tail of the volume, with letter-group markers A through Z and multi-reference entries on each line; the last Z entries share file 729 with the opening of the Ordo rerum.",
                "source_files": [str(path) for path in index_files],
                "helper_status": helper_output.get("status"),
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
            "page_start": 1457,
            "page_end": 1476,
            "file_start": str(ordo_files[0]),
            "file_end": str(ordo_files[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Editorial contents table following the alphabetical index.",
                "source_files": [str(path) for path in ordo_files],
                "helper_status": helper_output.get("status"),
            },
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "Tail alphabetical index recovered together with the closing Ordo rerum.",
            "OCR file suffixes do not match printed-page order in the underlying source, so target files were anchored conservatively and refined where the helper produced a direct match.",
        ],
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the alphabetical index and the closing Ordo rerum from the OCR tail with conservative segmentation.",
        "evidence_files": evidence_files,
    }

    notes = [
        "The first section is an analytical subject index over the Concordia regularum.",
        "The final section is editorial contents, not a second alphabetical index.",
        "Bare remissions were kept inside entries and not serialized as standalone refs.",
    ]

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
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(output_file),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate the PL103 alphabetical payload and keep OCR literals intact.",
            "completed": [
                "index and ordo sections identified",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "validate final JSON payload",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR file, printed page, and cited reference separate.",
                "Use helper evidence conservatively and preserve ambiguity in raw_json when present.",
            ],
        },
    )

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
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL103 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--skip-helper", action="store_true")
    args = ap.parse_args()

    build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
        skip_helper=args.skip_helper,
    )


if __name__ == "__main__":
    main()

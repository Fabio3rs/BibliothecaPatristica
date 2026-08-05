#!/usr/bin/env python3
"""Usage: build the PG156 alphabetical-index payload from the OCR tail and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg156_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG156/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG156_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG156_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG156 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG156_alphabetical_indices.json
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG156"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 156"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS AD GEORGII PHRANTZÆ CHRONICON MAJUS.",
        "heading_norm": "index analyticus ad georgii phrantzae chronicon majus",
        "heading_letter": None,
        "file_start_seq": 770,
        "file_end_seq": 779,
        "page_start": 1079,
        "page_end": 1098,
        "kind_reason": (
            "Alphabetical analytical subject index headed INDEX ANALYTICUS / INDEX IN GEORGIUM "
            "PHRANTZAM and continuing through the A-Z letter sequence."
        ),
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:foreign_terms:002",
        "section_order": 2,
        "section_kind": "foreign_terms",
        "heading_raw": "INDEX GRÆCITATIS.",
        "heading_norm": "index graecitatis",
        "heading_letter": None,
        "file_start_seq": 780,
        "file_end_seq": 781,
        "page_start": 1099,
        "page_end": 1102,
        "kind_reason": (
            "Alphabetical Greek vocabulary index headed INDEX GRÆCITATIS; modeled as foreign_terms rather "
            "than as the general subject index."
        ),
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "file_start_seq": 781,
        "file_end_seq": 782,
        "page_start": 1101,
        "page_end": 1104,
        "kind_reason": (
            "Closing table of contents headed ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR, distinct from the "
            "two preceding indices."
        ),
    },
]

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.S | re.I)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]$")
HEADER_ORDO_TOP_RE = re.compile(r"^\d{4}\s*ORDO RERUM\s*\d{4}$")
ROMAN_ITEM_RE = re.compile(r"^[IVXLCDM]+\.\s*[—-]?\s*", re.I)
UPPER_HEADING_RE = re.compile(r"^[A-ZÆŒ.\- ,;:'()]+$")
REF_END_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})\.?$")
GREEK_START_RE = re.compile(r"(?<!\S)([Ἀ-ῼἀ-ῳΑ-Ω])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda path: int(path.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(raw.strip())
        blocks: list[dict[str, Any]] = []
        for idx, block in enumerate(root.findall("bloco"), start=1):
            content = "".join(block.itertext())
            lines = [normalize_space(line) for line in content.splitlines()]
            blocks.append(
                {
                    "index": idx,
                    "type": (block.attrib.get("tipo") or "").strip().lower(),
                    "lines": [line for line in lines if line],
                }
            )
        return blocks
    except ET.ParseError:
        blocks = []
        for idx, match in enumerate(BLOCK_RE.finditer(raw), start=1):
            attrs = match.group("attrs") or ""
            type_match = re.search(r'tipo="([^"]+)"', attrs)
            block_type = type_match.group(1).strip().lower() if type_match else ""
            content = re.sub(r"<[^>]+>", " ", match.group("content") or "")
            lines = [normalize_space(line) for line in content.splitlines()]
            blocks.append({"index": idx, "type": block_type, "lines": [line for line in lines if line]})
        return blocks


def extract_header_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for block in extract_blocks(path):
            if block["type"] != "cabecalho":
                continue
            header = " ".join(block["lines"])
            for match in PAGE_RE.finditer(header):
                page = int(match.group(1))
                mapping.setdefault(page, str(path))
            break
    return mapping


def is_continuation(fragment: str) -> bool:
    if not fragment:
        return False
    first = fragment[0]
    if first.islower():
        return True
    if first in ",;:.)]—-":
        return True
    return False


def is_heading_line(line: str) -> bool:
    if not line:
        return False
    if LETTER_RE.fullmatch(line):
        return True
    if HEADER_ORDO_TOP_RE.fullmatch(line):
        return True
    if line in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
        return True
    return False


def parse_page_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str | None, str | None]] = set()
    for match in PAGE_RANGE_RE.finditer(entry_raw):
        token = normalize_space(match.group(0)).rstrip(" ,;:.")
        start = int(match.group(1))
        end = match.group(2)
        key = ("editorial_range", start, match.group(1), end)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": match.group(1),
                "range_end_raw": end,
            }
        )
    covered_spans = [m.span() for m in PAGE_RANGE_RE.finditer(entry_raw)]
    for match in PAGE_RE.finditer(entry_raw):
        if any(start <= match.start() < end for start, end in covered_spans):
            continue
        token = match.group(1)
        page = int(token)
        key = ("editorial_page", page, None, None)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def infer_lemma(entry_raw: str, refs: list[dict[str, Any]], section_kind: str) -> str | None:
    text = normalize_space(entry_raw).strip(" ,;:.")
    if not text:
        return None
    if section_kind == "ordo_rerum":
        match = REF_END_RE.match(text)
        if match:
            return normalize_space(match.group("body")).rstrip(" ,;:.") or text
        return text
    first_ref_pos = None
    for ref in refs:
        pos = text.find(ref["ref_raw"])
        if pos >= 0 and (first_ref_pos is None or pos < first_ref_pos):
            first_ref_pos = pos
    if first_ref_pos is not None and first_ref_pos > 0:
        return text[:first_ref_pos].strip(" ,;:.") or text
    if ". V." in text or ". Vid." in text or ". Vide" in text:
        return text.split(".", 1)[0].strip(" ,;:.") or text
    return text


def infer_entry_kind(entry_raw: str, refs: list[dict[str, Any]], section_kind: str) -> str:
    stripped = normalize_space(entry_raw)
    if not stripped:
        return "editorial_note"
    if LETTER_RE.fullmatch(stripped):
        return "heading_group"
    if not refs and re.search(r"\b[Vv](?:id\.|ide|oir|\. )\b|\bcf\.\b|\bid\.\b", stripped):
        return "cross_reference"
    if section_kind == "ordo_rerum" and UPPER_HEADING_RE.fullmatch(stripped.rstrip(".")):
        return "heading_group"
    return "lemma"


def split_greek_line(line: str) -> list[str]:
    line = normalize_space(line)
    if not line:
        return []
    starts: list[int] = []
    for match in GREEK_START_RE.finditer(line):
        start = match.start()
        window = line[start : start + 120]
        if re.search(r"\d{1,4}", window):
            starts.append(start)
    if len(starts) <= 1:
        return [line]
    parts: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(line)
        part = normalize_space(line[start:end])
        if part:
            parts.append(part)
    return parts or [line]


def segment_foreign_terms_line(line: str, carry: str | None) -> tuple[list[str], str | None]:
    line = normalize_space(line)
    if not line:
        return [], carry

    starts: list[int] = []
    for match in GREEK_START_RE.finditer(line):
        start = match.start()
        window = line[start : start + 120]
        if re.search(r"\d{1,4}", window):
            starts.append(start)

    emitted: list[str] = []
    pending = carry

    if starts:
        if starts[0] > 0:
            prefix = normalize_space(line[: starts[0]])
            if prefix and pending:
                emitted.append(normalize_space(f"{pending} {prefix}"))
                pending = None
        segments = []
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(line)
            segment = normalize_space(line[start:end])
            if segment:
                segments.append(segment)
        if segments:
            first = segments[0]
            if pending:
                if re.search(r"\d{1,4}", first):
                    emitted.append(normalize_space(f"{pending} {first}"))
                    pending = None
                else:
                    pending = normalize_space(f"{pending} {first}")
                    segments = segments[1:]
            emitted.extend(segments if pending is None else segments[1:])
    else:
        if pending:
            if line[:1].isdigit() or re.search(r"\d{1,4}", line):
                emitted.append(normalize_space(f"{pending} {line}"))
                pending = None
            else:
                pending = normalize_space(f"{pending} {line}")
        elif re.search(r"\d{1,4}", line):
            emitted.append(line)
        else:
            pending = line

    emitted = [item for item in emitted if item]
    if pending and re.search(r"\d{1,4}", pending):
        emitted.append(pending)
        pending = None
    return emitted, pending


def add_node(
    nodes: list[dict[str, Any]],
    section_key: str,
    node_order: int,
    node_kind: str,
    label: str,
    source_file: str,
    source_block_index: int,
    parent_node_key: str | None = None,
) -> dict[str, Any]:
    node_key = f"{section_key}:node:{node_order:04d}"
    node = {
        "node_key": node_key,
        "section_key": section_key,
        "parent_node_key": parent_node_key,
        "node_order": node_order,
        "node_kind": node_kind,
        "label_raw": label,
        "label_norm": sort_norm(label),
        "label_sort": sort_norm(label),
        "node_level": 1 if parent_node_key is None else 2,
        "confidence": 0.99,
        "raw_json": {
            "source_file": source_file,
            "source_block_index": source_block_index,
        },
    }
    nodes.append(node)
    return node


def build_entry(
    *,
    section_def: dict[str, Any],
    entry_key: str,
    entry_order: int,
    parent_node_key: str | None,
    entry_raw: str,
    source_file: str,
    source_block_index: int,
    heading_letter: str | None,
    page_map: dict[int, str],
    helper_info: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    refs = parse_page_refs(entry_raw)
    lemma_raw = infer_lemma(entry_raw, refs, section_def["section_kind"])
    entry_kind = infer_entry_kind(entry_raw, refs, section_def["section_kind"])
    helper_best = (helper_info or {}).get("best_candidate") or {}
    target_best = helper_best.get("file")
    if not target_best and refs:
        target_best = page_map.get(refs[0]["page_ref_int"])
    entry = {
        "entry_key": entry_key,
        "section_key": section_def["section_key"],
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": sort_norm(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": entry_raw,
        "context_raw": None,
        "heading_letter": heading_letter,
        "inferred_printed_page": refs[0]["page_ref_int"] if refs else None,
        "section_start_file": None,
        "editorial_anchor_file": source_file,
        "target_file_best": target_best,
        "confidence": 0.86 if refs else 0.72,
        "raw_json": {
            "source_file": source_file,
            "source_block_index": source_block_index,
            "section_kind_reason": section_def["kind_reason"],
            "helper": helper_info or None,
        },
    }
    entry_refs: list[dict[str, Any]] = []
    for idx, ref in enumerate(refs, start=1):
        target_file = page_map.get(ref["page_ref_int"])
        probability = 0.98 if target_file else None
        if not target_file and helper_best.get("file") and len(refs) == 1:
            target_file = helper_best["file"]
            probability = helper_best.get("probability")
        entry_refs.append(
            {
                "entry_key": entry_key,
                "ref_order": idx,
                "ref_kind": ref["ref_kind"],
                "ref_raw": ref["ref_raw"],
                "page_ref_raw": ref["page_ref_raw"],
                "page_ref_int": ref["page_ref_int"],
                "page_ref_col": ref["page_ref_col"],
                "line_ref_raw": ref["line_ref_raw"],
                "range_start_raw": ref["range_start_raw"],
                "range_end_raw": ref["range_end_raw"],
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": None,
                "editorial_anchor_file": source_file,
                "confidence": 0.9 if target_file else 0.62,
                "raw_json": {
                    "source_file": source_file,
                    "helper_status": (helper_info or {}).get("status"),
                    "helper_candidate_role": helper_best.get("candidate_role"),
                    "helper_reason_summary": helper_best.get("reason_summary") or (helper_info or {}).get("reason_summary"),
                    "helper_candidates": [
                        {
                            "file": candidate.get("file"),
                            "probability": candidate.get("probability"),
                            "evidence_kinds": candidate.get("evidence_kinds"),
                        }
                        for candidate in ((helper_info or {}).get("candidates") or [])[:3]
                    ] or None,
                },
            }
        )
    return entry, entry_refs


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    seen_page = 0
    for entry in entries:
        refs = parse_page_refs(entry["entry_raw"])
        page_hints = [ref["page_ref_int"] for ref in refs if ref["page_ref_int"] is not None]
        if not page_hints:
            continue
        needs_helper = entry["target_file_best"] is None or entry["section_key"].endswith("ordo_rerum:003")
        if not needs_helper:
            continue
        seen_page += 1
        if seen_page > 80:
            break
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:80],
                "query_names": [value for value in [entry.get("lemma_raw"), entry.get("lemma_display"), entry.get("lemma_norm")] if value][:4],
                "page_hints": [str(value) for value in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry["entry_raw"][:240],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(helper_request_json), "--output", str(helper_output_json), "--pretty"]
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("results") or helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        mapping[str(entry_id)] = {
            "status": item.get("status"),
            "reason_summary": item.get("reason_summary"),
            "best_candidate": item.get("best_candidate") or {},
            "candidates": item.get("candidates") or [],
        }
    return mapping


def extract_sections(source_root: Path, page_map: dict[int, str], helper_info_by_entry: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = discover_files(source_root)
    blocks_by_file = {str(path): extract_blocks(path) for path in files}
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_counter = 0
    node_counter = 0

    letter_node_map: dict[str, str] = {}

    for section_def in SECTION_DEFS:
        section_files = [path for path in files if section_def["file_start_seq"] <= file_seq(path) <= section_def["file_end_seq"]]
        if not section_files:
            continue
        section_start_file = str(section_files[0])
        section_end_file = str(section_files[-1])
        current_letter: str | None = None
        current_node_key: str | None = None
        pending_chunks: list[str] = []
        pending_source_file = section_start_file
        pending_block_index = 0
        pending_foreign_prefix: str | None = None
        started = section_def["section_kind"] != "analytic_subject"
        if section_def["section_kind"] == "foreign_terms":
            started = False
        if section_def["section_kind"] == "ordo_rerum":
            started = False

        def flush_pending() -> None:
            nonlocal entry_counter, pending_chunks, pending_source_file, pending_block_index
            if not pending_chunks:
                return
            entry_raw = normalize_space(" ".join(pending_chunks))
            if not entry_raw or is_heading_line(entry_raw):
                pending_chunks = []
                return
            entry_counter += 1
            entry_key = f"{section_def['section_key']}:entry:{entry_counter:04d}"
            entry, entry_refs = build_entry(
                section_def=section_def,
                entry_key=entry_key,
                entry_order=entry_counter,
                parent_node_key=current_node_key,
                entry_raw=entry_raw,
                source_file=pending_source_file,
                source_block_index=pending_block_index,
                heading_letter=current_letter if section_def["section_kind"] != "ordo_rerum" else None,
                page_map=page_map,
                helper_info=helper_info_by_entry.get(entry_key),
            )
            entry["section_start_file"] = section_start_file
            if entry["context_raw"] is None and len(entry_raw) > 150:
                entry["context_raw"] = entry_raw[:150]
            entries.append(entry)
            for ref in entry_refs:
                ref["section_start_file"] = section_start_file
                refs.append(ref)
            pending_chunks = []

        for path in section_files:
            for block in blocks_by_file[str(path)]:
                block_type = block["type"]
                for raw_line in block["lines"]:
                    line = normalize_space(raw_line)
                    upper = line.upper()
                    if line == "Digitized by Google":
                        continue
                    if section_def["section_kind"] == "analytic_subject":
                        if not started:
                            if "INDEX ANALYTICUS" in upper:
                                started = True
                            continue
                        if "INDEX IN GEORGIUM PHRANTZAM" in upper:
                            continue
                    elif section_def["section_kind"] == "foreign_terms":
                        if not started:
                            if "INDEX GRÆCITATIS" in upper or "INDEX GRAECITATIS" in upper:
                                started = True
                            continue
                        if "ORDO RERUM" in upper and "QUÆ" not in upper and "QUAE" not in upper and file_seq(path) == 781 and block["index"] >= 4:
                            flush_pending()
                            started = False
                            break
                        if HEADER_ORDO_TOP_RE.fullmatch(line):
                            continue
                    else:
                        if not started:
                            if line == "ORDO RERUM":
                                started = True
                            continue
                        if upper.startswith("FINIS TOMI") or upper.startswith("PARISIIS"):
                            flush_pending()
                            continue
                        if HEADER_ORDO_TOP_RE.fullmatch(line):
                            continue
                        if line in {"QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
                            continue

                    if not started:
                        continue

                    if block_type == "nota_marginal" and LETTER_RE.fullmatch(line):
                        flush_pending()
                        if line not in letter_node_map:
                            node_counter += 1
                            node = add_node(
                                nodes,
                                section_def["section_key"],
                                node_counter,
                                "letter_group",
                                line,
                                str(path),
                                block["index"],
                            )
                            letter_node_map[line] = node["node_key"]
                        current_letter = line
                        current_node_key = letter_node_map[line]
                        continue

                    if section_def["section_kind"] == "analytic_subject" and LETTER_RE.fullmatch(line):
                        flush_pending()
                        if line not in letter_node_map:
                            node_counter += 1
                            node = add_node(
                                nodes,
                                section_def["section_key"],
                                node_counter,
                                "letter_group",
                                line,
                                str(path),
                                block["index"],
                            )
                            letter_node_map[line] = node["node_key"]
                        current_letter = line
                        current_node_key = letter_node_map[line]
                        continue

                    if section_def["section_kind"] == "foreign_terms":
                        fragments, pending_foreign_prefix = segment_foreign_terms_line(line, pending_foreign_prefix)
                    else:
                        fragments = [line]

                    for fragment in fragments:
                        frag = normalize_space(fragment)
                        if not frag or is_heading_line(frag):
                            continue
                        if section_def["section_kind"] in {"analytic_subject", "foreign_terms"} and current_node_key is None and not parse_page_refs(frag):
                            continue
                        if section_def["section_kind"] == "ordo_rerum" and UPPER_HEADING_RE.fullmatch(frag.rstrip(".")) and not parse_page_refs(frag):
                            flush_pending()
                            node_counter += 1
                            node = add_node(
                                nodes,
                                section_def["section_key"],
                                node_counter,
                                "heading_group",
                                frag.rstrip("."),
                                str(path),
                                block["index"],
                            )
                            current_node_key = node["node_key"]
                            continue
                        if pending_chunks and not is_continuation(frag):
                            flush_pending()
                        if not pending_chunks:
                            pending_source_file = str(path)
                            pending_block_index = block["index"]
                        pending_chunks.append(frag)

            if section_def["section_kind"] == "foreign_terms" and not started:
                break

        if section_def["section_kind"] == "foreign_terms" and pending_foreign_prefix:
            pending_chunks = [pending_foreign_prefix]
            flush_pending()
            pending_foreign_prefix = None
        flush_pending()

        sections.append(
            {
                "section_key": section_def["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section_def["section_order"],
                "section_kind": section_def["section_kind"],
                "heading_raw": section_def["heading_raw"],
                "heading_norm": section_def["heading_norm"],
                "heading_letter": section_def["heading_letter"],
                "page_start": section_def["page_start"],
                "page_end": section_def["page_end"],
                "file_start": section_start_file,
                "file_end": section_end_file,
                "confidence": 0.95 if section_def["section_kind"] != "ordo_rerum" else 0.97,
                "raw_json": {
                    "section_kind_reason": section_def["kind_reason"],
                    "file_start_seq": section_def["file_start_seq"],
                    "file_end_seq": section_def["file_end_seq"],
                    "editorial_page_note": (
                        "The ORDO header on OCR file 782 is numerically uncertain in OCR; page_end=1104 is inferred from sequence continuity."
                        if section_def["section_kind"] == "ordo_rerum"
                        else None
                    ),
                },
            }
        )

    return sections, nodes, entries, refs


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> None:
    files = discover_files(source_root)
    page_map = extract_header_page_map(files)

    # First pass: entries without helper data, so the request can include every materialized line item.
    sections, nodes, pre_entries, pre_refs = extract_sections(source_root, page_map, {})
    helper_request = build_helper_request(pre_entries, helper_request_json, source_root)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_info = helper_map(helper_output)

    # Second pass: rebuild entries and refs with helper evidence attached.
    sections, nodes, entries, refs = extract_sections(source_root, page_map, helper_info)

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "PG156 tail contains an analytic subject index, an Index Græcitatis, and a closing Ordo rerum.",
    }
    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered line-level entries for the analytical index, the Greek vocabulary index, and the ordo rerum closure block "
            "from OCR files 770-782."
        ),
        "evidence_files": [str(path) for path in files if 770 <= file_seq(path) <= 782],
    }
    notes = [
        "Section 1 is the analytical subject index for Georgius Phrantza.",
        "Section 2 is the separate Index Græcitatis and is modeled as foreign_terms.",
        "Section 3 is the closing ordo rerum rather than an alphabetical index proper.",
        "OCR literals were preserved; ambiguous or noisy targets rely on page-map plus helper evidence in raw_json.",
    ]
    scripture_refs: list[dict[str, Any]] = []
    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "source_root": str(source_root),
        "output_file": str(output_file),
    }
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "assembled final payload",
        "completed": [
            "confirmed INDEX ANALYTICUS section",
            "confirmed INDEX GRÆCITATIS section",
            "confirmed ORDO RERUM closure section",
            "generated helper request and helper output",
            "assembled canonical payload",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The page numbering on OCR file 782 is noisy; the section record preserves that uncertainty in raw_json.",
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "manifest.json", manifest)
    write_json(intermediate_dir / "todo.json", todo)

    payload = {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }
    write_json(output_file, payload)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PG012 alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg012_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG012/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG012_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG012_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG012 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG012_alphabetical_indices.json
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG012"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 12"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION1 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "section_kind_reason": (
        "Analytical alphabetical index headed 'INDEX ANALYTICUS.' with letter-group nodes A-U; "
        "the following ORDO RERUM is editorial closure and is kept as a separate section."
    ),
    "file_start_seq": 845,
    "file_end_seq": 853,
    "start_marker": "INDEX ANALYTICUS",
    "stop_marker": "ORDO RERUM",
    "page_start": 1685,
    "page_end": 1702,
}

SECTION2 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM",
    "section_kind_reason": (
        "Closing editorial contents table headed 'ORDO RERUM'; not alphabetical, but a valid "
        "editorial-closure section for the volume."
    ),
    "file_start_seq": 854,
    "file_end_seq": 856,
    "start_marker": "ORDO RERUM",
    "stop_marker": None,
    "page_start": 1763,
    "page_end": 1708,
}

SECTION_DEFS = [SECTION1, SECTION2]

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_CLUSTER_RE = re.compile(
    r"(?P<cluster>(?:,\s*(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)(?:\s*et\s+seqq?\.?)?)+)\s*$",
    re.IGNORECASE,
)
SPLIT_AFTER_REFS_RE = re.compile(
    r"(?:(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)|(?:ibid\.?)|(?:not\.?)|(?:monit\.?))\.\s+(?=[A-ZÆŒΑ-Ω])",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒΑ-Ω])")
BLOCK_RE = re.compile(
    r'<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>',
    flags=re.DOTALL | re.IGNORECASE,
)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
PAGE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
NOISE_LINES = {"Digitized by Google", "PATROL. GR. XII."}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
    text = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def lemma_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, list[str]]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = (attrs.get("tipo") or "").strip().lower()
        if block_type not in {"texto_principal", "nota_marginal"}:
            continue
        lines: list[str] = []
        for raw_line in (match.group("content") or "").splitlines():
            cleaned = normalize(raw_line)
            if cleaned and cleaned not in NOISE_LINES:
                lines.append(cleaned)
        if lines:
            blocks.append((block_type, lines))
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        for match in PAGE_NUM_RE.finditer(header):
            page_map.setdefault(int(match.group(1)), str(path))
    return page_map


def split_fragments(text: str) -> list[str]:
    if not text:
        return []
    text = normalize(text)
    text = SENTENCE_SPLIT_RE.sub("\n", text)
    text = SPLIT_AFTER_REFS_RE.sub(lambda m: m.group(0).replace(". ", ".\n"), text)
    return [normalize(chunk) for chunk in text.splitlines() if normalize(chunk)]


def collect_lines(section: dict[str, Any], files: list[Path]) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    started = False
    entry_started = section["section_kind"] == "ordo_rerum"

    for path in files:
        seq = file_seq(path)
        if seq < section["file_start_seq"] or seq > section["file_end_seq"]:
            continue
        for block_type, lines in extract_blocks(path):
            buffer: list[str] = []
            buffer_file = str(path)

            def flush() -> None:
                chunk = normalize(" ".join(buffer))
                if chunk:
                    collected.append((chunk, buffer_file))
                buffer.clear()

            for line in lines:
                upper = line.upper()
                if not started:
                    if section["start_marker"] in upper:
                        started = True
                    continue
                if section["stop_marker"] and section["stop_marker"] in upper:
                    flush()
                    return collected
                if upper in NOISE_LINES:
                    continue
                if section["section_kind"] == "analytic_subject" and upper.startswith("INDEX ANALYTICUS"):
                    continue
                if section["section_kind"] == "ordo_rerum" and upper.startswith("ORDO RERUM"):
                    continue
                if section["section_kind"] == "analytic_subject" and not entry_started:
                    if LETTER_RE.fullmatch(line):
                        entry_started = True
                    else:
                        continue
                if LETTER_RE.fullmatch(line):
                    flush()
                    collected.append((line, str(path)))
                    continue
                if buffer and buffer[-1].endswith("-"):
                    buffer[-1] = f"{buffer[-1][:-1]}{line.lstrip()}"
                else:
                    buffer.append(line)
            flush()
    return collected


def extract_tail_refs(text: str) -> tuple[str, list[dict[str, Any]]]:
    cleaned = normalize(text)
    match = PAGE_CLUSTER_RE.search(cleaned)
    refs: list[dict[str, Any]] = []
    if not match:
        return cleaned.strip(" ,;:.") or cleaned, refs
    cluster = match.group("cluster")
    body = cleaned[: match.start("cluster")].strip(" ,;:.")
    for token in re.findall(r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*et\s+seqq?\.?)?", cluster, flags=re.IGNORECASE):
        token = normalize(token).rstrip(" ,;:.")
        if not token:
            continue
        range_match = re.fullmatch(r"(?P<start>\d{1,4})(?:\s*[-–—]\s*(?P<end>\d{1,4}))?(?P<tail>\s*et\s+seqq?\.?)?", token, flags=re.IGNORECASE)
        if not range_match:
            continue
        start = int(range_match.group("start"))
        end = range_match.group("end")
        tail = range_match.group("tail")
        ref_kind = "editorial_range" if end or tail else "editorial_page"
        refs.append(
            {
                "ref_kind": ref_kind,
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_match.group("start") if (end or tail) else None,
                "range_end_raw": end if end else None,
            }
        )
    return body, refs


def infer_entry_kind(entry_raw: str, refs: list[dict[str, Any]]) -> str:
    stripped = entry_raw.strip()
    if stripped in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U"}:
        return "heading_group"
    if re.search(r"\b(Vid\.?|Vide|Voir|Cf\.?|Id\.?)\b", stripped, flags=re.IGNORECASE) and not refs:
        return "cross_reference"
    if stripped.isupper() and not refs and len(stripped.split()) <= 4:
        return "heading_group"
    if not refs and (stripped.startswith("HOMILIÆ") or stripped.startswith("SELECTA") or stripped.startswith("Monitum") or stripped.startswith("PROLOGUS")):
        return "heading_group"
    return "lemma"


def infer_lemma(entry_raw: str, refs: list[dict[str, Any]]) -> str | None:
    if not entry_raw:
        return None
    text = normalize(entry_raw)
    if not refs:
        return text.strip(" ,;:.") or None
    ref_raw = refs[0]["ref_raw"]
    idx = text.rfind(ref_raw)
    if idx > 0:
        return text[:idx].strip(" ,;:.") or None
    return text.strip(" ,;:.") or None


def make_query_names(entry_raw: str, lemma_raw: str | None) -> list[str]:
    values = [lemma_raw, entry_raw]
    if lemma_raw and "." in lemma_raw:
        values.append(lemma_raw.replace(".", ""))
    if entry_raw and "." in entry_raw:
        values.append(entry_raw.replace(".", ""))
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out[:5]


def build_initial_entries(section: dict[str, Any], lines: list[tuple[str, str]], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    current_letter_key: str | None = None
    current_letter_label: str | None = None
    node_order = 0
    entry_order = 0

    for text, source_file in lines:
        for fragment in split_fragments(text):
            frag = fragment.strip()
            if not frag:
                continue
            if section["section_kind"] == "analytic_subject" and LETTER_RE.fullmatch(frag):
                node_order += 1
                current_letter_label = frag
                current_letter_key = f"{VOLUME_ID}:node:{section['section_order']:02d}:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_letter_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": frag,
                        "label_norm": sort_norm(frag),
                        "label_sort": sort_norm(frag),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": source_file, "role": "alphabetic_letter"},
                    }
                )
                continue
            body, tail_refs = extract_tail_refs(frag)
            entry_kind = infer_entry_kind(body, tail_refs)
            lemma_raw = infer_lemma(body, tail_refs)
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
            inferred_printed_page = tail_refs[0]["page_ref_int"] if tail_refs else None
            target_file_best = page_map.get(inferred_printed_page) if inferred_printed_page is not None else source_file
            entry_payload = {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": current_letter_key if section["section_kind"] == "analytic_subject" else None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": body,
                "context_raw": body if len(body) <= 220 else body[:220],
                "heading_letter": current_letter_label,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "target_file_best": target_file_best,
                "confidence": 0.82 if tail_refs else 0.7,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section["section_kind"],
                    "section_kind_reason": section["section_kind_reason"],
                    "query_names": make_query_names(body, lemma_raw),
                },
            }
            refs_for_entry: list[dict[str, Any]] = []
            for ref_order, ref in enumerate(tail_refs, start=1):
                target_file = page_map.get(ref["page_ref_int"])
                refs_for_entry.append(
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
                        "target_file": target_file,
                        "target_file_probability": 1.0 if target_file else None,
                        "section_start_file": source_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.97 if target_file else 0.66,
                        "raw_json": {
                            "source_file": source_file,
                            "resolver": "page_map_header_text",
                            "section_kind": section["section_kind"],
                            "page_hint": ref["page_ref_int"],
                        },
                    }
                )
            if refs_for_entry:
                entry_payload["confidence"] = 0.9
                if refs_for_entry[0]["target_file"]:
                    entry_payload["target_file_best"] = refs_for_entry[0]["target_file"]
            entries.append(entry_payload)
            refs.extend(refs_for_entry)

    return nodes, entries, refs


def build_ordo_entries(section: dict[str, Any], lines: list[tuple[str, str]], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    current_heading_key: str | None = None
    heading_order = 0

    for text, source_file in lines:
        for fragment in split_fragments(text):
            frag = fragment.strip()
            if not frag:
                continue
            body, tail_refs = extract_tail_refs(frag)
            entry_kind = infer_entry_kind(body, tail_refs)
            if entry_kind == "heading_group" and not tail_refs:
                heading_order += 1
                current_heading_key = f"{VOLUME_ID}:node:{section['section_order']:02d}:heading:{heading_order:03d}"
                nodes.append(
                    {
                        "node_key": current_heading_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": heading_order,
                        "node_kind": "heading_group",
                        "label_raw": body,
                        "label_norm": sort_norm(body),
                        "label_sort": sort_norm(body),
                        "node_level": 1,
                        "confidence": 0.96,
                        "raw_json": {"source_file": source_file, "role": "ordo_heading"},
                    }
                )
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
            inferred_printed_page = tail_refs[0]["page_ref_int"] if tail_refs else None
            entry_payload = {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": current_heading_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": infer_lemma(body, tail_refs),
                "lemma_display": infer_lemma(body, tail_refs),
                "lemma_norm": lemma_norm(infer_lemma(body, tail_refs)),
                "lemma_sort": sort_norm(infer_lemma(body, tail_refs)),
                "entry_raw": body,
                "context_raw": body if len(body) <= 220 else body[:220],
                "heading_letter": None,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "target_file_best": page_map.get(inferred_printed_page) if inferred_printed_page is not None else source_file,
                "confidence": 0.82 if tail_refs else 0.7,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section["section_kind"],
                    "section_kind_reason": section["section_kind_reason"],
                    "query_names": make_query_names(body, infer_lemma(body, tail_refs)),
                },
            }
            refs_for_entry: list[dict[str, Any]] = []
            for ref_order, ref in enumerate(tail_refs, start=1):
                target_file = page_map.get(ref["page_ref_int"])
                refs_for_entry.append(
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
                        "target_file": target_file,
                        "target_file_probability": 1.0 if target_file else None,
                        "section_start_file": source_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.97 if target_file else 0.66,
                        "raw_json": {
                            "source_file": source_file,
                            "resolver": "page_map_header_text",
                            "section_kind": section["section_kind"],
                            "page_hint": ref["page_ref_int"],
                        },
                    }
                )
            if refs_for_entry and refs_for_entry[0]["target_file"]:
                entry_payload["target_file_best"] = refs_for_entry[0]["target_file"]
            entries.append(entry_payload)
            refs.extend(refs_for_entry)
    return nodes, entries, refs


def build_helper_request(entries: list[dict[str, Any]], refs: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if not entry_refs:
            continue
        page_hints: list[str] = []
        page_hint_ints: list[int] = []
        for ref in entry_refs:
            raw = str(ref["page_ref_raw"])
            if raw not in page_hints:
                page_hints.append(raw)
            if ref["page_ref_int"] not in page_hint_ints:
                page_hint_ints.append(ref["page_ref_int"])
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
                "query_names": entry.get("query_names") or make_query_names(entry["entry_raw"], entry.get("lemma_raw")),
                "page_hints": page_hints[:4],
                "page_hint_ints": page_hint_ints[:4],
                "context_raw": entry["entry_raw"][:240],
            }
        )
        if len(helper_entries) >= 16:
            break
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


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
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract PG012 analytical index and ordo rerum, then validate helper targets.",
        "completed": [
            "Confirmed index tail begins at file 845 and ordo rerum begins at file 854",
            "Built OCR page map from all header numbers in the volume",
        ],
        "pending": [
            "Run helper on a compact page-hint sample",
            "Write final payload and intermediate fragments",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact and preserve page drift separately from file suffixes.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

    # First pass without helper to build a helper request from actual extracted entries.
    provisional_sections = []
    for section in SECTION_DEFS:
        section_files = [p for p in files if section["file_start_seq"] <= file_seq(p) <= section["file_end_seq"]]
        lines = collect_lines(section, files)
        if section["section_kind"] == "analytic_subject":
            sec_nodes, sec_entries, sec_refs = build_initial_entries(section, lines, page_map)
        else:
            sec_nodes, sec_entries, sec_refs = build_ordo_entries(section, lines, page_map)
        provisional_sections.append((section, section_files, sec_nodes, sec_entries, sec_refs))
        entries.extend(sec_entries)
        refs.extend(sec_refs)
        nodes.extend(sec_nodes)

    refs_by_entry: dict[str, int] = {}
    for ref in refs:
        refs_by_entry[ref["entry_key"]] = refs_by_entry.get(ref["entry_key"], 0) + 1
    for entry in entries:
        entry["raw_json"]["ref_count"] = refs_by_entry.get(entry["entry_key"], 0)

    helper_request = build_helper_request(entries, refs, source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {}
    helper_lookup = helper_map(helper_output)

    # Rebuild with helper hints folded in where available.
    sections.clear()
    nodes.clear()
    entries.clear()
    refs.clear()

    for section, section_files, _, _, _ in provisional_sections:
        lines = collect_lines(section, files)
        if section["section_kind"] == "analytic_subject":
            sec_nodes, sec_entries, sec_refs = build_initial_entries(section, lines, page_map)
        else:
            sec_nodes, sec_entries, sec_refs = build_ordo_entries(section, lines, page_map)

        for entry in sec_entries:
            helper_item = helper_lookup.get(entry["entry_key"])
            if helper_item:
                entry["raw_json"]["helper_status"] = helper_item.get("status")
                entry["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
                entry["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
                entry["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
                entry["raw_json"]["helper_top_candidates"] = helper_item.get("top_candidates") or helper_item.get("candidates")
                best = helper_item.get("best_candidate") or {}
                if best.get("file"):
                    entry["target_file_best"] = best["file"]
        entries.extend(sec_entries)
        refs.extend(sec_refs)
        nodes.extend(sec_nodes)
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": sort_norm(section["heading_raw"]),
                "heading_letter": None,
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": str(section_files[0]) if section_files else None,
                "file_end": str(section_files[-1]) if section_files else None,
                "confidence": 0.97 if section["section_kind"] == "analytic_subject" else 0.96,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "section_file_seq_start": section["file_start_seq"],
                    "section_file_seq_end": section["file_end_seq"],
                    "source_files": [str(p) for p in section_files],
                    "helper_status": helper_output.get("status"),
                    "helper_entry_count": len(helper_output.get("entries", [])),
                },
            }
        )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the analytical alphabetical index and the closing Ordo Rerum contents table from the OCR tail; refs were resolved through the volume-wide page map.",
        "evidence_files": [
            str(next(p for p in files if file_seq(p) == 845)),
            str(next(p for p in files if file_seq(p) == 853)),
            str(next(p for p in files if file_seq(p) == 854)),
            str(next(p for p in files if file_seq(p) == 856)),
        ],
    }

    notes = [
        "The first section is an analytical alphabetical index with letter-group nodes A-U.",
        "The second section is the closing Ordo Rerum contents table and is stored separately from the index proper.",
        "OCR page numbers, cited references, and physical OCR file suffixes are preserved as separate numbering systems.",
    ]

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
    }

    fragments = {
        "manifest.json": manifest,
        "volume.json": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": scripture_refs,
        "coverage.json": coverage,
        "notes.json": notes,
        "todo.json": todo,
    }
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

    return {
        "schema_version": "1.0",
        "generated_at": manifest["generated_at"],
        "volume": fragments["volume.json"],
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG012 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

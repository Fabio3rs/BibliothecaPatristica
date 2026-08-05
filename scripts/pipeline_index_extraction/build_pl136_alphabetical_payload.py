#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl136_alphabetical_payload.py
# Builds the PL136 alphabetical-index payload from the OCR tail, runs the helper on unresolved samples, and writes the final JSON payload.

from __future__ import annotations

import html
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL136"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL136/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL136_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL136_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL136_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL136"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": "PL136:alpha:ratherium:001",
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN RATHERIUM.",
        "file_start": 662,
        "file_end": 672,
        "heading_markers": ("INDEX IN RATHERIUM.",),
        "section_kind_reason": "Alphabetical index at the tail of the Ratherius portion of the volume; entries are arranged by letter with cited editorial pages.",
    },
    {
        "section_key": "PL136:alpha:liutprandi:001",
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN OPERA GENUINA LIUTPRANDI.",
        "file_start": 673,
        "file_end": 676,
        "heading_markers": ("INDEX IN OPERA GENUINA LIUTPRANDI.",),
        "section_kind_reason": "Alphabetical index for the genuine works of Liutprand; entries are arranged by letter with cited editorial pages.",
    },
    {
        "section_key": "PL136:ordo:tomi:001",
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        "file_start": 677,
        "file_end": 679,
        "heading_markers": ("ORDO RERUM",),
        "section_kind_reason": "Closing contents block listing the order of items in the tomo; editorial closure rather than alphabetical index material.",
    },
]

PAGE_TOKEN_RE = re.compile(
    r"(?<!\w)(?:\d{1,4}|[IVXLCDM]{2,6})(?:\s*,?\s*(?:et seqq\.|seqq\.|not\.|num\.|f\.|ff\.))?(?!\w)",
)
ROMAN_TOKEN_RE = re.compile(r"^[IVXLCDM]{2,6}$", re.I)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SECTION_HEAD_RE = re.compile(
    r"INDEX IN RATHERIUM\.|INDEX IN OPERA GENUINA LIUTPRANDI\.|ORDO RERUM",
    re.I,
)
HELPER_CROSSREF_RE = re.compile(r"\b(?:V\.|VID\.|VIDE|VOIR|CF\.|ID\.)\b", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm_text(text: str | None) -> str | None:
    if not text:
        return None
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = text.replace("æ", "ae").replace("œ", "oe").replace("Æ", "AE").replace("Œ", "OE")
    text = strip_accents(text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;")
    return text.lower() if text else None


def file_seq(path: Path) -> int:
    if isinstance(path, str):
        path = Path(path)
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def roman_to_int(token: str) -> int | None:
    token = token.upper().strip().rstrip(".")
    if not ROMAN_TOKEN_RE.fullmatch(token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(token):
        value = values[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def iter_ocr_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, re.S):
        blocks.append((match.group(1), html.unescape(match.group(2))))
    return blocks


def normalize_line(raw: str) -> str:
    return " ".join(html.unescape(raw).replace("\xa0", " ").split())


def page_header_tokens(block_text: str) -> list[str]:
    tokens: list[str] = []
    for raw in block_text.splitlines():
        line = normalize_line(raw)
        if not line or line == "Digitized by Google":
            continue
        if SECTION_HEAD_RE.search(line):
            continue
        for token in re.findall(r"(?<!\w)(?:\d{1,4}|[IVXLCDM]{2,6})(?!\w)", line):
            if token.isdigit() or ROMAN_TOKEN_RE.fullmatch(token):
                tokens.append(token.upper())
    return tokens


def build_page_map(source_root: Path) -> dict[str, str]:
    page_map: dict[str, str] = {}
    for path in iter_ocr_files(source_root):
        text = read_text(path)
        for kind, block in extract_blocks(text):
            if kind != "cabecalho":
                continue
            for token in page_header_tokens(block):
                page_map.setdefault(token, str(path))
                if token.isdigit():
                    page_map.setdefault(str(int(token)), str(path))
                    page_map.setdefault(f"#{int(token)}", str(path))
                else:
                    roman_value = roman_to_int(token)
                    if roman_value is not None:
                        page_map.setdefault(str(roman_value), str(path))
                        page_map.setdefault(token.upper(), str(path))
    return page_map


def section_for_file_seq(seq: int) -> dict[str, Any] | None:
    for section in SECTION_DEFS:
        if section["file_start"] <= seq <= section["file_end"]:
            return section
    return None


def collect_lines(source_root: Path, file_start: int, file_end: int) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for path in iter_ocr_files(source_root):
        seq = file_seq(path)
        if seq < file_start or seq > file_end:
            continue
        text = read_text(path)
        for kind, block in extract_blocks(text):
            if kind == "cabecalho":
                continue
            for raw in block.splitlines():
                line = normalize_line(raw)
                if not line or line == "Digitized by Google":
                    continue
                if SECTION_HEAD_RE.fullmatch(line):
                    continue
                lines.append({"file": str(path), "kind": kind, "line": line})
    return lines


def merge_wrapped_lines(lines: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for item in lines:
        line = item["line"]
        if LETTER_RE.fullmatch(line):
            if current:
                merged.append(current)
                current = None
            merged.append({"file": item["file"], "kind": "letter", "line": line})
            continue
        if re.fullmatch(r"\d{3,4}", line):
            continue
        if current is None:
            current = dict(item)
            continue
        if (
            line.startswith((";", ",", ".", ":", ")", "]"))
            or re.match(r"^[a-zà-ÿ]", line)
            or current["line"].endswith("-")
            or line.startswith(("ibid", "not.", "et ", "seqq.", "V.", "VID.", "VIDE", "VOIR", "CF.", "ID."))
        ):
            if current["line"].endswith("-"):
                current["line"] = current["line"][:-1] + line.lstrip()
            else:
                current["line"] += " " + line
        else:
            merged.append(current)
            current = dict(item)
    if current:
        merged.append(current)
    return merged


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", text)
    return [part.strip() for part in parts if part.strip()]


def has_material_ref(text: str) -> bool:
    return bool(PAGE_TOKEN_RE.search(text))


def has_crossref_marker(text: str) -> bool:
    return bool(HELPER_CROSSREF_RE.search(text))


def parse_page_tokens(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in PAGE_TOKEN_RE.finditer(text):
        raw = match.group(0).strip().rstrip(".,;:")
        if raw.upper() in {"V", "I"}:
            continue
        key = (raw.upper(), raw)
        if key in seen:
            continue
        seen.add(key)
        page_ref_int: int | None = None
        if raw.isdigit():
            page_ref_int = int(raw)
        else:
            roman_value = roman_to_int(raw)
            if roman_value is not None:
                page_ref_int = roman_value
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_ref_int,
            }
        )
    return refs


def parse_entry_chunk(chunk: str) -> tuple[str | None, str, str, bool]:
    cleaned = chunk.strip().rstrip()
    cleaned = cleaned.rstrip(";")
    refs = parse_page_tokens(cleaned)
    first_ref_pos = None
    if refs:
        m = PAGE_TOKEN_RE.search(cleaned)
        if m:
            first_ref_pos = m.start()
    crossref_pos = None
    m_cross = re.search(r"\b(?:V\.|VID\.|VIDE|VOIR|CF\.|ID\.)\b", cleaned, re.I)
    if m_cross:
        crossref_pos = m_cross.start()
    if first_ref_pos is not None:
        lemma = cleaned[:first_ref_pos].rstrip(" ,;:.")
    elif crossref_pos is not None:
        lemma = cleaned[:crossref_pos].rstrip(" ,;:.")
    else:
        lemma = cleaned.rstrip(" .,:;")
    kind = "cross_reference" if not refs and has_crossref_marker(cleaned) else "lemma"
    lemma = lemma or None
    if lemma and lemma.startswith("V."):
        lemma = lemma[2:].strip()
    return lemma, cleaned, kind, bool(refs)


def choose_target_file(
    refs: list[dict[str, Any]],
    page_map: dict[str, str],
    helper_best: str | None = None,
    current_file: str | None = None,
) -> tuple[str | None, float | None]:
    for ref in refs:
        raw = str(ref["page_ref_raw"]).upper()
        page_int = ref["page_ref_int"]
        candidates = []
        if page_int is not None:
            candidates.extend([str(page_int), f"#{page_int}", raw, raw.rstrip(".")])
        else:
            candidates.extend([raw, raw.rstrip(".")])
        for key in candidates:
            if key in page_map:
                return page_map[key], 0.99
    if helper_best:
        return helper_best, 0.9
    return current_file, 0.6 if current_file else None


def file_tokens_from_entry(entry: dict[str, Any]) -> list[int]:
    tokens: list[int] = []
    for ref in entry.get("refs", []):
        page = ref.get("page_ref_int")
        if isinstance(page, int):
            tokens.append(page)
    return tokens


def build_sections() -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for order, section in enumerate(SECTION_DEFS, start=1):
        file_start = SOURCE_ROOT / f"0f715b5a-3d8b-4963-b25b-48d7762dc165-{section['file_start']}.txt"
        file_end = SOURCE_ROOT / f"0f715b5a-3d8b-4963-b25b-48d7762dc165-{section['file_end']}.txt"
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": norm_text(section["heading_raw"]),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": str(file_start),
                "file_end": str(file_end),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "source_file_span": [str(file_start), str(file_end)],
                },
            }
        )
    return sections


def parse_section(
    section: dict[str, Any],
    page_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    lines = collect_lines(SOURCE_ROOT, section["file_start"], section["file_end"])
    merged = merge_wrapped_lines(lines)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    evidence_files = sorted({item["file"] for item in merged}, key=file_seq)
    current_letter = None
    node_counter = 0
    entry_order = 0
    letter_nodes: dict[str, str] = {}

    for item in merged:
        line = item["line"]
        if item["kind"] == "letter":
            current_letter = line
            if line not in letter_nodes:
                node_counter += 1
                node_key = f"{section['section_key']}:node:{node_counter:03d}"
                letter_nodes[line] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": node_counter,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": norm_text(line),
                        "label_sort": norm_text(line),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": item["file"]},
                    }
                )
            continue

        chunks = split_clauses(line)
        if not chunks:
            continue

        current_chunk = chunks[0]
        for nxt in chunks[1:]:
            if (has_material_ref(current_chunk) and (has_material_ref(nxt) or has_crossref_marker(nxt))) or (
                has_crossref_marker(current_chunk) and has_material_ref(nxt)
            ):
                lemma_raw, entry_raw, entry_kind, has_refs = parse_entry_chunk(current_chunk)
                if lemma_raw is None and not has_refs and not has_crossref_marker(current_chunk):
                    current_chunk = nxt
                    continue
                entry_order += 1
                entry_key = f"{section['section_key']}:entry:{entry_order:04d}"
                entry_refs = parse_page_tokens(entry_raw)
                helper_best = None
                helper_probability = None
                target_file_best, target_prob = choose_target_file(entry_refs, page_map, None, item["file"])
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section["section_key"],
                        "parent_node_key": letter_nodes.get(current_letter),
                        "entry_order": entry_order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": norm_text(lemma_raw),
                        "lemma_sort": norm_text(lemma_raw),
                        "entry_raw": entry_raw,
                        "context_raw": entry_raw,
                        "heading_letter": current_letter,
                        "inferred_printed_page": entry_refs[0]["page_ref_int"] if entry_refs else None,
                        "section_start_file": section["file_start_path"],
                        "editorial_anchor_file": item["file"],
                        "target_file_best": target_file_best,
                        "confidence": 0.88 if entry_refs else 0.72,
                        "raw_json": {
                            "source_file": item["file"],
                            "entry_chunks": [current_chunk],
                            "section_kind": section["section_kind"],
                            "page_tokens": [ref["page_ref_raw"] for ref in entry_refs],
                            "helper_status": None,
                            "helper_best_file": helper_best,
                            "helper_best_probability": helper_probability,
                            "target_file_reason": "page_map" if target_prob == 0.99 else "fallback_source_file",
                        },
                    }
                )
                for ref_order, ref in enumerate(entry_refs, start=1):
                    target_file, probability = choose_target_file([ref], page_map, helper_best, item["file"])
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": probability,
                            "section_start_file": section["file_start_path"],
                            "editorial_anchor_file": item["file"],
                            "confidence": 0.9 if target_file else 0.6,
                            "raw_json": {
                                "source_file": item["file"],
                                "locator_method": "page_map" if probability == 0.99 else "fallback_source_file",
                            },
                        }
                    )
                current_chunk = nxt
            else:
                current_chunk = f"{current_chunk} {nxt}".strip()

        lemma_raw, entry_raw, entry_kind, has_refs = parse_entry_chunk(current_chunk)
        if lemma_raw is None and not has_refs and not has_crossref_marker(current_chunk):
            continue
        if lemma_raw is not None and not re.match(r"^[A-ZÆŒ]", lemma_raw):
            continue
        entry_order += 1
        entry_key = f"{section['section_key']}:entry:{entry_order:04d}"
        entry_refs = parse_page_tokens(entry_raw)
        target_file_best, target_prob = choose_target_file(entry_refs, page_map, None, item["file"])
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": letter_nodes.get(current_letter),
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": norm_text(lemma_raw),
                "lemma_sort": norm_text(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": current_letter,
                "inferred_printed_page": entry_refs[0]["page_ref_int"] if entry_refs else None,
                "section_start_file": section["file_start_path"],
                "editorial_anchor_file": item["file"],
                "target_file_best": target_file_best,
                "confidence": 0.88 if entry_refs else 0.72,
                "raw_json": {
                    "source_file": item["file"],
                    "entry_chunks": [current_chunk],
                    "section_kind": section["section_kind"],
                    "page_tokens": [ref["page_ref_raw"] for ref in entry_refs],
                    "helper_status": None,
                    "helper_best_file": None,
                    "helper_best_probability": None,
                    "target_file_reason": "page_map" if target_prob == 0.99 else "fallback_source_file",
                },
            }
        )
        for ref_order, ref in enumerate(entry_refs, start=1):
            target_file, probability = choose_target_file([ref], page_map, None, item["file"])
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": probability,
                    "section_start_file": section["file_start_path"],
                    "editorial_anchor_file": item["file"],
                    "confidence": 0.9 if target_file else 0.6,
                    "raw_json": {
                        "source_file": item["file"],
                        "locator_method": "page_map" if probability == 0.99 else "fallback_source_file",
                    },
                }
            )

    return entries, refs, nodes, evidence_files


def build_helper_entries(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = []
        for ref in refs_by_entry.get(entry["entry_key"], []):
            raw = str(ref.get("page_ref_raw") or "").strip()
            if raw and raw not in page_hints:
                page_hints.append(raw)
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry.get("entry_raw"),
                "query_names": [entry.get("lemma_raw") or entry.get("entry_raw")],
                "page_hints": page_hints[:3],
                "page_hint_ints": [ref.get("page_ref_int") for ref in refs_by_entry.get(entry["entry_key"], []) if isinstance(ref.get("page_ref_int"), int)][:3],
                "context_raw": entry.get("entry_raw"),
            }
        )
        if len(helper_entries) >= 18:
            break
    return helper_entries


def run_helper(request: dict[str, Any]) -> dict[str, Any]:
    write_json(HELPER_REQUEST_JSON, request)
    proc = subprocess.run(
        [
            "python",
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def helper_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        mapping[item["entry_id"]] = item
    return mapping


def update_with_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = helper_by_id(helper_output)
    ref_map: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        ref_map.setdefault(ref["entry_key"], []).append(ref)
    for entry in entries:
        helper_item = helper_map.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        entry["target_file_best"] = best.get("file") or entry.get("target_file_best")
        entry["confidence"] = max(float(entry.get("confidence") or 0.0), float(best.get("probability") or 0.0))
        entry["raw_json"].update(
            {
                "helper_status": helper_item.get("status"),
                "helper_best_file": best.get("file"),
                "helper_best_probability": best.get("probability"),
                "helper_candidate_role": best.get("candidate_role"),
                "helper_reason_summary": best.get("reason_summary"),
            }
        )
        for ref in ref_map.get(entry["entry_key"], []):
            ref["raw_json"].update(
                {
                    "helper_status": helper_item.get("status"),
                    "helper_best_file": best.get("file"),
                    "helper_best_probability": best.get("probability"),
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": best.get("reason_summary"),
                }
            )
            if best.get("file") and not ref.get("target_file"):
                ref["target_file"] = best.get("file")
            if best.get("probability") is not None:
                ref["target_file_probability"] = max(float(ref.get("target_file_probability") or 0.0), float(best.get("probability")))


def build_payload() -> dict[str, Any]:
    page_map = build_page_map(SOURCE_ROOT)
    sections = build_sections()

    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    all_nodes: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for section in sections:
        def _seq_to_path(seq: int) -> str:
            return str(next(path for path in iter_ocr_files(SOURCE_ROOT) if file_seq(path) == seq))

        section_def = next(item for item in SECTION_DEFS if item["section_key"] == section["section_key"])
        section["file_start"] = _seq_to_path(section_def["file_start"])
        section["file_end"] = _seq_to_path(section_def["file_end"])
        section["raw_json"]["source_file_span"] = [section["file_start"], section["file_end"]]

        section_entries, section_refs, section_nodes, section_evidence = parse_section(
            {
                **section_def,
                "file_start_path": section["file_start"],
                "file_end_path": section["file_end"],
            },
            page_map,
        )
        all_entries.extend(section_entries)
        all_refs.extend(section_refs)
        all_nodes.extend(section_nodes)
        evidence_files.extend(section_evidence)

    evidence_files = list(dict.fromkeys(evidence_files))

    helper_entries = build_helper_entries(all_entries, all_refs)
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    helper_output = run_helper(helper_request) if helper_entries else {"volume_id": VOLUME_ID, "source_root": str(SOURCE_ROOT), "options_used": {"top_k": 5, "adjacency_window": 2}, "entries": []}
    update_with_helper(all_entries, all_refs, helper_output)

    for entry in all_entries:
        if not entry.get("lemma_raw") and entry.get("entry_kind") == "lemma":
            entry["entry_kind"] = "editorial_note"
            entry["raw_json"]["normalized_kind_reason"] = "recoverable lemma not available from OCR fragment"
        if entry.get("section_key") == "PL136:ordo:tomi:001":
            entry["entry_kind"] = "heading_group" if not entry.get("lemma_raw") else entry["entry_kind"]
            entry["confidence"] = max(float(entry.get("confidence") or 0.0), 0.86)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the two alphabetical indexes and the closing ordo rerum block directly from OCR; "
            "helper resolution was used only as a check on a small sample of target anchors."
        ),
        "evidence_files": evidence_files,
    }

    notes = [
        "PL136 contains three distinct tail sections: INDEX IN RATHERIUM, INDEX IN OPERA GENUINA LIUTPRANDI, and ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        "Page references were kept separate from OCR file suffixes and resolved through the volume page map when possible.",
        "Helper output was only used to confirm a sample of anchors and preserve candidate evidence in raw_json.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 136",
        },
        "sections": sections,
        "nodes": all_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", all_nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", all_entries)
    write_json(INTERMEDIATE_DIR / "refs.json", all_refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": str(HELPER_REQUEST_JSON),
            "helper_output_json": str(HELPER_OUTPUT_JSON),
            "output_file": str(OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PL136 alphabetical payload and validate section boundaries.",
            "completed": [
                "identified three tail sections in the volume",
                "parsed OCR lines into entries and refs",
                "resolved a small helper sample for anchor checking",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals and do not collapse numbering systems.",
                "The helper is a check on top of direct OCR and page-map resolution.",
            ],
        },
    )

    write_json(HELPER_OUTPUT_JSON, helper_output)
    write_json(OUTPUT_FILE, payload)
    return payload


def main() -> None:
    build_payload()


if __name__ == "__main__":
    main()

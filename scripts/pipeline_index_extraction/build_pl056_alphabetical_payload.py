#!/usr/bin/env python3
"""Usage: rebuild the PL056 alphabetical payload from OCR blocks and helper evidence.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl056_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ocr_xml_utils import OcrBlock, read_ocr_page


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL056"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 56"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
ASSEMBLED_FRAGMENTS = INTERMEDIATE_DIR / "assembled_fragments.json"
OUTPUT_FILE = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
TODO_FILE = INTERMEDIATE_DIR / "todo.json"
TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

ALPHABETICAL_SEQS = list(range(584, 597))
ORDO_SEQS = list(range(597, 601))

ABBREV_TOKENS = {
    "ibid",
    "id",
    "vid",
    "vide",
    "cf",
    "not",
    "n",
    "ms",
    "cap",
    "lib",
    "s",
    "st",
    "seq",
    "seqq",
    "etc",
}
CONTINUATION_PREFIXES = (
    "ibid",
    "id.",
    "et ",
    "autem ",
    "item ",
    "unde ",
    "quod ",
    "quæ ",
    "quae ",
    "qui ",
    "quibus ",
    "quibusdam ",
    "quatenus ",
    "qualis ",
    "quomodo ",
    "cur ",
    "cum ",
    "nec ",
    "non ",
    "sed ",
    "vel ",
    "ut ",
    "ubi ",
    "unde ",
    "ejus ",
    "ejusdem ",
    "eidem ",
    "ipsi ",
    "ipsis ",
    "huius ",
    "hujus ",
    "horum ",
    "earum ",
    "illud ",
    "alia ",
    "aliud ",
    "alii ",
    "de ",
    "in ",
    "ad ",
    "ab ",
    "a ",
    "ex ",
)
LETTER_RE = re.compile(r"^[A-Z]$")
ROMAN_RE = re.compile(r"\b[IVXLCDMivxlcdm]{2,8}\b")
ARABIC_RANGE_RE = re.compile(
    r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*(?:not\.|n\.))?(?:\s*(?:et\s+seq\.|et\s+seqq\.|seq\.|seqq\.))?",
    re.IGNORECASE,
)
ROMAN_LOC_RE = re.compile(
    r"\b[IVXLCDMivxlcdm]{2,8}(?:\s*(?:,\s*\d{1,4}\s*(?:not\.|n\.)?)|\s*(?:et\s+seq\.|et\s+seqq\.|seq\.|seqq\.))?",
    re.IGNORECASE,
)
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
RAW_HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
REF_ID_SEP = "::ref:"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ").replace("\u202f", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str | None) -> str:
    if not text:
        return ""
    value = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    import unicodedata

    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: str | Path) -> int:
    name = Path(path).name
    match = re.search(r"-(\d+)\.txt$", name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def roman_to_int(value: str | None) -> int | None:
    if not value:
        return None
    token = value.strip().upper()
    if token.isdigit():
        return int(token)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if any(ch not in values for ch in token):
        return None
    total = 0
    prev = 0
    for ch in reversed(token):
        current = values[ch]
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total


def protect_sentence_breaks(text: str) -> str:
    parts: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        if text[i] in ".!?":
            j = i + 1
            while j < len(text) and text[j] == " ":
                j += 1
            if j < len(text) and text[j].isupper():
                prev = re.search(r"([A-Za-zÆŒæœ]+)\.$", text[: i + 1])
                token = strip_accents(prev.group(1)).lower() if prev else ""
                if token and token not in ABBREV_TOKENS:
                    parts.append(text[start : i + 1].strip())
                    start = j
                    i = j
                    continue
        i += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return "\n".join(parts)


def split_collapsed_line(text: str) -> list[str]:
    value = normalize(text) or ""
    if not value:
        return []
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"(?<=\d)\.\s+(?=[A-ZÆŒ])", ".\n", value)
    value = re.sub(r"(\b[IVXLCDMivxlcdm]{2,8})\.\s+(?=[A-ZÆŒ])", r"\1.\n", value)
    value = re.sub(r"\b((?:ibid|id)\.)\s+(?=[A-ZÆŒ])", r"\1\n", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\d)(\d{1,4})(?!\d)\s+(?=[A-ZÆŒ])", r"\1\n", value)
    value = protect_sentence_breaks(value)
    out = [segment.strip() for segment in value.splitlines() if normalize(segment)]
    merged: list[str] = []
    for segment in out:
        if merged and looks_like_continuation(merged[-1], segment):
            merged[-1] = f"{merged[-1]} {segment}".strip()
        else:
            merged.append(segment)
    return merged


def block_column(block: OcrBlock) -> int:
    bbox = block.bbox.split(",")
    try:
        x0 = int(bbox[0])
    except (ValueError, IndexError):
        return 0
    return 0 if x0 < 500 else 1


def block_top(block: OcrBlock) -> int:
    bbox = block.bbox.split(",")
    try:
        return int(bbox[1])
    except (ValueError, IndexError):
        return 0


def is_heading_line(text: str) -> bool:
    normalized = normalize(text) or ""
    if not normalized:
        return False
    if normalized == "Digitized by Google":
        return True
    if normalized in {
        "INDEX",
        "RERUM ET SENTENTIARUM MEMORABILIUM.",
        "INDEX RERUM ET SENTENTIARUM MEMORABILIUM.",
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    }:
        return True
    if LETTER_RE.fullmatch(normalized):
        return True
    return False


def line_starts_new_entry(line: str) -> bool:
    normalized = normalize(line) or ""
    if not normalized:
        return False
    if normalized[0].islower():
        return False
    lowered = strip_accents(normalized).lower()
    if any(lowered.startswith(prefix) for prefix in CONTINUATION_PREFIXES):
        return False
    return True


def buffer_has_locator(text: str) -> bool:
    return bool(ARABIC_RANGE_RE.search(text) or ROMAN_LOC_RE.search(text) or IBID_RE.search(text))


def buffer_seems_complete(text: str) -> bool:
    normalized = normalize(text) or ""
    if not normalized:
        return False
    if not buffer_has_locator(normalized):
        return False
    if re.search(r"(?:\d{1,4}|[IVXLCDMivxlcdm]{2,8}|ibid\.?|id\.?)(?:\s*(?:not\.|n\.|seq\.|seqq\.|et\s+seq\.|et\s+seqq\.))?[.;]?$", normalized, re.IGNORECASE):
        return True
    return normalized.endswith(("ibid.", "ibid", "id.", "id"))


def looks_like_continuation(current_text: str, next_line: str) -> bool:
    normalized = normalize(next_line) or ""
    if not normalized:
        return True
    lowered = strip_accents(normalized).lower()
    if normalized[0].islower():
        return True
    if any(lowered.startswith(prefix) for prefix in CONTINUATION_PREFIXES):
        return True
    if current_text and not buffer_seems_complete(current_text):
        return True
    return False


def leading_letter(text: str | None, fallback: str | None) -> str | None:
    if fallback:
        return fallback
    norm = strip_accents(normalize(text) or "")
    for ch in norm:
        if ch.isalpha():
            return ch.upper()
    return None


def extract_page_numbers_from_header(header_text: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for match in RAW_HEADER_NUM_RE.finditer(header_text):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def build_header_page_maps() -> tuple[dict[int, str], dict[int, str]]:
    arabic: dict[int, str] = {}
    roman: dict[int, str] = {}
    for path in sorted(SOURCE_ROOT.glob("*.txt"), key=file_seq):
        header = read_ocr_page(path).header_text
        for value in extract_page_numbers_from_header(header):
            arabic.setdefault(value, str(path))
        for token in ROMAN_RE.findall(header):
            page = roman_to_int(token)
            if page is not None:
                roman.setdefault(page, str(path))
    return arabic, roman


def seq_path(seq: int) -> Path:
    return next(SOURCE_ROOT.glob(f"*-{seq}.txt"))


def inferred_source_page(seq: int, header_numbers: list[int]) -> int | None:
    if seq == 584:
        return 1155
    if not header_numbers:
        return None
    smallest = min(header_numbers)
    if len(header_numbers) == 1:
        return smallest
    if any(num == smallest + 1 for num in header_numbers):
        return smallest
    if smallest % 2 == 0:
        return smallest - 1
    return smallest


def parse_locator_tokens(text: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<ibid>\b(?:ibid\.?|id\.?)\b)"
        r"|(?P<roman>\b[IVXLCDMivxlcdm]{2,8}\b(?:\s*(?:not\.|n\.|seq\.|seqq\.|et\s+seq\.|et\s+seqq\.))?)"
        r"|(?P<arabic>\b\d{1,4}\b(?:\s*[-–—]\s*\d{1,4})?(?:\s*(?:not\.|n\.))?(?:\s*(?:seq\.|seqq\.|et\s+seq\.|et\s+seqq\.))?)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        raw = match.group(0).strip()
        kind = "ibid" if match.group("ibid") else ("roman" if match.group("roman") else "arabic")
        tokens.append({"raw": raw, "kind": kind})
    return tokens


def page_int_from_token(token: dict[str, Any]) -> int | None:
    raw = token["raw"]
    if token["kind"] == "ibid":
        return None
    if token["kind"] == "roman":
        roman_match = re.match(r"[IVXLCDMivxlcdm]{2,8}", raw)
        return roman_to_int(roman_match.group(0) if roman_match else None)
    arabic_match = re.match(r"\d{1,4}", raw)
    return int(arabic_match.group(0)) if arabic_match else None


def target_from_maps(page_int: int | None, token_kind: str, arabic_map: dict[int, str], roman_map: dict[int, str]) -> tuple[str | None, str | None]:
    if page_int is None:
        return None, None
    if token_kind == "roman":
        if page_int in roman_map:
            return roman_map[page_int], "exact_roman_header"
        if page_int in arabic_map:
            return arabic_map[page_int], "roman_fell_back_to_arabic_header"
        return None, None
    if page_int in arabic_map:
        return arabic_map[page_int], "exact_arabic_header"
    return None, None


def parse_entry_refs(
    entry_key: str,
    entry_raw: str,
    section_start_file: str,
    editorial_anchor_file: str,
    arabic_map: dict[int, str],
    roman_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    helper_jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    previous_page: int | None = None
    for token in parse_locator_tokens(entry_raw):
        raw = token["raw"]
        page_int = page_int_from_token(token)
        if token["kind"] == "ibid":
            page_int = previous_page
        if page_int is not None:
            previous_page = page_int
        dedupe_key = (raw.lower(), page_int)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        target_file, target_method = target_from_maps(page_int, token["kind"], arabic_map, roman_map)
        range_start_raw = None
        range_end_raw = None
        ref_kind = "editorial_page"
        if token["kind"] == "arabic":
            range_match = re.match(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})", raw)
            if range_match:
                ref_kind = "editorial_range"
                range_start_raw = range_match.group(1)
                range_end_raw = range_match.group(2)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
                "target_file": target_file,
                "target_file_probability": 0.99 if target_file else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": 0.9 if target_file else 0.72,
                "raw_json": {
                    "locator_token_kind": token["kind"],
                    "target_resolution_method": target_method,
                },
            }
        )
        if target_file is None and page_int is not None:
            helper_jobs.append(
                {
                    "entry_id": f"{entry_key}{REF_ID_SEP}{len(refs)}",
                    "lemma_raw": normalize(entry_raw.split(",", 1)[0]) or entry_raw[:160],
                    "query_names": build_query_names(entry_raw),
                    "page_hints": [raw],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
    return refs, helper_jobs


def build_query_names(entry_raw: str) -> list[str]:
    base = normalize(entry_raw) or ""
    if not base:
        return []
    lemma = base
    first_ref = re.search(r"(?:\b\d{1,4}\b|\b[IVXLCDMivxlcdm]{2,8}\b|\bibid\.?\b|\bid\.?\b)", base)
    if first_ref:
        lemma = base[: first_ref.start()].strip(" ,.;:")
    out: list[str] = []
    for candidate in (lemma, " ".join(lemma.split()[:14]), base[:200].strip()):
        candidate = normalize(candidate)
        if candidate and candidate not in out:
            out.append(candidate)
    return out[:3]


def segment_block_lines(lines: list[str]) -> list[str]:
    entries: list[str] = []
    current: str | None = None
    for raw_line in lines:
        line = normalize(raw_line)
        if not line or is_heading_line(line):
            continue
        if current is None:
            current = line
            continue
        if looks_like_continuation(current, line):
            current = f"{current} {line}".strip()
            continue
        if line_starts_new_entry(line):
            entries.append(current)
            current = line
        else:
            current = f"{current} {line}".strip()
    if current:
        entries.append(current)
    return entries


def extract_alphabetical_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    arabic_map, roman_map = build_header_page_maps()
    section_start_file = str(seq_path(584))
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_jobs: list[dict[str, Any]] = []

    for seq in ALPHABETICAL_SEQS:
        path = seq_path(seq)
        page = read_ocr_page(path)
        header_nums = extract_page_numbers_from_header(page.header_text)
        source_page = inferred_source_page(seq, header_nums)
        blocks = sorted(page.blocks, key=lambda block: (block_column(block), block_top(block)))
        current_letter: str | None = None
        for block in blocks:
            block_type = block.tipo or block.tag_name
            if block_type == "nota_marginal":
                note = normalize(block.content_clean)
                if note and LETTER_RE.fullmatch(note):
                    current_letter = note
                continue
            if block_type != "texto_principal":
                continue
            raw_lines = [normalize(line) for line in block.content_clean.splitlines() if normalize(line)]
            if not raw_lines:
                continue
            if len(raw_lines) == 1 and len(raw_lines[0]) > 220:
                raw_lines = split_collapsed_line(raw_lines[0])
            for entry_raw in segment_block_lines(raw_lines):
                if is_heading_line(entry_raw):
                    continue
                entry_kind = "cross_reference" if re.search(r"\b(?:vid\.?|vide|cf\.?|voir|v\.)\b", entry_raw, re.IGNORECASE) and not buffer_has_locator(entry_raw) else "lemma"
                if entry_raw.startswith("Numeri minusculi romani"):
                    entry_kind = "editorial_note"
                lemma_raw = None if entry_kind == "editorial_note" else normalize(entry_raw.split(",", 1)[0].rstrip("."))
                heading_letter = leading_letter(lemma_raw, current_letter if current_letter else None)
                entry_key = f"{VOLUME_ID}:entry:{len(entries)+1:04d}"
                entry_refs, jobs = parse_entry_refs(
                    entry_key,
                    entry_raw,
                    section_start_file,
                    str(path),
                    arabic_map,
                    roman_map,
                )
                helper_jobs.extend(jobs)
                refs.extend(entry_refs)
                target_file_best = None
                if entry_refs:
                    for ref in entry_refs:
                        if ref["target_file"]:
                            target_file_best = ref["target_file"]
                            break
                elif entry_kind in {"cross_reference", "editorial_note"}:
                    target_file_best = str(path)
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": f"{VOLUME_ID}:section:alphabetical_general:001",
                        "parent_node_key": None,
                        "entry_order": len(entries) + 1,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": entry_raw,
                        "context_raw": None,
                        "heading_letter": heading_letter,
                        "inferred_printed_page": source_page,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(path),
                        "target_file_best": target_file_best,
                        "confidence": 0.9 if entry_refs or entry_kind == "cross_reference" else 0.78,
                        "raw_json": {
                            "source_files": [str(path)],
                            "source_pages": [source_page] if source_page is not None else [],
                            "segmentation_method": "collapsed_block_split" if len(block.content_clean.splitlines()) == 1 and len(block.content_clean) > 220 else "ocr_line_join",
                            "page_ref_source": "header_sequence_inference" if source_page == 1155 else "explicit_header",
                        },
                    }
                )
    return entries, refs, helper_jobs


def helper_summary(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates = item.get("candidates") or []
    return {
        "helper_status": item.get("status"),
        "helper_candidate_role": best.get("candidate_role"),
        "helper_reason_summary": best.get("reason_summary"),
        "helper_best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "inferred_printed_page": best.get("inferred_printed_page"),
        },
        "helper_top_candidates": [
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "evidence_kinds": [ev.get("kind") for ev in candidate.get("evidence", [])[:6] if ev.get("kind")],
            }
            for candidate in candidates[:3]
        ],
    }


def build_helper_request(helper_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in helper_jobs:
        if item["entry_id"] in seen:
            continue
        seen.add(item["entry_id"])
        deduped.append(item)
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"candidate_limit": 8},
        "entries": deduped,
    }


def run_helper() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(TARGET_LOCATOR), "--input", str(HELPER_REQUEST), "--output", str(HELPER_OUTPUT), "--pretty"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return load_json(HELPER_OUTPUT)


def apply_helper_resolution(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = {item["entry_id"]: item for item in helper_output.get("entries", [])}
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for ref in refs:
        if ref.get("target_file"):
            continue
        ref_id = f"{ref['entry_key']}{REF_ID_SEP}{ref['ref_order']}"
        helper_item = by_id.get(ref_id)
        if not helper_item:
            continue
        summary = helper_summary(helper_item)
        ref.setdefault("raw_json", {}).update(summary)
        best = helper_item.get("best_candidate") or {}
        candidate_file = best.get("file")
        probability = best.get("probability")
        status = helper_item.get("status")
        if candidate_file and status in {"resolved", "ambiguous"}:
            ref["target_file"] = candidate_file
            ref["target_file_probability"] = probability
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.78 if status == "resolved" else 0.72)
            ref["raw_json"]["target_resolution_method"] = "helper_ref_locator"

    for entry in entries:
        if entry.get("target_file_best"):
            continue
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        for ref in entry_refs:
            if ref.get("target_file"):
                entry["target_file_best"] = ref["target_file"]
                break
        if entry["target_file_best"] is None and entry["entry_kind"] in {"cross_reference", "editorial_note"}:
            entry["target_file_best"] = entry["editorial_anchor_file"]
        unresolved_helpers = [
            helper_summary(by_id[f"{entry['entry_key']}{REF_ID_SEP}{ref['ref_order']}"])
            for ref in entry_refs
            if f"{entry['entry_key']}{REF_ID_SEP}{ref['ref_order']}" in by_id
        ]
        if unresolved_helpers:
            entry.setdefault("raw_json", {})["helper_ref_locators"] = unresolved_helpers[:6]


def rebuild_ordo_from_fragments(
    start_entry_index: int,
    section_key: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    assembled = load_json(ASSEMBLED_FRAGMENTS)["data"]
    section = next(item for item in assembled["sections"] if item["section_kind"] == "ordo_rerum")
    old_entries = [item for item in assembled["entries"] if item["section_key"] == section["section_key"]]
    entry_key_map: dict[str, str] = {}
    new_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(old_entries, start=1):
        new_key = f"{VOLUME_ID}:entry:{start_entry_index + idx - 1:04d}"
        entry_key_map[entry["entry_key"]] = new_key
        new_entry = deepcopy(entry)
        new_entry["entry_key"] = new_key
        new_entry["section_key"] = section_key
        new_entry["entry_order"] = idx
        new_entry["parent_node_key"] = None
        new_entries.append(new_entry)
    notes: list[str] = []
    for note in assembled.get("notes", []):
        if isinstance(note, str):
            text = note
        elif isinstance(note, dict):
            text = note.get("note_raw") or note.get("text") or json.dumps(note, ensure_ascii=False)
        else:
            text = str(note)
        if "ordo" in text.lower() or "contents" in text.lower():
            notes.append(text)
    new_section = deepcopy(section)
    new_section["section_key"] = section_key
    new_section["section_order"] = 2
    new_section["file_start"] = str(seq_path(597))
    new_section["file_end"] = str(seq_path(600))
    new_section["page_start"] = 1181
    new_section["page_end"] = 1188
    return new_section, new_entries, notes


def resolve_direct_targets(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> None:
    arabic_map, roman_map = build_header_page_maps()
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
        if ref.get("target_file"):
            continue
        target_file, target_method = target_from_maps(ref.get("page_ref_int"), (ref.get("raw_json") or {}).get("locator_token_kind", "arabic"), arabic_map, roman_map)
        if target_file:
            ref["target_file"] = target_file
            ref["target_file_probability"] = 0.99
            ref.setdefault("raw_json", {})["target_resolution_method"] = target_method
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.9)
    for entry in entries:
        if entry.get("target_file_best"):
            continue
        for ref in refs_by_entry.get(entry["entry_key"], []):
            if ref.get("target_file"):
                entry["target_file_best"] = ref["target_file"]
                break


def main() -> None:
    alphabetical_entries, alphabetical_refs, helper_jobs = extract_alphabetical_entries()
    alphabetical_section = {
        "section_key": f"{VOLUME_ID}:section:alphabetical_general:001",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX RERUM ET SENTENTIARUM MEMORABILIUM.",
        "heading_norm": "index rerum et sententiarum memorabilium",
        "heading_letter": None,
        "page_start": 1155,
        "page_end": 1180,
        "file_start": str(seq_path(584)),
        "file_end": str(seq_path(596)),
        "confidence": 0.98,
        "raw_json": {
            "source_files": [str(seq_path(seq)) for seq in ALPHABETICAL_SEQS],
            "detected_structure": "alphabetical subject index with a leading editorial note on 1155 and a closing ordo rerum section after 1180",
            "helper_request": str(HELPER_REQUEST),
            "helper_output": str(HELPER_OUTPUT),
            "section_kind_reason": "Closing alphabetical index of subjects and memoranda; not an opening table of works.",
        },
    }

    ordo_section, ordo_entries, ordo_notes = rebuild_ordo_from_fragments(
        start_entry_index=len(alphabetical_entries) + 1,
        section_key=f"{VOLUME_ID}:section:ordo_rerum:002",
    )
    arabic_map, roman_map = build_header_page_maps()
    ordo_refs: list[dict[str, Any]] = []
    for entry in ordo_entries:
        parsed_refs, _ = parse_entry_refs(
            entry["entry_key"],
            entry["entry_raw"],
            entry["section_start_file"],
            entry["editorial_anchor_file"],
            arabic_map,
            roman_map,
        )
        ordo_refs.extend(parsed_refs)
        if parsed_refs and not entry.get("target_file_best"):
            for ref in parsed_refs:
                if ref.get("target_file"):
                    entry["target_file_best"] = ref["target_file"]
                    break

    entries = alphabetical_entries + ordo_entries
    refs = alphabetical_refs + ordo_refs
    resolve_direct_targets(entries, refs)

    helper_request = build_helper_request(
        helper_jobs
        + [
            {
                "entry_id": f"{ref['entry_key']}{REF_ID_SEP}{ref['ref_order']}",
                "lemma_raw": next(entry["lemma_raw"] for entry in entries if entry["entry_key"] == ref["entry_key"]),
                "query_names": build_query_names(next(entry["entry_raw"] for entry in entries if entry["entry_key"] == ref["entry_key"])),
                "page_hints": [ref["ref_raw"]],
                "page_hint_ints": [ref["page_ref_int"]] if ref.get("page_ref_int") is not None else [],
                "context_raw": next(entry["entry_raw"] for entry in entries if entry["entry_key"] == ref["entry_key"]),
            }
            for ref in refs
            if not ref.get("target_file") and ref.get("page_ref_int") is not None
        ]
    )
    dump_json(HELPER_REQUEST, helper_request)
    helper_output = run_helper() if helper_request["entries"] else {"entries": []}
    if helper_request["entries"]:
        apply_helper_resolution(entries, refs, helper_output)

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Rebuilt the PL056 closing alphabetical index directly from OCR blocks, reused the validated ORDO RERUM fragment set, "
            "resolved direct header-mapped locators first, and then retried remaining material anchors through ref-level helper requests."
        ),
        "evidence_files": [str(seq_path(seq)) for seq in range(584, 601)],
    }

    notes = [
        "PL056 INDEX RERUM begins on file 584 (editorial page 1155 inferred from the damaged header and neighboring spreads) and runs through file 596 before ORDO RERUM starts on file 597.",
        "The previous PL056 payload was replaced because its alphabetical section only preserved a small checkpoint subset and left many locators null by inertia.",
        "Ref-level helper requests were generated only for locators still unresolved after exact editorial-header mapping within the current volume.",
        "Collapsed one-line OCR columns on files 585 and 593 were re-segmented conservatively into smaller logical entries instead of preserving page-sized blobs.",
    ]
    notes.extend(ordo_notes[:4])

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": "Closing alphabetical index plus ordo rerum tail rebuilt from OCR and validated fragments.",
        },
        "sections": [alphabetical_section, ordo_section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    dump_json(
        TODO_FILE,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL056 rebuild completed; payload assembled and ready for validation",
            "completed": [
                "rebuilt alphabetical index entries from OCR blocks",
                "reused stable ORDO RERUM fragment objects",
                "ran direct header-page mapping and ref-level helper locator retry",
                "wrote final PL056 alphabetical payload",
            ],
            "pending": ["validate payload with import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "Files 585 and 593 required conservative re-segmentation of collapsed column text.",
                "Helper evidence is stored at ref level in raw_json when it changed a target locator.",
            ],
        },
    )
    dump_json(OUTPUT_FILE, payload)

    print(json.dumps({"status": "ok", "volume_id": VOLUME_ID, "written_file": str(OUTPUT_FILE)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

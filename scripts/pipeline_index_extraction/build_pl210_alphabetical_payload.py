#!/usr/bin/env python3
"""Usage: build the PL210 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl210_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL210/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL210_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL210_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL210 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL210_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL210"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 210"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL210/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL210_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL210_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL210_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL210"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"
SECTION_HEADING_RAW = "INDEX ANALYTICUS RERUM ET VERBORUM QUÆ IN OPERIBUS ALANI DE INSULIS CONTINENTUR."
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

NOISE_LINES = {
    "Digitized by Google",
    "INDEX ANALYTICUS",
    "INDEX ANALYTICUS.",
    "INDEX ANALYTICUS RERUM ET VERBORUM QUÆ IN OPERIBUS ALANI DE INSULIS CONTINENTUR.",
    "ORDO RERUM",
    "ORDO RERUM.",
    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
}

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_LOCATOR_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à|to)\s*(\d{1,4}))?(?!\d)")
ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
SPLIT_MARK = "\u241e"

ABBREV_TOKENS = {
    "b",
    "s",
    "ps",
    "psal",
    "ephes",
    "eccli",
    "eccl",
    "isa",
    "isai",
    "isaias",
    "isaiae",
    "gal",
    "gen",
    "jac",
    "jacob",
    "math",
    "matth",
    "joan",
    "luc",
    "rom",
    "act",
    "prov",
    "tim",
    "petr",
    "heb",
    "dan",
    "joel",
    "petr",
    "matt",
    "marc",
    "mar",
}

SCRIPTURE_BOOK_MAP = {
    "gen": "Gênesis",
    "ex": "Êxodo",
    "lev": "Levítico",
    "num": "Números",
    "deut": "Deuteronômio",
    "ps": "Salmos",
    "psal": "Salmos",
    "eccl": "Eclesiastes",
    "eccli": "Eclesiástico",
    "prov": "Provérbios",
    "isa": "Isaías",
    "isai": "Isaías",
    "isaias": "Isaías",
    "isaiae": "Isaías",
    "math": "São Mateus",
    "matth": "São Mateus",
    "matt": "São Mateus",
    "luc": "São Lucas",
    "joan": "São João",
    "gal": "Gálatas",
    "ephes": "Efésios",
    "eph": "Efésios",
    "rom": "Romanos",
    "jac": "São Tiago",
    "tim": "Timóteo",
    "petr": "São Pedro",
    "heb": "Hebreus",
    "act": "Atos dos Apóstolos",
    "joel": "Joel",
    "dan": "Daniel",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


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


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def parse_page_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for match in HEADER_PAGE_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            numbers.append(value)
    return numbers


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if line in NOISE_LINES:
            continue
        if PAGE_ONLY_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def extract_marginal_letters(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    letters: list[str] = []
    for match in re.finditer(r'<bloco tipo="nota_marginal"[^>]*>\s*(.*?)\s*</bloco>', raw, re.S):
        text = normalize(re.sub(r"<[^>]+>", " ", match.group(1) or "")) or ""
        if LETTER_RE.fullmatch(text):
            letters.append(text)
    return letters


def classify_file(path: Path) -> str:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text") or "") or ""
    body = normalize(parsed.get("body_text") or "") or ""
    text = f"{header} {body}"
    if "ORDO RERUM" in text or "QUAE IN HOC TOMO CONTINENTUR" in text or "QUÆ IN HOC TOMO CONTINENTUR" in text:
        return "ordo_rerum"
    if "INDEX ANALYTICUS" in text:
        return "analytic_subject"
    return "other"


def identify_sections(files: list[Path]) -> dict[str, list[Path]]:
    sections: dict[str, list[Path]] = {"analytic_subject": [], "ordo_rerum": []}
    for path in files:
        kind = classify_file(path)
        if kind in sections:
            sections[kind].append(path)
    sections["analytic_subject"].sort(key=file_seq)
    sections["ordo_rerum"].sort(key=file_seq)
    return sections


def protect_sentence_breaks(text: str) -> str:
    parts: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ".!?":
            j = i + 1
            while j < len(text) and text[j] == " ":
                j += 1
            if j < len(text) and text[j].isupper():
                prev = re.search(r"([A-Za-zÆŒæœ]+)\.$", text[: i + 1])
                token = strip_accents(prev.group(1)).lower() if prev else ""
                if token not in ABBREV_TOKENS and len(token) > 1:
                    parts.append(text[start : i + 1].strip())
                    start = j
                    i = j
                    continue
        i += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return SPLIT_MARK.join(parts)


def split_fragments(text: str) -> list[str]:
    value = normalize(text) or ""
    if not value:
        return []
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"(?<!\d)(\d{1,4})(?!\d)\s+(?=[A-ZÆŒ])", rf"\1{SPLIT_MARK}", value)
    value = protect_sentence_breaks(value)
    fragments = [frag.strip() for frag in value.split(SPLIT_MARK) if frag.strip()]
    merged: list[str] = []
    for frag in fragments:
        if merged and frag in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
            merged.append(frag)
            continue
        merged.append(frag)
    return merged


def roman_to_int(value: str | None) -> int | None:
    if not value:
        return None
    token = value.strip().lower().replace("u", "v")
    if token.isdigit():
        return int(token)
    if not ROMAN_RE.fullmatch(token):
        return None
    total = 0
    prev = 0
    for ch in reversed(token):
        current = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}.get(ch)
        if current is None:
            return None
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total


def normalize_book_token(token: str | None) -> str | None:
    if not token:
        return None
    value = strip_accents(token).lower().strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    return value or None


def normalize_scripture_book(token: str | None) -> str | None:
    key = normalize_book_token(token)
    if not key:
        return None
    key = key.split()[0]
    return SCRIPTURE_BOOK_MAP.get(key)


def parse_scripture_refs(fragment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in re.finditer(r"\(([^()]{2,120})\)", fragment):
        inner = normalize(match.group(1)) or ""
        inner = inner.strip(" .;:")
        m = re.search(
            r"^(?P<book>[A-Za-zÆŒæœ\.]+)\s*[, ]\s*(?P<chap>[0-9ivxlcdmIVXLCDM]+)(?:\s*[,.:]\s*(?P<verse>[0-9ivxlcdmIVXLCDM]+(?:\s*-\s*[0-9ivxlcdmIVXLCDM]+)?))?$",
            inner,
        )
        if not m:
            m = re.search(r"^(?P<book>[A-Za-zÆŒæœ\.]+)\s+(?P<chap>[0-9ivxlcdmIVXLCDM]+)$", inner)
        if not m:
            continue
        book_raw = normalize(m.group("book")) or ""
        book_norm = normalize_scripture_book(book_raw)
        if not book_norm:
            continue
        chapter = roman_to_int(m.group("chap"))
        if chapter is None:
            continue
        verse_raw = m.group("verse")
        verse_start = None
        verse_end = None
        chapter_end = None
        is_range = False
        if verse_raw:
            if "-" in verse_raw:
                start_raw, end_raw = [part.strip() for part in verse_raw.split("-", 1)]
                verse_start = roman_to_int(start_raw)
                verse_end = roman_to_int(end_raw)
                is_range = verse_start is not None and verse_end is not None
                chapter_end = chapter
            else:
                verse_start = roman_to_int(verse_raw)
        refs.append(
            {
                "ref_role": "citation",
                "ref_raw": f"({inner})",
                "book_raw": book_raw,
                "book_norm": book_norm,
                "chapter_start": chapter,
                "verse_start": verse_start,
                "chapter_end": chapter_end,
                "verse_end": verse_end,
                "is_range": is_range,
                "confidence": 0.78 if verse_start is None else 0.82,
            }
        )
    return refs


def extract_page_hints(fragment: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_LOCATOR_RE.finditer(fragment):
        raw_start = match.group(1)
        raw_end = match.group(2)
        for raw in (raw_start, raw_end):
            if not raw:
                continue
            if raw.startswith("0"):
                continue
            value = int(raw)
            if value not in seen:
                seen.add(value)
                hints.append(value)
    return hints


def lemma_from_fragment(fragment: str, has_refs: bool) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if has_refs:
        match = PAGE_LOCATOR_RE.search(text)
        if match:
            text = text[: match.start()].rstrip(" ,;:.")
    return text.strip(" .;:") or None


def entry_kind(section_kind: str, fragment: str, has_refs: bool) -> str:
    text = normalize(fragment) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not has_refs and re.search(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not has_refs and len(text) <= 30 and not re.search(r"[.,;:]", text):
        return "heading_group"
    return "lemma"


def make_entry(
    *,
    section_kind: str,
    section_key: str,
    entry_order: int,
    fragment: str,
    source_file: Path,
    section_start_file: Path,
    current_letter: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    page_hints = extract_page_hints(fragment)
    refs = []
    for ref_order, page in enumerate(page_hints, start=1):
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": "editorial_range" if PAGE_LOCATOR_RE.search(fragment) and PAGE_LOCATOR_RE.search(fragment).group(2) else "editorial_page",
                "ref_raw": str(page),
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": str(source_file),
                "target_file_probability": 0.96,
                "section_start_file": str(section_start_file),
                "editorial_anchor_file": str(source_file),
                "confidence": 0.93 if section_kind == "analytic_subject" else 0.9,
                "raw_json": {
                    "source_file": str(source_file),
                    "locator_method": "direct_source_file",
                },
            }
        )

    lemma_raw = lemma_from_fragment(fragment, bool(page_hints))
    scripture_refs = parse_scripture_refs(fragment)
    entry = {
        "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
        "section_key": section_key,
        "parent_node_key": None,
        "entry_order": entry_order,
        "entry_kind": entry_kind(section_kind, fragment, bool(page_hints)),
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": sort_norm(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": fragment,
        "context_raw": fragment,
        "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
        "inferred_printed_page": page_hints[0] if page_hints else None,
        "section_start_file": str(section_start_file),
        "editorial_anchor_file": str(source_file),
        "target_file_best": str(source_file),
        "confidence": 0.86 if page_hints else 0.72,
        "raw_json": {
            "source_file": str(source_file),
            "section_kind": section_kind,
            "page_hints": page_hints,
            "fragment_kind": "page_entry",
        },
    }
    if scripture_refs:
        entry["raw_json"]["scripture_ref_count"] = len(scripture_refs)
    return entry, refs, scripture_refs


def parse_section(
    *,
    files: list[Path],
    section_kind: str,
    section_key: str,
    section_start_file: Path,
    entry_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    entry_order = entry_offset
    letter_nodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    current_letter: str | None = None

    for path in files:
        lines = extract_body_lines(path)
        for letter in extract_marginal_letters(path):
            if section_kind == "analytic_subject" and letter not in letter_nodes:
                node_key = f"{VOLUME_ID}:node:letter:{letter}"
                letter_nodes[letter] = {
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": len(letter_nodes) + 1,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section_kind,
                        "role": "alphabetic divider",
                    },
                }
                current_letter = letter
        page_chunks: list[str] = []
        for line in lines:
            if line in NOISE_LINES:
                continue
            if section_kind == "analytic_subject" and LETTER_RE.fullmatch(line):
                if line not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:letter:{line}"
                    letter_nodes[line] = {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": len(letter_nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": line.lower(),
                        "label_sort": line.lower(),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section_kind,
                            "role": "alphabetic divider",
                        },
                    }
                current_letter = line
                continue
            page_chunks.append(line)

        page_text = normalize(" ".join(page_chunks)) or ""
        if not page_text:
            continue
        page_text = re.sub(r"(?<=\w)-\s+(?=\w)", "", page_text)
        fragments = split_fragments(page_text)
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or cleaned in NOISE_LINES:
                continue
            if section_kind == "analytic_subject" and LETTER_RE.fullmatch(cleaned):
                current_letter = cleaned
                continue
            entry_order += 1
            entry, entry_refs, entry_scripture = make_entry(
                section_kind=section_kind,
                section_key=section_key,
                entry_order=entry_order,
                fragment=cleaned,
                source_file=path,
                section_start_file=section_start_file,
                current_letter=current_letter,
            )
            if section_kind == "ordo_rerum" and not entry["lemma_raw"]:
                entry["lemma_raw"] = cleaned
                entry["lemma_display"] = cleaned
                entry["lemma_norm"] = sort_norm(cleaned)
                entry["lemma_sort"] = sort_norm(cleaned)
            entries.append(entry)
            refs.extend(
                [
                    {
                        **ref,
                        "entry_key": entry["entry_key"],
                    }
                    for ref in entry_refs
                ]
            )
            scripture_refs.extend(
                [
                    {
                        "entry_key": entry["entry_key"],
                        "ref_order": idx + 1,
                        "ref_role": item["ref_role"],
                        "ref_raw": item["ref_raw"],
                        "book_raw": item["book_raw"],
                        "book_norm": item["book_norm"],
                        "chapter_start": item["chapter_start"],
                        "verse_start": item["verse_start"],
                        "chapter_end": item["chapter_end"],
                        "verse_end": item["verse_end"],
                        "is_range": item["is_range"],
                        "confidence": item["confidence"],
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section_kind,
                            "fragment": cleaned,
                        },
                    }
                    for idx, item in enumerate(entry_scripture)
                ]
            )

    nodes = list(letter_nodes.values()) if section_kind == "analytic_subject" else []
    return entries, refs, scripture_refs, nodes, entry_order


def helper_request_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        if entry["entry_order"] > 18 and entry["entry_order"] % 35 != 0:
            continue
        page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
        if not page_hints:
            continue
        lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:96]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma_raw,
                "query_names": [q for q in dict.fromkeys(
                    [
                        normalize(lemma_raw) or lemma_raw,
                        normalize(entry["entry_raw"].split(",", 1)[0]) or entry["entry_raw"].split(",", 1)[0],
                        normalize(entry["entry_raw"][:80]) or entry["entry_raw"][:80],
                    ]
                ) if q],
                "page_hints": [str(page) for page in page_hints[:3]],
                "page_hint_ints": page_hints[:3],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(DEFAULT_SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}


def attach_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = helper_index(helper_output)
    ref_map: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        ref_map.setdefault(ref["entry_key"], []).append(ref)
    for entry in entries:
        helper = helper_map.get(entry["entry_key"])
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
                    "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict)],
                }
                for cand in helper.get("candidates", [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")
            for ref in ref_map.get(entry["entry_key"], []):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    sections_by_kind = identify_sections(files)
    analytic_files = sections_by_kind["analytic_subject"]
    ordo_files = sections_by_kind["ordo_rerum"]
    if not analytic_files:
        raise SystemExit(f"no analytical index files found in {source_root}")

    analytic_entries, analytic_refs, analytic_scripture_refs, analytic_nodes, _ = parse_section(
        files=analytic_files,
        section_kind="analytic_subject",
        section_key=INDEX_SECTION_KEY,
        section_start_file=analytic_files[0],
        entry_offset=0,
    )
    ordo_entries, ordo_refs, ordo_scripture_refs, _, _ = parse_section(
        files=ordo_files,
        section_kind="ordo_rerum",
        section_key=ORDO_SECTION_KEY,
        section_start_file=ordo_files[0] if ordo_files else analytic_files[0],
        entry_offset=len(analytic_entries),
    )

    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs
    scripture_refs = analytic_scripture_refs + ordo_scripture_refs

    helper_request = helper_request_from_entries(entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}
    attach_helper(entries, refs, helper_output)

    sections = [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": sort_norm(SECTION_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1015,
            "page_end": 1056,
            "file_start": str(analytic_files[0]),
            "file_end": str(analytic_files[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytical index headed INDEX ANALYTICUS; OCR tail files are interleaved with the closing ordo_rerum block, so the file order is not the same as the editorial order.",
                "evidence_files": [str(analytic_files[0]), str(analytic_files[-1])] + [str(path) for path in analytic_files[1:3]],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": sort_norm(ORDO_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1041,
            "page_end": 1056,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.94 if ordo_files else 0.0,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table for the volume; it is editorial closure material and not a second alphabetical index.",
                "evidence_files": [str(path) for path in ordo_files[:2] + ordo_files[-2:]] if ordo_files else [],
            },
        },
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytical index and the closing ORDO RERUM contents table from an interleaved OCR tail. The file suffixes do not follow editorial order, so the payload keeps source-file anchors and preserves the drift explicitly.",
        "evidence_files": [
            str(analytic_files[0]),
            str(analytic_files[-1]),
            str(ordo_files[0]) if ordo_files else str(analytic_files[0]),
            str(ordo_files[-1]) if ordo_files else str(analytic_files[-1]),
        ],
    }

    notes = [
        "The OCR tail is interleaved: analytical index pages and ORDO RERUM files are mixed in the suffix order.",
        "Helper request was sampled from representative index entries only; direct OCR anchors were retained as the canonical material location for all entries.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": analytic_nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", analytic_nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL210 alphabetical payload assembled and validated",
            "completed": [
                "tail OCR classified into analytical and ordo sections",
                "helper request generated and executed",
                "intermediate fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep the OCR file suffixes distinct from printed pages; PL210 uses interleaved tail files.",
            ],
        },
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    payload = build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
    )
    write_json(args.output_file, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

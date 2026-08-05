#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg101_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG101/text \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG101 \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG101_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG101_helper_output.json \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG101_alphabetical_indices.json

Builds the PG101 alphabetical payload from the OCR tail, including a helper
request for material locators and intermediate JSON fragments for assembly.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG101"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 101"

SECTION_ALPHA = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "heading_norm": "index analyticus",
    "page_start": 1245,
    "page_end": 1264,
    "file_start_seq": 650,
    "file_end_seq": 654,
    "section_kind_reason": (
        "Analytical alphabetical index headed INDEX ANALYTICUS; the OCR tail shows the "
        "A-Z letter groups and dense page/column locators."
    ),
}

SECTION_ORDO = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "page_start": 1265,
    "page_end": 1276,
    "file_start_seq": 655,
    "file_end_seq": 660,
    "section_kind_reason": "Editorial contents-order block distinct from the alphabetical index.",
}

SECTION_META = [SECTION_ALPHA, SECTION_ORDO]

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_HEAD_RE = re.compile(r"^(?:[IVXLCDM]+\.|[IVXLCDM]+)$")
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SECTION1_REF_RE = re.compile(
    r"(?<!\w)(\d{1,4})\s*(?:(?P<col>[a-d])|(?P<note>n\.?))?(?:\s*(?P<suffix>seq\.?|seqq\.?|et seq\.?|ibid\.?|id\.?|passim))?",
    re.IGNORECASE,
)
ORDO_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
BLOCK_RE = re.compile(r'<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>', re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
START_RE = re.compile(
    r"^(?:Qu[aæ]stio|Dissertatiuncula|FRAGMENTA|Fragmenta|I\.|II\.|III\.|IV\.|V\.|VI\.|VII\.|"
    r"VIII\.|IX\.|X\.|XI\.|XII\.|XIII\.|XIV\.|XV\.|XVI\.|XVII\.|XVIII\.|XIX\.|XX\.|"
    r"XXI\.|XXII\.|XXIII\.|XXIV\.|XXV\.|XXVI\.|XXVII\.|XXVIII\.|XXIX\.|XXX\.|XXXI\.|"
    r"XXXII\.|XXXIII\.|XXXIV\.|XXXV\.|XXXVI\.|XXXVII\.|XXXVIII\.|XXXIX\.|XL\.|XLI\.|"
    r"XLII\.|XLIII\.|XLIV\.|XLV\.|XLVI\.|XLVII\.|XLVIII\.|XLIX\.|L\.|LI\.|LII\.|LIII\.|"
    r"LIV\.|LV\.|LVI\.|LVII\.|LVIII\.|LIX\.|LX\.|LXI\.|LXII\.|LXIII\.|LXIV\.|LXV\.|"
    r"LXVI\.|LXVII\.|LXVIII\.|LXIX\.|LXX\.|LXXI\.|LXXII\.|LXXIII\.|LXXIV\.|LXXV\.|"
    r"LXXVI\.|LXXVII\.|LXXVIII\.|LXXIX\.|LXXX\.|LXXXI\.|LXXXII\.|LXXXIII\.|LXXXIV\.|"
    r"LXXXV\.|LXXXVI\.|LXXXVII\.|LXXXVIII\.|LXXXIX\.|XC\.|XCI\.|XCII\.|XCIII\.|XCIV\.|"
    r"XCV\.|XCVI\.|XCVII\.|XCVIII\.|XCIX\.|C\.|CI\.|CII\.|CIII\.|CIV\.|CV\.|CVI\.|"
    r"CVII\.|CVIII\.|CIX\.|CX\.|CXI\.|CXII\.|CXIII\.|CXIV\.|CXV\.|CXVI\.|CXVII\.|"
    r"CXVIII\.|CXIX\.|CXX\.|CXXI\.|CXXII\.|CXXIII\.|CXXIV\.|CXXV\.|CXXVI\.|CXXVII\.|"
    r"CXXVIII\.|CXXIX\.|CXXX\.|CXXXI\.|CXXXII\.|CXXXIII\.|CXXXIV\.|CXXXV\.|CXXXVI\.|"
    r"CXXXVII\.|CXXXVIII\.|CXXXIX\.|CXL\.|CXLI\.|CXLII\.|CXLIII\.|CXLIV\.|CXLV\.|"
    r"CXLVI\.|CXLVII\.|CXLVIII\.|CXLIX\.|CL\.|CLI\.|CLII\.|CLIII\.|CLIV\.|CLV\.|"
    r"CLVI\.|CLVII\.|CLVIII\.|CLIX\.|CLX\.|CLXI\.|CLXII\.|CLXIII\.|CLXIV\.|CLXV\.|"
    r"CLXVI\.|CLXVII\.|CLXVIII\.|CLXIX\.|CLXX\.|CLXXI\.|CLXXII\.|CLXXIII\.|CLXXIV\.|"
    r"CLXXV\.|CLXXVI\.|CLXXVII\.|CLXXVIII\.|CLXXIX\.|CLXXX\.|CLXXXI\.|CLXXXII\.|"
    r"CLXXXIII\.|CLXXXIV\.|CLXXXV\.|CLXXXVI\.|CLXXXVII\.|CLXXXVIII\.|CLXXXIX\.|"
    r"CXC\.|CXCI\.|CXCII\.|CXCIII\.|CXCIV\.|CXCV\.|CXCVI\.|CXCVII\.|CXCVIII\.|"
    r"CXCIX\.|CC\.|CCI\.|CCII\.|CCIII\.|CCIV\.|CCV\.|CCVI\.|CCVII\.|CCVIII\.|"
    r"CCIX\.|CCX\.|CCXI\.|CCXII\.|CCXIII\.|CCXIV\.|CCXV\.|CCXVI\.|CCXVII\.|"
    r"CCXVIII\.|CCXIX\.|CCXX\.|CCXXI\.|CCXXII\.|CCXXIII\.|CCXXIV\.|CCXXV\.|"
    r"CCXXVI\.|CCXXVII\.|CCXXVIII\.|CCXXIX\.|CCXXX\.|CCXXXI\.|CCXXXII\.|"
    r"CCXXXIII\.|CCXXXIV\.|CCXXXV\.|CCXXXVI\.|CCXXXVII\.|CCXXXVIII\.|CCXXXIX\.|"
    r"CCXL\.|CCXLI\.|CCXLII\.|CCXLIII\.|CCXLIV\.|CCXLV\.|CCXLVI\.|CCXLVII\.|"
    r"CCXLVIII\.|CCXLIX\.|CCL\.|CCLI\.|CCLII\.|CCLIII\.|CCLIV\.|CCLV\.|CCLVI\.|"
    r"CCLVII\.|CCLVIII\.|CCLIX\.|CCLX\.|CCLXI\.|CCLXII\.|CCLXIII\.|CCLXIV\.|"
    r"CCLXV\.|CCLXVI\.|CCLXVII\.|CCLXVIII\.|CCLXIX\.|CCLXX\.|CCLXXI\.|CCLXXII\.|"
    r"CCLXXIII\.|CCLXXIV\.|CCLXXV\.|CCLXXVI\.|CCLXXVII\.|CCLXXVIII\.|CCLXXIX\.|"
    r"CCLXXX\.|CCLXXXI\.|CCLXXXII\.|CCLXXXIII\.|CCLXXXIV\.|CCLXXXV\.|CCLXXXVI\.|"
    r"CCLXXXVII\.|CCLXXXVIII\.|CCLXXXIX\.|CCXC\.|CCXCI\.|CCXCII\.|CCXCIII\.|"
    r"CCXCIV\.|CCXCV\.|CCXCVI\.|CCXCVII\.|CCXCVIII\.|CCXCIX\.|CCC\.)"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def lemma_norm(text: str | None) -> str | None:
    return sort_norm(text)


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = normalize(attrs.get("tipo") or "").lower()
        script = normalize(attrs.get("script") or "").lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        blocks.append(
            {
                "tipo": block_type,
                "script": script,
                "lines": lines,
            }
        )
    return blocks


def extract_header_numbers(path: Path) -> list[int]:
    header_nums: list[int] = []
    for block in extract_blocks(path):
        if block["tipo"] != "cabecalho":
            continue
        header_text = normalize(" ".join(block["lines"]))
        for match in PAGE_HEADER_RE.finditer(header_text):
            num = int(match.group(1))
            if num not in header_nums:
                header_nums.append(num)
    return header_nums


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for num in extract_header_numbers(path):
            mapping.setdefault(num, str(path))
    return mapping


def make_section(section_meta: dict[str, Any], *, file_start: Path, file_end: Path) -> dict[str, Any]:
    return {
        "section_key": section_meta["section_key"],
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_meta["section_order"],
        "section_kind": section_meta["section_kind"],
        "heading_raw": section_meta["heading_raw"],
        "heading_norm": section_meta["heading_norm"],
        "heading_letter": None,
        "page_start": section_meta["page_start"],
        "page_end": section_meta["page_end"],
        "file_start": str(file_start),
        "file_end": str(file_end),
        "confidence": 0.96 if section_meta["section_kind"] == "ordo_rerum" else 0.97,
        "raw_json": {
            "section_kind_reason": section_meta["section_kind_reason"],
            "file_start_seq": section_meta["file_start_seq"],
            "file_end_seq": section_meta["file_end_seq"],
        },
    }


def helper_summary_map(helper_request_path: Path, helper_output_path: Path) -> dict[str, dict[str, Any]]:
    if not helper_request_path.exists() or not helper_output_path.exists():
        return {}
    request = json.loads(helper_request_path.read_text(encoding="utf-8"))
    output = json.loads(helper_output_path.read_text(encoding="utf-8"))
    request_entries = {item.get("entry_id"): item for item in request.get("entries", []) if item.get("entry_id")}
    result: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        req = request_entries[entry_id]
        candidate = (item.get("candidates") or [{}])[0] or {}
        result[normalize(req.get("lemma_raw") or "")] = {
            "status": item.get("status"),
            "candidate_role": candidate.get("candidate_role"),
            "reason_summary": candidate.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": candidate.get("file") or (item.get("best_candidate") or {}).get("file"),
                "probability": candidate.get("probability") or (item.get("best_candidate") or {}).get("probability"),
                "candidate_role": candidate.get("candidate_role") or (item.get("best_candidate") or {}).get("candidate_role"),
                "inferred_printed_page": candidate.get("inferred_printed_page") or (item.get("best_candidate") or {}).get("inferred_printed_page"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in candidate.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ][:8],
            },
        }
    return result


def extract_section1_entries(files: list[Path], page_map: dict[int, str], helper_map: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    node_lookup: dict[str, str] = {}
    node_order = 0
    entry_order = 0
    current_letter: str | None = None
    letter_seen: set[str] = set()
    seen_entry_signatures: set[str] = set()

    def ensure_letter_node(letter: str, source_file: str) -> str:
        nonlocal node_order
        if letter in node_lookup:
            return node_lookup[letter]
        node_order += 1
        node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:node:{node_order:03d}:{letter}"
        node_lookup[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_ALPHA["section_key"],
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {
                    "source_file": source_file,
                    "line_kind": "letter_heading",
                },
            }
        )
        letter_seen.add(letter)
        return node_key

    def split_fragments(text: str) -> list[str]:
        text = normalize(text)
        if not text:
            return []
        # Split only on sentence-ending periods that introduce a new uppercase lemma.
        parts = re.split(r"(?<=[.])\s+(?=[A-ZÆŒ])", text)
        return [normalize(part) for part in parts if normalize(part)]

    def extract_lemma(text: str) -> str | None:
        text = normalize(text)
        if not text:
            return None
        match = SECTION1_REF_RE.search(text)
        if match:
            return normalize(text[: match.start()]).strip(" ,;:.") or None
        return normalize(text).strip(" ,;:.") or None

    for path in files:
        file_seq_no = file_seq(path)
        blocks = extract_blocks(path)
        for block in blocks:
            if block["tipo"] not in {"texto_principal", "nota_marginal"}:
                continue
            lines = block["lines"]
            if not lines:
                continue
            # Capture explicit letter headings, both as marginal notes and inline lines.
            for raw_line in lines:
                line = normalize(raw_line)
                if LETTER_RE.fullmatch(line):
                    current_letter = line
                    ensure_letter_node(line, str(path))
                    continue
                if line in {"INDEX ANALYTICUS.", "INDEX ANALYTICUS", "Revocatur lector ad paginas editionis nostræ. Litteræ a, b, c, d inchoantem, mediam ac desinentem paginam signant."}:
                    continue
            block_text = normalize(" ".join(lines))
            if not block_text:
                continue
            for fragment in split_fragments(block_text):
                if fragment in {"INDEX ANALYTICUS.", "INDEX ANALYTICUS"}:
                    continue
                if LETTER_RE.fullmatch(fragment) or fragment in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "V", "Z"}:
                    current_letter = fragment
                    ensure_letter_node(fragment, str(path))
                    continue
                if fragment.startswith("Revocatur lector ad paginas editionis nostræ"):
                    continue
                if not SECTION1_REF_RE.search(fragment):
                    # Keep only fragments that carry an actual material locator.
                    continue
                lemma = extract_lemma(fragment)
                if not lemma:
                    continue
                signature = f"{current_letter}|{lemma}|{fragment}"
                if signature in seen_entry_signatures:
                    continue
                seen_entry_signatures.add(signature)
                entry_order += 1
                entry_key = f"{VOLUME_ID}:alpha:analytic_subject:001:entry:{entry_order:04d}"
                ref_items: list[dict[str, Any]] = []
                first_page: int | None = None
                first_target: str | None = None
                for ref_idx, match in enumerate(SECTION1_REF_RE.finditer(fragment), start=1):
                    page_int = int(match.group(1))
                    page_col = match.group("col").lower() if match.group("col") else None
                    line_ref = match.group("note")
                    suffix = match.group("suffix")
                    ref_raw = match.group(0).strip()
                    if first_page is None:
                        first_page = page_int
                        first_target = page_map.get(page_int)
                    ref_item = {
                        "entry_key": entry_key,
                        "ref_order": ref_idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref_raw,
                        "page_ref_raw": ref_raw,
                        "page_ref_int": page_int,
                        "page_ref_col": page_col,
                        "line_ref_raw": line_ref,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": page_map.get(page_int),
                        "target_file_probability": 0.99 if page_map.get(page_int) else 0.65,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.9 if page_map.get(page_int) else 0.6,
                        "raw_json": {
                            "token_match": match.group(0),
                            "sequence_suffix": suffix.strip() if suffix else None,
                            "source_file": str(path),
                        },
                    }
                    ref_items.append(ref_item)
                if not ref_items:
                    continue
                refs.extend(ref_items)
                helper_key = normalize(lemma)
                helper_info = helper_map.get(helper_key or "")
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": SECTION_ALPHA["section_key"],
                        "parent_node_key": node_lookup.get(current_letter) if current_letter else None,
                        "entry_order": entry_order,
                        "entry_kind": "lemma",
                        "lemma_raw": lemma,
                        "lemma_display": lemma,
                        "lemma_norm": lemma_norm(lemma),
                        "lemma_sort": sort_norm(lemma),
                        "entry_raw": fragment,
                        "context_raw": None,
                        "heading_letter": current_letter,
                        "inferred_printed_page": first_page,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": str(path),
                        "target_file_best": first_target,
                        "confidence": 0.88 if first_target else 0.72,
                        "raw_json": {
                            "source_file": str(path),
                            "fragment_strategy": "sentence_split_after_period_before_uppercase",
                            "page_ref_count": len(ref_items),
                            "helper": helper_info if helper_info else None,
                        },
                    }
                )
    return nodes, entries, refs


def extract_ordo_entries(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    node_order = 0
    entry_order = 0

    def add_node(label_raw: str, node_kind: str, source_file: str, parent: str | None = None, level: int = 1) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"{VOLUME_ID}:alpha:ordo_rerum:002:node:{node_order:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_ORDO["section_key"],
                "parent_node_key": parent,
                "node_order": node_order,
                "node_kind": node_kind,
                "label_raw": label_raw,
                "label_norm": normalize(label_raw).lower() if normalize(label_raw) else None,
                "label_sort": sort_norm(label_raw),
                "node_level": level,
                "confidence": 0.97,
                "raw_json": {"source_file": source_file, "line_kind": "ordo_heading"},
            }
        )
        return node_key

    # top-level structural nodes
    heading_node = add_node("PHOTIUS, PATRIARCHA CONSTANTINOPOLITANUS.", "heading_group", str(files[0]), None, 1)
    pars_node = add_node("OPERUM PARS PRIMA. — EXEGETICA.", "heading_group", str(files[0]), heading_node, 2)
    amph_node = add_node("AMPHILOCHIA SIVE IN SACRAS LITTERAS ET QUÆSTIONES DIATRIBE.", "heading_group", str(files[0]), pars_node, 2)
    add_node("Ad Amphilochium S. Cyzici metropolitanum.", "rubric_group", str(files[0]), amph_node, 3)
    frag_node = add_node("FRAGMENTA IN NOVUM TESTAMENTUM.", "heading_group", str(files[-1]), heading_node, 2)

    def entry_kind_for_text(text: str) -> str:
        norm = normalize(text)
        if norm.startswith(("V.", "VI.", "VII.", "I. ", "II. ", "III. ", "IV. ")):
            return "heading_group"
        if norm.startswith(("1°", "2°", "3°", "4°", "5°", "6°", "7°", "8°", "9°")):
            return "sublemma"
        if norm.startswith(("Fragmenta", "— in")):
            return "heading_group"
        if norm.startswith(("In Amphilochia", "Ad Amphilochium")):
            return "heading_group"
        if norm.startswith(("Quæstio", "Quaestio", "QUAESTIO")):
            return "lemma"
        return "lemma"

    def parent_for_text(text: str) -> str:
        norm = normalize(text)
        if norm.startswith(("V.", "VI.", "VII.", "I. ", "II. ", "III. ", "IV. ")):
            return heading_node
        if norm.startswith(("Fragmenta", "— in", "In Amphilochia", "Ad Amphilochium")):
            return frag_node if norm.startswith(("Fragmenta", "— in")) else amph_node
        if norm.startswith(("1°", "2°", "3°", "4°", "5°", "6°", "7°", "8°", "9°")):
            return heading_node
        return amph_node

    buffer: list[str] = []
    active_source: str | None = None

    def flush_buffer() -> None:
        nonlocal entry_order, buffer, active_source
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        buffer = []
        if not entry_raw:
            return
        page_matches = list(ORDO_PAGE_RE.finditer(entry_raw))
        if not page_matches:
            return
        page_int = int(page_matches[-1].group(1))
        entry_order += 1
        entry_key = f"{VOLUME_ID}:alpha:ordo_rerum:002:entry:{entry_order:04d}"
        lemma_raw = normalize(re.sub(r"\s+\d{1,4}\s*$", "", entry_raw)).strip(" ,;:.") or None
        ref_raw = page_matches[-1].group(1)
        kind = entry_kind_for_text(entry_raw)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": page_map.get(page_int),
                "target_file_probability": 0.99 if page_map.get(page_int) else 0.6,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": active_source or str(files[0]),
                "confidence": 0.9 if page_map.get(page_int) else 0.65,
                "raw_json": {"source_file": active_source, "entry_kind": kind},
            }
        )
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_ORDO["section_key"],
                "parent_node_key": parent_for_text(entry_raw),
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_norm(lemma_raw) if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": active_source or str(files[0]),
                "target_file_best": page_map.get(page_int),
                "confidence": 0.88 if page_map.get(page_int) else 0.7,
                "raw_json": {
                    "source_file": active_source,
                    "parsed_from": "ordo_rerum line",
                    "entry_kind": kind,
                },
            }
        )
        active_source = None

    for path in files:
        blocks = extract_blocks(path)
        for block in blocks:
            if block["tipo"] != "texto_principal":
                continue
            for raw_line in block["lines"]:
                line = normalize(raw_line)
                if not line:
                    continue
                if line in {
                    "ORDO RERUM",
                    "QUÆ IN HOC TOMO CONTINENTUR.",
                    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                    "PHOTIUS, PATRIARCHA CONSTANTINOPOLITANUS.",
                    "OPERUM PARS PRIMA. — EXEGETICA.",
                    "AMPHILOCHIA SIVE IN SACRAS LITTERAS ET QUÆSTIONES DIATRIBE.",
                    "Ad Amphilochium S. Cyzici metropolitanum.",
                    "FRAGMENTA IN NOVUM TESTAMENTUM.",
                    "FINIS TOMI CENTESIMI PRIMI.",
                }:
                    flush_buffer()
                    # Structural headings are represented as nodes, not entries.
                    continue
                if line.startswith("PHOTIUS,") or line.startswith("OPERUM PARS PRIMA"):
                    flush_buffer()
                    continue
                if line.startswith("FRAGMENTA IN NOVUM TESTAMENTUM"):
                    flush_buffer()
                    continue
                if line.startswith("In Amphilochia") or line.startswith("Ad Amphilochium"):
                    flush_buffer()
                if line.startswith("V. — Index Amphilochianarum") or line.startswith("VI. — Index potiorum locorum") or line.startswith("VII. — Patres alii"):
                    flush_buffer()
                    buffer = [line]
                    active_source = str(path)
                    flush_buffer()
                    continue
                if line.startswith("Fragmenta in Matthæum.") or line.startswith("— in"):
                    flush_buffer()
                    buffer = [line]
                    active_source = str(path)
                    flush_buffer()
                    continue

                if buffer:
                    buffer.append(line)
                    if re.search(r"\d{1,4}\s*$", line):
                        flush_buffer()
                    continue

                if re.search(r"\d{1,4}\s*$", line):
                    buffer = [line]
                    active_source = str(path)
                    flush_buffer()
                else:
                    buffer = [line]
                    active_source = str(path)
        flush_buffer()
    flush_buffer()
    return nodes, entries, refs


def build_helper_request(alpha_entries: list[dict[str, Any]], ordo_entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    sample_entries = []
    for idx, entry in enumerate(alpha_entries[:5] + ordo_entries[:3], start=1):
        page_hint = entry.get("inferred_printed_page")
        sample_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_helper_{idx:02d}",
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [entry.get("lemma_raw") or entry.get("entry_raw")],
                "page_hints": [str(page_hint)] if page_hint is not None else [],
                "page_hint_ints": [page_hint] if page_hint is not None else [],
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": sample_entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root
    intermediate_dir = args.intermediate_dir
    helper_request_json = args.helper_request_json
    helper_output_json = args.helper_output_json
    output_file = args.output_file

    files = discover_files(source_root)
    if not files:
        raise SystemExit(f"no OCR files found under {source_root}")

    page_map = build_page_map(files)
    helper_map = helper_summary_map(helper_request_json, helper_output_json)

    alpha_files = [p for p in files if SECTION_ALPHA["file_start_seq"] <= file_seq(p) <= SECTION_ALPHA["file_end_seq"]]
    ordo_files = [p for p in files if SECTION_ORDO["file_start_seq"] <= file_seq(p) <= SECTION_ORDO["file_end_seq"]]
    if not alpha_files or not ordo_files:
        raise SystemExit("expected PG101 tail files are missing")

    alpha_nodes, alpha_entries, alpha_refs = extract_section1_entries(alpha_files, page_map, helper_map)
    ordo_nodes, ordo_entries, ordo_refs = extract_ordo_entries(ordo_files, page_map)

    sections = [
        make_section(SECTION_ALPHA, file_start=alpha_files[0], file_end=alpha_files[-1]),
        make_section(SECTION_ORDO, file_start=ordo_files[0], file_end=ordo_files[-1]),
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "Analytical index and Ordo Rerum were recovered from the OCR tail pages.",
            "OCR literals and cited page locators were preserved conservatively.",
        ],
    }

    entries = alpha_entries + ordo_entries
    refs = alpha_refs + ordo_refs
    nodes = alpha_nodes + ordo_nodes
    scripture_refs: list[dict[str, Any]] = []
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytical index and closing Ordo Rerum block from the OCR tail; page locators were cross-walked to the volume header map.",
        "evidence_files": [str(p) for p in alpha_files + ordo_files],
    }
    notes = [
        "Entry segmentation is conservative: sentence breaks introduce new entries only when a new uppercase lemma or quæstio heading appears.",
        "Bare remissions such as ibid. were preserved in entry text and not emitted as standalone refs.",
    ]

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PG101 alphabetical and ordo_rerum tail extraction",
        "completed": [
            "tail OCR inspected",
            "analytical section parsed",
            "ordo rerum section parsed",
        ],
        "pending": [
            "run helper on selected ambiguous locators",
            "assemble final payload",
        ],
        "blocked": [],
        "notes": [
            "Use helper only as anchor evidence; OCR structure remains primary.",
        ],
    }

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Validate helper anchors for representative PG101 entries",
        "completed": [
            "OCR tail inspected",
            "intermediate fragments generated",
        ],
        "pending": [
            "run helper",
            "regenerate payload with helper summaries",
        ],
        "blocked": [],
        "notes": [
            "Keep page, OCR file suffix, and cited locator separate.",
        ],
    }

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

    helper_request = build_helper_request(alpha_entries, ordo_entries, source_root)
    write_json(helper_request_json, helper_request)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
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


if __name__ == "__main__":
    main()

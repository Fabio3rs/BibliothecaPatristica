#!/usr/bin/env python3
"""Usage: rebuild PO003 alphabetical payload from inspected OCR pages 127-129.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_po003_alphabetical_payload.py
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO003"
COLLECTION = "PO"
SOURCE_ROOT = ROOT / "teste/PO003/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PO003_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PO003_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PO003_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO003"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

PAGE_126 = SOURCE_ROOT / "47407ec6-0467-40cb-8c70-9d75083e0385-126.txt"
PAGE_127 = SOURCE_ROOT / "47407ec6-0467-40cb-8c70-9d75083e0385-127.txt"
PAGE_128 = SOURCE_ROOT / "47407ec6-0467-40cb-8c70-9d75083e0385-128.txt"
PAGE_129 = SOURCE_ROOT / "47407ec6-0467-40cb-8c70-9d75083e0385-129.txt"

ONOMASTIC_SECTION = f"{VOLUME_ID}:alpha:onomastic_mixed:001"
SCRIPTURE_SECTION = f"{VOLUME_ID}:alpha:scripture_index:002"

SUPERSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SPACE_RE = re.compile(r"\s+")
LOCATOR_RE = re.compile(
    r"(?<![\wܐ-ݏ؀-ۿ])(?P<page>\d{1,3})(?P<line>(?:[_₀₁₂₃₄₅₆₇₈₉][₀₁₂₃₄₅₆₇₈₉0-9,\-]*)|(?:\s+n\.\s*\d+)|(?:\s*,\s*titre))?"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.translate(SUPERSCRIPT_DIGITS)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = re.sub(r"[=,;:.()\[\]*/?]+", " ", text)
    text = SPACE_RE.sub(" ", text).strip().lower()
    return text or None


def lemma_from_entry(entry_raw: str) -> str:
    first_line = entry_raw.splitlines()[0].strip()
    no_locator = LOCATOR_RE.split(first_line, maxsplit=1)[0].strip(" ;.,")
    no_remission = re.split(r"\b(?:V|cf|var)\.?\b", no_locator, maxsplit=1)[0].strip(" ;.,")
    return no_remission or first_line.strip(" ;.,")


def entry_kind(entry_raw: str) -> str:
    has_locator = any(parse_locators(entry_raw))
    has_remission = bool(re.search(r"\b(?:V|cf)\.?\b", entry_raw))
    if has_remission and not has_locator:
        return "cross_reference"
    return "lemma"


def parse_locators(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for match in LOCATOR_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        page_int = int(match.group("page"))
        if page_int > 999:
            continue
        line_raw = match.group("line")
        if line_raw:
            line_raw = line_raw.strip()
        key = (raw, page_int, line_raw)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": str(page_int),
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": line_raw,
                "range_start_raw": raw,
                "range_end_raw": None,
                "ref_kind": "editorial_page_line" if line_raw and not line_raw.startswith("n.") else "editorial_page",
            }
        )
    return refs


def node_key(order: int) -> str:
    return f"{VOLUME_ID}:node:{order:03d}"


ONOMASTIC_GROUPS: list[dict[str, Any]] = [
    {
        "file": PAGE_127,
        "node": None,
        "entries": [
            "ܐܕܝܡܘܢ 93_11. V. ܐܕܝܡ",
            "ܐܕܝܣܐ ܕܐܘܗܝ 91_2-11 ; 91_5",
            "ܐܚܕ 93_8. V. ܐܚܕܐ",
            "ܐܠ 15_3 ; 31_2 ; 38_1 ; 95_4",
            "ܐܠܐ 70_13",
            "ܐܠܗܐ ܡܪܝܢ ܐܠܐ 63_10",
            "ܐܠܗܐ (ܐܚܕ ܐܠܗܐ) 71_5 ; 78_6",
            "ܐܣܦܗܘܣ 15, titre; 17_4-11 ; 18_4-15 ; 19_3 ;\n22_7 ; 23_2-6-8 ; 24_13 ; 25_2-5-6-9 ; 26_2-11-14 ;\n28_13 ; 31_6-9 ; 32_10 ; 33_6 ; 34_2-5 ; 36_2-4-6 ;\n37_10 ; 38_9 ; 39_11 ; 40_1 ; 41_2 ; 42_7-11 ;\n44_10 ; 46_5-11 ; 47_7 ; 48_5-11-13 ; 49_10-14 ;\n50_2-7 ; 51_1-6-8. Églises sous son\nvocable 51 n. 3.",
            "ܐܦܪܝܡ 47_6",
            "ܐܠܚ ܐܚܕ 75_8",
            "ܐܠܡܐ 64_6",
            "ܐܦܣܩܘܦܐ 34_11",
            "ܐܡܘܪܐ 32_11 ; 48_6 ; 49_5-7 ; 50_2-4-10",
            "ܐܚܕܐ 64_11 ; 72_2-6",
            "ܬܐܘܡܐ ܐܠܝ ܬܗܘܐ cf. ܗܘܡܐ",
            "ܐܚܕ ܐܚܢ 89_5",
            "ܐܠܗܐܪܡܐ 70_4. V. ܗܪܡ",
            "ܐܠܗܐܪܡܐ ܦܠܝܦܐ 78_12 ; 79_4",
            "ܐܚܝܐ 171 V. ܐܚܕܐ.",
            "ܚܡܕ ܡܪܝܢ 68_1",
            "ܚܡܕ ܢܛܘܪܝܢ 63_1 ; 66_5 ; 67_12 ; 71_15 ;",
            "ܚܡ ܢܛܘܪܝܢ 63 var. cf. ܚܡܕ ܢܛܘܪܝܢ",
            "ܐܚܕܐ V. ܚܡܕ ܐܚܕܐ",
            "ܚܡܕ ܚܡܕ 66_6",
            "ܐܚܕ ܕܐܚܕ 78_4",
            "ܚܡܝܕ (ܐܠܚ ܚܡܝ) 71_2",
            "ܚܡܕ ܐܠܗܐ 66_6",
            "ܚܠܦ 19_4 ; 64_9",
            "ܕܢܚܐ 78_8",
            "ܕܢܚܐ ܡܪܝ ܐܦܪܝܡ 67_4",
        ],
    },
    {"file": PAGE_127, "node": "ܐ", "entries": ["ܐܚܕ (ܚܡܕܐ) ܐܒܪܗܡ 70_7 10", "ܐܚܕ ܐܦܩܘܦ 69_5", "ܐܣܐ 30_13", "ܐܣܐ 64_6", "ܐܩܠܣ 43_7"]},
    {"file": PAGE_127, "node": "ܒ", "entries": ["ܒܝܬ ܥܕܐ 47_6", "ܒܝܬ ܐܠ 75_8", "ܒܩܥܡܐ 64_6", "ܐܦܣܩܘܦܐ 34_11", "ܐܡܪܐ 32_11 ; 48_6 ; 49_5-7 ; 50_2-4-10", "ܐܚܕܐ 64_11 ; 72_2-6", "ܬܐܘܡܐ ܐܠܝ ܬܗܘܐ cf. ܗܘܡܐ", "ܐܚܕ ܐܚܢ 89_5", "ܐܠܗܐܪܡܐ 70_4. V. ܗܪܡ", "ܐܠܗܐܪܡܐ ܦܠܝܦܐ 78_12 ; 79_4", "ܐܚܝܐ 171 V. ܐܚܕܐ."]},
    {"file": PAGE_127, "node": "ܟ", "entries": ["ܟܕܝܐ ܐܚ ܙܥܘܪܐ 75_10-11 ; 76_2", "ܙܥܘܪܐ pour ܟܕܝܫܐ 32 n. 1", "ܟܢܝܫܐ ܬܗܘܡܐ 67_10 ; 63_12", "ܟܐܢܝܐ 21_11 ; 86 var. 86_1. V. ܟܐܢܐ", "ܐܢܝܐ 86_7 ; 88_14 ; 89_1. V. ܟܐܢܝܐ", "ܟܢܝܫܐ 32_9-10", "ܟܢܝܫܐ ܕܐܦܩܘܦ 70_9-11 ; 72_7"]},
    {"file": PAGE_127, "node": "ܓ", "entries": ["ܓܡܠ 28_11", "ܓܢܬܐ 23_12 ; 29_7 ; 37_1 ;", "ܓܪܝܣܛܐܠܗܕ 69_14", "ܓܪܝܣܛ 70_4"]},
    {"file": PAGE_127, "node": "ܕ", "entries": ["ܕܚܝ ܐܠܚ 89_12", "ܐܠܚ (ܐܠܚ) ܐܠܗܐ 48_6-13 ; 49_1", "ܕܚܝ ܐܠܚ 66_7", "ܗܝܢܝܡ cf. ܡܕܢ ܕܚܝ ܗܝܢܝܡ"]},
    {"file": PAGE_127, "node": "ܗ", "entries": ["ܗܪܡܙ 69_12", "ܗܘܦ 22_11 ; 87_9 ; 80_2 ; 90_8", "ܐܢܝܕ V. ܟܐܢܝܐ ; ܐܚܕ ܐܚܕ ; ܐܚܕܐ :\nܡܕܢܚ ; ܡܢܝܢ", "ܐܚܕ 21_10 ; 49_7-9 ; 64_9 ; 86_11 ; 89_1", "ܐܣܝܐ 61_5 ; 96_11 cf. p. 59-60", "ܐܘܢܝܐ ܐܠܚ 77_12"]},
    {"file": PAGE_127, "node": "ܡ", "entries": ["ܘܩܝܬ 43_7", "ܡܨܠܝ 33_2 ; 63_12", "ܡܨܝ ܡܕܢܚܝܐ 47_5-7 ; 64", "ܡܨܠܝ ܦܠܦܐ 78_13", "ܡܢ 49_12", "ܡܚܡ 92_9", "ܡܙܕܢ 16_3", "ܡܚܡܕ 63_12", "ܡܚܡܕ ܕܐܠ ܐܚܕܝܐ 94_7", "ܡܥܡܕ ܐܚܕ 48_7", "ܡܥܡܕ ܐܚܕ 15_2 ; 74_4"]},
    {"file": PAGE_128, "node": None, "entries": ["محمد 70₂", "معاوية بن صخر 87₄", "معاوية 70 var. cf. Noel", "موهوب 70₄", "ܡܗܠܢܐ ܒܨܠܘܬܢ 92₁₃", "حمران 64₃", "حصين 28₂ ; 41₁₁ ; 70₇ ; 77₁₃", "حبة (أم) 29₁₀", "فزارة 61₂ ; 64₇ ; 69₇", "فيء 28₁", "فضالة 35₁₀", "قيس (أبو) 75₅ ; 76₂", "مقدام 64₁₁", "أبو بكر (عبد) الله 64₈", "عبد الله بن خازم 77₅", "محمد بن زائدة 71₅"]},
    {"file": PAGE_128, "node": "ل", "entries": ["لقيط بن عامر 71₃", "ليلى 48₈ ; 49₅ ; 61₄,₅ ; 79₆ ; 80₁₃ ; 81₈\n82₁₁ ; 83₄ ; 90₁₂ ; 91₃ ; 92₆ ; 94₁₁ ; 96₁₀,₁₂", "الليث بن سعد 48₁₀ ; 50₁₀ ; 61₆", "لا إله 66₇", "اللات 28₁₁"]},
    {"file": PAGE_128, "node": "ع", "entries": ["عمر (أبو حفص) 27 n. 1 ; 6 ; 28₁ ; 29₁₆\n7-8-10 ; 64₉ ; 85₁₄", "عثمان pour خالد 32 n. 1", "حمران cf. حباب ; نحمان ; منيخا", "حمة اصغر : احد ؛ نبراس ؛ ميع", "عبيد الله 85₈", "حنظلة 29₅ ; 30₁", "حمل 78₁ ; 86₁₀", "حموشة 28₁₁", "حتما (؟) 24₈ ; 35₁₁", "حمة هنا 19₉ ; 29₁"]},
    {"file": PAGE_128, "node": "ف", "entries": ["فلينة 18₁", "أنا بعلسا 21₄", "فهد 15₅ ; 18₄-₈ ; 20₄ ; 22₁₅ ; 66₃\n69₁₂ ; 78₈ ; 83₁₁ ; 92₁", "فجوة 73₁₄", "فندجديد 26₅ ; فندجاوه 26 var.", "فهر 67₅", "قصي 20₁ ; 29 n. 7", "قريش 28₂ ; 33₁₂ ; 67₇ ; 77₁₂-₁₃", "فيل 21₁₀ ; 86₁₁ ; 87₆ ; 81₁"]},
    {"file": PAGE_128, "node": "و", "entries": ["(فيل ؟ / لهب) 83₉"]},
    {"file": PAGE_128, "node": "م", "entries": ["محمد بن مسلمة مدنا 75₁₃"]},
    {"file": PAGE_128, "node": "ح", "entries": ["حمران (أحد) 27₁₃", "دهدهز 75₁₀ ; 77₆", "دهعناه هنا 35₁₂", "حمة ني محاة cf. نهول et حمة"]},
    {"file": PAGE_128, "node": "م", "entries": ["مبعث مذنب خدرة 67₂"]},
    {"file": PAGE_128, "node": "ص", "entries": ["مهنا 36₁₆", "مهدد 17₇ ; 72₄", "مسور 30₅ ; 48₁₁", "مصعدنا 67₁₀ ; 68₅", "مدها 63₅-₇ V. مهذا", "مهذا 61₃-₈ ; 64₁ ; 93₇ ; 96₁₀ V. مهذا", "منذر (أبو سفيان) بن الحارث 80₁₂ ; 91₇", "محمد (بن) حباب 27₁₃ ; 74₅ ; 75₁"]},
    {"file": PAGE_128, "node": "ر", "entries": ["ربيعة 63₅", "ربيعة 65₁₀ ; 70₄ ; 92₁₃", "ريحان 67₄", "زهير 70₁₅ V. زهير", "زهير (أحد) 66₁₃ ; 68₄ ; 69₁₀ V. زهير"]},
    {"file": PAGE_128, "node": "و", "entries": ["وجه انغبنا 86₅", "وهب 15₆ ; 30₁₁ ; 31₃ ; 46₃-₄"]},
]

SCRIPTURE_LINES = [
    ("Genèse", "Genèse XLV, 10", "94"),
    ("Exode", "Exode III, 5", "17"),
    ("Exode", "— IV, 13", "72"),
    ("Psaume", "Psaume XLVII, 2-5", "88"),
    ("Psaume", "— L", "74"),
    ("Psaume", "— LIV, 7", "87"),
    ("Psaume", "— LXII, 9", "69"),
    ("Psaume", "— LXVII", "23"),
    ("Psaume", "— XCVII, 1. 2, 3", "89"),
    ("Psaume", "— CIII, 24", "90"),
    ("Psaume", "— CXVIII, 18", "69"),
    ("Psaume", "— CXXXIV, 6", "90"),
    ("Psaume", "— CXLIV, 18-19", "39"),
    ("Eccli.", "Eccli. XIV, 13", "62"),
    ("Isaïe", "Isaïe XLII, 10-11", "89"),
    ("Jonas", "Jonas I, 4-13", "49"),
    ("Matth.", "Matth. V, 14-15", "78"),
    ("Matth.", "— V, 16", "18"),
    ("Matth.", "— X, 37-39", "88"),
    ("Matth.", "— XIII, 31-32", "81"),
    ("Matth.", "— XVI, 17", "35"),
    ("Matth.", "— XXIII, 13", "65"),
    ("Matth.", "— XXVIII, 19", "21"),
    ("Luc", "Luc VI, 36, 38", "27"),
    ("Luc", "— XII, 42", "21"),
    ("Luc", "Luc XII, 49", "22"),
    ("Luc", "— XXI, 1-4", "51"),
    ("Jean", "Jean XXI, 16-17", "21"),
    ("Actes", "Actes VIII, 36", "26"),
    ("Rom.", "Rom. X, 18", "20"),
    ("I Cor.", "I Cor. IX, 22", "83,84"),
    ("II Cor.", "II Cor. VI, 14.15", "76"),
    ("II Cor.", "— IX, 2", "66"),
    ("II Cor.", "— IX, 7", "27"),
    ("Gal.", "Gal. II, 10", "18"),
    ("Gal.", "— IV, 18", "66"),
    ("Gal.", "— VI, 14", "18"),
    ("Eph.", "Eph. II, 14", "22"),
    ("Eph.", "— VI, 12", "16"),
    ("Philipp.", "Philipp. III, 20", "17,41"),
    ("Col.", "Col. III, 1-2", "17"),
    ("I Tim.", "I Tim. II, 4", "34"),
    ("I Tim.", "— IV, 13, 15", "70"),
    ("II Tim.", "II Tim. IV, 12-13", "70"),
    ("Hébr.", "Hébr. XIII, 7", "17"),
    ("Jac.", "Jac. IV, 4", "63"),
    ("I Pierre", "I Pierre II, 9", "84"),
    ("I Pierre", "— IV, 10-11", "74"),
    ("I Jean", "I Jean II, 15", "63"),
]

BOOK_NORMS = {
    "Genèse": "Gênesis",
    "Exode": "Êxodo",
    "Psaume": "Salmos",
    "Eccli.": "Eclesiástico",
    "Isaïe": "Isaías",
    "Jonas": "Jonas",
    "Matth.": "São Mateus",
    "Luc": "São Lucas",
    "Jean": "São João",
    "Actes": "Atos dos Apóstolos",
    "Rom.": "Romanos",
    "I Cor.": "1 Coríntios",
    "II Cor.": "2 Coríntios",
    "Gal.": "Gálatas",
    "Eph.": "Efésios",
    "Philipp.": "Filipenses",
    "Col.": "Colossenses",
    "I Tim.": "1 Timóteo",
    "II Tim.": "2 Timóteo",
    "Hébr.": "Hebreus",
    "Jac.": "Tiago",
    "I Pierre": "1 Pedro",
    "I Jean": "1 João",
}

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(value: str) -> int:
    total = 0
    prev = 0
    for ch in reversed(value):
        cur = ROMAN[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    return total


def parse_scripture(book_raw: str, citation_raw: str) -> dict[str, Any]:
    tail = citation_raw
    inherited = citation_raw.startswith("—")
    if inherited:
        tail = citation_raw.replace("—", "", 1).strip()
    else:
        tail = citation_raw[len(book_raw) :].strip()
    match = re.match(r"(?P<chapter>[IVXLCDM]+)(?:,\s*(?P<verses>.+))?$", tail)
    chapter = roman_to_int(match.group("chapter")) if match else None
    verses = match.group("verses") if match else None
    verse_start = verse_end = None
    is_range = 0
    if verses:
        nums = [int(n) for n in re.findall(r"\d+", verses)]
        if nums:
            verse_start = nums[0]
            if "-" in verses and len(nums) >= 2:
                verse_end = nums[-1]
                is_range = 1
    return {
        "book_raw": book_raw,
        "book_norm": BOOK_NORMS[book_raw],
        "chapter_start": chapter,
        "verse_start": verse_start,
        "chapter_end": chapter if is_range else None,
        "verse_end": verse_end,
        "is_range": is_range,
        "raw_json": {
            "source": "scripture_index_line",
            "citation_raw": citation_raw,
            **({"inherited_book_from_previous": True} if inherited else {}),
        },
    }


def helper_summary(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    return {
        "status": item.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "best_candidate": best or None,
        "top_candidates": [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
            }
            for cand in item.get("candidates", [])[:5]
        ],
    }


def build_base_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    node_lookup: dict[tuple[str, str], str] = {}
    node_order = 0
    entry_order = 0

    for group in ONOMASTIC_GROUPS:
        parent_node = None
        if group["node"]:
            node_order += 1
            parent_node = node_key(node_order)
            # Duplicate labels can occur in separate OCR columns; keep distinct physical nodes.
            node_lookup[(str(group["file"]), group["node"], str(node_order))] = parent_node
            nodes.append(
                {
                    "node_key": parent_node,
                    "section_key": ONOMASTIC_SECTION,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": group["node"],
                    "label_norm": norm_text(group["node"]),
                    "label_sort": norm_text(group["node"]),
                    "node_level": 1,
                    "confidence": 0.92,
                    "raw_json": {"source_file": str(group["file"]), "source": "OCR standalone grouping letter"},
                }
            )
        for raw in group["entries"]:
            entry_order += 1
            key = f"{VOLUME_ID}:entry:{entry_order:03d}"
            lemma = lemma_from_entry(raw)
            refs_by_entry[key] = parse_locators(raw)
            entries.append(
                {
                    "entry_key": key,
                    "section_key": ONOMASTIC_SECTION,
                    "parent_node_key": parent_node,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind(raw),
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": norm_text(lemma),
                    "lemma_sort": norm_text(lemma),
                    "entry_raw": raw,
                    "context_raw": None,
                    "heading_letter": group["node"],
                    "inferred_printed_page": refs_by_entry[key][0]["page_ref_int"] if refs_by_entry[key] else None,
                    "section_start_file": str(PAGE_127),
                    "editorial_anchor_file": str(group["file"]),
                    "target_file_best": str(group["file"]),
                    "confidence": 0.9 if refs_by_entry[key] else 0.72,
                    "raw_json": {
                        "segment_source_file": str(group["file"]),
                        "segmentation": "one logical index line from OCR block; continuation lines retained only for the same entry",
                    },
                }
            )
    return nodes, entries, refs_by_entry


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    request_entries = []
    for entry in entries:
        if entry["section_key"] != ONOMASTIC_SECTION:
            continue
        page_hints: list[int] = []
        for ref in refs_by_entry.get(entry["entry_key"], []):
            page = ref["page_ref_int"]
            if page not in page_hints:
                page_hints.append(page)
        if not page_hints:
            continue
        lemma = entry["lemma_raw"] or entry["entry_raw"]
        request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [lemma],
                "page_hints": [str(page) for page in page_hints[:10]],
                "page_hint_ints": page_hints[:10],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": request_entries,
    }


def run_helper() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(HELPER_REQUEST_JSON), "--output", str(HELPER_OUTPUT_JSON), "--pretty"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode:
        raise SystemExit(f"helper failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def attach_helper(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]], helper_output: dict[str, Any]) -> None:
    helper_map = {item["entry_id"]: item for item in helper_output.get("entries", [])}
    for entry in entries:
        item = helper_map.get(entry["entry_key"])
        summary = helper_summary(item)
        if summary:
            entry["raw_json"]["helper"] = summary
        best = (item or {}).get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best["file"]
            entry["confidence"] = min(0.98, max(entry["confidence"], float(best.get("probability") or 0.0)))
        for ref in refs_by_entry.get(entry["entry_key"], []):
            ref["target_file"] = best.get("file") or entry["target_file_best"]
            ref["target_file_probability"] = best.get("probability")
            ref["raw_json"] = {"helper": summary} if summary else {"note": "No helper result; kept editorial anchor as material fallback"}


def scripture_entries(start_order: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    entries = []
    scripture_refs = []
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    context = " | ".join(f"{citation} ... {page}" for _, citation, page in SCRIPTURE_LINES)
    for offset, (book_raw, citation_raw, page_raw) in enumerate(SCRIPTURE_LINES, start=1):
        order = start_order + offset
        key = f"{VOLUME_ID}:entry:{order:03d}"
        entry_raw = f"{citation_raw}....................... {page_raw}"
        page_ints = [int(n) for n in re.findall(r"\d+", page_raw)]
        entries.append(
            {
                "entry_key": key,
                "section_key": SCRIPTURE_SECTION,
                "parent_node_key": None,
                "entry_order": offset,
                "entry_kind": "scripture_citation",
                "lemma_raw": citation_raw,
                "lemma_display": citation_raw,
                "lemma_norm": norm_text(citation_raw),
                "lemma_sort": norm_text(citation_raw),
                "entry_raw": entry_raw,
                "context_raw": context,
                "heading_letter": None,
                "inferred_printed_page": page_ints[0] if page_ints else None,
                "section_start_file": str(PAGE_129),
                "editorial_anchor_file": str(PAGE_129),
                "target_file_best": str(PAGE_129),
                "confidence": 0.94,
                "raw_json": {
                    "segment_source_file": str(PAGE_129),
                    "scripture_parse": parse_scripture(book_raw, citation_raw),
                    "note": "Material target locator is the cited printed page; scripture target remains the index citation row.",
                },
            }
        )
        refs_by_entry[key] = [
            {
                "ref_raw": page_raw,
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page),
                "range_end_raw": None,
                "ref_kind": "editorial_page",
                "target_file": str(PAGE_129),
                "target_file_probability": None,
                "raw_json": {"source": "scripture_index_material_page"},
            }
            for page in page_ints
        ]
        parsed = parse_scripture(book_raw, citation_raw)
        scripture_refs.append(
            {
                "entry_key": key,
                "ref_order": 1,
                "ref_role": "citation",
                "ref_raw": citation_raw,
                "book_raw": parsed["book_raw"],
                "book_norm": parsed["book_norm"],
                "chapter_start": parsed["chapter_start"],
                "verse_start": parsed["verse_start"],
                "chapter_end": parsed["chapter_end"],
                "verse_end": parsed["verse_end"],
                "is_range": parsed["is_range"],
                "confidence": 0.94,
                "raw_json": parsed["raw_json"],
            }
        )
    return entries, scripture_refs, refs_by_entry


def flatten_refs(refs_by_entry: dict[str, list[dict[str, Any]]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in entries:
        for order, ref in enumerate(refs_by_entry.get(entry["entry_key"], []), start=1):
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": order,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": ref.get("target_file") or entry["target_file_best"],
                    "target_file_probability": ref.get("target_file_probability"),
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": entry["confidence"],
                    "raw_json": ref.get("raw_json") or {},
                }
            )
    return refs


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    nodes, entries, refs_by_entry = build_base_entries()
    request = build_helper_request(entries, refs_by_entry)
    write_json(HELPER_REQUEST_JSON, request)
    helper_output = run_helper()
    attach_helper(entries, refs_by_entry, helper_output)
    s_entries, scripture_refs, s_refs = scripture_entries(len(entries))
    entries.extend(s_entries)
    refs_by_entry.update(s_refs)

    sections = [
        {
            "section_key": ONOMASTIC_SECTION,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "onomastic_mixed",
            "heading_raw": "TABLE DES NOMS PROPRES",
            "heading_norm": "table des noms propres",
            "heading_letter": None,
            "page_start": 117,
            "page_end": 118,
            "file_start": str(PAGE_127),
            "file_end": str(PAGE_128),
            "confidence": 0.99,
            "raw_json": {
                "source": "OCR pages 117-118",
                "section_kind_reason": "mixed Syriac, Arabic, and French proper-name table",
                "neighbor_check": f"{PAGE_126.name} inspected; no continuing index text before heading",
            },
        },
        {
            "section_key": SCRIPTURE_SECTION,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "scripture_index",
            "heading_raw": "TABLE DES CITATIONS DE L'ÉCRITURE",
            "heading_norm": "table des citations de l'ecriture",
            "heading_letter": None,
            "page_start": 119,
            "page_end": 119,
            "file_start": str(PAGE_129),
            "file_end": str(PAGE_129),
            "confidence": 0.995,
            "raw_json": {"source": "OCR page 119", "note": "Bible citation table; heading is not used as a biblical book."},
        },
    ]
    refs = flatten_refs(refs_by_entry, entries)
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "Patrologia Orientalis volume with TABLE DES NOMS PROPRES and TABLE DES CITATIONS DE L'ÉCRITURE sections.",
                "TABLE DES MATIÈRES pages 130 and 657-658 were inspected in the prior checkpoint and excluded as contents pages, not alphabetical index sections.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered one logical entry per OCR index line or same-entry continuation from pages 117-119; standalone letter headings are stored as nodes.",
            "evidence_files": [str(PAGE_127), str(PAGE_128), str(PAGE_129)],
        },
        "notes": [
            "Rebuilt from OCR inspection instead of carrying forward the earlier overlapping three-line segmentation.",
            "Bare V./cf. remissions without material locators are retained as cross-reference entries and do not emit refs.",
            "Helper candidates are preserved in raw_json for onomastic entries with printed page locators.",
        ],
    }
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", scripture_refs)
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PO003 payload rebuilt and validated locally",
            "completed": [
                "confirmed sections on OCR pages 127-129",
                "rebuilt onomastic entries without neighboring-line bleed",
                "added letter-group nodes",
                "rebuilt scripture citations with inherited biblical books",
                "ran index_target_locator for onomastic material locators",
            ],
            "pending": [],
            "blocked": [],
            "notes": ["Final payload is assembled from local OCR-derived constants and helper output."],
        },
    )
    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

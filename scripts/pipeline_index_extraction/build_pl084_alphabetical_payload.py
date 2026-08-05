#!/usr/bin/env python3
"""Usage: build the PL084 alphabetical payload and helper request from the OCR tail.

Run from the repository root, for example:

    python scripts/pipeline_index_extraction/build_pl084_alphabetical_payload.py \
      --source-root /homessddata/Projects/pdfocr/teste/PL084/text \
      --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL084_helper_request.json \
      --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL084_helper_output.json \
      --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL084 \
      --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL084_alphabetical_indices.json

The script extracts the three visible index sections near the tail of PL084:
the canonical-law subject index, the biblical locorum index, and the large
alphabetical index for S. Isidori opera. It preserves OCR literals and keeps
locator reconstruction conservative.
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


VOLUME_ID = "PL084"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, tomo 84"

SECTION1_FILES = list(range(461, 479))
SECTION2_FILES = list(range(481, 495))
SECTION3_FILES = list(range(495, 548))


ROMAN_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
    "XXI": 21,
    "XXII": 22,
    "XXIII": 23,
    "XXIV": 24,
    "XXV": 25,
    "XXVI": 26,
    "XXVII": 27,
    "XXVIII": 28,
    "XXIX": 29,
    "XXX": 30,
    "XXXI": 31,
    "XXXII": 32,
    "XXXIII": 33,
    "XXXIV": 34,
    "XXXV": 35,
    "XXXVI": 36,
    "XXXVII": 37,
    "XXXVIII": 38,
    "XXXIX": 39,
    "XL": 40,
    "XLI": 41,
    "XLII": 42,
    "XLIII": 43,
    "XLIV": 44,
    "XLV": 45,
    "XLVI": 46,
    "XLVII": 47,
    "XLVIII": 48,
    "XLIX": 49,
    "L": 50,
    "LI": 51,
    "LII": 52,
    "LIII": 53,
    "LIV": 54,
    "LV": 55,
    "LVI": 56,
    "LVII": 57,
    "LVIII": 58,
    "LIX": 59,
    "LX": 60,
    "LXI": 61,
    "LXII": 62,
    "LXIII": 63,
    "LXIV": 64,
    "LXV": 65,
    "LXVI": 66,
    "LXVII": 67,
    "LXVIII": 68,
    "LXIX": 69,
    "LXX": 70,
    "LXXI": 71,
    "LXXII": 72,
    "LXXIII": 73,
    "LXXIV": 74,
    "LXXV": 75,
    "LXXVI": 76,
    "LXXVII": 77,
    "LXXVIII": 78,
    "LXXIX": 79,
    "LXXX": 80,
    "LXXXI": 81,
    "LXXXII": 82,
    "LXXXIII": 83,
    "LXXXIV": 84,
    "LXXXV": 85,
    "LXXXVI": 86,
    "LXXXVII": 87,
    "LXXXVIII": 88,
    "LXXXIX": 89,
    "XC": 90,
    "XCI": 91,
    "XCII": 92,
    "XCIII": 93,
    "XCIV": 94,
    "XCV": 95,
    "XCVI": 96,
    "XCVII": 97,
    "XCVIII": 98,
    "XCIX": 99,
    "C": 100,
    "CI": 101,
    "CII": 102,
    "CIII": 103,
    "CIV": 104,
    "CV": 105,
    "CVI": 106,
    "CVII": 107,
    "CVIII": 108,
    "CIX": 109,
    "CX": 110,
    "CXI": 111,
    "CXII": 112,
    "CXIII": 113,
    "CXIV": 114,
    "CXV": 115,
    "CXVI": 116,
    "CXVII": 117,
    "CXVIII": 118,
    "CXIX": 119,
    "CXX": 120,
    "CXXI": 121,
    "CXXII": 122,
    "CXXIII": 123,
    "CXXIV": 124,
    "CXXV": 125,
    "CXXVI": 126,
    "CXXVII": 127,
    "CXXVIII": 128,
}

INDEX_HEADING_RE = re.compile(r"INDEX\s+RERUM|INDEX\s+S\.\s+SCRIPTUR", re.IGNORECASE)
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒ]$")
BLANK_MARKERS = {"Digitized by Google", "[ilegivel]", "[ilegível]"}
ENTRY_START_RE = re.compile(r"^(?:[A-ZÆŒ][^,]{1,80},|[A-ZÆŒ][^.:]{1,80}\.|CAP\.\s+[IVXLCDM]+\.|GENESIS\.|EXODUS\.|LEVITICUS\.|NUMERI\.|DEUTERONOMIUM\.|JOSUE\.|LIBER\s+JUDICUM\.|RUTH\.|REGUM\s+LIBER\s+[IIVX]+\.|PARALIPOMENON\s+LIBER\s+[IIVX]+\.|ESDRAE\s+LIBER\s+[IIVX]+\.|TOBIAS\.|JOB\.|PS\.|AD\s+[A-ZÁÉÍÓÚÆŒ].*)")
REFERENCE_RE = re.compile(
    r"(?P<vol>\bibid\.?|[IVXLCDM]{1,4}|[ivxlcdm]{1,4})\s*,\s*(?P<page>\d{1,4})(?P<suffix>\s*(?:seq\.|seqq\.|et seq\.))?",
)
ROMAN_HEADER_RE = re.compile(r"\b([IVXLCDM]{1,8})\b")
NUMERIC_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.casefold() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_by_suffix(source_root: Path, suffix: int) -> Path:
    matches = sorted(source_root.glob(f"*-{suffix}.txt"))
    if not matches:
        raise FileNotFoundError(f"No OCR file with suffix {suffix} found in {source_root}")
    return matches[0]


def page_header_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', text, re.DOTALL)
    if not m:
        return ""
    return " ".join(line.strip() for line in m.group(1).splitlines() if line.strip())


def infer_printed_page(path: Path) -> int | None:
    header = page_header_text(path)
    if not header:
        return None
    nums = NUMERIC_HEADER_RE.findall(header)
    if nums:
        return int(nums[0])
    romans = ROMAN_HEADER_RE.findall(header)
    for roman in romans:
        roman = roman.upper()
        if roman in ROMAN_MAP:
            return ROMAN_MAP[roman]
    return None


def block_texts(path: Path, block_types: tuple[str, ...] = ("texto_principal",)) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    pieces: list[str] = []
    for block_type in block_types:
        pattern = re.compile(rf'<bloco tipo="{re.escape(block_type)}"[^>]*>(.*?)</bloco>', re.DOTALL)
        for match in pattern.finditer(text):
            content = match.group(1).replace("\xa0", " ")
            pieces.append(content)
    return pieces


def flatten_lines(texts: list[str]) -> list[str]:
    lines: list[str] = []
    for text in texts:
        for raw in text.splitlines():
            line = norm(raw)
            if not line:
                continue
            if line in BLANK_MARKERS:
                continue
            if line.startswith("<"):
                continue
            lines.append(line)
    return lines


def is_page_header_line(line: str) -> bool:
    if LINE := norm(line):
        if re.fullmatch(r"[IVXLCDM]{1,8}", LINE):
            return True
        if re.fullmatch(r"\d{1,4}", LINE):
            return True
    return False


def is_new_entry_line(line: str, section_kind: str) -> bool:
    if not line or is_page_header_line(line):
        return False
    if LETTER_ONLY_RE.fullmatch(line):
        return False
    if line.startswith("Digitized by Google"):
        return False
    if line.startswith("—") or line.startswith("-"):
        return True
    if section_kind == "scripture_index":
        return bool(
            line.startswith("CAP.")
            or line.startswith("GENESIS.")
            or line.startswith("EXODUS.")
            or line.startswith("LEVITICUS.")
            or line.startswith("NUMERI.")
            or line.startswith("DEUTERONOMIUM.")
            or line.startswith("JOSUE.")
            or line.startswith("LIBER ")
            or line.startswith("RUTH.")
            or line.startswith("REGUM ")
            or line.startswith("PARALIPOMENON ")
            or line.startswith("ESDRAE ")
            or line.startswith("TOBIAS.")
            or line.startswith("JOB.")
            or line.startswith("PS.")
            or line.startswith("AD ")
        )
    return bool(ENTRY_START_RE.match(line))


def normalize_lemma(entry_raw: str, section_kind: str) -> str | None:
    text = norm(entry_raw)
    if not text:
        return None
    if section_kind == "scripture_index":
        if text.startswith("CAP."):
            return text.split("—", 1)[0].strip()
        return text.split(".", 1)[0].strip()
    if "," in text:
        return text.split(",", 1)[0].strip(" .;:")
    if ". " in text:
        return text.split(". ", 1)[0].strip(" .;:")
    return text.strip(" .;:")


def parse_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref_order, match in enumerate(REFERENCE_RE.finditer(entry_raw), start=1):
        vol = match.group("vol")
        page = match.group("page")
        suffix = match.group("suffix") or ""
        ref_raw = f"{vol.strip()} {page}{suffix}".replace("  ", " ").strip()
        vol_clean = vol.strip().lower().rstrip(".")
        page_int = int(page)
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": page,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None if not suffix else suffix.strip(),
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file_probability": 0.96,
                "vol_raw": vol_clean,
            }
        )
    return refs


def merge_lines_into_entries(lines: list[str], section_kind: str) -> list[str]:
    entries: list[str] = []
    buffer: list[str] = []
    seen_heading = False

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        entries.append(" ".join(buffer).strip())
        buffer = []

    for line in lines:
        if line in BLANK_MARKERS:
            continue
        if not seen_heading:
            if INDEX_HEADING_RE.search(line):
                seen_heading = True
            continue
        if line.startswith("Numeri respondent his qui in textu crassiori typo representati sunt"):
            continue
        if line.startswith("QUÆ IN HAC CANONUM COLLECTIONE") or line.startswith("QUI IN OPERIBUS S. ISIDORI HISPALENSIS OCCURRUNT"):
            continue
        if line.startswith("QUÆ SEPTEM TOMIS") or line.startswith("Numerus romanus tomum indicat"):
            continue
        if "videri possunt hoc tom." in line:
            continue
        if is_new_entry_line(line, section_kind) and buffer:
            flush()
        if not buffer:
            buffer.append(line)
        else:
            prev = buffer[-1]
            if prev.endswith(".") and is_new_entry_line(line, section_kind):
                flush()
                buffer.append(line)
            else:
                buffer.append(line)
    flush()
    return entries


def collect_page_entries(path: Path, section_kind: str) -> list[dict[str, Any]]:
    texts = block_texts(path)
    lines = flatten_lines(texts)
    merged = merge_lines_into_entries(lines, section_kind)
    page_no = infer_printed_page(path)
    result: list[dict[str, Any]] = []
    for entry_raw in merged:
        lemma = normalize_lemma(entry_raw, section_kind)
        if not lemma:
            continue
        if len(lemma) == 1 and lemma.isalpha():
            continue
        entry_kind = "lemma"
        if lemma.upper().startswith("VIDE ") or lemma.upper() in {"VIDE", "V.", "CF."}:
            entry_kind = "cross_reference"
        elif "VIDE" in entry_raw[:20].upper() and "," not in entry_raw:
            entry_kind = "cross_reference"
        elif section_kind == "scripture_index":
            entry_kind = "scripture_citation"
        result.append(
            {
                "source_file": str(path),
                "printed_page": page_no,
                "entry_raw": entry_raw,
                "lemma_raw": lemma,
                "entry_kind": entry_kind,
                "refs": parse_refs(entry_raw) if section_kind != "scripture_index" else [],
            }
        )
    return result


def page_range_paths(source_root: Path, suffixes: list[int]) -> list[Path]:
    return [file_by_suffix(source_root, suffix) for suffix in suffixes]


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "pl084_section_canonum_461",
                "lemma_raw": "INDEX RERUM IN CANONUM COLLECTIONE CONTENTARUM.",
                "query_names": [
                    "INDEX RERUM IN CANONUM COLLECTIONE CONTENTARUM",
                    "Abbas",
                    "Accusator"
                ],
                "page_hints": ["913", "914"],
                "page_hint_ints": [913, 914],
                "context_raw": "913 INDEX RERUM IN CANONUM COLLECTIONE CONTENTARUM. 914",
            },
            {
                "entry_id": "pl084_section_scriptura_481",
                "lemma_raw": "INDEX PRÆCIPUORUM SACRÆ SCRIPTURÆ LOCORUM.",
                "query_names": [
                    "INDEX S. SCRIPTURÆ LOCORUM IN OPERIBUS S. ISIDORI OCCURRENTIUM",
                    "GENESIS",
                    "CAP. I"
                ],
                "page_hints": ["19", "20"],
                "page_hint_ints": [19, 20],
                "context_raw": "INDEX PRÆCIPUORUM SACRÆ SCRIPTURÆ LOCORUM. QUI IN OPERIBUS S. ISIDORI HISPALENSIS OCCURRUNT.",
            },
            {
                "entry_id": "pl084_section_isidori_495",
                "lemma_raw": "INDEX RERUM ET VERBORUM IN S. ISIDORI OPP. CONTENTORUM.",
                "query_names": [
                    "INDEX RERUM ET VERBORUM IN S. ISIDORI OPP. CONTENTORUM",
                    "Aaron frater Moysis",
                    "Acolythus",
                ],
                "page_hints": ["29", "30"],
                "page_hint_ints": [29, 30],
                "context_raw": "INDEX RERUM ET VERBORUM IN S. ISIDORI OPP. CONTENTORUM.",
            },
        ],
    }


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
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return read_json(helper_output_json, {})


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        lookup[item.get("entry_id")] = item
    return lookup


def section_payload(
    *,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    page_start: int | None,
    page_end: int | None,
    file_start: Path,
    file_end: Path,
    helper_item: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_json: dict[str, Any] = {
        "section_kind_reason": (
            "Canonical-law subject index recovered from the tail of the volume."
            if section_kind == "alphabetical_general" and "CANONUM" in heading_raw
            else "Scripture locorum index recovered from the tail of the volume."
            if section_kind == "scripture_index"
            else "Main alphabetical index of matters and words with OCR page drift."
        ),
    }
    if helper_item:
        raw_json.update(
            {
                "helper_status": helper_item.get("status"),
                "helper_candidate_role": helper_item.get("candidate_role"),
                "helper_reason_summary": helper_item.get("reason_summary"),
                "helper_best_candidate": helper_item.get("best_candidate"),
            }
        )
    return {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": norm(heading_raw).lower() if heading_raw else None,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": str(file_start),
        "file_end": str(file_end),
        "confidence": 0.95 if helper_item else 0.88,
        "raw_json": raw_json,
    }


def build_payload(source_root: Path, helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_map = helper_lookup(helper_output)
    now = now_iso()

    section1_files = page_range_paths(source_root, SECTION1_FILES)
    section2_files = page_range_paths(source_root, SECTION2_FILES)
    section3_files = page_range_paths(source_root, SECTION3_FILES)

    sections = [
        section_payload(
            section_key=f"{VOLUME_ID}:alpha:alphabetical_general:001",
            section_order=1,
            section_kind="alphabetical_general",
            heading_raw="INDEX RERUM IN CANONUM COLLECTIONE CONTENTARUM.",
            page_start=913,
            page_end=948,
            file_start=section1_files[0],
            file_end=section1_files[-1],
            helper_item=helper_map.get("pl084_section_canonum_461"),
        ),
        section_payload(
            section_key=f"{VOLUME_ID}:alpha:scripture_index:002",
            section_order=2,
            section_kind="scripture_index",
            heading_raw="INDEX PRÆCIPUORUM SACRÆ SCRIPTURÆ LOCORUM. QUI IN OPERIBUS S. ISIDORI HISPALENSIS OCCURRUNT.",
            page_start=19,
            page_end=28,
            file_start=section2_files[0],
            file_end=section2_files[-1],
            helper_item=helper_map.get("pl084_section_scriptura_481"),
        ),
        section_payload(
            section_key=f"{VOLUME_ID}:alpha:alphabetical_general:003",
            section_order=3,
            section_kind="alphabetical_general",
            heading_raw="INDEX RERUM ET VERBORUM IN S. ISIDORI OPP. CONTENTORUM.",
            page_start=29,
            page_end=128,
            file_start=section3_files[0],
            file_end=section3_files[-1],
            helper_item=helper_map.get("pl084_section_isidori_495"),
        ),
    ]

    # Entry recovery is intentionally conservative: we serialize the visible
    # lemma lines from the two alphabetical indexes and a small set of scripture
    # headings, rather than trying to rebuild every cited target volume.
    entries_raw: list[dict[str, Any]] = []
    # Seed the first two sections with one manually verified line each so the
    # payload does not depend entirely on the page-line heuristic.
    entries_raw.append(
        {
            "source_file": str(section1_files[0]),
            "printed_page": infer_printed_page(section1_files[0]),
            "entry_raw": "Abbas. Episcopo suo summam humi- liationem et reverentiam exhibeat. Conc. Emerit., 672.",
            "lemma_raw": "Abbas",
            "entry_kind": "lemma",
            "refs": parse_refs("Abbas. Episcopo suo summam humi- liationem et reverentiam exhibeat. Conc. Emerit., 672."),
        }
    )
    entries_raw.append(
        {
            "source_file": str(section2_files[0]),
            "printed_page": infer_printed_page(section2_files[0]),
            "entry_raw": "GENESIS. CAP. I. - Vers. 1 et 2. In principio fecit Deus cœlum et terram, et spiritus Dei ferebatur super aquas, vi, 11.",
            "lemma_raw": "GENESIS",
            "entry_kind": "scripture_citation",
            "refs": [],
        }
    )
    for path in section1_files:
        entries_raw.extend(collect_page_entries(path, "alphabetical_general"))
    # Section 2 is structurally scripture-oriented and is left mostly at section
    # level in this conservative pass.
    for path in section3_files:
        entries_raw.extend(collect_page_entries(path, "alphabetical_general"))

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    entry_counter = 0
    for item in entries_raw:
        entry_counter += 1
        section_key = (
            f"{VOLUME_ID}:alpha:alphabetical_general:001"
            if int(Path(item["source_file"]).stem.rsplit("-", 1)[-1]) <= 478
            else f"{VOLUME_ID}:alpha:alphabetical_general:003"
        )
        entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": item["entry_kind"],
            "lemma_raw": item["lemma_raw"],
            "lemma_display": item["lemma_raw"],
            "lemma_norm": sort_norm(item["lemma_raw"]),
            "lemma_sort": sort_norm(item["lemma_raw"]),
            "entry_raw": item["entry_raw"],
            "context_raw": item["entry_raw"],
            "heading_letter": item["lemma_raw"][0].upper() if item["lemma_raw"] else None,
            "inferred_printed_page": item["printed_page"],
            "section_start_file": item["source_file"],
            "editorial_anchor_file": item["source_file"],
            "target_file_best": item["source_file"],
            "confidence": 0.88 if item["entry_kind"] == "lemma" else 0.74,
            "raw_json": {
                "source_file": item["source_file"],
                "section_kind": "alphabetical_general",
                "note": "Recovered conservatively from OCR lines; refs are retained as printed without expansion.",
            },
        }
        entries.append(entry)

        for ref_item in item["refs"]:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_item["ref_order"],
                    "ref_kind": ref_item["ref_kind"],
                    "ref_raw": ref_item["ref_raw"],
                    "page_ref_raw": ref_item["page_ref_raw"],
                    "page_ref_int": ref_item["page_ref_int"],
                    "page_ref_col": ref_item["page_ref_col"],
                    "line_ref_raw": ref_item["line_ref_raw"],
                    "range_start_raw": ref_item["range_start_raw"],
                    "range_end_raw": ref_item["range_end_raw"],
                    "target_file": item["source_file"],
                    "target_file_probability": ref_item["target_file_probability"],
                    "section_start_file": item["source_file"],
                    "editorial_anchor_file": item["source_file"],
                    "confidence": 0.9,
                    "raw_json": {},
                }
            )

    # Preserve a minimal scripture section footprint in notes/coverage.
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the visible alphabetical index lines from the two subject indexes; "
            "the scripture locorum block is present and anchored as a section, but it was "
            "not fully serialized in this pass to avoid inventing a large verse-level parse."
        ),
        "evidence_files": [
            str(section1_files[0]),
            str(section2_files[0]),
            str(section3_files[0]),
            str(section3_files[-1]),
        ],
    }

    notes = [
        "Section 1: canonical-law subject index (A-to-B visible tail).",
        "Section 2: scripture locorum index anchored but only described conservatively at section level.",
        "Section 3: main alphabetical index of S. Isidori works recovered from OCR lines in the tail pages.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Tail volume containing canonical-law, scripture, and Isidore alphabetical indexes.",
    }

    return {
        "schema_version": 1,
        "generated_at": now,
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL084 alphabetical payload from OCR tail pages.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Recover alphabetical tail indexes and keep scripture block conservative.",
        "completed": [
            "section headings identified",
            "OCR tail pages inspected",
        ],
        "pending": [
            "run helper on the section anchors",
            "assemble final JSON payload",
        ],
        "blocked": [
            "full verse-level scripture serialization deferred in this pass",
        ],
        "notes": [
            "Use current source_root only.",
            "Keep OCR literals and avoid expanding ibid. or seq. beyond the printed form.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)
    write_json(args.intermediate_dir / "volume.json", {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
    })

    helper_request = build_helper_request(args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(args.source_root, helper_output)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

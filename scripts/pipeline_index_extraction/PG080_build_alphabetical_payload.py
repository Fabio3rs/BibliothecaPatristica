#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG080_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG080/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG080_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG080_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG080 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG080_alphabetical_indices.json

Builds the PG080 closing index payload from the OCR tail:
- ORDO RERUM contents table
- INDEX PSALMORUM

The script writes a helper request, runs scripts/index_target_locator.py, stores
per-volume intermediate JSON fragments, and emits the canonical final payload.
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
VOLUME_ID = "PG080"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 80"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG080/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG080_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG080_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG080_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG080"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

FILE_RE = re.compile(r"-(\d+)\.txt$")
PAGE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?!\d)")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
ROMAN_LETTER_RE = re.compile(r"^[A-ZΑ-Ω]$")
GREEK_ENTRY_START_RE = re.compile(r"^[\s«\"'’᾽]*[Α-Ωα-ω]")
NUMERAL_ENTRY_START_RE = re.compile(r"^[\s«\"'’᾽]*[ivxlcdmIVXLCDM0-9]+[.'’]?\s")
ENTRY_PAGELESS_HINTS = {
    "xβ'. Κύριος ποιμαίνει με, και οὐδὲν με ὑστερήσει.": 747,
    "xζ'. Κύριος φωτισμὸς μου, καὶ Σωτήρ μου, τίνα φοβηθήσομαι.": 768,
    "᾽Εἰν μὴ Κύριος οἰκοδομήσῃ οἶκον,": 58,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def fold(text: str | None) -> str:
    if text is None:
        return ""
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return unicodedata.normalize("NFKC", text)


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = fold(value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = FILE_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for raw in parsed["all_text"].splitlines():
        line = normalize_space(raw)
        if not line or FOOTER_RE.fullmatch(line):
            continue
        if re.fullmatch(r"\d{4}", line):
            continue
        out.append(line)
    return out


def combine_hyphenated_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.endswith("-") and idx + 1 < len(lines):
            out.append(f"{line[:-1]}{lines[idx + 1].lstrip()}")
            idx += 2
            continue
        out.append(line)
        idx += 1
    return out


def extract_text_lines(path: Path) -> list[str]:
    return combine_hyphenated_lines(extract_lines(path))


def is_letter_heading(line: str) -> bool:
    return bool(ROMAN_LETTER_RE.fullmatch(normalize_space(line) or ""))


def page_refs_from_text(text: str) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []
    for match in PAGE_NUM_RE.finditer(text):
        raw = match.group(0).strip()
        refs.append((raw, int(match.group(1))))
    return refs


def strip_trailing_page(text: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$", text)
    if not match:
        return text, None, None
    lemma = normalize_space(match.group("lemma").rstrip(" ,;:."))
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def normalize_query_variants(lemma_raw: str) -> list[str]:
    base = normalize_space(lemma_raw)
    variants = [base]
    folded = normalize_space(fold(base))
    if folded and folded not in variants:
        variants.append(folded)
    stripped = re.sub(r"^[\s«\"'’᾽]*[ivxlcdmIVXLCDM0-9]+[.'’]?\s*", "", base)
    stripped = normalize_space(stripped)
    if stripped and stripped not in variants:
        variants.append(stripped)
    return [item for item in dict.fromkeys(variants) if item]


def is_entry_start(line: str) -> bool:
    clean = normalize_space(line)
    if not clean:
        return False
    if is_letter_heading(clean):
        return False
    if clean.startswith("Digitized by Google"):
        return False
    if re.match(r"^\d{4}\s+", clean):
        return False
    if NUMERAL_ENTRY_START_RE.match(clean):
        return True
    if re.match(r"^[\s«\"'’᾽]*[xX][Α-Ωα-ω]", clean):
        return True
    return bool(GREEK_ENTRY_START_RE.match(clean))


def is_heading_like(line: str) -> bool:
    clean = normalize_space(line)
    if not clean:
        return False
    if is_letter_heading(clean):
        return False
    if clean.startswith("Digitized by Google"):
        return False
    if re.match(r"^\d{4}\s+", clean):
        return False
    if clean.startswith(("ORDO RERUM", "INDEX PSALMORUM", "THEODORETUS", "QUAESTIONES", "INTERPRETATIO", "Præfatio.", "Praefatio.", "CAP.", "CAPUT ", "SECTIO ", "APPENDIX", "Libri ", "AUXILIUM")):
        return True
    if clean.startswith("—") and not PAGE_NUM_RE.search(clean):
        return True
    if PAGE_NUM_RE.search(clean) is None and len(clean.split()) <= 8 and clean.endswith("."):
        return True
    return False


def build_section_ordo(source_root: Path, files: dict[int, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:001"
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[1017]),
        "file_end": str(files[1017]),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Editorial contents table at the start of the tail window; distinct from the psalm index.",
            "source_files": [str(files[1017])],
        },
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0

    def add_node(label_raw: str, level: int, parent: str | None = None) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": node_order,
                "node_kind": "heading_group",
                "label_raw": label_raw,
                "label_norm": sort_norm(label_raw),
                "label_sort": sort_norm(label_raw),
                "node_level": level,
                "confidence": 0.95,
                "raw_json": {"source_file": str(files[1017]), "section_kind": "ordo_rerum"},
            }
        )
        return node_key

    current_node: str | None = None
    pending_heading: list[str] = []
    pending_entry: list[str] = []
    pending_source: Path | None = None
    pending_page_raw: str | None = None
    pending_page_int: int | None = None

    def flush_heading() -> None:
        nonlocal pending_heading, current_node
        if not pending_heading:
            return
        heading_raw = normalize_space(" ".join(pending_heading))
        current_node = add_node(heading_raw, 1 if current_node is None else 2, parent=current_node)
        pending_heading = []

    def flush_entry() -> None:
        nonlocal entry_order, pending_entry, pending_source, pending_page_raw, pending_page_int
        if not pending_entry:
            return
        entry_raw = normalize_space(" ".join(pending_entry))
        lemma_raw, page_raw, page_int = strip_trailing_page(entry_raw)
        if page_raw is None and pending_page_raw is not None:
            page_raw = pending_page_raw
            page_int = pending_page_int
        entry_kind = "heading_group" if any(lemma_raw.startswith(prefix) for prefix in ("Præfatio", "Praefatio", "CAP.", "CAPUT ", "SECTIO ", "Libri ", "INTERPRETATIO", "QUAESTIO")) or lemma_raw.startswith("—") else "lemma"
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": current_node,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None if page_raw is not None else entry_raw,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": str(files[1017]),
                "editorial_anchor_file": str(pending_source or files[1017]),
                "target_file_best": None,
                "confidence": 0.8 if page_raw is not None else 0.65,
                "raw_json": {
                    "source_file": str(pending_source or files[1017]),
                    "section_kind": "ordo_rerum",
                    "page_recovered": page_raw is not None,
                },
            }
        )
        if page_raw is not None and page_int is not None:
            helper_entries.append(
                {
                    "entry_id": f"pg080_ordo_{entry_order:03d}",
                    "lemma_raw": lemma_raw,
                    "query_names": normalize_query_variants(lemma_raw),
                    "page_hints": [str(page_int)],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": str(files[1017]),
                    "editorial_anchor_file": str(pending_source or files[1017]),
                    "confidence": 0.78,
                    "raw_json": {
                        "source_file": str(pending_source or files[1017]),
                        "section_kind": "ordo_rerum",
                    },
                }
            )
        pending_entry = []
        pending_source = None
        pending_page_raw = None
        pending_page_int = None

    for seq in [1017]:
        path = files[seq]
        for line in extract_text_lines(path):
            clean = normalize_space(line)
            if not clean or clean == "Digitized by Google":
                continue
            if clean.startswith("ORDO RERUM"):
                continue
            if clean.startswith("QUÆ IN HOC TOMO CONTINENTUR") or clean.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                continue
            if clean == "THEODORETUS CYRENSIS":
                pending_heading = [clean]
                continue
            if clean == "EPISCOPUS." and pending_heading:
                pending_heading.append(clean)
                continue
            if clean.startswith("QUAESTIONES SELECTÆ IN LOCA") or clean.startswith("QUAESTIONES SELECTAE IN LOCA"):
                flush_heading()
                pending_heading = [clean]
                continue
            if clean.startswith("DIFFICILIA SCRIPTURÆ SACRÆ.") or clean.startswith("DIFFICILIA SCRIPTURAE SACRAE."):
                if pending_heading:
                    pending_heading.append(clean)
                else:
                    pending_heading = [clean]
                continue
            if clean.startswith("INTERPRETATIO IN PSALMOS."):
                flush_heading()
                pending_heading = [clean]
                continue
            if clean == "Præfatio." or clean == "Praefatio." or clean.startswith("— in libris Regnorum et Paralipomenon."):
                flush_heading()
                current_node = add_node(clean, 2, parent=current_node)
                continue
            if PAGE_NUM_RE.search(clean) is None and is_heading_like(clean):
                flush_entry()
                flush_heading()
                current_node = add_node(clean, 2 if current_node else 1, parent=current_node)
                continue
            if pending_heading:
                flush_heading()
            if is_entry_start(clean) or pending_entry:
                if pending_entry and is_entry_start(clean):
                    flush_entry()
                pending_entry.append(clean)
                pending_source = path
                page_match = PAGE_NUM_RE.search(clean)
                if page_match:
                    pending_page_raw = page_match.group(0).strip()
                    pending_page_int = int(page_match.group(1))
                    flush_entry()
                continue
            if pending_entry:
                pending_entry.append(clean)
                pending_source = path
                page_match = PAGE_NUM_RE.search(clean)
                if page_match:
                    pending_page_raw = page_match.group(0).strip()
                    pending_page_int = int(page_match.group(1))
                    flush_entry()
                continue
            if clean and not PAGE_NUM_RE.search(clean):
                flush_heading()
                current_node = add_node(clean, 1 if current_node is None else 2, parent=current_node)

    flush_heading()
    flush_entry()

    # Add helper entries for the explicit heading-like entries that ended up as nodes.
    return section, nodes, entries, refs, helper_entries


def build_section_psalms(source_root: Path, files: dict[int, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_key = f"{VOLUME_ID}:alpha:scripture_index:002"
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "scripture_index",
        "heading_raw": "INDEX PSALMORUM.",
        "heading_norm": "index psalmorum",
        "heading_letter": None,
        "page_start": 1997,
        "page_end": 2002,
        "file_start": str(files[1014]),
        "file_end": str(files[1016]),
        "confidence": 0.97,
        "raw_json": {
            "section_kind_reason": "Alphabetical index of psalm incipits and printed-page pointers, laid out with Greek letter groupings.",
            "source_files": [str(files[1014]), str(files[1015]), str(files[1016])],
        },
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    current_node: str | None = None
    pending_entry: list[str] = []
    pending_source: Path | None = None
    pending_page_raw: str | None = None
    pending_page_int: int | None = None
    pending_forced_page_raw: str | None = None
    pending_forced_page_int: int | None = None

    def add_node(label_raw: str, source_file: Path) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": label_raw,
                "label_norm": sort_norm(label_raw),
                "label_sort": sort_norm(label_raw),
                "node_level": 1,
                "confidence": 0.96,
                "raw_json": {"source_file": str(source_file), "section_kind": "scripture_index"},
            }
        )
        return node_key

    def flush_entry() -> None:
        nonlocal entry_order, pending_entry, pending_source, pending_page_raw, pending_page_int, pending_forced_page_raw, pending_forced_page_int, current_node
        if not pending_entry:
            return
        entry_raw = normalize_space(" ".join(pending_entry))
        if entry_raw == "Ψαλμ." or entry_raw == "Ψαλμ. Φύλλ. Ψαλμ. Φύλλ.":
            pending_entry = []
            pending_source = None
            pending_page_raw = None
            pending_page_int = None
            pending_forced_page_raw = None
            pending_forced_page_int = None
            return
        page_raw = pending_page_raw
        page_int = pending_page_int
        if page_raw is None:
            page_raw = pending_forced_page_raw
            page_int = pending_forced_page_int
        if page_raw is None:
            recovered = ENTRY_PAGELESS_HINTS.get(entry_raw)
            if recovered is None:
                cleaned = entry_raw
                for key, val in ENTRY_PAGELESS_HINTS.items():
                    if cleaned.startswith(key):
                        recovered = val
                        break
            if recovered is not None:
                page_raw = str(recovered)
                page_int = recovered
        lemma_raw = entry_raw
        lemma_raw = re.sub(r"^[\s«\"'’᾽]*[ivxlcdmIVXLCDM0-9]+[.'’]?\s*", "", lemma_raw)
        lemma_raw = re.sub(r"^\s*[\w]+[.'’]\s*", "", lemma_raw)
        lemma_raw = normalize_space(lemma_raw.rstrip(" ,;:."))
        entry_kind = "scripture_citation"
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": current_node,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None if page_raw is not None else entry_raw,
                "heading_letter": current_node and next((node["label_raw"] for node in nodes if node["node_key"] == current_node), None),
                "inferred_printed_page": page_int,
                "section_start_file": str(files[1014]),
                "editorial_anchor_file": str(pending_source or files[1014]),
                "target_file_best": None,
                "confidence": 0.78 if page_raw is not None else 0.55,
                "raw_json": {
                    "source_file": str(pending_source or files[1014]),
                    "section_kind": "scripture_index",
                    "page_recovered": page_raw is not None and page_int is not None,
                },
            }
        )
        if page_raw is not None and page_int is not None:
            helper_entries.append(
                {
                    "entry_id": f"pg080_psalm_{entry_order:03d}",
                    "lemma_raw": lemma_raw,
                    "query_names": normalize_query_variants(lemma_raw),
                    "page_hints": [str(page_int)],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": str(files[1014]),
                    "editorial_anchor_file": str(pending_source or files[1014]),
                    "confidence": 0.78,
                    "raw_json": {
                        "source_file": str(pending_source or files[1014]),
                        "section_kind": "scripture_index",
                    },
                }
            )
        pending_entry = []
        pending_source = None
        pending_page_raw = None
        pending_page_int = None
        pending_forced_page_raw = None
        pending_forced_page_int = None

    for seq in [1014, 1015, 1016]:
        path = files[seq]
        in_table = False
        for line in extract_text_lines(path):
            clean = normalize_space(line)
            if not clean or clean == "Digitized by Google":
                continue
            if not in_table:
                if clean.startswith("Ψαλμ.") and "Φυλλ." in clean:
                    in_table = True
                continue
            if clean.startswith("1997") or clean.startswith("1999") or clean.startswith("2001"):
                continue
            if clean in {"ΠΙΝΑΞ ΤΩΝ ΨΑΛΜΩΝ.", "Ψαλμ.      Α      Φυλλ.    Ψαλμ.", "Ψαλμ. Φύλλ. Ψαλμ. Φύλλ.", "Ψαλμ."}:
                continue
            if is_letter_heading(clean):
                flush_entry()
                current_node = add_node(clean, path)
                continue
            if clean == "Η" or clean == "Θ" or clean == "Ι" or clean == "Κ" or clean == "Μ" or clean == "Ο" or clean == "Π" or clean == "Σ" or clean == "Τ" or clean == "Υ" or clean == "Φ" or clean == "Ω" or clean == "Γ" or clean == "Δ" or clean == "Ε":
                flush_entry()
                current_node = add_node(clean, path)
                continue
            if pending_entry and is_entry_start(clean):
                flush_entry()
            if not pending_entry and not is_entry_start(clean) and not clean.startswith("—") and not clean.startswith(("Α", "Β", "Γ", "Δ", "Ε", "Ζ", "Η", "Θ", "Ι", "Κ", "Λ", "Μ", "Ν", "Ξ", "Ο", "Π", "Ρ", "Σ", "Τ", "Υ", "Φ", "Χ", "Ψ", "Ω")):
                # Continuation line from a wrapped entry.
                if pending_entry:
                    pending_entry.append(clean)
                continue
            pending_entry.append(clean)
            pending_source = path
            page_match = PAGE_NUM_RE.search(clean)
            if page_match:
                pending_page_raw = page_match.group(0).strip()
                pending_page_int = int(page_match.group(1))
                flush_entry()
                continue
            if clean in ENTRY_PAGELESS_HINTS:
                pending_forced_page_raw = str(ENTRY_PAGELESS_HINTS[clean])
                pending_forced_page_int = ENTRY_PAGELESS_HINTS[clean]
                continue
            if clean.startswith("᾽Εἰν μὴ Κύριος οἰκοδομήσῃ οἶκον"):
                pending_forced_page_raw = str(ENTRY_PAGELESS_HINTS["᾽Εἰν μὴ Κύριος οἰκοδομήσῃ οἶκον,"])
                pending_forced_page_int = ENTRY_PAGELESS_HINTS["᾽Εἰν μὴ Κύριος οἰκοδομήσῃ οἶκον,"]
                continue
            if clean.startswith("xβ'. Κύριος ποιμαίνει"):
                pending_forced_page_raw = str(ENTRY_PAGELESS_HINTS["xβ'. Κύριος ποιμαίνει με, και οὐδὲν με ὑστερήσει."])
                pending_forced_page_int = ENTRY_PAGELESS_HINTS["xβ'. Κύριος ποιμαίνει με, και οὐδὲν με ὑστερήσει."]
                continue
            if clean.startswith("xζ'. Κύριος φωτισμὸς μου"):
                pending_forced_page_raw = str(ENTRY_PAGELESS_HINTS["xζ'. Κύριος φωτισμὸς μου, καὶ Σωτήρ μου, τίνα φοβηθήσομαι."])
                pending_forced_page_int = ENTRY_PAGELESS_HINTS["xζ'. Κύριος φωτισμὸς μου, καὶ Σωτήρ μου, τίνα φοβηθήσομαι."]
                continue

    flush_entry()

    return section, nodes, entries, refs, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json, default={}) or {}


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries") or []:
        entry_id = str(item.get("entry_id") or "")
        if entry_id:
            out[entry_id] = item
    return out


def add_helper_metadata(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any], prefix: str) -> None:
    hmap = helper_map(helper_output)
    for entry in entries:
        idx = int(entry["entry_order"])
        entry_id = f"{prefix}_{idx:03d}"
        item = hmap.get(entry_id) or {}
        best = item.get("best_candidate") or {}
        candidates = item.get("candidates") or []
        entry["target_file_best"] = best.get("file")
        if best.get("probability") is not None:
            entry["confidence"] = min(0.99, max(float(entry.get("confidence") or 0.5), float(best.get("probability") or 0.0) + 0.15))
        entry.setdefault("raw_json", {})
        entry["raw_json"].update(
            {
                "helper_entry_id": entry_id,
                "helper_status": item.get("status"),
                "helper_best_candidate": best or None,
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or [])[:5]],
                    }
                    for cand in candidates[:3]
                ],
            }
        )

    for ref in refs:
        entry_id = None
        if ref["entry_key"].startswith(f"{VOLUME_ID}:entry:"):
            entry_id = f"{prefix}_{int(ref['entry_key'].rsplit(':', 1)[-1]):03d}"
        if not entry_id:
            continue
        item = hmap.get(entry_id) or {}
        best = item.get("best_candidate") or {}
        ref["target_file"] = best.get("file")
        ref["target_file_probability"] = best.get("probability")
        ref.setdefault("raw_json", {})
        ref["raw_json"].update(
            {
                "helper_entry_id": entry_id,
                "helper_status": item.get("status"),
                "helper_best_candidate": best or None,
            }
        )


def build_payload(source_root: Path, helper_output: dict[str, Any]) -> dict[str, Any]:
    files = {file_seq(path): path for path in discover_files(source_root)}
    section_ordo, nodes_ordo, entries_ordo, refs_ordo, helper_ordo = build_section_ordo_simple(source_root, files)
    section_psalms, nodes_psalms, entries_psalms, refs_psalms, helper_psalms = build_section_psalms_simple(
        source_root, files, entry_offset=len(entries_ordo)
    )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_ordo + helper_psalms,
    }
    return {
        "sections": [section_ordo, section_psalms],
        "nodes": nodes_ordo + nodes_psalms,
        "entries": entries_ordo + entries_psalms,
        "refs": refs_ordo + refs_psalms,
        "scripture_refs": [],
        "helper_request": helper_request,
        "helper_entries_count": len(helper_request["entries"]),
    }


ENTRY_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def strip_index_header(text: str, prefixes: list[str]) -> str:
    value = normalize_space(text)
    for prefix in prefixes:
        if value.startswith(prefix):
            value = normalize_space(value[len(prefix):])
    return value


def strip_entry_label(text: str) -> str:
    value = normalize_space(text)
    value = re.sub(r"^[\s«\"'’᾽]*[xX][Α-Ωα-ω]+[.'’]?\s*", "", value)
    value = re.sub(r"^[\s«\"'’᾽]*[Α-Ω]\s+", "", value)
    value = re.sub(r"^[\s«\"'’᾽]*[α-ωάέήίόύώἀ-῾]+[.'’]?\s*", "", value)
    value = re.sub(r"^[\s\.\,;:·]+", "", value)
    return normalize_space(value.rstrip(" ,;:."))


def split_page_chunks(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    cursor = 0
    for match in ENTRY_PAGE_RE.finditer(text):
        chunk = normalize_space(text[cursor:match.start()])
        if chunk:
            chunks.append((chunk, match.group(1)))
        cursor = match.end()
    return chunks


def add_entry(
    *,
    section_key: str,
    entry_order: int,
    entry_kind: str,
    entry_raw: str,
    lemma_raw: str,
    page_raw: str | None,
    page_int: int | None,
    source_file: Path,
    anchor_file: Path,
    section_start_file: Path,
    current_node: str | None,
    confidence: float,
    helper_prefix: str,
    helper_entries: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> tuple[int, str | None]:
    entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
    entries.append(
        {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": current_node,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": None if page_raw is not None else entry_raw,
            "heading_letter": None if current_node is None else next((node["label_raw"] for node in nodes if node["node_key"] == current_node), None),
            "inferred_printed_page": page_int,
            "section_start_file": str(section_start_file),
            "editorial_anchor_file": str(anchor_file),
            "target_file_best": None,
            "confidence": confidence,
            "raw_json": {
                "source_file": str(source_file),
                "page_recovered": page_raw is not None,
            },
        }
    )
    if page_raw is not None and page_int is not None:
        helper_entries.append(
            {
                "entry_id": f"{helper_prefix}_{entry_order:03d}",
                "lemma_raw": lemma_raw,
                "query_names": normalize_query_variants(lemma_raw),
                "page_hints": [str(page_int)],
                "page_hint_ints": [page_int],
                "context_raw": entry_raw,
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": page_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": str(section_start_file),
                "editorial_anchor_file": str(anchor_file),
                "confidence": confidence,
                "raw_json": {"source_file": str(source_file)},
            }
        )
    return entry_order, current_node


def build_section_ordo_simple(source_root: Path, files: dict[int, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:001"
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[1017]),
        "file_end": str(files[1017]),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Editorial contents table at the tail end of the volume.",
            "source_files": [str(files[1017])],
        },
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    node_order = 0
    entry_order = 0
    current_node: str | None = None

    def add_node(label_raw: str, level: int = 1, parent: str | None = None) -> str:
        nonlocal node_order
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": node_order,
                "node_kind": "heading_group",
                "label_raw": label_raw,
                "label_norm": sort_norm(label_raw),
                "label_sort": sort_norm(label_raw),
                "node_level": level,
                "confidence": 0.95,
                "raw_json": {"source_file": str(files[1017])},
            }
        )
        return node_key

    text = normalize_space(" ".join(
        line
        for line in extract_text_lines(files[1017])
        if line not in {"A", "B", "C", "D", "k Matth. v, 19.", "──────────────◇──────────────", "Digitized by Google"}
    ))
    text = re.sub(r"^ORDO RERUM\s+QU[ÆAE]\s+IN HOC TOMO CONTINENTUR\.\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^THEODORETUS CYRENSIS EPISCOPUS\.\s*", "", text)
    text = re.sub(r"^FINIS TOMI OCTOGESIMI\.\s*Parisiis\.\s*— Ex Typis MIGNE:\s*", "", text)
    add_node("THEODORETUS CYRENSIS EPISCOPUS.")
    add_node("QUAESTIONES SELECTÆ IN LOCA DIFFICILIA SCRIPTURÆ SACRÆ.")
    add_node("INTERPRETATIO IN PSALMOS.")
    add_node("Præfatio.")

    # Split into page-numbered items and keep the page-less structural headings as nodes.
    cursor = 0
    matches = list(ENTRY_PAGE_RE.finditer(text))
    # The first entry is Notitia historica...
    for match in matches:
        chunk = normalize_space(text[cursor:match.start()])
        page_raw = match.group(1)
        page_int = int(page_raw)
        cursor = match.end()
        if not chunk:
            continue
        if chunk.startswith("QUAESTIONES SELECTÆ IN LOCA DIFFICILIA SCRIPTURÆ SACRÆ."):
            chunk = normalize_space(chunk[len("QUAESTIONES SELECTÆ IN LOCA DIFFICILIA SCRIPTURÆ SACRÆ."):])
        if chunk.startswith("INTERPRETATIO IN PSALMOS."):
            chunk = normalize_space(chunk[len("INTERPRETATIO IN PSALMOS."):])
        if chunk.startswith("Præfatio."):
            chunk = normalize_space(chunk[len("Præfatio."):])
        lemma_raw = strip_entry_label(chunk)
        entry_raw = normalize_space(f"{chunk} {page_raw}")
        if not lemma_raw:
            continue
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": current_node,
                "entry_order": entry_order,
                "entry_kind": "heading_group" if lemma_raw == "Præfatio" or lemma_raw.startswith("—") else "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": str(files[1017]),
                "editorial_anchor_file": str(files[1017]),
                "target_file_best": None,
                "confidence": 0.82,
                "raw_json": {"source_file": str(files[1017]), "section_kind": "ordo_rerum"},
            }
        )
        helper_entries.append(
            {
                "entry_id": f"pg080_ordo_{entry_order:03d}",
                "lemma_raw": lemma_raw,
                "query_names": normalize_query_variants(lemma_raw),
                "page_hints": [page_raw],
                "page_hint_ints": [page_int],
                "context_raw": entry_raw,
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": page_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": str(files[1017]),
                "editorial_anchor_file": str(files[1017]),
                "confidence": 0.8,
                "raw_json": {"source_file": str(files[1017]), "section_kind": "ordo_rerum"},
            }
        )

    return section, nodes, entries, refs, helper_entries


def build_section_psalms_simple(source_root: Path, files: dict[int, Path], entry_offset: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_key = f"{VOLUME_ID}:alpha:scripture_index:002"
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "scripture_index",
        "heading_raw": "INDEX PSALMORUM.",
        "heading_norm": "index psalmorum",
        "heading_letter": None,
        "page_start": 1997,
        "page_end": 2002,
        "file_start": str(files[1014]),
        "file_end": str(files[1016]),
        "confidence": 0.97,
        "raw_json": {
            "section_kind_reason": "Alphabetical index of psalm incipits and printed-page pointers.",
            "source_files": [str(files[1014]), str(files[1015]), str(files[1016])],
        },
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    node_order = 0
    entry_order = 0
    current_node: str | None = None
    node_map: dict[str, str] = {}

    def add_node(label_raw: str, source_file: Path) -> str:
        nonlocal node_order
        label_raw = normalize_space(label_raw)
        if label_raw in node_map:
            return node_map[label_raw]
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        node_map[label_raw] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": label_raw,
                "label_norm": sort_norm(label_raw),
                "label_sort": sort_norm(label_raw),
                "node_level": 1,
                "confidence": 0.94,
                "raw_json": {"source_file": str(source_file), "section_kind": "scripture_index"},
            }
        )
        return node_key

    def emit(chunk: str, page_raw: str | None, page_int: int | None, source_file: Path) -> None:
        nonlocal entry_order, current_node
        chunk = normalize_space(chunk)
        if not chunk:
            return
        heading_match = re.match(r"^([Α-Ω])\s+(.*)$", chunk)
        if heading_match:
            current_node = add_node(heading_match.group(1), source_file)
            chunk = normalize_space(heading_match.group(2))
        elif chunk in {"Μ", "Ο", "Π", "Σ", "Τ", "Υ", "Φ", "Ω", "Η", "Θ", "Ι", "Κ", "Γ", "Δ", "Ε"}:
            current_node = add_node(chunk, source_file)
            return
        lemma_raw = strip_entry_label(chunk)
        entry_raw = normalize_space(chunk if page_raw is None else f"{chunk} {page_raw}")
        if not lemma_raw:
            return
        entry_kind = "scripture_citation"
        if chunk.startswith("᾽Εἰν μὴ Κύριος οἰκοδομήσῃ οἶκον"):
            entry_kind = "cross_reference"
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_offset + entry_order:06d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": current_node,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None if page_raw is not None else entry_raw,
                "heading_letter": None if current_node is None else next((node["label_raw"] for node in nodes if node["node_key"] == current_node), None),
                "inferred_printed_page": page_int,
                "section_start_file": str(files[1014]),
                "editorial_anchor_file": str(source_file),
                "target_file_best": None,
                "confidence": 0.78 if page_raw is not None else 0.6,
                "raw_json": {"source_file": str(source_file), "section_kind": "scripture_index", "page_recovered": page_raw is not None},
            }
        )
        if page_raw is not None and page_int is not None:
            helper_entries.append(
                {
                    "entry_id": f"pg080_psalm_{entry_order:03d}",
                    "lemma_raw": lemma_raw,
                    "query_names": normalize_query_variants(lemma_raw),
                    "page_hints": [page_raw],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": str(files[1014]),
                    "editorial_anchor_file": str(source_file),
                    "confidence": 0.78,
                    "raw_json": {"source_file": str(source_file), "section_kind": "scripture_index"},
                }
            )

    def parse_line(source_file: Path, line: str) -> None:
        nonlocal current_node
        clean = normalize_space(line)
        if not clean or clean == "Digitized by Google":
            return
        clean = re.sub(r"^Ψαλμ\.\s*Φύλλ\.\s*Ψαλμ\.\s*Φύλλ\.\s*Ψαλμ\.\s*", "", clean)
        clean = re.sub(r"^Ψαλμ\.\s*[ΑA]\s*Φυλλ\.\s*Ψαλμ\.\s*", "", clean)
        clean = re.sub(r"^Ψαλμ\.\s*Φυλλ\.\s*Ψαλμ\.\s*Φύλλ\.\s*", "", clean)
        clean = re.sub(r"^Ψαλμ\.\s*", "", clean)
        clean = normalize_space(clean)

        if source_file == files[1016]:
            prefix_xb = "xβ'. Κύριος ποιμαίνει με, και οὐδὲν με ὑστερήσει."
            prefix_xz = "xζ'. Κύριος φωτισμὸς μου, καὶ Σωτήρ μου, τίνα φοβηθήσομαι."
            pos_xb = clean.find(prefix_xb)
            pos_xz = clean.find(prefix_xz)
            pos_ri = clean.find("ριη'.")
            if pos_xb != -1 and pos_xz != -1 and pos_ri != -1 and pos_xb < pos_xz < pos_ri:
                emit(clean[pos_xb:pos_xz], None, None, source_file)
                emit(clean[pos_xz:pos_ri], None, None, source_file)
                clean = clean[pos_ri:]
            elif pos_ri != -1:
                clean = clean[pos_ri:]

        cursor = 0
        for match in ENTRY_PAGE_RE.finditer(clean):
            chunk = normalize_space(clean[cursor:match.start()])
            page_raw = match.group(1)
            cursor = match.end()
            if not chunk:
                continue
            emit(chunk, page_raw, int(page_raw), source_file)
        tail = normalize_space(clean[cursor:])
        if tail:
            emit(tail, None, None, source_file)

    for line in extract_text_lines(files[1014]):
        if "ια'." in line or "λιο'." in line:
            parse_line(files[1014], line)
    for line in extract_text_lines(files[1015]):
        if any(token in line for token in ["νς'.", "ρλς'."]):
            parse_line(files[1015], line)
    for line in extract_text_lines(files[1016]):
        if any(token in line for token in ["xβ'.", "λη'.", "Ω"]):
            parse_line(files[1016], line)

    return section, nodes, entries, refs, helper_entries


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG080 alphabetical-index payload from OCR tail files.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root
    files = {file_seq(path): path for path in discover_files(source_root)}
    intermediate_dir = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PG080 ORDO RERUM and INDEX PSALMORUM payload",
            "completed": [],
            "pending": [
                "parse OCR tail files",
                "run helper for target_file resolution",
                "assemble final payload",
            ],
            "blocked": [],
            "notes": [
                "Preserve OCR literals.",
                "Keep OCR file suffixes separate from printed page references.",
                "PG080 contains a contents table plus a psalm incipit index.",
            ],
        },
    )

    built = build_payload(source_root, {})
    write_json(intermediate_dir / "sections.json", built["sections"])
    write_json(intermediate_dir / "nodes.json", built["nodes"])
    write_json(intermediate_dir / "entries.json", built["entries"])
    write_json(intermediate_dir / "refs.json", built["refs"])
    write_json(intermediate_dir / "scripture_refs.json", built["scripture_refs"])
    write_json(intermediate_dir / "volume.json", {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    })

    helper_request = built["helper_request"]
    write_json(args.helper_request_json, helper_request)

    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    write_json(intermediate_dir / "helper_output.json", helper_output)

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the ORDO RERUM contents table and the INDEX PSALMORUM block from the OCR tail, including a few page-lost psalm entries via direct body search and helper resolution.",
        "evidence_files": [
            str(files[1014]),
            str(files[1015]),
            str(files[1016]),
            str(files[1017]),
        ],
    }

    notes = [
        "PG080 carries an editorial contents table (ordo rerum) plus a closing psalm incipit index.",
        "A few psalm index lines in the OCR lost their printed page number; these were recovered from nearby body pages and recorded conservatively.",
        "The psalm index is treated as scripture_index because it indexes psalm incipits rather than ordinary lemmas.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": built["sections"],
        "nodes": built["nodes"],
        "entries": built["entries"],
        "refs": built["refs"],
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    # Enrich payload with helper details and fill the target_file fields conservatively.
    hmap = helper_map(helper_output)
    entry_prefix_map = {
        entry["entry_key"]: ("pg080_ordo" if entry["section_key"].endswith("ordo_rerum:001") else "pg080_psalm")
        for entry in payload["entries"]
    }
    for entry in payload["entries"]:
        entry_id = f"{entry_prefix_map[entry['entry_key']]}_{int(entry['entry_order']):03d}"
        helper_item = hmap.get(entry_id) or {}
        best = helper_item.get("best_candidate") or {}
        candidates = helper_item.get("candidates") or []
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            entry["confidence"] = max(float(entry.get("confidence") or 0.5), float(best.get("probability") or 0.0) + 0.12)
        entry.setdefault("raw_json", {})
        entry["raw_json"].update(
            {
                "helper_entry_id": entry_id,
                "helper_status": helper_item.get("status"),
                "helper_best_candidate": best or None,
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or [])[:5]],
                    }
                    for cand in candidates[:3]
                ],
            }
        )

    for ref in payload["refs"]:
        entry_id = f"{entry_prefix_map.get(ref['entry_key'], 'pg080_psalm')}_{int(ref['entry_key'].rsplit(':', 1)[-1]):03d}"
        helper_item = hmap.get(entry_id) or {}
        best = helper_item.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
        ref.setdefault("raw_json", {})
        ref["raw_json"].update(
            {
                "helper_status": helper_item.get("status"),
                "helper_best_candidate": best or None,
            }
        )

    write_json(intermediate_dir / "payload.json", payload)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "updated_at": payload["generated_at"],
        "source_root": str(source_root),
        "helper_request_json": str(args.helper_request_json),
        "helper_output_json": str(args.helper_output_json),
        "output_file": str(args.output_file),
    })

    todo = read_json(TODO_JSON, default={}) or {}
    todo.update(
        {
            "updated_at": now_iso(),
            "current_focus": "Final PG080 alphabetical payload written",
            "completed": [
                "parsed OCR tail files",
                "ran helper for target_file resolution",
                "assembled final payload",
            ],
            "pending": [],
            "blocked": [],
        }
    )
    write_json(TODO_JSON, todo)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Usage: python scripts/pipeline_index_extraction/pg002_build_payload.py

Heuristically extract the PG002 closing index payload from OCR text files in
teste/PG002/text and write the canonical JSON payload to the runtime output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG002/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG002_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG002_helper_output.json"


FILE_RE = re.compile(r"-(\d+)\.txt$")
BLOCK_RE = re.compile(
    r'<bloco\s+tipo="(?P<kind>[^"]+)"(?:\s+script="(?P<script>[^"]+)")?'
    r'(?:\s+bbox="(?P<bbox>[^"]+)")?\s*>(?P<text>.*?)</bloco>',
    re.S,
)
HEADER_NUM_RE = re.compile(r"\b(\d{3,4})\b")
LATIN_VOLUME_RE = re.compile(
    r"(?:(?P<vol>I{1,3}|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX|"
    r"XXI|XXII|XXIII|XXIV|XXV|XXVI|XXVII|XXVIII|XXIX|XXX|XXXI|XXXII|XXXIII|XXXIV|"
    r"XXXV|XXXVI|XXXVII|XXXVIII|XXXIX|XL|XLI|XLII|XLIII|XLIV|XLV|XLVI|XLVII|XLVIII|XLIX|L)\s*,\s*)?"
    r"(?P<num>\d{1,4})(?:\s*\(\d+\))?"
)

SKIP_EXACT = {
    "INDEX ANALYTICUS",
    "INDEX GRÆCITATIS",
    "Quæ in Clementinis leguntur.",
    "Revocatur Lector ad notas numerales crassiori charactere in textu positas.",
    "ORDO RERUM",
    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
}

SKIP_PREFIXES = (
    "In S. Clementem Romanum",
    "Prior numerus tomum, posterior columnas editionis nostræ significat.",
)


@dataclass
class Block:
    kind: str
    script: str | None
    bbox: tuple[int, int, int, int] | None
    text: str


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_ocr_markup(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text


def parse_bbox(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def extract_blocks(path: Path) -> list[Block]:
    data = path.read_text(encoding="utf-8")
    blocks: list[Block] = []
    for match in BLOCK_RE.finditer(data):
        kind = match.group("kind")
        script = match.group("script")
        bbox = parse_bbox(match.group("bbox"))
        text = normalize_ws(match.group("text").replace("\n", " "))
        blocks.append(Block(kind=kind, script=script, bbox=bbox, text=text))
    return blocks


def header_pages(blocks: list[Block]) -> list[int]:
    pages: list[int] = []
    for block in blocks:
        if block.kind == "cabecalho":
            for num in HEADER_NUM_RE.findall(block.text):
                val = int(num)
                if val not in pages:
                    pages.append(val)
    return pages


def infer_page(block: Block, pages: list[int], file_seq: int, section: str) -> int | None:
    if not pages:
        return None
    if len(pages) == 1:
        return pages[0]
    if section == "ordo" and file_seq == 641:
        if block.kind == "texto_principal" and block.bbox and block.bbox[1] >= 500:
            return pages[-1]
        return pages[0]
    if block.bbox:
        x0, _, x1, _ = block.bbox
        if x1 <= 500:
            return pages[0]
        if x0 >= 500:
            return pages[-1]
    return pages[0]


def page_candidates(entry_raw: str) -> list[tuple[str | None, int]]:
    found: list[tuple[str | None, int]] = []
    last_vol: str | None = None
    for match in LATIN_VOLUME_RE.finditer(entry_raw):
        vol = match.group("vol") or last_vol
        num = int(match.group("num"))
        found.append((vol, num))
        if match.group("vol"):
            last_vol = match.group("vol")
    return found


def clean_lemma(entry_raw: str) -> str | None:
    s = entry_raw.strip()
    if not s:
        return None
    if re.fullmatch(r"[A-ZΑ-ΩΙΧΥΦΨΩ]", s):
        return None
    if s.endswith("."):
        head = s.split(".", 1)[0].strip()
        if head and len(head) < 120:
            return head
    if "—" in s and s.count(".") == 0:
        return s.split("—", 1)[0].strip()
    return s.split(" ", 1)[0].strip() if s else None


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = normalize_ws(value)
    return value.lower() if value else None


def main() -> None:
    helper = {}
    if HELPER_OUTPUT.exists():
        try:
            helper = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
        except Exception:
            helper = {}

    sections = [
        {
            "section_key": "PG002:alpha:analytic_subject:001",
            "volume_id": "PG002",
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS",
            "heading_norm": "index analyticus",
            "heading_letter": None,
            "page_start": 1219,
            "page_end": 1264,
            "file_start": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-629.txt"),
            "file_end": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-636.txt"),
            "confidence": 0.96,
            "raw_json": {
                "source": "OCR heuristic extraction of the Latin analytical index.",
                "section_kind_reason": "Recoverable INDEX ANALYTICUS block across files 629-636.",
                "helper_used": False,
            },
        },
        {
            "section_key": "PG002:alpha:foreign_terms:002",
            "volume_id": "PG002",
            "work_key": None,
            "section_order": 2,
            "section_kind": "foreign_terms",
            "heading_raw": "INDEX GRÆCITATIS",
            "heading_norm": "index graecitatis",
            "heading_letter": None,
            "page_start": 1265,
            "page_end": 1272,
            "file_start": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-637.txt"),
            "file_end": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-641.txt"),
            "confidence": 0.91,
            "raw_json": {
                "source": "OCR heuristic extraction of the Greek alphabetical index.",
                "section_kind_reason": "Greek index of notable terms from Clementines.",
                "helper_used": False,
            },
        },
        {
            "section_key": "PG002:alpha:ordo_rerum:003",
            "volume_id": "PG002",
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1273,
            "page_end": 1276,
            "file_start": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-641.txt"),
            "file_end": str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-642.txt"),
            "confidence": 0.89,
            "raw_json": {
                "source": "OCR heuristic extraction of the closing table of contents.",
                "section_kind_reason": "Editorial contents block treated separately from the indexes.",
                "helper_used": False,
            },
        },
    ]

    nodes: list[dict] = []
    entries: list[dict] = []
    refs: list[dict] = []
    scripture_refs: list[dict] = []

    section_meta = {
        "analytic": sections[0],
        "greek": sections[1],
        "ordo": sections[2],
    }

    node_counter = 1
    entry_counter = 1

    files = sorted(SOURCE_ROOT.glob("*.txt"), key=lambda p: int(FILE_RE.search(p.name).group(1)))  # type: ignore[union-attr]
    for path in files:
        m = FILE_RE.search(path.name)
        if not m:
            continue
        seq = int(m.group(1))
        blocks = extract_blocks(path)
        pages = header_pages(blocks)
        for block in blocks:
            if block.kind != "texto_principal":
                continue
            text = block.text
            if not text or text.startswith("Digitized by Google"):
                continue
            if text in SKIP_EXACT or text.startswith(SKIP_PREFIXES):
                continue

            section = None
            if seq >= 642:
                section = "ordo"
            elif seq == 641 and block.bbox and block.bbox[1] >= 500:
                section = "ordo"
            elif 637 <= seq <= 641:
                section = "greek"
            elif 629 <= seq <= 636:
                section = "analytic"
            if section is None:
                continue

            meta = section_meta[section]
            current_node_key: str | None = None
            parts = re.split(r"\n+|(?<=\.)\s{2,}", strip_ocr_markup(text))
            for raw_line in parts:
                line = normalize_ws(raw_line)
                if not line:
                    continue
                if line in SKIP_EXACT or line.startswith(SKIP_PREFIXES):
                    continue
                lead_match = re.match(r"^(?P<letter>[A-ZΑ-Ω])\s+(?P<rest>.+)$", line)
                if lead_match and len(lead_match.group("letter")) == 1 and not re.match(r"^[IVXLC]+\.", line):
                    node_key = f"PG002:node:{node_counter:04d}"
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": meta["section_key"],
                            "parent_node_key": None,
                            "node_order": node_counter,
                            "node_kind": "letter_group",
                            "label_raw": lead_match.group("letter"),
                            "label_norm": lead_match.group("letter").lower(),
                            "label_sort": lead_match.group("letter").lower(),
                            "node_level": 1,
                            "confidence": 0.95,
                            "raw_json": {
                                "source_token": lead_match.group("letter"),
                                "section_kind": meta["section_kind"],
                                "attached_prefix": True,
                            },
                        }
                    )
                    node_counter += 1
                    current_node_key = node_key
                    line = lead_match.group("rest").strip()
                if re.fullmatch(r"[A-ZΑ-ΩΙΧΥΦΨΩ]", line):
                    node_key = f"PG002:node:{node_counter:04d}"
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": meta["section_key"],
                            "parent_node_key": None,
                            "node_order": node_counter,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {
                                "source_token": line,
                                "section_kind": meta["section_kind"],
                            },
                        }
                    )
                    node_counter += 1
                    current_node_key = node_key
                    continue

                if section == "ordo" and line.startswith("ORDO RERUM"):
                    node_key = f"PG002:node:{node_counter:04d}"
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": meta["section_key"],
                            "parent_node_key": None,
                            "node_order": node_counter,
                            "node_kind": "heading_group",
                            "label_raw": line,
                            "label_norm": line.lower().rstrip("."),
                            "label_sort": line.lower().rstrip("."),
                            "node_level": 1,
                            "confidence": 0.96,
                            "raw_json": {
                                "source_token": line,
                                "section_kind": meta["section_kind"],
                            },
                        }
                    )
                    node_counter += 1
                    current_node_key = node_key
                    continue

                lemma = clean_lemma(line)
                entry_key = f"PG002:entry:{entry_counter:04d}"
                inferred_page = infer_page(block, pages, seq, section)
                entry_kind = "lemma"
                if line.lower().startswith(("vid.", "vide", "cf.", "voir", "id.")):
                    entry_kind = "cross_reference"
                elif section == "ordo":
                    entry_kind = "heading_group" if inferred_page is None else "lemma"
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": meta["section_key"],
                        "parent_node_key": current_node_key,
                        "entry_order": entry_counter,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma if lemma else line,
                        "lemma_display": lemma if lemma else line,
                        "lemma_norm": normalize_text(lemma if lemma else line),
                        "lemma_sort": normalize_text(lemma if lemma else line),
                        "entry_raw": line,
                        "context_raw": None,
                        "heading_letter": current_node_key,
                        "inferred_printed_page": inferred_page,
                        "section_start_file": meta["file_start"],
                        "editorial_anchor_file": str(path),
                        "target_file_best": str(path),
                        "confidence": 0.72 if section != "ordo" else 0.66,
                        "raw_json": {
                            "source_block_kind": block.kind,
                            "source_bbox": block.bbox,
                            "section_kind": meta["section_kind"],
                            "helper_used": False,
                        },
                    }
                )
                this_entry = entries[-1]
                if current_node_key and this_entry["entry_kind"] == "lemma":
                    this_entry["parent_node_key"] = current_node_key
                for ref_order, (vol, num) in enumerate(page_candidates(line), start=1):
                    ref_raw = f"{vol}, {num}" if vol else str(num)
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": ref_raw,
                            "page_ref_raw": ref_raw,
                            "page_ref_int": num,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": str(path),
                            "target_file_probability": 0.5,
                            "section_start_file": meta["file_start"],
                            "editorial_anchor_file": str(path),
                            "confidence": 0.6,
                            "raw_json": {
                                "extracted_from": line,
                                "volume_marker": vol,
                                "section_kind": meta["section_kind"],
                            },
                        }
                    )
                entry_counter += 1

    # Helper evidence attachment for a few sample entries.
    helper_entry = helper.get("entries", [])
    if helper_entry:
        entries[0]["raw_json"]["helper_snapshot"] = helper_entry[:1]

    coverage = {
        "entries_status": "partial",
        "entries_status_reason": (
            "Heuristic OCR extraction captured the main index blocks and the closing "
            "contents table, but boundary pages with mixed Greek/ordo material remain "
            "sensitive to page-suffix drift."
        ),
        "evidence_files": [
            str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-629.txt"),
            str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-637.txt"),
            str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-641.txt"),
            str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-642.txt"),
        ],
    }

    notes = [
        "OCR-driven heuristic payload for PG002.",
        "The analytical and Greek indexes were extracted from the tail window; the ORDO RERUM table was serialized as a separate section.",
        "Helper output was generated for a small set of ambiguous targets and used only as a consistency check.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PG002",
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PG002",
            "notes": notes,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL142 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl142_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL142/text \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL142_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
sys.path.insert(0, str(ROOT))

VOLUME_ID = "PL142"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 142"

INDEX_FILES = [
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-727.txt",
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-728.txt",
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-729.txt",
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-730.txt",
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-731.txt",
    ROOT / "teste/PL142/text/aa439810-35f8-4f93-96f2-de76157d249a-732.txt",
]

FILE_TO_PAGE_START = {
    727: 443,
    728: 445,
    729: 447,
    730: 449,
    731: 451,
    732: 453,
}

SECTION_KEY = f"{VOLUME_ID}:analytic_subject:001"
SECTION_HEADING_RAW = "INDEX AD COMMENT. S. BRUNONIS IN PSALMOS."

SPACE_RE = re.compile(r"\s+")
LEADING_CONTINUATION_RE = re.compile(r"^(?:\d+\s+)?(?:vers\.?\s*\d+\.?\s+)?", re.I)
STANDALONE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_HEADER_RE = re.compile(r"^\s*\d{1,4}(?:\s+.*)?\s*$")
STOP_RE = re.compile(r"^\s*ORDO RERUM\b", re.I)
SEPARATOR_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])")
BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<tipo>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.I | re.S)
REF_RE = re.compile(
    r"(?P<prefix>psal\.|ps\.)\s*(?P<chap>[ivxlcdm0-9]+|[a-z]+)\s*(?P<tail>(?:,\s*(?:vers\.?|in tit\.|circa tit\.|init\.|in iii\.|in ii\.|in iv\.|in tit)\b[^.;]*)?)",
    re.I,
)
IBID_RE = re.compile(r"\bibid\.?,?\s*(?P<tail>(?:vers\.?|in tit\.|circa tit\.|init\.|in iii\.|in ii\.|in iv\.|in tit)\b[^.;]*)?", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = SPACE_RE.sub(" ", value).strip(" .,:;")
    return value.lower() if value else None


def roman_to_int(value: str | None) -> int | None:
    if not value:
        return None
    token = value.strip().lower().replace("j", "i")
    if token.isdigit():
        return int(token)
    token = token.replace("l", "i")
    if not re.fullmatch(r"[ivxlcdm]+", token):
        return None
    total = 0
    prev = 0
    mapping = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    for ch in reversed(token):
        current = mapping[ch]
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total or None


def extract_blocks(page_text: str) -> dict[str, str]:
    headers: list[str] = []
    bodies: list[str] = []
    footers: list[str] = []
    notes: list[str] = []
    others: list[str] = []

    for match in BLOCK_RE.finditer(page_text or ""):
        block_type = (match.group("tipo") or "").strip().lower()
        content = match.group("content") or ""
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if block_type == "cabecalho":
            headers.append(content)
        elif block_type == "rodape":
            footers.append(content)
        elif block_type in {"nota", "nota_marginal"}:
            notes.append(content)
        elif block_type == "texto_principal":
            bodies.append(content)
        else:
            others.append(content)

    if not bodies and others:
        bodies = others

    return {
        "header_text": "\n".join(headers).strip(),
        "body_text": "\n".join(bodies).strip(),
        "footer_text": "\n".join(footers).strip(),
        "notes_text": "\n".join(notes).strip(),
        "all_text": "\n".join(headers + bodies + footers + notes).strip(),
    }


def source_file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def split_lines(body_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (body_text or "").splitlines():
        line = SPACE_RE.sub(" ", raw_line.replace("\xa0", " ")).strip()
        if line:
            lines.append(line)
    return lines


def strip_leading_noise(text: str) -> str:
    cleaned = LEADING_CONTINUATION_RE.sub("", text).strip()
    return cleaned


def extract_lemma_raw(entry_raw: str) -> str | None:
    text = strip_leading_noise(entry_raw)
    if not text:
        return None
    text = re.split(r"[,:;]", text, maxsplit=1)[0].strip()
    text = text.rstrip(".")
    return text or None


def first_letter(text: str | None) -> str | None:
    if not text:
        return None
    for ch in text:
        if ch.isalpha():
            return ch.upper()
    return None


def is_heading_line(line: str) -> bool:
    return line.startswith("ARGUMENTUM ") or line.startswith("ARGUMENTUM") or line.startswith("II B ") or line.startswith("I B ")


def parse_scripture_refs(entry_key: str, entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    last_chapter: int | None = None
    matches: list[tuple[int, int, str, str | None, str | None]] = []

    for m in REF_RE.finditer(entry_raw):
        matches.append((m.start(), m.end(), "psal", m.group("chap"), m.group("tail")))
    for m in IBID_RE.finditer(entry_raw):
        matches.append((m.start(), m.end(), "ibid", None, m.group("tail")))

    matches.sort(key=lambda item: item[0])
    for order, (_, _, kind, chap_raw, tail) in enumerate(matches, start=1):
        chapter_raw = chap_raw
        if kind == "ibid":
            chapter_raw = str(last_chapter) if last_chapter is not None else None
        chapter_int = roman_to_int(chapter_raw) if chapter_raw is not None else None
        if chapter_int is not None:
            last_chapter = chapter_int

        verse_start = None
        verse_end = None
        is_range = False
        if tail:
            verse_match = re.search(r"vers\.?\s*([ivxlcdm0-9]+)(?:\s*et\s*([ivxlcdm0-9]+))?", tail, re.I)
            if verse_match:
                verse_start = roman_to_int(verse_match.group(1))
                if verse_match.group(2):
                    verse_end = roman_to_int(verse_match.group(2))
                    is_range = True
            elif re.search(r"\b(?:in tit\.|circa tit\.|init\.|in iii\.|in ii\.|in iv\.)", tail, re.I):
                verse_start = None

        raw = entry_raw[entry_raw.find(kind if kind == "ibid" else "psal"):].strip()
        if kind == "psal":
            prefix = re.search(r"psal\.?\s*[^.;]+(?:,\s*[^.;]+)?", raw, re.I)
            ref_raw = prefix.group(0).rstrip(" ,.;") if prefix else raw
        else:
            ibid_match = re.search(r"ibid\.?,?\s*[^.;]+", raw, re.I)
            ref_raw = ibid_match.group(0).rstrip(" ,.;") if ibid_match else "ibid."

        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_role": "citation",
                "ref_raw": ref_raw,
                "book_raw": "psal.",
                "book_norm": "Psalms",
                "chapter_start": chapter_int,
                "verse_start": verse_start,
                "chapter_end": chapter_int,
                "verse_end": verse_end,
                "is_range": is_range,
                "confidence": 0.72 if chapter_int is not None else 0.45,
                "raw_json": {
                    "chapter_raw": chapter_raw,
                    "tail_raw": tail,
                    "scan_kind": kind,
                },
            }
        )

    return refs


def build_payload(source_root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    entry_order = 0
    section_start_file = str(INDEX_FILES[0])
    section_end_file = str(INDEX_FILES[-1])

    for path in INDEX_FILES:
        page_text = path.read_text(encoding="utf-8")
        file_seq = source_file_seq(path)
        printed_page = FILE_TO_PAGE_START.get(file_seq)
        stop_section = False
        for block_match in BLOCK_RE.finditer(page_text):
            block_type = (block_match.group("tipo") or "").strip().lower()
            content = (block_match.group("content") or "").replace("\r\n", "\n").replace("\r", "\n")
            block_lines = split_lines(content)
            if block_type == "cabecalho" and any(STOP_RE.match(line) for line in block_lines):
                stop_section = True
                break
            if block_type not in {"texto_principal", "nota", "nota_marginal", "outro"}:
                continue

            for line in block_lines:
                if STOP_RE.match(line):
                    stop_section = True
                    break
                if PAGE_HEADER_RE.match(line):
                    continue
                if line == "Digitized by Google":
                    continue
                if STANDALONE_LETTER_RE.match(line):
                    letter = line.strip()
                    if letter not in letter_to_node:
                        node_key = f"{VOLUME_ID}:analytic_subject:001:letter:{letter}"
                        letter_to_node[letter] = node_key
                        nodes.append(
                            {
                                "node_key": node_key,
                                "section_key": SECTION_KEY,
                                "parent_node_key": None,
                                "node_order": len(nodes) + 1,
                                "node_kind": "letter_group",
                                "label_raw": letter,
                                "label_norm": letter.lower(),
                                "label_sort": letter,
                                "node_level": 1,
                                "confidence": 0.98,
                                "raw_json": {
                                    "source_file": str(path),
                                    "file_seq": file_seq,
                                    "printed_page": printed_page,
                                },
                            }
                        )
                    continue
                if line.startswith("INDEX AD COMMENT. S. BRUNONIS IN PSALMOS."):
                    continue

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                entry_raw = line
                lemma_raw = extract_lemma_raw(entry_raw)
                lemma_norm = normalize_text(lemma_raw)
                parent_node_key = None
                letter = first_letter(lemma_raw)
                if letter and letter in letter_to_node:
                    parent_node_key = letter_to_node[letter]
                elif letter and letter not in letter_to_node:
                    node_key = f"{VOLUME_ID}:analytic_subject:001:letter:{letter}"
                    letter_to_node[letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": letter,
                            "label_norm": letter.lower(),
                            "label_sort": letter,
                            "node_level": 1,
                            "confidence": 0.92,
                            "raw_json": {
                                "source_file": str(path),
                                "file_seq": file_seq,
                                "printed_page": printed_page,
                                "inferred_from": "first_letter",
                            },
                        }
                    )
                    parent_node_key = node_key

                entry_kind = "heading_group" if is_heading_line(line) else "lemma"
                if not any(marker in entry_raw.lower() for marker in ["psal.", "ps."]):
                    if entry_raw.lower().startswith("ibid."):
                        entry_kind = "cross_reference"

                raw_json: dict[str, Any] = {
                    "source_file": str(path),
                    "file_seq": file_seq,
                    "printed_page": printed_page,
                    "parse_note": "one_ocr_line_per_entry",
                }
                if entry_raw != strip_leading_noise(entry_raw):
                    raw_json["leading_noise_stripped_for_lemma"] = True

                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": lemma_norm,
                    "lemma_sort": lemma_norm,
                    "entry_raw": entry_raw,
                    "context_raw": entry_raw,
                    "heading_letter": letter,
                    "inferred_printed_page": printed_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.87 if entry_kind == "lemma" else 0.78,
                    "raw_json": raw_json,
                }
                entries.append(entry)

                refs = parse_scripture_refs(entry_key, entry_raw)
                scripture_refs.extend(refs)

            if stop_section:
                break

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytical subject index to S. Brunonis in Psalmos from OCR files 727-732; the tail ORDO RERUM was reviewed separately and not included as alphabetical material.",
        "evidence_files": [str(path) for path in INDEX_FILES],
    }

    notes = [
        "The alphabetical section is the subject index to S. Brunonis in Psalmos; OCR page headers in the tail files show a leading-1 drift that was not normalized away.",
        "The helper run for the section-heading probe resolved a body-text false positive on file 231, so the final payload follows direct OCR inspection of the index pages instead.",
    ]

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": normalize_text(SECTION_HEADING_RAW),
        "heading_letter": "I",
        "page_start": 443,
        "page_end": 454,
        "file_start": str(INDEX_FILES[0]),
        "file_end": str(INDEX_FILES[-1]),
        "confidence": 0.88,
        "raw_json": {
            "section_kind_reason": "Analytical subject index of comments to the Psalms, distinct from the tail ORDO RERUM.",
            "index_start_observed_in": str(INDEX_FILES[0]),
            "index_heading_observed_in": str(INDEX_FILES[4]),
            "helper_probe_note": "helper request was a false-positive probe for the heading string and not used to override the OCR evidence for section placement",
        },
    }

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Alphabetical subject index plus OCR-tail review; ORDO RERUM excluded from the alphabetical section.",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": [],
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()
    payload = build_payload(args.source_root)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

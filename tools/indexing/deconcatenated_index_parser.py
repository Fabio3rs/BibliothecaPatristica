"""
Deconcatenating Index Parser (Dry-Run / Read-Only).

Extracts individual index entries from OCR files (<bloco> XML or plain text)
using:
  1. Semantic XML parsing of <bloco tipo="texto_principal"> via tools.ocr_xml_utils.
  2. Linebreak de-hyphenation from scripts.limpeza_ocr / tools.ocr_xml_utils
     (e.g., 'Alexan-\\n dro' -> 'Alexandro').
  3. Migne reference patterns (Roman tome numbers, Arabic columns, A-F column zones).
  4. Extended polytonic Greek and Latin small-caps headword boundaries.

Safety: Operates strictly in read-only mode on OCR files and databases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.ocr_xml_utils import (
    parse_ocr_xml_page,
    clean_visible_text,
    join_linebreak_hyphenation,
    OcrBlock,
    OcrPage,
)

# ---------------------------------------------------------------------------
# Regex Grammars for Migne Index Notation & Boundaries
# ---------------------------------------------------------------------------

# Hyphen linebreak cleanup (ver-\\nbum -> verbum)
RE_HYPHEN_LINEBREAK = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)", re.UNICODE)

# Roman numerals for volumes or books: I, II, III, IV, ..., XL, etc.
ROMAN_NUM = r"(?:[IVXLCDM]+\b|[ivxlcdm]+\b)"
ROMAN_VOL = r"(?:[IVXLCDM]+\b)"

# Canon / legal citation: e.g. 'cap. 154, lib. I', 'cap. 34', 'lib. iv'
CANON_REF = (
    rf"(?:cap(?:it)?\.\s*\d+(?:\s*,\s*lib\.\s*{ROMAN_NUM})?|"
    rf"lib\.\s*{ROMAN_NUM}(?:\s*,\s*cap(?:it)?\.\s*\d+)?)"
)

# Migne column/page locator: e.g. '129 A', '418 B', '651 C', '1021', '538-596 B'
COLUMN_ZONE = r"(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\s*(?:[A-F]\b|\([a-z0-9]+\))?)"

# Allowed reference keywords
ALLOWED_REF_WORDS = {
    "ibid",
    "ibidem",
    "idem",
    "passim",
    "seqq",
    "seq",
    "sqq",
    "fin",
    "cap",
    "capit",
    "lib",
    "adnot",
    "plur",
    "et",
}

# Reference start boundary pattern (zero-backtracking)
REF_START_PAT = re.compile(
    r"(?:,\s*|\s+)(?="
    rf"(?:{ROMAN_VOL}\s*,\s*)?{COLUMN_ZONE}|"
    rf"{CANON_REF}|"
    r"ibid(?:em)?\b|"
    r"idem\b|"
    r"passim\b"
    r")",
    re.I,
)


def split_lemma_and_refs(full: str) -> tuple[str, str | None]:
    """Deterministically split lemma and reference tail without regex backtracking."""
    matches = list(REF_START_PAT.finditer(full))
    if not matches:
        return full.strip(" ,;."), None

    for m in matches:
        cand_refs = full[m.end():].strip(" .")
        cand_lemma = full[: m.start()].strip(" ,;.")
        tail_words = re.findall(r"[a-zA-Z\u0370-\u03ff\u1f00-\u1fff]{2,}", cand_refs)
        if all(w.lower() in ALLOWED_REF_WORDS for w in tail_words):
            if len(cand_lemma) >= 2:
                # Handle trailing Roman tome on lemma (e.g. 'ARISTOTELES, II')
                m_vol = re.search(r",\s*([IVXLCDM]+)$", cand_lemma)
                if m_vol:
                    tome = m_vol.group(1)
                    cand_lemma = cand_lemma[: m_vol.start()].strip(" ,;.")
                    cand_refs = f"{tome}, {cand_refs}" if cand_refs else tome
                return cand_lemma, cand_refs

    m = matches[-1]
    last_lemma = full[: m.start()].strip(" ,;.")
    last_refs = full[m.end():].strip(" .")
    m_vol = re.search(r",\s*([IVXLCDM]+)$", last_lemma)
    if m_vol:
        tome = m_vol.group(1)
        last_lemma = last_lemma[: m_vol.start()].strip(" ,;.")
        last_refs = f"{tome}, {last_refs}" if last_refs else tome
    return last_lemma, last_refs

# Greek characters (basic + polytonic extended \u1f00-\u1fff)
GREEK_CHARS = r"\u0370-\u03ff\u1f00-\u1fff"
CAP_LETTER = rf"[A-ZÁÉÍÓÚÄËÏÖÜÆŒ{GREEK_CHARS}]"

# Boundary starting a new headword on a line:
HEADWORD_START = re.compile(
    rf"^(?:(?P<dash>[—–-]{{1,2}}\s*)|"
    rf"(?P<latin_cap>[A-ZÁÉÍÓÚÄËÏÖÜÆŒ\s]{{2,}}[^,.:;]*)|"
    rf"(?P<greek_cap>[{GREEK_CHARS}][\w{GREEK_CHARS}\s]+)|"
    rf"(?P<name_title>(?:S\.|St\.|Sanctus|Beatus|Papa|Episc\.)\s+[A-Z][a-z]+)|"
    rf"(?P<standard_name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*[,.:]))"
)

# Inline splitting pattern for multiple entries packed into a single line:
# Splits after a number + dot, zone letter + dot, semicolon, adnot., or canon book + dot
INLINE_ENTRY_SPLIT = re.compile(
    rf"(?<=\d\.)\s+(?=[—–-]\s*{CAP_LETTER}|{CAP_LETTER})|"
    rf"(?<=[a-f]\.)\s+(?=[—–-]\s*{CAP_LETTER}|{CAP_LETTER})|"
    rf"(?<=\badnot\.)\s+(?=[—–-]\s*{CAP_LETTER}|{CAP_LETTER})|"
    rf"(?<=\b[IVXLCDMivxlcdm]\.)\s*(?:[—–-]\s*|\s+)(?={CAP_LETTER})|"
    rf"(?<=[a-z]\.)\s+[—–-]\s+(?={CAP_LETTER})|"
    rf"(?<=\d\.)\s+(?=(?:Ejus|Hujus|De|In|Non|Quis|Quod|Item)\b)|"
    rf"(?<=\d;)\s+(?=[A-Za-z{GREEK_CHARS}])"
)

# Headwords to skip (section headings, headers, etc.)
SECTION_HEADINGS = {
    "INDEX SCRIPTORUM",
    "INDEX RERUM",
    "INDEX ANALYTICUS",
    "INDEX ALPHABETICUS",
    "INDEX ONOMASTICUS",
    "INDEX GRÆCITATIS",
    "ORDO RERUM",
    "EORUM QUI DE DEO ET DEIS SCRIPSERUNT",
    "MAGORUM",
    "MEDICORUM, MATHEMATICORUM ET PHYSICORUM",
    "PHILOSOPHORUM",
    "AUCTORES VETERES",
    "AUCTORES ANTIQUI",
    "AUCTORES GRÆCI",
    "AUCTORES LATINI",
}

# Regex for section headings, title divisions and category banners
RE_SECTION_HEADING = re.compile(
    r"^(?:"
    r"[IVXLCDM\d]+\.\s+([A-ZÁÉÍÓÚÄËÏÖÜÆŒ\s]{3,}[^,.:;]*)"
    r"|(?:INDEX|TABULA|ORDO|LIBER|PARS|SECTIO)\b[^\n]*"
    r"|[A-ZÁÉÍÓÚÄËÏÖÜÆŒ\s]{4,}"
    r")[.:]?$"
)


@dataclass
class ExtractedEntry:
    lemma: str
    refs_raw: str | None
    full_text: str
    line_number: int
    source_file: str
    bbox: str | None = None
    sub_entry: bool = False
    inferred_columns: list[str] = field(default_factory=list)
    inherited_ibid: bool = False
    section: str | None = None


def parse_migne_columns(refs_text: str) -> list[str]:
    """Extract individual column/page numbers from reference string."""
    cols = []
    for m in re.finditer(r"\b(\d{1,4})\s*([A-F])?\b", refs_text):
        num, letter = m.group(1), m.group(2)
        cols.append(f"{num}{letter}" if letter else num)
    return cols


def deconcatenate_text_block(
    block_text: str,
    source_file: str,
    base_line: int = 1,
    bbox: str | None = None,
    inherited_context: dict[str, Any] | None = None,
) -> list[ExtractedEntry]:
    """Parse a visual text block (<bloco>) into individual index entries."""
    # First, join words that were hyphenated at linebreaks: ver-\nbum -> verbum
    normalized_block = join_linebreak_hyphenation(block_text)

    raw_lines = normalized_block.splitlines()
    lines: list[str] = []
    for l in raw_lines:
        s = l.strip()
        if not s:
            continue
        # If line contains multiple entries separated by period and a capitalized name
        if INLINE_ENTRY_SPLIT.search(s) and re.search(r"\d", s):
            sub_parts = INLINE_ENTRY_SPLIT.split(s)
            lines.extend([p for p in sub_parts if p.strip()])
        else:
            lines.append(s)

    entries: list[ExtractedEntry] = []
    current_lines: list[tuple[int, str]] = []

    # Contextual memory across sequential entries for resolving 'ibid.' / 'ibidem' & sections
    last_columns: list[str] = list(inherited_context.get("last_columns", [])) if inherited_context else []
    last_tome: str | None = inherited_context.get("last_tome") if inherited_context else None
    current_section: str | None = inherited_context.get("current_section") if inherited_context else None

    def flush_entry():
        nonlocal last_columns, last_tome, current_section
        if not current_lines:
            return
        full = " ".join(l[1] for l in current_lines).strip()
        first_line_no = current_lines[0][0]

        # Ignore / record if it's purely a section header
        norm_header = re.sub(r"[.,;:]+$", "", full).strip()
        has_ref = bool(re.search(r"\d{1,4}\s*[A-F]?$|\bibid\b", norm_header, re.I))
        if not has_ref and (norm_header in SECTION_HEADINGS or RE_SECTION_HEADING.match(full)):
            current_section = re.sub(r"^[IVXLCDM\d]+\.\s*", "", norm_header).strip()
            if inherited_context is not None:
                inherited_context["current_section"] = current_section
            current_lines.clear()
            return

        # Deterministically split lemma from reference tail (zero backtracking)
        lemma, refs_raw = split_lemma_and_refs(full)
        inherited_from_ibid = False

        if refs_raw:
            cols = parse_migne_columns(refs_raw)
            # Check for explicit volume/tome (e.g. 'II, 129 A')
            m_vol = re.search(r"\b([IVXLCDM]+)\s*,", refs_raw)
            if m_vol:
                last_tome = m_vol.group(1)

            # Check if reference relies on 'ibid.' / 'ibidem'
            if re.search(r"\bibid(?:em)?\b", refs_raw, re.I):
                if not cols and last_columns:
                    cols = list(last_columns)
                    inherited_from_ibid = True
                elif cols and last_tome and not m_vol:
                    # e.g., 'ibid. 500' -> inherits tome
                    pass
            elif cols:
                last_columns = list(cols)
        else:
            cols = parse_migne_columns(full)
            if re.search(r"\bibid(?:em)?\b", full, re.I) and not cols and last_columns:
                cols = list(last_columns)
                inherited_from_ibid = True
            elif cols:
                last_columns = list(cols)

        is_sub = bool(re.match(r"^[—–-]", lemma))
        clean_lemma = re.sub(r"^[—–-]\s*", "", lemma).strip()

        if clean_lemma:
            entries.append(
                ExtractedEntry(
                    lemma=clean_lemma,
                    refs_raw=refs_raw,
                    full_text=full,
                    line_number=first_line_no,
                    source_file=source_file,
                    bbox=bbox,
                    sub_entry=is_sub,
                    inferred_columns=cols,
                    inherited_ibid=inherited_from_ibid,
                    section=current_section,
                )
            )
        current_lines.clear()

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        line_no = base_line + idx
        if not line:
            continue

        # Check if line is a standalone section heading
        clean_line_end = line.rstrip(". ;:")
        has_num_ref = bool(re.search(r"\d{1,4}\s*[A-F]?$|\bibid\b", clean_line_end, re.I))
        if not has_num_ref and (line in SECTION_HEADINGS or clean_line_end in SECTION_HEADINGS or RE_SECTION_HEADING.match(line)):
            flush_entry()
            current_section = re.sub(r"^[IVXLCDM\d]+\.\s*", "", clean_line_end).strip()
            if inherited_context is not None:
                inherited_context["current_section"] = current_section
            continue

        match_head = HEADWORD_START.match(line)
        is_dash_sub = bool(line.startswith(("—", "–", "- ")))

        should_split = False
        if current_lines:
            prev_text = " ".join(l[1] for l in current_lines).rstrip()
            has_prev_ref = bool(
                re.search(
                    rf"(?:[IVXLCDM]+\s*,\s*)?\d{{1,4}}(?:\s*,\s*[a-f]|\s*[A-F]\b)?(?:\s*[a-f]\b)*\.?$|"
                    rf"(?<=[a-f])\.?$|"
                    rf"[0-9IVXLCDMivxlcdm];\s*$|"
                    rf"ibid\.?$|"
                    rf"adnot\.?$|"
                    rf"passim\.?$|"
                    rf"seqq?\.?$|"
                    rf"{CANON_REF}\.?$",
                    prev_text,
                    re.I,
                )
            )

            if has_prev_ref:
                should_split = True
            elif is_dash_sub:
                should_split = True
            elif match_head:
                if prev_text.endswith((".", ";")):
                    should_split = True
                elif re.match(r"^[A-ZÁÉÍÓÚÄËÏÖÜÆŒ]{3,}\b", line):
                    should_split = True

        if should_split:
            flush_entry()
            current_lines = [(line_no, line)]
        else:
            current_lines.append((line_no, line))

    flush_entry()
    return entries


def parse_ocr_file(file_path: Path | str) -> list[ExtractedEntry]:
    """Safely parse an OCR file (read-only), handling XML <bloco> via tools.ocr_xml_utils."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    entries: list[ExtractedEntry] = []

    # Use robust XML page parsing from tools.ocr_xml_utils
    ocr_page = parse_ocr_xml_page(content)

    context: dict[str, Any] = {"last_columns": [], "last_tome": None}

    if ocr_page.is_xml and ocr_page.blocks:
        for block in ocr_page.blocks:
            # We process main text blocks; ignore headers, footers and page margins
            if block.tipo in {"cabecalho", "rodape"} or block.tag_name in {"cabecalho", "rodape"}:
                continue

            block_entries = deconcatenate_text_block(
                block_text=block.content_raw or block.content_clean,
                source_file=str(path),
                base_line=1,
                bbox=block.bbox,
                inherited_context=context,
            )
            if block_entries:
                # Update rolling context from last entry
                last_entry = block_entries[-1]
                if last_entry.inferred_columns:
                    context["last_columns"] = list(last_entry.inferred_columns)
            entries.extend(block_entries)
    else:
        # Plain text fallback
        entries = deconcatenate_text_block(
            block_text=content,
            source_file=str(path),
            base_line=1,
            bbox=None,
            inherited_context=context,
        )

    return entries

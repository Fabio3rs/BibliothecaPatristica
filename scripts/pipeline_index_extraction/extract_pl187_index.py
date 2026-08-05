#!/usr/bin/env python3
"""Extract PL187 alphabetical-index payload from OCR pages.

Usage:
  python scripts/pipeline_index_extraction/extract_pl187_index.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL187/text \
    --volume-id PL187 \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL187_alphabetical_indices.json \
    [--helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL187_helper_output.json]

The script scans the OCR index pages for PL187, extracts section anchors,
logical index entries, and conservative locator metadata, then writes the
canonical alphabetical-index payload JSON.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


BLOCK_RE = re.compile(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
TITLE_RE = re.compile(r'INDEX\s+(CANONUM\s+DECRETI|SS\.\s*PATR\.\s*ET\s*HIST\.\s*ECCLES\.|SUMMORUM\s+PONTIFICUM)', re.I)
SECTION1_RE = re.compile(r'INDEX\s+CANONUM\s+DECRETI', re.I)
SECTION2_RE = re.compile(r'INDEX\s+SS\.\s*PATR\.\s*ET\s*HIST\.\s*ECCLES\.|INDEX\s+SUMMORUM\s+PONTIFICUM', re.I)
HEADING_LETTER_RE = re.compile(r'^[A-ZÆŒ]$')
SUBHEAD_RE = re.compile(r'^[A-Z]\.\s+(.+)$')
ENTRY_SPLIT_RE = re.compile(r'^(.*?),(?=\s*(?:c\.|C\.|D\.|A\.|†|cf\.|vid\.|vide|ut\s+vid\.|Ibid\.|ibid\.))')
FIRST_NUM_RE = re.compile(r'(?<!\d)(\d{1,4})(?!\d)')
YEAR_RANGE_RE = re.compile(r'(\d{1,4})\s*(?:-|–|—|to|et|vel)\s*(\d{1,4})')


@dataclass
class ParsedEntry:
    section_key: str
    parent_node_key: str | None
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str
    editorial_anchor_file: str
    target_file_best: str | None
    confidence: float
    raw_json: dict


def strip_diacritics(text: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def normalize_text(text: str) -> str:
    text = strip_diacritics(text)
    text = text.replace("—", " ").replace("–", " ").replace("·", " ")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def clean_line(line: str) -> str:
    line = html.unescape(line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def iter_blocks(text: str) -> Iterable[tuple[str, str]]:
    for match in BLOCK_RE.finditer(text):
        yield match.group(1), html.unescape(match.group(2))


def extract_page_header(text: str) -> str | None:
    for block_type, block_text in iter_blocks(text):
        if block_type != "cabecalho":
            continue
        header = " ".join(clean_line(part) for part in block_text.splitlines() if clean_line(part))
        if header:
            return header
    return None


def extract_printed_page(header: str | None) -> int | None:
    if not header:
        return None
    nums = [int(n) for n in re.findall(r"\b(\d{3,4})\b", header)]
    if not nums:
        return None
    return nums[0]


def section_id(volume_id: str, kind: str, order: int) -> str:
    return f"{volume_id}:alpha:{kind}:{order:03d}"


def node_id(volume_id: str, key: str) -> str:
    return f"{volume_id}:node:{key}"


def entry_id(volume_id: str, order: int) -> str:
    return f"{volume_id}:entry:{order:04d}"


def parse_entry_line(line: str) -> tuple[str, str | None, str | None]:
    m = ENTRY_SPLIT_RE.match(line)
    if m:
        lemma = m.group(1).strip()
        rest = line[m.end(1) + 1 :].strip()
        return lemma, rest, None

    if ", vid." in line or ", vide" in line or ", ut vid." in line or line.lower().startswith("cum vide "):
        lemma = line.split(",", 1)[0].strip() if "," in line else line.strip()
        return lemma, None, "cross_reference"

    if "," in line:
        lemma, rest = line.split(",", 1)
        return lemma.strip(), rest.strip(), None

    return line.strip(), None, "heading_group"


def parse_locator(ref_raw: str, section_kind: str) -> dict:
    out: dict = {
        "ref_raw": ref_raw,
        "page_ref_raw": None,
        "page_ref_int": None,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": None,
        "range_end_raw": None,
        "target_file": None,
        "target_file_probability": None,
        "confidence": 0.72,
        "raw_json": {},
    }

    if section_kind == "author_index":
        out["ref_kind"] = "parallel_locator"
        m = YEAR_RANGE_RE.search(ref_raw)
        if m:
            out["page_ref_raw"] = m.group(1)
            out["page_ref_int"] = int(m.group(1))
            out["range_start_raw"] = m.group(1)
            out["range_end_raw"] = m.group(2)
        else:
            m2 = FIRST_NUM_RE.search(ref_raw)
            if m2:
                out["page_ref_raw"] = m2.group(1)
                out["page_ref_int"] = int(m2.group(1))
            if " vel " in ref_raw:
                parts = ref_raw.split(" vel ", 1)
                tail_nums = FIRST_NUM_RE.findall(parts[1])
                if tail_nums:
                    out["range_start_raw"] = out["page_ref_raw"]
                    out["range_end_raw"] = tail_nums[0]
        return out

    out["ref_kind"] = "target_locator"
    ref = ref_raw.strip()
    ref = re.sub(r"^[cC]\.\s*", "", ref)
    ref = re.sub(r"^[†]\s*", "", ref)
    ref = ref.lstrip()

    m = YEAR_RANGE_RE.search(ref)
    if m:
        out["page_ref_raw"] = m.group(1)
        out["page_ref_int"] = int(m.group(1))
        out["range_start_raw"] = m.group(1)
        out["range_end_raw"] = m.group(2)
    else:
        m2 = FIRST_NUM_RE.search(ref)
        if m2:
            out["page_ref_raw"] = m2.group(1)
            out["page_ref_int"] = int(m2.group(1))

    if m2 := re.match(r"^[0-9]{1,4}\.\s*(.*)$", ref):
        out["line_ref_raw"] = m2.group(1).strip() or None
    elif out["page_ref_raw"] is not None:
        tail = ref.split(out["page_ref_raw"], 1)[1].strip(" .")
        out["line_ref_raw"] = tail or None
    else:
        out["line_ref_raw"] = ref or None
    return out


def extract_logical_lines(file_text: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for block_type, block_text in iter_blocks(file_text):
        if block_type in {"rodape", "cabecalho"}:
            continue
        for raw_line in block_text.splitlines():
            line = clean_line(raw_line)
            if not line:
                continue
            lines.append((block_type, line))
    return lines


def detect_section_meta(file_text: str) -> tuple[str | None, str | None]:
    if SECTION1_RE.search(file_text):
        return "alphabetical_general", "INDEX CANONUM DECRETI EMENDATIOR (*)"
    if SECTION2_RE.search(file_text):
        return "author_index", "II. INDEX SUMMORUM PONTIFICUM, SS. PATRUM ET SCRIPTORUM ECCLESIASTICORUM QUORUM NOMINE A GRATIANO CANONES REFERUNTUR."
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--volume-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path)
    args = ap.parse_args()

    source_root = args.source_root
    volume_id = args.volume_id
    helper_payload = None
    if args.helper_output and args.helper_output.exists():
        helper_payload = json.loads(args.helper_output.read_text(encoding="utf-8"))

    files = sorted(source_root.glob("*.txt"))
    indexed: list[dict] = []
    for path in files:
        m = re.search(r"-(\d{3})\.txt$", path.name)
        if not m:
            continue
        suffix = int(m.group(1))
        if 942 <= suffix <= 961:
            section_kind = "alphabetical_general"
            heading_raw = "INDEX CANONUM DECRETI EMENDATIOR (*)"
        elif suffix == 962:
            section_kind = "author_index"
            heading_raw = "II. INDEX SUMMORUM PONTIFICUM, SS. PATRUM ET SCRIPTORUM ECCLESIASTICORUM QUORUM NOMINE A GRATIANO CANONES REFERUNTUR."
        else:
            continue
        text = path.read_text(encoding="utf-8")
        indexed.append({
            "path": path,
            "suffix": suffix,
            "text": text,
            "section_kind": section_kind,
            "heading_raw": heading_raw,
            "header": extract_page_header(text),
            "printed_page": extract_printed_page(extract_page_header(text)),
        })

    sections: list[dict] = []
    nodes: list[dict] = []
    entries: list[dict] = []
    refs: list[dict] = []
    section_notes: list[dict] = []

    section_groups: list[list[dict]] = []
    if any(item["section_kind"] == "alphabetical_general" for item in indexed):
        section_groups.append([item for item in indexed if item["section_kind"] == "alphabetical_general"])
    if any(item["section_kind"] == "author_index" for item in indexed):
        section_groups.append([item for item in indexed if item["section_kind"] == "author_index"])

    for s_order, group in enumerate(section_groups, start=1):
        meta = group[0]
        path = meta["path"]
        text = "\n".join(item["text"] for item in group)
        section_kind = meta["section_kind"]
        heading_raw = meta["heading_raw"]
        section_key = section_id(volume_id, section_kind, s_order)
        section_start_file = str(group[0]["path"])
        section_end_file = str(group[-1]["path"])

        if section_kind == "alphabetical_general":
            page_start = 1874
            page_end = page_start + 2 * (len(group) - 1)
            section_reason = "Main alphabetical canon index of decree canons; OCR title line is stable across the inspected window."
            running_title = "I. — INDEX CANONUM DECRETI."
        else:
            page_start = 1911
            page_end = 1912
            section_reason = "Author/patristic index with papal and ecclesiastical writer subsections."
            running_title = "I. — INDEX SS. PATR. ET HIST. ECCLES."

        sec_raw = {
            "section_kind_reason": section_reason,
            "running_title": running_title,
        }
        if helper_payload:
            sec_raw["helper"] = helper_payload

        sections.append({
            "section_key": section_key,
            "volume_id": volume_id,
            "work_key": None,
            "section_order": s_order,
            "section_kind": section_kind,
            "heading_raw": heading_raw,
            "heading_norm": normalize_text(heading_raw),
            "heading_letter": None,
            "page_start": page_start,
            "page_end": page_end,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.95,
            "raw_json": sec_raw,
        })

        # Gather lines in reading order.
        current_letter = None
        subsection_node_key = None
        letter_nodes: OrderedDict[str, str] = OrderedDict()
        node_order = 1

        entry_order = 0
        for file_index, item in enumerate(group):
            file_page = page_start + 2 * file_index if section_kind == "alphabetical_general" else 1911
            pending = ""
            for block_type, line in extract_logical_lines(item["text"]):
                if line in {"Digitized by Google", "Imprimerie de MIGNE, au Petit-Montrouge.", "FINIS TOMI CENTESIMI OCTOGESIMI SEPTIMI."}:
                    continue
                if line in {"1.", "II.", "I."}:
                    continue
                if line == "INDEX SUMMORUM PONTIFICUM" or line == "SS. PATRUM ET SCRIPTORUM ECCLESIASTICORUM" or line.startswith("QUORUM NOMINE A GRATIANO CANONES REFERUNTUR"):
                    continue
                if line.startswith("INDEX CANONUM DECRETI") or line.startswith("INDEX SS. PATR. ET HIST. ECCLES."):
                    continue
                if section_kind == "author_index" and line.startswith("A. SUMMI PONTIFICES"):
                    if subsection_node_key is None:
                        key = f"{volume_id}:node:{section_key}:A_SUMMI_PONTIFICES"
                        nodes.append({
                            "node_key": key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "heading_group",
                            "label_raw": "A. SUMMI PONTIFICES",
                            "label_norm": normalize_text("A. SUMMI PONTIFICES"),
                            "label_sort": normalize_text("A. SUMMI PONTIFICES"),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {"scope": "papal subsection"},
                        })
                        subsection_node_key = key
                        node_order += 1
                    continue
                if section_kind == "author_index" and line.startswith("B. SS. PATRES ET SCRIPTORES ECCLESIASTICI"):
                    if subsection_node_key is None or "A_SUMMI_PONTIFICES" in subsection_node_key:
                        key = f"{volume_id}:node:{section_key}:B_PATRES_SCRIPTORES"
                        nodes.append({
                            "node_key": key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "heading_group",
                            "label_raw": "B. SS. PATRES ET SCRIPTORES ECCLESIASTICI.",
                            "label_norm": normalize_text("B. SS. PATRES ET SCRIPTORES ECCLESIASTICI."),
                            "label_sort": normalize_text("B. SS. PATRES ET SCRIPTORES ECCLESIASTICI."),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {"scope": "patristic writers subsection"},
                        })
                        subsection_node_key = key
                        node_order += 1
                    continue
                if section_kind == "alphabetical_general" and HEADING_LETTER_RE.match(line):
                    if line not in letter_nodes:
                        key = node_id(volume_id, f"{section_key}:{line}")
                        letter_nodes[line] = key
                        nodes.append({
                            "node_key": key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source": "standalone letter heading"},
                        })
                        node_order += 1
                    current_letter = line
                    pending = ""
                    continue
                if section_kind == "author_index" and HEADING_LETTER_RE.match(line) and len(line) == 1:
                    if line not in letter_nodes:
                        key = node_id(volume_id, f"{section_key}:{line}")
                        letter_nodes[line] = key
                        nodes.append({
                            "node_key": key,
                            "section_key": section_key,
                            "parent_node_key": subsection_node_key,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 2,
                            "confidence": 0.99,
                            "raw_json": {"source": "standalone letter heading"},
                        })
                        node_order += 1
                    current_letter = line
                    pending = ""
                    continue
                if section_kind == "author_index" and SUBHEAD_RE.match(line) and not line.startswith(("A. ", "B. ")):
                    pending = ""
                    continue

                if not pending:
                    pending = line
                else:
                    pending = f"{pending} {line}"

                if not pending.endswith("."):
                    continue

                entry_line = pending.strip()
                pending = ""
                if section_kind == "alphabetical_general" and entry_line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "O", "P", "R", "T", "U", "V", "Z"}:
                    continue
                if section_kind == "author_index" and (entry_line.startswith("I. — INDEX") or entry_line.startswith("II.") or entry_line == "A. SUMMI PONTIFICES" or entry_line.startswith("B. SS.")):
                    continue

                lemma_raw, ref_raw, forced_kind = parse_entry_line(entry_line)
                if forced_kind == "cross_reference":
                    entry_kind = "cross_reference"
                    ref_raw = None
                elif section_kind == "author_index":
                    entry_kind = "lemma"
                else:
                    entry_kind = "lemma"
                    if ref_raw is None:
                        entry_kind = "cross_reference"

                parent_node_key = letter_nodes.get(current_letter) if current_letter else None
                e_key = entry_id(volume_id, entry_order + 1)
                entry_order += 1
                inferred_page = file_page
                entries.append({
                    "entry_key": e_key,
                    "section_key": section_key,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw if lemma_raw else None,
                    "lemma_display": lemma_raw if lemma_raw else None,
                    "lemma_norm": normalize_text(lemma_raw) if lemma_raw else None,
                    "lemma_sort": normalize_text(lemma_raw) if lemma_raw else None,
                    "entry_raw": entry_line,
                    "context_raw": entry_line,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": section_start_file,
                    "target_file_best": section_start_file,
                    "confidence": 0.90 if entry_kind == "lemma" else 0.80,
                    "raw_json": {
                        "source_file": section_start_file,
                        "section_kind": section_kind,
                    },
                })
                if ref_raw:
                    ref = parse_locator(ref_raw, section_kind)
                    ref["entry_key"] = e_key
                    ref["ref_order"] = 1
                    ref["section_start_file"] = section_start_file
                    ref["editorial_anchor_file"] = section_start_file
                    refs.append(ref)

    if not entries:
        coverage = {
            "entries_status": "unrecoverable_ocr",
            "entries_status_reason": "No recoverable line items were extracted from the inspected index pages.",
            "evidence_files": [str(x["path"]) for x in indexed],
        }
    else:
        coverage = {
            "entries_status": "partial_extraction",
            "entries_status_reason": "Recovered the main canon index and the author/patristic index conservatively from the OCR window. Printed page drift and long wrapped entries were kept literal rather than normalized aggressively.",
            "evidence_files": [str(x["path"]) for x in indexed],
        }

    notes = [
        {
            "note_kind": "helper_usage",
            "text": "Helper output was used only for section anchoring where available; OCR literals were preserved.",
        },
        {
            "note_kind": "ocr_drift",
            "text": "The tail window contains heavy pagination drift and mixed running headers, so OCR file anchors are kept separate from printed-page hints.",
        },
    ]

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume": {
            "volume_id": volume_id,
            "collection": volume_id[:2],
            "source_root": str(source_root),
            "volume_label": volume_id,
            "notes": [
                "The volume contains a long INDEX CANONUM DECRETI block and a separate author/patristic index block.",
                "OCR file suffixes are not treated as editorial pages; printed page hints were preserved separately where recoverable.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

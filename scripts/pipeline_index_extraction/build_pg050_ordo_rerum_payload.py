#!/usr/bin/env python3
"""Build the PG050 Ordo Rerum alphabetical-index payload.

Run:
  python scripts/pipeline_index_extraction/build_pg050_ordo_rerum_payload.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


VOLUME_ID = "PG050"
SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PG050/text")
OUTPUT_FILE = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG050_alphabetical_indices.json")
HELPER_OUTPUT = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG050_helper_output.json")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(text: str) -> str:
    text = text.replace("œ", "oe").replace("Œ", "oe").replace("æ", "ae").replace("Æ", "ae")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_block_lines(path: Path) -> list[str]:
    lines: list[str] = []
    in_text = False
    in_block = False
    block_kind = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("  <bloco "):
            in_block = True
            block_kind = "texto_principal" if 'tipo="texto_principal"' in raw else "cabecalho" if 'tipo="cabecalho"' in raw else None
            continue
        if in_block and raw.strip() == "</bloco>":
            in_block = False
            block_kind = None
            continue
        if raw.strip() == "<notas>":
            break
        if in_block and block_kind in {"texto_principal", "cabecalho"}:
            s = raw.strip()
            if not s or s == "Digitized by Google":
                continue
            if s.startswith("<"):
                continue
            # Keep OCR text lines only.
            lines.append(s)
    return lines


START_RE = re.compile(
    r"^(?:"
    r"PRÆFATIO|"
    r"S\. PATRIS NOSTRI|"
    r"S\. JOANNIS CHRYSOSTOMI|"
    r"S\. JOANNIS CHRYSOSTOMI DUBIA OPERA\.|"
    r"HOMILIA\b|"
    r"HOMIL\.|"
    r"HOM\.|"
    r"MONITUM\b|"
    r"ADMONITIO\b|"
    r"CATECHESIS\b|"
    r"CATECH\.|"
    r"ADVERSUS EOS QUI A DIVINIS ABSUNT OFFICIIS\b|"
    r"IN CŒMETERII APPELLATIONEM\b|"
    r"IN QUATRIDUANUM LAZARUM\b|"
    r"IN S\. MARTYREM IGNATIUM DEFENSIO\b|"
    r"IN S\. BABYLAM\b|"
    r"IN S\. THEOPHANIA\b|"
    r"DE HIEROMARTYRE PHOCA\b|"
    r"DE HIEROMARTYRE BABYLA\b|"
    r"LAUDATIO\b|"
    r"ORATIO\b|"
    r"ORAT\.|"
    r"EPISTOLA\b|"
    r"SERMO\b|"
    r"SELECTA\b|"
    r"SPURIA\.|"
    r"DE FATO ET PROVIDENTIA\b"
    r")",
    re.IGNORECASE,
)


def should_skip(line: str) -> bool:
    if re.fullmatch(r"[0-9]+(?:\s*[–-]\s*[0-9]+)?", line):
        return True
    if line in {
        "ORDO RERUM",
        "QUÆ IN DUABUS PARTIBUS TOMI SECUNDI CONTINENTUR.",
        "QUÆ IN DUABUS PARTIBUS TOMI II CONTINENTUR.",
    }:
        return True
    if "QUÆ IN DUABUS PARTIBUS TOMI SECUNDI CONTINENTUR." in line or "QUÆ IN DUABUS PARTIBUS TOMI II CONTINENTUR." in line:
        return True
    if line in {
        "Transcrição literal de índice (Ordo Rerum) em latim.",
        "Transcrição de página de índice (Ordo Rerum) em latim.",
        "Transcrição de índice (Patrologia Graeca/Latina). Algumas numerações de página no texto original parecem conter erros tipográficos (ex: 599-408, 455-442, 515-514), transcritos conforme visíveis. A palavra no início da coluna esquerda \"sonæ\" é continuação da página anterior. A palavra no início da coluna direita \"racula\" completa \"miracula\" do final da coluna esquerda.",
        "Transcrição de índice (Ordo Rerum) em latim. Layout em duas colunas. Números de página 849 e 850 no topo. Alguns números de página no texto parecem sobrepostos ou com erros de impressão original (ex: 685-684).",
        "Transcrição literal mantendo erros tipográficos aparentes do original (ex: \"censeudi\", \"impeditat\", \"Præcursorern\", \"negligentis\").",
    }:
        return True
    if line.startswith("Digitized by Google"):
        return True
    if line.startswith("Parisiis. — Ex Typis J.-P. MIGNE."):
        return True
    if line.startswith("FINIS TOMI QUINQUAGESIMI."):
        return True
    return False


def parse_page_ref(text: str) -> tuple[str | None, int | None, str | None, str | None, str]:
    stripped = text.rstrip()
    m = re.search(r"(?:\s+|^)(ibid\.|Ibid\.|ibid|Ibid\.?)\.?$", stripped, re.IGNORECASE)
    if m:
        raw = m.group(1)
        return raw, None, None, None, stripped[: m.start()].rstrip(" ,.;")
    m = re.search(r"\s+(\d+)\s*[-–]\s*(\d+)\.?\s*$", stripped)
    if m:
        start, end = m.group(1), m.group(2)
        return f"{start}-{end}", int(start), start, end, stripped[: m.start()].rstrip(" ,.;")
    m = re.search(r"\s+(\d+)\.?\s*$", stripped)
    if m:
        start = m.group(1)
        return start, int(start), start, None, stripped[: m.start()].rstrip(" ,.;")
    return None, None, None, None, stripped


SCRIPTURE_RULES = [
    {
        "pattern": re.compile(r"\(1 Tim\. 5, 23\)"),
        "book_raw": "1 Tim.",
        "book_norm": "1 Timóteo",
        "chapter_start": 5,
        "verse_start": 23,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(1 Tim\. 6, 17\)"),
        "book_raw": "1 Tim.",
        "book_norm": "1 Timóteo",
        "chapter_start": 6,
        "verse_start": 17,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Gen\. 3, 8\)"),
        "book_raw": "Gen.",
        "book_norm": "Gênesis",
        "chapter_start": 3,
        "verse_start": 8,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Psal\. 18, 2\)"),
        "book_raw": "Psal.",
        "book_norm": "Salmos",
        "chapter_start": 18,
        "verse_start": 2,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Eccli\. 9, 20\)"),
        "book_raw": "Eccli.",
        "book_norm": "Eclesiástico",
        "chapter_start": 9,
        "verse_start": 20,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Philem\. 1\)"),
        "book_raw": "Philem.",
        "book_norm": "Filemom",
        "chapter_start": 1,
        "verse_start": None,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Philipp\. 4, 4\)"),
        "book_raw": "Philipp.",
        "book_norm": "Filipenses",
        "chapter_start": 4,
        "verse_start": 4,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"\(Luc\. 2\. 1\)"),
        "book_raw": "Luc.",
        "book_norm": "Lucas",
        "chapter_start": 2,
        "verse_start": 1,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
    {
        "pattern": re.compile(r"Psal\. 141, Voce mea ad Dominum clamavi; voce mea ad Deum deprecatus sum\.", re.IGNORECASE),
        "book_raw": "Psal.",
        "book_norm": "Salmos",
        "chapter_start": 141,
        "verse_start": None,
        "chapter_end": None,
        "verse_end": None,
        "is_range": False,
    },
]


def extract_scripture_refs(entry_key: str, entry_raw: str) -> list[dict]:
    refs: list[dict] = []
    for idx, rule in enumerate(SCRIPTURE_RULES, start=1):
        if rule["pattern"].search(entry_raw):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": len(refs) + 1,
                    "ref_role": "citation",
                    "ref_raw": rule["pattern"].search(entry_raw).group(0).strip("()"),
                    "book_raw": rule["book_raw"],
                    "book_norm": rule["book_norm"],
                    "chapter_start": rule["chapter_start"],
                    "verse_start": rule["verse_start"],
                    "chapter_end": rule["chapter_end"],
                    "verse_end": rule["verse_end"],
                    "is_range": rule["is_range"],
                    "confidence": 0.96 if rule["book_norm"] != "Salmos" or rule["chapter_start"] != 141 else 0.82,
                    "raw_json": {
                        "matched_pattern": rule["pattern"].pattern,
                    },
                }
            )
    return refs


def classify_entry(entry_raw_no_page: str) -> str:
    up = entry_raw_no_page.upper()
    if up.startswith("MONITUM") or up.startswith("ADMONITIO"):
        return "editorial_note"
    if up.startswith("SPURIA"):
        return "heading_group"
    if up.startswith("S. JOANNIS CHRYSOSTOMI SERMONES PANEGYRICI"):
        return "heading_group"
    if up.startswith("S. JOANNIS CHRYSOSTOMI DUBIA OPERA."):
        return "heading_group"
    if up.startswith("S. PATRIS NOSTRI JOANNIS CHRYSOSTOMI, ARCHIEPISCOPI CONSTANTINOPOLITANI, DE FATO ET PROVIDENTIA ORATIONES SEX."):
        return "heading_group"
    if up.startswith("SELECTA"):
        return "heading_group"
    return "lemma"


def build_payload() -> dict:
    source_files = [SOURCE_ROOT / f"a0551315-7b7b-4f0e-bc32-86309a4493e9-{i}.txt" for i in range(430, 435)]
    lines: list[tuple[str, str]] = []
    for path in source_files:
        for line in extract_block_lines(path):
            if should_skip(line):
                continue
            if re.fullmatch(r"\d{3,4}\s+[A-ZÆŒ].*", line) and " ORDO RERUM " in line:
                # Header-only spread marker; keep it out of the serialized entries.
                continue
            lines.append((str(path), line))

    entries = []
    refs = []
    scripture_refs = []
    current = None

    def flush_current() -> None:
        nonlocal current
        if current is None:
            return
        entry_raw = " ".join(piece for _, piece in current["lines"]).strip()
        entry_raw = re.sub(r"\s+", " ", entry_raw)
        page_raw, page_int, page_start_raw, page_end_raw, entry_no_page = parse_page_ref(entry_raw)
        entry_key = f"{VOLUME_ID}:entry:{len(entries) + 1:04d}"
        entry_kind = classify_entry(entry_no_page)
        lemma_raw = entry_no_page
        lemma_display = lemma_raw
        lemma_norm = normalize_text(lemma_raw)
        entry = {
            "entry_key": entry_key,
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
            "parent_node_key": None,
            "entry_order": len(entries) + 1,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_norm,
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": str(source_files[0]),
            "editorial_anchor_file": current["lines"][0][0],
            "target_file_best": None,
            "confidence": 0.9 if page_int is not None else 0.74,
            "raw_json": {
                "source_files": sorted({file for file, _ in current["lines"]}),
                "section_kind": "ordo_rerum",
                "line_count": len(current["lines"]),
            },
        }
        page_refs = []
        if page_raw is not None and page_raw.lower() != "ibid":
            page_ref = {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_range" if page_end_raw is not None else "editorial_page",
                "ref_raw": page_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page_start_raw,
                "range_end_raw": page_end_raw,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": str(source_files[0]),
                "editorial_anchor_file": current["lines"][0][0],
                "confidence": 0.92 if page_end_raw is not None else 0.9,
                "raw_json": {
                    "source_files": sorted({file for file, _ in current["lines"]}),
                    "parsed_from": "trailing page ref",
                },
            }
            page_refs.append(page_ref)
            refs.append(page_ref)
        else:
            entry["raw_json"]["note"] = "ibid. remission preserved in entry_raw; no material ref serialized."

        srefs = extract_scripture_refs(entry_key, entry_raw)
        scripture_refs.extend(srefs)
        if srefs:
            entry["raw_json"]["scripture_refs"] = [sr["ref_raw"] for sr in srefs]
            entry["confidence"] = max(entry["confidence"], 0.92)

        entry["raw_json"]["page_refs"] = page_refs
        entries.append(entry)
        current = None

    for file_path, line in lines:
        if current is None:
            current = {"lines": [(file_path, line)]}
            continue
        if START_RE.match(line):
            flush_current()
            current = {"lines": [(file_path, line)]}
        else:
            current["lines"].append((file_path, line))

    flush_current()

    helper = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    section_helper = next((e for e in helper["entries"] if e["entry_id"] == "pg050_ordo_rerum"), None)

    section_raw_json = {
        "helper_status": section_helper["status"] if section_helper else "unresolved",
        "helper_entry_id": "pg050_ordo_rerum" if section_helper else None,
        "helper_candidate_role": section_helper["best_candidate"].get("candidate_role") if section_helper and section_helper.get("best_candidate") else None,
        "helper_reason_summary": section_helper["best_candidate"].get("reason_summary") if section_helper and section_helper.get("best_candidate") else None,
        "helper_best_candidate": section_helper.get("best_candidate") if section_helper else None,
        "section_kind_reason": "Editorial closure / table of contents at the end of the volume; the OCR tail is a dense Ordo Rerum list with many synonymic oration titles and several ibid. remissions.",
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_utc(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "PG050 ends with an Ordo Rerum contents block and subsequent guard pages.",
                "The OCR tail contains many literal ibid. remissions; those were kept at entry level and not emitted as material refs.",
            ],
        },
        "sections": [
            {
                "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": "ORDO RERUM. QUÆ IN DUABUS PARTIBUS TOMI SECUNDI CONTINENTUR.",
                "heading_norm": normalize_text("ORDO RERUM. QUÆ IN DUABUS PARTIBUS TOMI SECUNDI CONTINENTUR."),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": str(source_files[0]),
                "file_end": str(source_files[-1]),
                "confidence": 0.96,
                "raw_json": section_raw_json,
            }
        ],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the Ordo Rerum contents tail from OCR files 430-434; the section is dense but structurally regular, and later files are guard pages.",
            "evidence_files": [str(path) for path in source_files],
        },
        "notes": [
            "Section headings were kept separate from entries.",
            "Biblical citations inside titles were parsed into scripture_refs when the book label was explicit.",
            "Bare ibid. remissions were not promoted to refs.",
        ],
    }
    return payload


def main() -> None:
    payload = build_payload()
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

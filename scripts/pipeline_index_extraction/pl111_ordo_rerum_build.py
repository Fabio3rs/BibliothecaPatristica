#!/usr/bin/env python3
"""
Build the PL111 final-volume ORDO RERUM payload and helper request.

Usage:
  python scripts/pipeline_index_extraction/pl111_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL111/text \
    --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL111_helper_request.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL111_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECTION_FILES = [811, 812, 814]
ENTRY_START_RE = re.compile(
    r"^(?:\d{1,4}\s+)?(?:LIBER|CAPUT|CAP\.|Præfatio|Prologus|COMMENTARIA|ENARRATIONES|EXPOSITIO|Iustitiam\b|Sequitur\b)"
)
PAGE_END_RE = re.compile(r"^(.*?)(?:\s+)(\d{1,4})$")
PAGE_START_RE = re.compile(r"^(\d{1,4})\s+(.*)$")
LEADING_PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
SPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class TocEntry:
    entry_order: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    query_names: list[str]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    text = text.casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    text = SPACE_RE.sub(" ", text)
    return text.strip()


def extract_blocks(text: str) -> dict[str, list[str]]:
    header_texts: list[str] = []
    body_texts: list[str] = []
    footer_texts: list[str] = []
    if not text.strip().startswith("<pagina"):
        return {"cabecalho": header_texts, "texto_principal": [text], "rodape": footer_texts}

    for match in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", text, flags=re.S):
        attrs = match.group("attrs") or ""
        content = TAG_RE.sub(" ", match.group("content") or "")
        content = content.replace("\xa0", " ")
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = (tipo_m.group(1) if tipo_m else "").strip().lower()
        if not content.strip():
            continue
        if tipo == "cabecalho":
            header_texts.append(content)
        elif tipo == "texto_principal":
            body_texts.append(content)
        elif tipo == "rodape":
            footer_texts.append(content)
    return {"cabecalho": header_texts, "texto_principal": body_texts, "rodape": footer_texts}


def load_lines(path: Path) -> tuple[list[str], str]:
    blocks = extract_blocks(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for block_name in ("cabecalho", "texto_principal", "rodape"):
        for chunk in blocks.get(block_name, []):
            for raw_line in chunk.splitlines():
                line = SPACE_RE.sub(" ", raw_line.replace("\xa0", " ")).strip()
                if line:
                    lines.append(line)
    return lines, "\n".join(blocks.get("cabecalho", []) + blocks.get("texto_principal", []) + blocks.get("rodape", []))


def parse_toc_entries(source_root: Path) -> tuple[dict[str, Any], list[TocEntry]]:
    entries: list[TocEntry] = []
    section_heading_lines: list[str] = []
    in_section = False
    order = 0

    for seq in SECTION_FILES:
        path = source_root / f"0b288039-c8ab-4b08-ae56-5f5ca14b0e97-{seq}.txt"
        lines, _ = load_lines(path)
        for line in lines:
            if line == "ORDO RERUM" or line == "QUÆ IN HOC TOMO CONTINENTUR.":
                in_section = True
                section_heading_lines.append(line)
                continue
            if line.startswith("B. RABANI") or line.startswith("OPERUM ") or line.startswith("DE UNIVERSO."):
                continue
            if line.startswith("Digitized by Google") or line.startswith("THIS VOLUME DOES NOT CIRCULATE"):
                continue
            if not in_section:
                continue
            if line.startswith("161") or line.startswith("162") or line.startswith("4619"):
                # Header noise inside the OCR page, not a TOC entry.
                continue

            page_ref_raw: str | None = None
            page_ref_int: int | None = None
            lemma = line
            match_end = PAGE_END_RE.match(line)
            match_start = PAGE_START_RE.match(line)
            if match_end:
                lemma = match_end.group(1).strip()
                page_ref_raw = match_end.group(2)
                page_ref_int = int(page_ref_raw)
            elif match_start and (line.startswith("529 ") or line.startswith("479 ") or line.startswith("1623 ") or line.startswith("1615 ")):
                page_ref_raw = match_start.group(1)
                page_ref_int = int(page_ref_raw)
                lemma = match_start.group(2).strip()

            order += 1
            lemma = lemma.strip()
            if not lemma:
                continue
            query_names = [lemma]
            if " — " in lemma:
                query_names.append(lemma.split(" — ", 1)[1].strip())
            if " - " in lemma:
                query_names.append(lemma.split(" - ", 1)[1].strip())
            if ". " in lemma and lemma.split(". ", 1)[1]:
                query_names.append(lemma.split(". ", 1)[1].strip())
            query_names = [q for q in dict.fromkeys(q for q in query_names if q)]
            entries.append(
                TocEntry(
                    entry_order=order,
                    entry_raw=line,
                    lemma_raw=lemma,
                    page_ref_raw=page_ref_raw,
                    page_ref_int=page_ref_int,
                    query_names=query_names,
                )
            )

    section_heading = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
    if section_heading_lines:
        section_heading = " ".join(section_heading_lines).strip()
    section = {
        "section_key": "pl111_ordo_rerum_final",
        "volume_id": "PL111",
        "work_key": "B. RABANI MAURI OPERUM SECUNDÆ PARTIS CONTINUATIO. DE UNIVERSO.",
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": section_heading,
        "heading_norm": normalize(section_heading),
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-811.txt"),
        "file_end": str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-814.txt"),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "final_volume_ordo_rerum_closure",
            "section_evidence_files": [
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-811.txt"),
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-812.txt"),
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-814.txt"),
            ],
        },
    }
    return section, entries


def build_helper_request(volume_id: str, source_root: Path, entries: list[TocEntry]) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": f"{volume_id.lower()}_{item.entry_order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names,
                "page_hints": [item.page_ref_raw] if item.page_ref_raw else [],
                "page_hint_ints": [item.page_ref_int] if item.page_ref_int is not None else [],
                "context_raw": item.entry_raw,
            }
            for item in entries
        ],
    }


def load_volume_text_map(source_root: Path) -> dict[Path, dict[str, Any]]:
    out: dict[Path, dict[str, Any]] = {}
    for path in sorted(source_root.glob("*.txt")):
        lines, joined = load_lines(path)
        norm = normalize(joined)
        header_norm = normalize("\n".join(lines[:4]))
        start_norm = normalize("\n".join(lines[:14]))
        start_blob = normalize(" ".join(lines[:14]))
        out[path] = {
            "lines": lines,
            "joined": joined,
            "norm": norm,
            "header_norm": header_norm,
            "start_norm": start_norm,
            "start_blob": start_blob,
        }
    return out


def resolve_target_file(title: str, source_root: Path, text_map: dict[Path, dict[str, Any]]) -> tuple[Path | None, float, list[str]]:
    norm_title = normalize(title)
    tokens = [tok for tok in norm_title.split() if len(tok) >= 2]
    section_seq_markers = {f"-{seq}.txt" for seq in SECTION_FILES}
    best: tuple[float, Path | None, list[str]] = (-1.0, None, [])
    for path, data in text_map.items():
        if any(path.name.endswith(marker) for marker in section_seq_markers):
            continue
        joined = data["norm"]
        if not joined:
            continue
        start_norm = data.get("start_norm", "")
        start_blob = data.get("start_blob", "")
        score = 0.0
        reasons: list[str] = []
        if norm_title and norm_title in joined:
            score += 10.0
            reasons.append("exact_norm_match")
        if norm_title and norm_title in start_norm:
            score += 8.0
            reasons.append("start_norm_match")
        if norm_title and norm_title in start_blob:
            score += 6.0
            reasons.append("start_blob_match")
        if norm_title and norm_title in data["header_norm"]:
            score += 2.0
            reasons.append("header_match")
        hits = sum(1 for tok in tokens if tok in joined)
        if hits:
            score += hits * 0.5
            reasons.append(f"token_hits={hits}")
        early_hits = sum(1 for tok in tokens if tok in start_norm)
        if early_hits:
            score += early_hits * 0.75
            reasons.append(f"early_token_hits={early_hits}")
        if score > best[0]:
            best = (score, path, reasons)
    if best[1] is None or best[0] <= 0:
        return None, 0.0, []
    return best[1], min(0.99, 0.6 + best[0] / 20.0), best[2]


def extract_page_ref_from_target(path: Path, text_map: dict[Path, dict[str, Any]]) -> int | None:
    lines = text_map[path]["lines"]
    for line in lines[:6]:
        m = re.search(r"\b(\d{1,4})\b", line)
        if m:
            return int(m.group(1))
    for line in lines:
        m = re.search(r"\b(\d{1,4})\b", line)
        if m:
            return int(m.group(1))
    return None


def build_payload(volume_id: str, source_root: Path, helper_request_path: Path, section: dict[str, Any], toc_entries: list[TocEntry]) -> dict[str, Any]:
    text_map = load_volume_text_map(source_root)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    notes: list[str] = []

    for item in toc_entries:
        target_file, target_prob, target_reasons = resolve_target_file(item.lemma_raw, source_root, text_map)
        target_file_str = str(target_file) if target_file else None
        inferred_page = item.page_ref_int
        if inferred_page is None and target_file:
            inferred_page = extract_page_ref_from_target(target_file, text_map)
        if inferred_page is None and target_file:
            inferred_page = extract_page_ref_from_target(target_file, text_map)
        if item.page_ref_int is None and inferred_page is not None:
            page_ref_raw = str(inferred_page)
            page_ref_int = inferred_page
        else:
            page_ref_raw = item.page_ref_raw
            page_ref_int = item.page_ref_int
        lemma_norm = normalize(item.lemma_raw)
        entry_key = f"{volume_id.lower()}_ordo_{item.entry_order:03d}"
        entry_raw = item.entry_raw
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": item.entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": lemma_norm,
                "lemma_sort": lemma_norm,
                "entry_raw": entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": inferred_page,
                "section_start_file": section["file_start"],
                "editorial_anchor_file": target_file_str,
                "target_file_best": target_file_str,
                "confidence": 0.9 if target_file else 0.45,
                "raw_json": {
                    "source": "pl111_ordo_rerum_build.py",
                    "query_names": item.query_names,
                    "target_resolution": {
                        "target_file": target_file_str,
                        "target_probability": target_prob,
                        "reasons": target_reasons,
                    },
                    "page_ref_source": "toc" if item.page_ref_int is not None else "target_header",
                    "helper_request": str(helper_request_path),
                },
            }
        )
        if page_ref_raw is not None and page_ref_int is not None:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_ref_raw,
                    "page_ref_raw": page_ref_raw,
                    "page_ref_int": page_ref_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file_str,
                    "target_file_probability": target_prob,
                    "section_start_file": section["file_start"],
                    "editorial_anchor_file": target_file_str,
                    "confidence": 0.9 if target_file else 0.45,
                    "raw_json": {
                        "source": "pl111_ordo_rerum_build.py",
                        "target_resolution": {
                            "target_file": target_file_str,
                            "target_probability": target_prob,
                            "reasons": target_reasons,
                        },
                        "helper_request": str(helper_request_path),
                    },
                }
            )
        else:
            notes.append(f"missing_page_ref:{entry_key}")

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": volume_id,
            "collection": "PL",
            "source_root": str(source_root),
            "volume_label": "PL111",
            "notes": "Final-volume ORDO RERUM closure; OCR body is two-column and some TOC lines lack explicit page numbers in the OCR literal.",
        },
        "sections": [section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "Final ORDO RERUM section recovered from OCR with direct target-file resolution.",
            "evidence_files": [
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-811.txt"),
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-812.txt"),
                str(source_root / "0b288039-c8ab-4b08-ae56-5f5ca14b0e97-814.txt"),
            ],
        },
        "notes": [
            "This volume ends with an ORDO RERUM closure rather than a true alphabetical index.",
            "Some OCR lines omit visible page numbers; those were resolved from the target body file headers.",
        ]
        + notes,
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--write-helper-request", action="store_true", default=True)
    args = ap.parse_args()

    section, toc_entries = parse_toc_entries(args.source_root)
    helper_request = build_helper_request("PL111", args.source_root, toc_entries)
    args.helper_request.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = build_payload("PL111", args.source_root, args.helper_request, section, toc_entries)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the PG092 alphabetical-index payload from OCR text.

Run:
  python scripts/pipeline_index_extraction/pg092_build_payload.py \
    --source-root teste/PG092/text \
    --output data/alphabetical_index_payloads/PG092_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECTION_SPLITS = {
    "PG092:alpha:analytic_subject:001": "INDEX AD CHRONICON PASCHALE.",
    "PG092:alpha:ordo_rerum:006": "ORDO RERUM",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def read_block_text(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, re.S):
        bloco_tipo = match.group(1)
        if bloco_tipo in {"rodape", "capa_ou_guarda"}:
            continue
        block_text = match.group(2)
        for line in block_text.splitlines():
            line = normalize_ws(line)
            if line:
                lines.append(line)
    return lines


def is_noise_line(line: str) -> bool:
    text = normalize_ws(line)
    if not text:
        return True
    if text == "Digitized by Google":
        return True
    if text in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Y", "Z"}:
        return True
    if re.fullmatch(r"\d{3,4}", text):
        return True
    if text.startswith("INDEX ") and text.upper() == text:
        return True
    if text.startswith("ORDO RERUM"):
        return True
    if text.startswith("INDICES."):
        return True
    if text.startswith("FINIS TOMI"):
        return True
    return False


def split_sections_882(lines: list[str]) -> tuple[list[str], list[str]]:
    marker = "INDEX AD CHRONICON PASCHALE."
    first: list[str] = []
    second: list[str] = []
    seen = False
    for line in lines:
        if not seen and line == marker:
            seen = True
            continue
        if seen:
            second.append(line)
        else:
            first.append(line)
    return first, second


def split_sections_899(lines: list[str]) -> tuple[list[str], list[str]]:
    marker = "ORDO RERUM"
    first: list[str] = []
    second: list[str] = []
    seen = False
    for line in lines:
        if not seen and line.startswith(marker):
            seen = True
            continue
        if seen:
            second.append(line)
        else:
            first.append(line)
    return first, second


def section_lines(source_root: Path, file_seq: int) -> list[str]:
    path = source_root / f"03d3e940-c85a-4a0e-a619-b6994efa9d12-{file_seq}.txt"
    return [line for line in read_block_text(path) if not is_noise_line(line)]


def split_entry_segments(text: str) -> list[str]:
    text = normalize_ws(text)
    if not text:
        return []
    segments: list[str] = []
    start = 0
    for match in re.finditer(r"\.\s+(?=[A-Za-zÆŒÀ-ÿΑ-Ωα-ω])", text):
        boundary = match.start()
        window = text[max(0, boundary - 40):boundary]
        if not (
            re.search(r"\d", window)
            or re.search(r"\b[IVXLCDM]{2,}\b", window)
            or re.search(r"\b(?:ibid|cf|vid|vide|voir|id)\.?\s*$", window, re.I)
        ):
            continue
        piece = text[start:boundary + 1].strip()
        if piece:
            segments.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def guess_entry_kind(segment: str) -> str:
    if re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.)\b", segment, re.I) and not re.search(r"\d", segment):
        return "cross_reference"
    if re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.)\b", segment, re.I):
        return "cross_reference"
    return "lemma"


def normalize_lemma(text: str) -> str:
    text = normalize_ws(text)
    text = strip_accents(text)
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    text = re.sub(r"[.;:]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def derive_lemma(segment: str) -> str:
    head = segment
    m = re.search(r"\b\d{1,4}\b", segment)
    if m:
        head = segment[: m.start()]
    else:
        cross = re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.)\b", segment, re.I)
        if cross:
            head = segment[: cross.start()]
    head = head.strip(" ,;:.–—-")
    return normalize_ws(head)


def extract_refs(segment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str | None]] = set()

    for m in re.finditer(r"\b(\d{1,4})\s*[-–—]\s*(\d{1,4})\b", segment):
        start = int(m.group(1))
        end = int(m.group(2))
        key = (start, f"{end}")
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": m.group(0),
                "page_ref_raw": None,
                "page_ref_int": None,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": m.group(1),
                "range_end_raw": m.group(2),
            }
        )

    for m in re.finditer(r"\b(\d{1,4})(?:\s*([a-z]))?(?:\.\s*([a-z]))?", segment):
        page = int(m.group(1))
        col_a = m.group(2)
        col_b = m.group(3)
        cols = [c for c in [col_a, col_b] if c]
        if not cols:
            key = (page, None)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": m.group(0).rstrip("."),
                    "page_ref_raw": m.group(0).rstrip("."),
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            continue
        for col in cols:
            key = (page, col)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref_kind": "editorial_page_column",
                    "ref_raw": f"{page} {col}.",
                    "page_ref_raw": f"{page} {col}.",
                    "page_ref_int": page,
                    "page_ref_col": col,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )

    return refs


def build_entry(
    *,
    entry_key: str,
    section_key: str,
    entry_order: int,
    segment: str,
    section_start_file: str,
    printed_page: int | None,
    source_file: str,
    section_kind: str,
    target_file: str,
) -> dict[str, Any]:
    entry_kind = guess_entry_kind(segment)
    lemma_raw = derive_lemma(segment)
    refs = extract_refs(segment)
    if printed_page is None and refs:
        first_ref = refs[0]
        printed_page = first_ref.get("page_ref_int")
    confidence = 0.88 if entry_kind == "lemma" else 0.76
    return {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": None,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw or None,
        "lemma_display": lemma_raw or None,
        "lemma_norm": normalize_lemma(lemma_raw) if lemma_raw else None,
        "lemma_sort": normalize_lemma(lemma_raw) if lemma_raw else None,
        "entry_raw": segment,
        "context_raw": None,
        "heading_letter": None,
        "inferred_printed_page": printed_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": source_file,
        "target_file_best": target_file,
        "confidence": confidence,
        "raw_json": {
            "source_file": source_file,
            "section_kind": section_kind,
            "split_strategy": "numeric-citation boundary heuristic",
            "ref_count": len(refs),
        },
        "_refs": refs,
    }


def build_section_entries(
    *,
    section_key: str,
    section_kind: str,
    files: list[int],
    source_root: Path,
    section_start_file: str,
    target_file: str,
    split_marker: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    lines: list[str] = []
    evidence_files: list[str] = []
    for seq in files:
        file_path = source_root / f"03d3e940-c85a-4a0e-a619-b6994efa9d12-{seq}.txt"
        evidence_files.append(str(file_path))
        file_lines = section_lines(source_root, seq)
        if split_marker and seq == files[0]:
            if split_marker == "INDEX AD CHRONICON PASCHALE.":
                before, after = split_sections_882(file_lines)
                if section_key.endswith(":001"):
                    file_lines = before
                else:
                    file_lines = after
            elif split_marker == "ORDO RERUM":
                before, after = split_sections_899(file_lines)
                if section_key.endswith(":005"):
                    file_lines = before
                else:
                    file_lines = after
        lines.extend(file_lines)
    text = normalize_ws(" ".join(lines))
    segments = split_entry_segments(text)
    entries: list[dict[str, Any]] = []
    for idx, segment in enumerate(segments, start=1):
        if not re.search(r"\d", segment) and not re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.)\b", segment, re.I):
            continue
        entry = build_entry(
            entry_key=f"{section_key}:entry:{idx:04d}",
            section_key=section_key,
            entry_order=idx,
            segment=segment,
            section_start_file=section_start_file,
            printed_page=None,
            source_file=section_start_file,
            section_kind=section_kind,
            target_file=target_file,
        )
        if entry["lemma_raw"] is None and entry["entry_kind"] != "cross_reference":
            continue
        entries.append(entry)
    return entries, evidence_files


def load_helper_summary() -> dict[str, dict[str, Any]]:
    helper_path = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG092_helper_output.json")
    if not helper_path.exists():
        return {}
    data = json.loads(helper_path.read_text(encoding="utf-8"))
    summary: dict[str, dict[str, Any]] = {}
    for item in data.get("entries", []):
        best = item.get("best_candidate") or {}
        summary[item.get("entry_id") or ""] = {
            "status": item.get("status"),
            "best_file": best.get("file"),
            "best_probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
    return summary


def build_payload(source_root: Path) -> dict[str, Any]:
    volume_id = "PG092"
    helper_summary = load_helper_summary()
    section_specs = [
        {
            "section_key": "PG092:alpha:analytic_subject:001",
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX AD DUCANGII PRÆFATIONEM IN CHRONICON PASCHALE.",
            "heading_norm": "index ad ducangii praefationem in chronicon paschale",
            "page_start": 1756,
            "page_end": 1756,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-882.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-882.txt"),
            "files": [882],
            "split_marker": None,
            "helper_entry_id": "pg092_sec1_index_ad_ducangii_prefatio",
        },
        {
            "section_key": "PG092:alpha:onomastic_mixed:002",
            "section_kind": "onomastic_mixed",
            "heading_raw": "INDEX AD CHRONICON PASCHALE.",
            "heading_norm": "index ad chronicon paschale",
            "page_start": 1757,
            "page_end": 1774,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-882.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-891.txt"),
            "files": list(range(882, 892)),
            "split_marker": "INDEX AD CHRONICON PASCHALE.",
            "helper_entry_id": "pg092_sec2_index_ad_chronicon_paschale",
        },
        {
            "section_key": "PG092:alpha:foreign_terms:003",
            "section_kind": "foreign_terms",
            "heading_raw": "INDEX VERBORUM MIXOBARBARORUM CHRONICI PASCHALIS.",
            "heading_norm": "index verborum mixobarbarorum chronici paschalis",
            "page_start": 1775,
            "page_end": 1780,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-892.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-894.txt"),
            "files": [892, 893, 894],
            "split_marker": None,
            "helper_entry_id": "pg092_sec3_index_verborum_mixobarbarorum",
        },
        {
            "section_key": "PG092:alpha:analytic_subject:004",
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS IN GEORGIUM PISIDAM.",
            "heading_norm": "index analyticus in georgium pisidam",
            "page_start": 1781,
            "page_end": 1788,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-895.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-898.txt"),
            "files": [895, 896, 897, 898],
            "split_marker": None,
            "helper_entry_id": "pg092_sec4_index_analyticus_georgius_pisida",
        },
        {
            "section_key": "PG092:alpha:foreign_terms:005",
            "section_kind": "foreign_terms",
            "heading_raw": "INDEX GRÆCUS AD BELLUM AVARICUM, HERACLIADEM ET EXPEDITIONEM PERSICAM GEORGII PISIDÆ.",
            "heading_norm": "index graecus ad bellum avaricum heracliadem et expeditionem persicam georgii pisidae",
            "page_start": 1789,
            "page_end": 1790,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-899.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-899.txt"),
            "files": [899],
            "split_marker": "ORDO RERUM",
            "helper_entry_id": "pg092_sec5_index_graecus",
        },
        {
            "section_key": "PG092:alpha:ordo_rerum:006",
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM.",
            "heading_norm": "ordo rerum",
            "page_start": 1791,
            "page_end": 1792,
            "file_start": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-899.txt"),
            "file_end": str(source_root / "03d3e940-c85a-4a0e-a619-b6994efa9d12-900.txt"),
            "files": [899, 900],
            "split_marker": "ORDO RERUM",
            "helper_entry_id": "pg092_sec6_ordo_rerum",
        },
    ]

    sections: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for order, spec in enumerate(section_specs, start=1):
        section_entries, section_evidence = build_section_entries(
            section_key=spec["section_key"],
            section_kind=spec["section_kind"],
            files=spec["files"],
            source_root=source_root,
            section_start_file=spec["file_start"],
            target_file=spec["file_start"],
            split_marker=spec["split_marker"],
        )
        evidence_files.extend(section_evidence)
        sections.append(
            {
                "section_key": spec["section_key"],
                "volume_id": volume_id,
                "work_key": None,
                "section_order": order,
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": spec["file_start"],
                "file_end": spec["file_end"],
                "confidence": 0.94 if spec["section_kind"] != "ordo_rerum" else 0.98,
                "raw_json": {
                    "section_kind_reason": "Recovered from OCR headings in the volume tail; section split inferred from printed headings and page headers.",
                    "evidence_files": section_evidence,
                    "helper_summary": helper_summary.get(spec.get("helper_entry_id") or "", {}),
                },
            }
        )
        for entry in section_entries:
            refs_for_entry = entry.pop("_refs")
            entries.append(entry)
            for ref_order, ref in enumerate(refs_for_entry, start=1):
                refs.append(
                    {
                        "entry_key": entry["entry_key"],
                        "ref_order": ref_order,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": spec["file_start"],
                        "target_file_probability": 0.58,
                        "section_start_file": spec["file_start"],
                        "editorial_anchor_file": spec["file_start"],
                        "confidence": 0.72,
                        "raw_json": {
                            "source_file": entry["raw_json"]["source_file"],
                            "split_strategy": entry["raw_json"]["split_strategy"],
                        },
                    }
                )

    notes = [
        "PG092 tail indices built from OCR heuristics over the indexed pages and the closing contents table.",
        "The Latin analytical/onomastic index and the Greek/foreign-term indices are kept as separate sections.",
    ]
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Entries were recovered from OCR tail pages with heuristic sentence splitting and section-boundary detection.",
        "evidence_files": sorted(set(evidence_files)),
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": volume_id,
            "collection": "PG",
            "source_root": str(source_root),
            "volume_label": "Patrologiae Graecae Tomus XCII",
            "notes": "Tail-index volume with multiple index sections and an ordo rerum closure.",
        },
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    payload = build_payload(args.source_root)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build a conservative alphabetical-index payload for PG033 from OCR files.

Run:
  python scripts/pipeline_index_extraction/build_pg033_alphabetical_payload.py \
    --source-root teste/PG033/text \
    --intermediate-dir data/intermediate_payloads/PG033 \
    --output data/alphabetical_index_payloads/PG033_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HEADER_RE = re.compile(r"INDEX IN CYRILLUM\.|ORDO RERUM|INDEX RERUM")
PAGE_RE = re.compile(r"^\s*(\d{3,4})\s*(?:INDEX IN CYRILLUM\.)?.*$")
ROMAN_CITATION_RE = re.compile(r"\b([MDCLXVI]{1,8}|[0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*(?:\s*-\s*[0-9]{1,4})?)\b")
PARA_SPLIT_RE = re.compile(r"\n\s*\n+")
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒΑ-ΩΜΝΟΠΡΣΤΥΦΧΨΖ]$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def suffix_int(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    return int(m.group(1)) if m else 0


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_paragraphs(text: str) -> list[str]:
    parts = [normalize_ws(p) for p in PARA_SPLIT_RE.split(text) if normalize_ws(p)]
    return parts


def candidate_files(source_root: Path) -> list[Path]:
    files = sorted(source_root.glob("*.txt"), key=suffix_int)
    return [p for p in files if 845 <= suffix_int(p) <= 868]


def extract_page_hint(text: str) -> int | None:
    for line in text.splitlines()[:8]:
        m = re.search(r"(\d{3,4})", line)
        if m:
            return int(m.group(1))
    return None


def clean_entry_text(text: str) -> str:
    text = normalize_ws(text)
    text = text.lstrip("- ").strip()
    return text


def lemma_from_entry(text: str) -> str | None:
    if not text:
        return None
    head = text.split(".")[0]
    if "," in head:
        head = head.split(",")[0]
    head = head.strip()
    if not head:
        return None
    return head


def parse_citation(text: str) -> tuple[str | None, int | None]:
    matches = list(ROMAN_CITATION_RE.finditer(text))
    if not matches:
        return None, None
    token = matches[-1].group(1)
    token = token.strip()
    if re.fullmatch(r"[0-9]{1,4}", token):
        return token, int(token)
    return token, None


def letter_sort(label: str) -> str:
    return label.lower()


def make_node(section_key: str, order: int, label: str) -> dict[str, Any]:
    return {
        "node_key": f"{section_key}:node:{order:04d}",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": order,
        "node_kind": "letter_group",
        "label_raw": label,
        "label_norm": label.lower(),
        "label_sort": letter_sort(label),
        "node_level": 1,
        "confidence": 0.95,
        "raw_json": {"source": "standalone letter heading"},
    }


def make_entry(section_key: str, order: int, entry_raw: str, source_file: str, page_hint: int | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    lemma_raw = lemma_from_entry(entry_raw)
    ref_raw, ref_int = parse_citation(entry_raw)
    entry_kind = "cross_reference" if re.search(r"\b(Vide|Vid\.|Vide|cf\.|voir|id\.)\b", entry_raw, re.I) and ref_raw is None else "lemma"
    entry = {
        "entry_key": f"{section_key}:entry:{order:04d}",
        "section_key": section_key,
        "parent_node_key": None,
        "entry_order": order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": lemma_raw.lower() if lemma_raw else None,
        "lemma_sort": lemma_raw.lower() if lemma_raw else None,
        "entry_raw": entry_raw,
        "context_raw": None if len(entry_raw) < 160 else entry_raw[:160],
        "heading_letter": lemma_raw[0].upper() if lemma_raw else None,
        "inferred_printed_page": page_hint,
        "section_start_file": source_file,
        "editorial_anchor_file": source_file,
        "target_file_best": source_file,
        "confidence": 0.62 if ref_raw else 0.48,
        "raw_json": {
            "source_file": source_file,
            "source_page_hint": page_hint,
            "entry_kind_reason": "Heuristic paragraph-level extraction from OCR.",
        },
    }
    ref = {
        "entry_key": entry["entry_key"],
        "ref_order": 1,
        "ref_kind": "editorial_page",
        "ref_raw": ref_raw,
        "page_ref_raw": ref_raw,
        "page_ref_int": ref_int,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": None,
        "range_end_raw": None,
        "target_file": source_file,
        "target_file_probability": 0.55,
        "section_start_file": source_file,
        "editorial_anchor_file": source_file,
        "confidence": 0.52 if ref_raw else 0.2,
        "raw_json": {"citation_extracted_from": entry_raw[:240]},
    }
    raw_json = {
        "source_file": source_file,
        "source_page_hint": page_hint,
        "citation_candidate": ref_raw,
    }
    return entry, ref, raw_json


def build_payload(source_root: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = candidate_files(source_root)
    if not files:
        raise SystemExit(f"no candidate OCR files found under {source_root}")

    section_key = "PG033:alpha:alphabetical_general:001"
    sections = [
        {
            "section_key": section_key,
            "volume_id": "PG033",
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX IN CYRILLUM.",
            "heading_norm": "index in cyrillum",
            "heading_letter": None,
            "page_start": 1666,
            "page_end": 1714,
            "file_start": str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.78,
            "raw_json": {
                "source_window": [str(files[0]), str(files[-1])],
                "section_kind_reason": "Alphabetical subject index with mixed onomastic and doctrinal material.",
                "excluded_sections": [
                    {
                        "heading_raw": "ORDO RERUM",
                        "file": str(source_root / "fe1694f0-f983-4606-8ec2-91eafca00214-869.txt"),
                        "reason": "Editorial contents section, not part of the alphabetical index.",
                    }
                ],
            },
        }
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    node_order = 1
    entry_order = 1
    seen_entries: set[str] = set()
    current_file = None
    current_page_hint = None

    for file_path in files:
        current_file = str(file_path)
        text = read_text(file_path)
        current_page_hint = extract_page_hint(text)
        if re.search(r"^\s*[A-Z]\s*$", text, re.M):
            for letter in re.findall(r"^\s*([A-ZÆŒ])\s*$", text, re.M):
                if not any(node["label_raw"] == letter for node in nodes):
                    nodes.append(make_node(section_key, node_order, letter))
                    node_order += 1
        paragraphs = split_paragraphs(text)
        for para in paragraphs:
            if HEADER_RE.search(para):
                continue
            if PAGE_RE.match(para):
                continue
            if LETTER_ONLY_RE.fullmatch(para):
                continue
            if para.startswith("Digitized by Google") or para == ".":
                continue
            if para.lower().startswith("pagina") or para.lower().startswith("<pagina"):
                continue
            entry_raw = clean_entry_text(para)
            if not entry_raw:
                continue
            key = entry_raw[:120]
            if key in seen_entries:
                continue
            seen_entries.add(key)
            entry, ref, note = make_entry(section_key, entry_order, entry_raw, str(file_path), current_page_hint)
            entries.append(entry)
            if ref["ref_raw"] is not None:
                refs.append(ref)
            notes.append(note)
            entry_order += 1

    coverage = {
        "entries_status": "partial",
        "entries_status_reason": "Heuristic paragraph-level extraction of the main INDEX IN CYRILLUM section; later editorial ORDO RERUM pages excluded.",
        "evidence_files": [str(p) for p in files[:3]] + [str(files[-1])],
    }

    volume = {
        "volume_id": "PG033",
        "collection": "PG",
        "source_root": str(source_root),
        "volume_label": "PG033",
        "notes": "Conservative extraction of the alphabetical index in Cyrillum from OCR paragraph blocks.",
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    (intermediate_dir / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": "PG033",
                "updated_at": now_iso(),
                "current_focus": "Conservative extraction of INDEX IN CYRILLUM and exclusion of ORDO RERUM.",
                "completed": [
                    "identified index section boundaries",
                    "parsed OCR paragraphs into entry stubs",
                ],
                "pending": [
                    "run helper on representative ambiguous entries",
                    "validate final JSON payload",
                ],
                "blocked": [],
                "notes": [
                    "Paragraph-level extraction is conservative and may merge some subentries.",
                    "ORDO RERUM pages were excluded from the alphabetical payload.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG033 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.intermediate_dir)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()

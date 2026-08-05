#!/usr/bin/env python3
"""Build PG068 alphabetical-index payload and helper request.

Usage:
  python scripts/pipeline_index_extraction/pg068_build_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG068/text \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG068_alphabetical_indices.json \
    --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG068_helper_request.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG068_helper_output.json

The script parses the final PG068 OCR window, segments index lines conservatively,
and writes a draft canonical payload plus a helper request for boundary cases.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


FILE_RE = re.compile(r"-(\d+)\.txt$")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADER_RE = re.compile(r"^\d{4}\.?\s*$")
INDEX_RE = re.compile(r"INDEX ANALYTICUS\.?$")
NUM_RE = re.compile(r"\d+(?:-\d+)?")


@dataclass
class Segment:
    text: str
    files: List[Path]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text


def lemma_from_entry(text: str) -> str:
    text = text.strip()
    m = re.search(r"(?=,\s*\d)", text)
    if m:
        return text[: m.start()].strip()
    m = re.search(r"(?<=\.)\s+[A-ZÆŒ]", text)
    if m:
        return text[: m.start()].strip()
    return text.rstrip(".").strip()


def first_letter(text: str) -> Optional[str]:
    for ch in text:
        if ch.isalpha():
            return ch.upper()
    return None


def extract_blocks(path: Path) -> tuple[list[str], list[str]]:
    text_lines: list[str] = []
    letters: list[str] = []
    inside: Optional[str] = None
    buf: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if '<bloco tipo="' in raw:
            if "texto_principal" in raw:
                inside = "texto_principal"
                buf = []
            elif "nota_marginal" in raw:
                inside = "nota_marginal"
                buf = []
            else:
                inside = None
            continue
        if inside is None:
            continue
        if raw.strip().startswith("</bloco>"):
            if inside == "texto_principal":
                text_lines.extend(buf)
            elif inside == "nota_marginal":
                for item in buf:
                    s = item.strip()
                    if LETTER_RE.fullmatch(s):
                        letters.append(s)
            inside = None
            buf = []
            continue
        buf.append(raw.rstrip())
    return text_lines, letters


def merge_lines(lines: Iterable[str]) -> list[str]:
    merged: list[Segment] = []
    for raw in lines:
        line = raw.strip()
        if not line or line == "---":
            continue
        if HEADER_RE.fullmatch(line) or INDEX_RE.fullmatch(line):
            continue
        if LETTER_RE.fullmatch(line):
            continue

        if merged:
            prev = merged[-1].text.rstrip()
            incomplete = (not re.search(r'[.!?;:]["\')\]]?\s*$', prev)) or prev.endswith("-")
            if incomplete:
                if prev.endswith("-"):
                    merged[-1].text = prev[:-1] + line.lstrip()
                else:
                    merged[-1].text = prev + " " + line.lstrip()
                continue

        merged.append(Segment(text=line, files=[]))
    return [seg.text for seg in merged]


def extract_line_segments(source_root: Path) -> list[dict]:
    files = sorted(
        p
        for p in source_root.glob("*.txt")
        if FILE_RE.search(p.name) and 584 <= int(FILE_RE.search(p.name).group(1)) <= 589
    )
    segments: list[dict] = []
    current: Optional[dict] = None
    for path in files:
        text_lines, _letters = extract_blocks(path)
        for line in merge_lines(text_lines):
            # Re-run a simpler streaming merge so we can retain file provenance.
            if current is None:
                current = {"text": line, "files": [path]}
                continue
            prev = current["text"].rstrip()
            incomplete = (not re.search(r'[.!?;:]["\')\]]?\s*$', prev)) or prev.endswith("-")
            if incomplete:
                if prev.endswith("-"):
                    current["text"] = prev[:-1] + line.lstrip()
                else:
                    current["text"] = prev + " " + line.lstrip()
                current["files"].append(path)
            else:
                segments.append(current)
                current = {"text": line, "files": [path]}
    if current is not None:
        segments.append(current)
    return segments


def parse_refs(text: str) -> list[dict]:
    refs: list[dict] = []
    seen = set()
    order = 1
    for m in NUM_RE.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        if "-" in raw:
            ref_kind = "editorial_range"
            start_raw, end_raw = raw.split("-", 1)
        else:
            ref_kind = "editorial_page"
            start_raw = raw
            end_raw = None
        refs.append(
            {
                "ref_order": order,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": int(start_raw),
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": start_raw if ref_kind == "editorial_range" else None,
                "range_end_raw": end_raw if ref_kind == "editorial_range" else None,
            }
        )
        order += 1
    return refs


def slugify(text: str, max_len: int = 42) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:max_len] or "entry"


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict]) -> dict:
    selected = []
    # Favor boundary-crossing and especially long multi-ref entries.
    candidates = []
    for entry in entries:
        span = len(entry["raw_json"]["source_files"])
        ref_count = len(entry["refs"])
        text_len = len(entry["entry_raw"])
        score = span * 3 + ref_count + (1 if text_len > 120 else 0)
        if span > 1 or ref_count >= 5 or text_len > 220:
            candidates.append((score, entry))
    candidates.sort(key=lambda item: (-item[0], item[1]["entry_order"]))
    for _, entry in candidates[:10]:
        refs = [r["page_ref_int"] for r in entry["refs"][:4] if r["page_ref_int"] is not None]
        selected.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"], entry["lemma_norm"]],
                "page_hints": [str(x) for x in refs],
                "page_hint_ints": refs,
                "context_raw": entry["context_raw"] or entry["entry_raw"][:240],
            }
        )
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": selected,
    }


def maybe_apply_helper(entries: list[dict], helper_output_path: Path) -> None:
    if not helper_output_path.exists():
        return
    try:
        helper = json.loads(helper_output_path.read_text(encoding="utf-8"))
    except Exception:
        return
    by_id = {e["entry_key"]: e for e in entries}
    for item in helper.get("entries", []):
        entry_id = item.get("entry_id")
        entry = by_id.get(entry_id)
        if not entry:
            continue
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper_status"] = item.get("status") or "resolved"
        if "best_candidate" in item:
            raw_json["helper_best_candidate"] = item["best_candidate"]
            best = item["best_candidate"]
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                entry["confidence"] = max(entry["confidence"], float(best.get("probability") or entry["confidence"]))
                for ref in entry.get("refs", []):
                    ref["target_file"] = best["file"]
                    ref["target_file_probability"] = float(best.get("probability") or ref["target_file_probability"])
                    ref["confidence"] = float(best.get("probability") or ref["confidence"])
        if "candidates" in item:
            raw_json["helper_candidates"] = item["candidates"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--volume-id", default="PG068")
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--helper-request", required=True)
    ap.add_argument("--helper-output", required=True)
    args = ap.parse_args()

    source_root = Path(args.source_root)
    output_path = Path(args.output)
    helper_request_path = Path(args.helper_request)
    helper_output_path = Path(args.helper_output)

    segments = extract_line_segments(source_root)
    entries: list[dict] = []
    for idx, seg in enumerate(segments, 1):
        text = seg["text"].strip()
        lemma = lemma_from_entry(text)
        refs = parse_refs(text)
        source_files = sorted({str(p) for p in seg["files"]})
        entry_key = f"{args.volume_id}:entry:{idx:04d}"
        first_file = source_files[0]
        last_file = source_files[-1]
        head = first_letter(lemma) or "?"
        entry = {
            "entry_key": entry_key,
            "section_key": f"{args.volume_id}:alpha:analytic_subject:001",
            "parent_node_key": None,  # filled after nodes are built
            "entry_order": idx,
            "entry_kind": "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": normalize_text(lemma),
            "lemma_sort": normalize_text(lemma),
            "entry_raw": text,
            "context_raw": None,
            "heading_letter": head,
            "inferred_printed_page": refs[0]["page_ref_int"] if refs else None,
            "section_start_file": first_file,
            "editorial_anchor_file": first_file,
            "target_file_best": first_file,
            "confidence": round(max(0.78, 0.97 - 0.03 * (len(source_files) - 1)), 6),
            "raw_json": {
                "source_files": source_files,
                "segment_strategy": "conservative_line_merge",
                "first_file": first_file,
                "last_file": last_file,
                "ref_count": len(refs),
                "helper_used": helper_output_path.exists(),
            },
        }
        entry["refs"] = []
        for ref in refs:
            entry["refs"].append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref["ref_order"],
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": first_file,
                    "target_file_probability": entry["confidence"],
                    "section_start_file": first_file,
                    "editorial_anchor_file": first_file,
                    "confidence": entry["confidence"],
                    "raw_json": {"source_token": ref["ref_raw"]},
                }
            )
        entries.append(entry)

    # Build nodes from leading letters in encounter order.
    nodes: list[dict] = []
    node_by_letter: dict[str, str] = {}
    node_order = 1
    for entry in entries:
        letter = entry["heading_letter"]
        if letter not in node_by_letter:
            node_key = f"{args.volume_id}:node:{node_order:03d}"
            node_by_letter[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": f"{args.volume_id}:alpha:analytic_subject:001",
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": normalize_text(letter),
                    "label_sort": normalize_text(letter),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_token": letter},
                }
            )
            node_order += 1
        entry["parent_node_key"] = node_by_letter[letter]

    maybe_apply_helper(entries, helper_output_path)

    helper_request = build_helper_request(args.volume_id, source_root, entries)
    helper_request_path.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    section = {
        "section_key": f"{args.volume_id}:alpha:analytic_subject:001",
        "volume_id": args.volume_id,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS. RERUM QUÆ IN LIBRIS DE ADORATIONE IN SPIRITU ET VERITATE CONTINENTUR.",
        "heading_norm": normalize_text(
            "INDEX ANALYTICUS. RERUM QUÆ IN LIBRIS DE ADORATIONE IN SPIRITU ET VERITATE CONTINENTUR."
        ),
        "heading_letter": None,
        "page_start": 1139,
        "page_end": 1150,
        "file_start": str(source_root / "0d6539ae-7718-4bb1-a02c-51bb4be138a5-584.txt"),
        "file_end": str(source_root / "0d6539ae-7718-4bb1-a02c-51bb4be138a5-589.txt"),
        "confidence": 0.96,
        "raw_json": {
            "section_start_heading": "INDEX ANALYTICUS. RERUM QUÆ IN LIBRIS DE ADORATIONE IN SPIRITU ET VERITATE CONTINENTUR.",
            "section_kind_reason": "Recoverable analytic subject index covering the final index pages before the separate ORDO RERUM tail; OCR page-number headers drift on 585 but the alphabetic sequence is continuous.",
            "evidence_files": [
                str(source_root / f"0d6539ae-7718-4bb1-a02c-51bb4be138a5-{n}.txt")
                for n in [584, 585, 586, 587, 588, 589]
            ],
            "excluded_tail": str(source_root / "0d6539ae-7718-4bb1-a02c-51bb4be138a5-590.txt"),
        },
    }

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the visible analytic-index line items from the final PG068 OCR window; the separate ORDO RERUM page at 590 was inspected as a boundary witness and excluded from the alphabetical index payload.",
        "evidence_files": [
            str(source_root / f"0d6539ae-7718-4bb1-a02c-51bb4be138a5-{n}.txt")
            for n in [584, 585, 586, 587, 588, 589, 590]
        ],
    }

    notes = [
        "The OCR on 585 misreads one header pair but the index sequence continues cleanly across the tail window.",
        "Entries are segmented conservatively by incomplete-line continuation; multi-reference lines keep separate refs.",
        "The ORDO RERUM page is intentionally not serialized as part of the analytic index section.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": args.volume_id,
            "collection": "PG",
            "source_root": str(source_root),
            "volume_label": "PG068",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": [ref for entry in entries for ref in entry["refs"]],
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

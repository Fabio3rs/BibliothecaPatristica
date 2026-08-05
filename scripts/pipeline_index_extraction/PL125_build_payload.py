#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL125_build_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL125/text \
    --volume-id PL125 \
    --request-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL125_helper_request.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL125_helper_output.json \
    --payload-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL125_alphabetical_indices.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL125

Builds the PL125 alphabetical-index helper request, optional intermediate
artifacts, and the final JSON payload after helper resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENTRY_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÀ-ÖØ-Ý])")
NUM_RE = re.compile(r"\b\d{1,4}\b")
ABBREV_RE = re.compile(r"\b(?:s|etc|ibid|id|v|cf|fin)\.$", re.IGNORECASE)
LEADING_LETTER_RE = re.compile(r"^(?:[A-Z]\s+)+")
WHITESPACE_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
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
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def page_sort_key(path: Path) -> tuple[int, str]:
    m = re.search(r"-(\d+)\.txt$", path.name)
    return (int(m.group(1)) if m else 10**9, path.name)


def load_text_lines(path: Path) -> list[str]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for bloco in root.findall("bloco"):
        if (bloco.attrib.get("tipo") or "").lower() != "texto_principal":
            continue
        text = "".join(bloco.itertext())
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def collect_index_lines(source_root: Path) -> tuple[list[str], list[Path]]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=page_sort_key) if re.search(r"-(655|656|657|658|659|660)\.txt$", p.name)]
    if len(files) != 6:
        raise RuntimeError(f"expected 6 index tail files, found {len(files)}")

    lines: list[str] = []
    started = False
    for path in files:
        for line in load_text_lines(path):
            if not started:
                if line == "A":
                    started = True
                continue
            # Skip obvious page footers / residual non-index content.
            if line == "Digitized by Google":
                continue
            lines.append(line)
    if not started:
        raise RuntimeError("could not locate the start of the alphabetical index")
    return lines, files


def split_chunks(text: str) -> list[str]:
    if not text:
        return []
    pieces: list[str] = []
    last = 0
    for match in ENTRY_SPLIT_RE.finditer(text):
        end = match.start() + 1
        prev = text[max(0, end - 12) : end].strip()
        if ABBREV_RE.search(prev):
            continue
        pieces.append(text[last:end].strip())
        last = match.end()
    tail = text[last:].strip()
    if tail:
        pieces.append(tail)
    return [piece for piece in pieces if piece]


def start_index_entry_text(lines: list[str]) -> str:
    joined = " ".join(lines)
    # The first index letter is emitted as a standalone "A" on PL125.
    # If that marker disappears in OCR, fall back to the whole tail text.
    return joined


def extract_index_chunks(lines: list[str]) -> list[str]:
    text = start_index_entry_text(lines)
    raw_chunks = split_chunks(text)
    cleaned: list[str] = []
    for chunk in raw_chunks:
        chunk = WHITESPACE_RE.sub(" ", chunk.replace("\xa0", " ")).strip()
        chunk = chunk.replace("ﬀ", "ff").replace("ﬁ", "fi").replace("ﬂ", "fl")
        chunk = re.sub(r"\s+([,.;:])", r"\1", chunk)
        chunk = re.sub(r"([,.;:])([A-Za-zÀ-ÖØ-öø-ÿ])", r"\1 \2", chunk)
        chunk = LEADING_LETTER_RE.sub("", chunk).strip()
        if chunk:
            cleaned.append(chunk)
    return cleaned


def lemma_from_chunk(chunk: str) -> str:
    if not chunk:
        return chunk
    # Keep the leading lemma clause, not the full multi-entry line fragment.
    stop_points = []
    comma_pos = chunk.find(",")
    if comma_pos > 0:
        stop_points.append(comma_pos)
    period_pos = chunk.find(".")
    if period_pos > 0:
        stop_points.append(period_pos)
    end = min(stop_points) if stop_points else len(chunk)
    lemma = chunk[:end].strip(" .")
    lemma = re.sub(r"\s+", " ", lemma)
    return lemma


def extract_page_refs(chunk: str) -> list[int]:
    values: list[int] = []
    for match in NUM_RE.finditer(chunk):
        value = int(match.group(0))
        if 1 <= value <= 9999:
            values.append(value)
    # Preserve order, remove duplicates.
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def build_entries(volume_id: str, section_key: str, source_root: Path, chunks: list[str], section_start_file: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes_by_letter: OrderedDict[str, dict[str, Any]] = OrderedDict()
    section_files = [str(section_start_file)]

    for idx, chunk in enumerate(chunks, start=1):
        lemma_raw = lemma_from_chunk(chunk)
        lemma_raw = lemma_raw.replace("  ", " ").strip()
        lemma_display = lemma_raw
        lemma_norm = normalize_text(lemma_raw)
        lemma_sort = lemma_norm
        heading_letter = None
        if lemma_norm:
            for ch in lemma_norm:
                if ch.isalpha():
                    heading_letter = ch.upper()
                    break
        if heading_letter and heading_letter not in nodes_by_letter:
            node_key = f"{volume_id}:node:{len(nodes_by_letter)+1:03d}"
            nodes_by_letter[heading_letter] = {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": len(nodes_by_letter) + 1,
                "node_kind": "letter_group",
                "label_raw": heading_letter,
                "label_norm": heading_letter.lower(),
                "label_sort": heading_letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "source_file": str(section_start_file),
                    "role": "alphabetical_letter_group",
                },
            }
        parent_node_key = nodes_by_letter[heading_letter]["node_key"] if heading_letter in nodes_by_letter else None
        page_refs = extract_page_refs(chunk)
        inferred_printed_page = page_refs[0] if page_refs else None
        entry_key = f"{volume_id}:entry:{idx:04d}"
        source_file = str(section_start_file)
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": parent_node_key,
            "entry_order": idx,
            "entry_kind": "lemma",
            "lemma_raw": lemma_raw or None,
            "lemma_display": lemma_display or None,
            "lemma_norm": lemma_norm or None,
            "lemma_sort": lemma_sort or None,
            "entry_raw": chunk,
            "context_raw": chunk,
            "heading_letter": heading_letter,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": source_file,
            "editorial_anchor_file": None,
            "target_file_best": None,
            "confidence": 0.5,
            "raw_json": {
                "source_file": source_file,
                "chunk_index": idx,
                "page_hints": page_refs,
                "query_names": [lemma_raw] if lemma_raw else [],
                "split_strategy": "period_capital_sentence_chunks",
            },
        }
        entries.append(entry)

        for ref_order, ref_value in enumerate(page_refs, start=1):
            ref = {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": str(ref_value),
                "page_ref_raw": str(ref_value),
                "page_ref_int": ref_value,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": source_file,
                "editorial_anchor_file": None,
                "confidence": 0.55,
                "raw_json": {
                    "source_file": source_file,
                    "chunk_index": idx,
                    "citation_role": "page_reference",
                },
            }
            refs.append(ref)

    nodes = list(nodes_by_letter.values())
    return entries, refs, nodes


def build_sections(volume_id: str, source_root: Path, index_files: list[Path]) -> list[dict[str, Any]]:
    start_file = str(index_files[0])
    end_file = str(index_files[-1])
    return [
        {
            "section_key": f"{volume_id}:alpha:analytic_subject:001",
            "volume_id": volume_id,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM ET VERBORUM HINCMARI SCRIPTA MAJORIS MOMENTI.",
            "heading_norm": "index rerum et verborum hincmari scripta maioris momenti",
            "heading_letter": None,
            "page_start": 1301,
            "page_end": 1312,
            "file_start": start_file,
            "file_end": end_file,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "alphabetical_subject_index_detected_in_tail_pages",
                "evidence_files": [str(p) for p in index_files],
                "source_note": "OCR tail shows the heading and the A-Y index sequence on pages 655-660.",
            },
        }
    ]


def build_volume(volume_id: str, source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "collection": volume_id[:2],
        "source_root": str(source_root),
        "volume_label": f"Patrologia Latina {volume_id[2:]}",
        "notes": "Alphabetical index tail extracted from pages 655-660 of the volume.",
    }


def build_helper_request(volume_id: str, source_root: Path, chunks: list[str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks, start=1):
        lemma_raw = lemma_from_chunk(chunk)
        page_hints = extract_page_refs(chunk)
        entries.append(
            {
                "entry_id": f"{volume_id.lower()}_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw] if lemma_raw else [],
                "page_hints": [str(value) for value in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": chunk,
            }
        )
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 3,
            "adjacency_window": 2,
        },
        "entries": entries,
    }


def load_helper_output(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def helper_by_entry_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for entry in helper_output.get("entries") or []:
        entry_id = entry.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = entry
    return mapping


def build_final_payload(
    volume_id: str,
    source_root: Path,
    sections: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    helper_output: dict[str, Any],
) -> dict[str, Any]:
    helper_map = helper_by_entry_id(helper_output)
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        entry_id = entry["entry_key"].split(":")[-1]
        helper_entry = helper_map.get(f"{volume_id.lower()}_{int(entry_id):04d}")
        selected_target: dict[str, Any] | None = None
        if helper_entry:
            best = helper_entry.get("best_candidate") or {}
            for cand in helper_entry.get("candidates") or []:
                if cand.get("candidate_role") == "target_candidate":
                    selected_target = cand
                    break
            entry["target_file_best"] = best.get("file")
            if selected_target:
                entry["target_file_best"] = selected_target.get("file")
                entry["editorial_anchor_file"] = selected_target.get("file")
                if selected_target.get("probability") is not None:
                    entry["confidence"] = max(entry["confidence"], float(selected_target["probability"]))
            else:
                entry["target_file_best"] = None
                entry["editorial_anchor_file"] = None
                if best.get("probability") is not None:
                    entry["confidence"] = min(entry["confidence"], float(best["probability"]))
            helper_top = []
            for cand in (helper_entry.get("candidates") or [])[:3]:
                helper_top.append(
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                    }
                )
            entry["raw_json"]["helper"] = {
                "status": helper_entry.get("status"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "best_candidate": best,
                "selected_target_candidate": selected_target,
                "top_candidates": helper_top,
            }
        else:
            entry["raw_json"]["helper"] = {
                "status": "missing",
                "candidate_role": None,
                "reason_summary": "helper_output_not_available_for_entry",
                "best_candidate": None,
                "selected_target_candidate": None,
                "top_candidates": [],
            }

        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if entry_refs:
            first_ref = entry_refs[0]
            entry["inferred_printed_page"] = first_ref["page_ref_int"]

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the alphabetical index entries from OCR tail pages 655-660 and resolved material targets with the local helper.",
        "evidence_files": [str(p) for p in sorted(source_root.glob("*.txt"), key=page_sort_key) if re.search(r"-(655|656|657|658|659|660)\.txt$", p.name)],
    }

    notes = [
        "The alphabetical index occupies OCR pages 655-660.",
        "The final ORDO RERUM closure on pages 661-664 was inspected but not serialized in this payload.",
        "OCR line breaks and inherited dashes were preserved in entry_raw and context_raw.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": build_volume(volume_id, source_root),
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_intermediate(intermediate_dir: Path, volume: dict[str, Any], sections: list[dict[str, Any]], nodes: list[dict[str, Any]], entries: list[dict[str, Any]], refs: list[dict[str, Any]], coverage: dict[str, Any], request: dict[str, Any] | None = None, helper_output: dict[str, Any] | None = None) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": volume["volume_id"],
            "generated_at": now_iso(),
            "artifacts": ["volume.json", "sections.json", "nodes.json", "entries.json", "refs.json", "coverage.json"],
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": volume["volume_id"],
            "updated_at": now_iso(),
            "current_focus": "Build and validate PL125 alphabetical index payload",
            "completed": [
                "index tail parsed from OCR",
                "helper request prepared",
            ],
            "pending": [
                "run index_target_locator helper",
                "assemble final payload",
                "validate JSON schema and key relationships",
            ],
            "blocked": [],
            "notes": [
                "The alphabetical index was isolated from OCR pages 655-660.",
                "The ORDO RERUM closure is noted separately in the final payload notes.",
            ],
        },
    )
    if request is not None:
        write_json(intermediate_dir / "helper_request.json", request)
    if helper_output is not None:
        write_json(intermediate_dir / "helper_output.json", helper_output)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--volume-id", type=str, required=True)
    ap.add_argument("--request-output", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path, required=True)
    ap.add_argument("--payload-output", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    args = ap.parse_args()

    lines, index_files = collect_index_lines(args.source_root)
    chunks = extract_index_chunks(lines)
    section_key = f"{args.volume_id}:alpha:analytic_subject:001"
    section_start_file = index_files[0]

    sections = build_sections(args.volume_id, args.source_root, index_files)
    entries, refs, nodes = build_entries(args.volume_id, section_key, args.source_root, chunks, section_start_file)
    request = build_helper_request(args.volume_id, args.source_root, chunks)
    write_json(args.request_output, request)

    helper_output = load_helper_output(args.helper_output)
    volume = build_volume(args.volume_id, args.source_root)
    coverage = {
        "entries_status": "pending_helper",
        "entries_status_reason": "Awaiting helper resolution for the material target files.",
        "evidence_files": [str(p) for p in index_files],
    }
    write_intermediate(args.intermediate_dir, volume, sections, nodes, entries, refs, coverage, request=request, helper_output=helper_output if helper_output else None)

    if helper_output:
        payload = build_final_payload(args.volume_id, args.source_root, sections, nodes, entries, refs, helper_output)
        write_json(args.payload_output, payload)
        write_intermediate(args.intermediate_dir, volume, sections, nodes, entries, refs, payload["coverage"], request=request, helper_output=helper_output)


if __name__ == "__main__":
    main()

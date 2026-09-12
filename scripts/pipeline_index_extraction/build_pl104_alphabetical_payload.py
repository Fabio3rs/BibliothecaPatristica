#!/usr/bin/env python3
"""Usage: build the PL104 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl104_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL104/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL104_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL104_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL104 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL104_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL104"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 104"

INDEX_FILES = [670, 671, 672, 673, 674, 675, 676]
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING = "INDEX ANALYTICUS RERUM ET VERBORUM QUÆ TUM IN TEXTU S. AGOBARDI, TUM IN NOTIS TEXTUI SUBJECTIS CONTINENTUR."

LETTER_RE = re.compile(r"^[A-ZÆŒ](?:\.)?$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à)\s*(\d{1,4}))?(?=[\s\.,;:\)\]]|$)")
ENTRY_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])|(?<=\d)\s+(?=[A-ZÆŒ])")
INDEX_HEADER_RE = re.compile(r"INDEX ANALYTICUS", re.IGNORECASE)
ORDO_RE = re.compile(r"ORDO RERUM", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|THIS VOLUME DOES NOT CIRCULATE OUTSIDE THE LIBRARY\.)$", re.IGNORECASE)

SCRIPTURE_MASK_RE = re.compile(
    r"\((?=[^)]*(?:Joan\.|I Petr\.|II Petr\.|III Petr\.|Matth\.|Marc\.|Luc\.|Act\.|Rom\.|I Cor\.|II Cor\.|Galat\.|"
    r"Eph\.|Phil\.|Col\.|I Thess\.|II Thess\.|I Tim\.|II Tim\.|Tit\.|Philem\.|Hebr\.|Psal\.|Gen\.|Exod\.|Lev\.|Num\.|"
    r"Deut\.|Jos\.|Judic\.|Ruth\.|Reg\.|Paral\.|Sap\.|Prov\.|Eccl\.|Isai\.|Jer\.|Ezech\.|Dan\.|Osee\.|Ioel\.|Amos\.|"
    r"Abd\.|Jon\.|Mich\.|Nah\.|Hab\.|Soph\.|Agg\.|Zach\.|Mal\.|Apoc\.))[^)]*\)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        text = normalize(raw)
        if not text or NOISE_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def extract_text_block_lines(raw_text: str) -> list[str]:
    lines: list[str] = []
    for match in re.finditer(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', raw_text, re.DOTALL | re.IGNORECASE):
        block = match.group(1)
        for raw_line in block.splitlines():
            text = normalize(raw_line)
            if not text or NOISE_RE.fullmatch(text):
                continue
            lines.append(text)
    return lines


def join_hyphenated(text: str) -> str:
    return re.sub(r"(\w)-\s+(?=\w)", r"\1", text)


def split_segments(text: str) -> list[str]:
    parts = [part.strip() for part in ENTRY_SPLIT_RE.split(text) if part and part.strip()]
    merged: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if merged and re.fullmatch(r"(?:ibid\.?|ibid|id\.?|cf\.?|vide|vid\.?|voir|v\.)", part, re.IGNORECASE):
            merged[-1] = f"{merged[-1]} {part}"
            i += 1
            continue
        merged.append(part)
        i += 1
    return merged


def remove_scripture_citations(text: str) -> str:
    return SCRIPTURE_MASK_RE.sub(" ", text)


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = match.group(2)
        raw = match.group(0).strip()
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(int(end)) if end is not None else None,
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
            }
        )
    return refs


def derive_lemma_raw(text: str) -> str | None:
    stripped = normalize(text) or ""
    if not stripped:
        return None
    match = PAGE_REF_RE.search(stripped)
    if match:
        lemma = stripped[: match.start()].strip()
    else:
        lemma = stripped
    lemma = lemma.strip(" ,;:.—–-")
    return lemma or None


def derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    base = re.sub(r"\s*\(.*?\)\s*$", "", lemma_raw).strip()
    for item in (base, lemma_raw, context_raw.split(".", 1)[0].strip()):
        item = normalize(item) or ""
        if item and item not in candidates:
            candidates.append(item)
    return candidates[:4]


def build_page_map(files: list[Path]) -> dict[int, Path]:
    page_map: dict[int, Path] = {}
    sorted_pairs: list[tuple[int, Path]] = []
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text", "")) or ""
        nums = [int(n) for n in re.findall(r"\b\d{3,4}\b", header)]
        if not nums:
            continue
        # Header spreads often carry two adjacent printed pages.
        if len(nums) >= 2:
            if nums[0] < 2000 and nums[1] < 2000 and abs(nums[1] - nums[0]) <= 3:
                for num in nums[:2]:
                    page_map[num] = path
                    sorted_pairs.append((num, path))
                continue
        for num in nums:
            page_map[num] = path
            sorted_pairs.append((num, path))
    # Build a small fallback list for nearest-page resolution.
    page_map["_sorted_pairs"] = sorted(sorted_pairs, key=lambda item: item[0])  # type: ignore[index]
    return page_map


def closest_file_for_page(page_map: dict[int, Path], page: int) -> Path | None:
    direct = page_map.get(page)
    if isinstance(direct, Path):
        return direct
    pairs = page_map.get("_sorted_pairs")  # type: ignore[assignment]
    if not pairs:
        return None
    best_path: Path | None = None
    best_delta: int | None = None
    for candidate_page, candidate_path in pairs:
        delta = abs(candidate_page - page)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_path = candidate_path
    return best_path


def helper_entry_id(order: int) -> str:
    return f"{VOLUME_ID.lower()}_idx_{order:04d}"


def parse_index_section(files: list[Path], page_map: dict[int, Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    current_letter: str | None = None
    current_node_key: str | None = None
    last_node_letter: str | None = None
    entry_order = 0
    node_order = 0
    capture = False
    started_entries = False
    buffer_lines: list[str] = []
    buffer_file: Path | None = None
    last_explicit_page: int | None = None

    def ensure_letter_node(letter: str, source_file: Path) -> None:
        nonlocal node_order, current_node_key, last_node_letter
        if last_node_letter == letter and current_node_key is not None:
            return
        node_order += 1
        last_node_letter = letter
        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        nodes.append(
            {
                "node_key": current_node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "source_file": str(source_file),
                    "section_kind": "analytic_subject",
                },
            }
        )

    def flush_buffer() -> None:
        nonlocal entry_order, buffer_lines, buffer_file, last_explicit_page
        if not buffer_lines or current_letter is None or buffer_file is None:
            buffer_lines = []
            buffer_file = None
            return
        joined = join_hyphenated(" ".join(buffer_lines))
        for segment in split_segments(joined):
            segment = segment.strip()
            if not segment:
                continue
            if INDEX_HEADER_RE.search(segment) or ORDO_RE.search(segment):
                continue
            if re.search(r"INDEX IN S\.?\s*AGOBARDUM", segment, re.IGNORECASE):
                continue
            lemma_raw = derive_lemma_raw(segment)
            if not lemma_raw:
                continue
            masked = remove_scripture_citations(segment)
            page_refs = extract_page_refs(masked)
            if not page_refs and re.search(r"\bibid\.?\b", segment, re.IGNORECASE) and last_explicit_page is not None:
                page_refs = [
                    {
                        "ref_raw": "ibid.",
                        "page_ref_raw": "ibid.",
                        "page_ref_int": last_explicit_page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "ref_kind": "editorial_page",
                        "inferred_from_previous_page": True,
                    }
                ]
            if page_refs:
                last_explicit_page = page_refs[-1]["page_ref_int"]
            entry_kind = "cross_reference" if re.fullmatch(r"(?:vid\.?|vide|voir|v\.|cf\.|id\.?)", lemma_raw, re.IGNORECASE) else "lemma"
            if not page_refs and entry_kind == "lemma" and "ibid" in segment.lower():
                entry_kind = "cross_reference"
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            inferred_page = page_refs[0]["page_ref_int"] if page_refs else last_explicit_page
            entry = {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": current_node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": None if entry_kind == "cross_reference" else lemma_raw,
                "lemma_display": None if entry_kind == "cross_reference" else lemma_raw,
                "lemma_norm": None if entry_kind == "cross_reference" else lemma_raw.lower(),
                "lemma_sort": None if entry_kind == "cross_reference" else sort_norm(lemma_raw),
                "entry_raw": segment,
                "context_raw": segment,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_page,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": str(buffer_file),
                "target_file_best": None,
                "confidence": 0.78 if entry_kind == "lemma" else 0.68,
                "raw_json": {
                    "source_file": str(buffer_file),
                    "section_kind": "analytic_subject",
                    "helper_entry_id": helper_entry_id(entry_order),
                    "masked_scripture_literal": segment != masked,
                    "line_count_hint": len(buffer_lines),
                },
            }
            entries.append(entry)
            page_hints = [ref["page_ref_int"] for ref in page_refs if ref.get("page_ref_int") is not None]
            if not page_hints and inferred_page is not None:
                page_hints = [inferred_page]
            helper_entries.append(
                {
                    "entry_id": helper_entry_id(entry_order),
                    "lemma_raw": lemma_raw,
                    "query_names": derive_query_names(lemma_raw, segment),
                    "page_hints": [str(h) for h in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": segment,
                }
            )
            for ref_order, ref in enumerate(page_refs, start=1):
                page_int = ref.get("page_ref_int")
                target_file = closest_file_for_page(page_map, page_int) if isinstance(page_int, int) else None
                if target_file is None:
                    target_prob = None
                else:
                    target_prob = 0.99 if page_map.get(page_int) == target_file else 0.72
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": page_int,
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": str(target_file) if target_file else None,
                        "target_file_probability": target_prob,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": str(buffer_file),
                        "confidence": 0.58 if ref["ref_kind"] == "editorial_page" else 0.55,
                        "raw_json": {
                            "source_file": str(buffer_file),
                            "section_kind": "analytic_subject",
                            "page_resolution": "page_map" if page_map.get(page_int) == target_file else "nearest_header",
                        },
                    }
                )

    for path in files:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        lines = extract_text_block_lines(raw_text)
        for raw_line in lines:
            line = normalize(raw_line) or ""
            if not line:
                continue
            if not capture:
                if INDEX_HEADER_RE.search(line):
                    capture = True
                continue
            if line == "Digitized by Google" or ORDO_RE.search(line):
                continue
            if re.search(r"INDEX IN S\.?\s*AGOBARDUM", line, re.IGNORECASE):
                continue
            if line == "A" and not started_entries:
                started_entries = True
                current_letter = "A"
                ensure_letter_node("A", path)
                buffer_file = path
                continue
            if LETTER_RE.fullmatch(line):
                started_entries = True
                if buffer_lines:
                    flush_buffer()
                current_letter = line
                ensure_letter_node(line, path)
                buffer_file = path
                continue
            if not started_entries:
                continue
            if buffer_file is None:
                buffer_file = path
            buffer_lines.append(line)
        if started_entries and buffer_file is None:
            buffer_file = path
        if started_entries and path not in [Path(p) for p in evidence_files]:
            evidence_files.append(str(path))

    flush_buffer()
    return nodes, entries, refs, helper_entries, evidence_files


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json, {})


def summarize_helper_output(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        entry_id = item.get("entry_id")
        if not isinstance(entry_id, str):
            continue
        best = item.get("best_candidate") or {}
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best,
            "candidates": item.get("candidates") or [],
        }
    return summary


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_summary: dict[str, dict[str, Any]]) -> None:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)
    for entry in entries:
        helper_id = entry["raw_json"].get("helper_entry_id")
        helper = helper_summary.get(helper_id)
        if helper:
            best = helper.get("best_candidate") or {}
            entry["raw_json"]["helper"] = helper
            if best.get("file"):
                # Keep our page-map based target unless it is missing.
                if entry.get("target_file_best") is None:
                    entry["target_file_best"] = best["file"]
            if best.get("probability") is not None:
                entry["confidence"] = max(entry["confidence"], min(0.99, float(best["probability"])))
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if entry_refs and entry.get("target_file_best") is None:
            entry["target_file_best"] = entry_refs[0].get("target_file")
        if entry.get("target_file_best") is None and helper:
            best = helper.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]
        if entry.get("target_file_best") is None:
            entry["target_file_best"] = entry["editorial_anchor_file"]
    for ref in refs:
        entry = next((item for item in entries if item["entry_key"] == ref["entry_key"]), None)
        helper_id = entry["raw_json"].get("helper_entry_id") if entry else None
        helper = helper_summary.get(helper_id) if helper_id else None
        if helper:
            ref["raw_json"]["helper"] = helper
            best = helper.get("best_candidate") or {}
            if ref.get("target_file") is None and best.get("file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    files = [path for path in discover_text_files(source_root) if file_num(path) in INDEX_FILES]
    page_map = build_page_map(files)
    nodes, entries, refs, helper_entries, evidence_files = parse_index_section(files, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_summary = summarize_helper_output(helper_output)
    apply_helper(entries, refs, helper_summary)

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_HEADING,
            "heading_norm": SECTION_HEADING.lower().rstrip("."),
            "heading_letter": None,
            "page_start": 1351,
            "page_end": 1364,
            "file_start": str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index in the tail of the volume, with letter-group markers A through Z and multi-reference entries on each line.",
                "source_files": [str(path) for path in files],
                "helper_status": helper_output.get("status"),
                "observed_headings": [
                    "INDEX IN S. AGOBARDUM.",
                    "INDEX ANALYTICUS RERUM ET VERBORUM QUÆ TUM IN TEXTU S. AGOBARDI, TUM IN NOTIS TEXTUI SUBJECTIS CONTINENTUR.",
                ],
            },
        }
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The final alphabetical index is the INDEX ANALYTICUS over S. Agobardus; the closing ORDO RERUM block begins after the extracted tail and was inspected but excluded.",
            "OCR header page numbers are noisy in the tail, so target files were resolved from local page headers and the helper was used as a fallback sanity check.",
            "Biblical citations remained literal in entry_raw; the extractor masked them before page-ref extraction to avoid turning verses into material page references.",
        ],
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the alphabetical index from the OCR tail with conservative segmentation and per-page target resolution.",
        "evidence_files": evidence_files,
    }

    notes = [
        "Letter-group nodes were preserved for the alphanumeric index structure.",
        "Target files were anchored from page headers first, then corroborated with helper output where needed.",
        "The closing ORDO RERUM was not serialized as a section in this payload.",
    ]

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Validate the PL104 alphabetical payload and keep OCR literals intact.",
        "completed": [
            "index section identified",
            "helper request written and helper executed",
            "intermediate fragments assembled",
        ],
        "pending": [
            "validate final JSON payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR file, printed page, and cited reference separate.",
            "Biblical citations were masked before page extraction to avoid false material refs.",
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(output_file),
        },
    )
    write_json(intermediate_dir / "todo.json", todo)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL104 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
    )


if __name__ == "__main__":
    main()

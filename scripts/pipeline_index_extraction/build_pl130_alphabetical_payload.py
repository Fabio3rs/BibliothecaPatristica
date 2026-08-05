#!/usr/bin/env python3
"""Usage: build the PL130 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl130_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL130/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL130_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL130_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL130 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL130_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml

VOLUME_ID = "PL130"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 130"

SOURCE_START = 641
SOURCE_END = 657

SECTION1_KEY = f"{VOLUME_ID}:section:001"
SECTION2_KEY = f"{VOLUME_ID}:section:002"

TITLE_RE = re.compile(r"INDEX ALPHABETICUS(?:\s+FIDELISSIMUS)?\s+IN ISIDORUM MERCATOREM\.", re.IGNORECASE)
ORDO_RE = re.compile(r"^ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:[-–—à]\s*)(\d{1,4}))?")
ABBREV_TAIL_RE = re.compile(r"(?:\b(?:c|cap|p|pp|n|nn)\.\s*\d+)+", re.IGNORECASE)
EM_DASH_SPLIT_RE = re.compile(r"\s+—\s+")
LEADING_INDEX_PAGE_RE = re.compile(r"^\s*\d{3,4}\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def lowercase_sort(text: str | None) -> str | None:
    value = normalize_ws(text)
    return value.lower() if value else None


def clean_line(text: str) -> str:
    return normalize_ws(text).strip(" \t\r\n")


def split_text_blocks(raw_text: str) -> list[str]:
    parsed = parse_ocr_page_xml(raw_text)
    return [clean_line(line) for line in parsed["all_text"].splitlines() if clean_line(line)]


def parse_header_pages(raw_text: str) -> tuple[int | None, int | None]:
    parsed = parse_ocr_page_xml(raw_text)
    header = normalize_ws(parsed.get("header_text", ""))
    nums = [int(n) for n in re.findall(r"\b\d{3,4}\b", header)]
    if not nums:
        return None, None
    if len(nums) >= 2:
        return nums[0], nums[1]
    return nums[0], nums[0]


def discover_files(source_root: Path) -> list[Path]:
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    return [path for path in files if SOURCE_START <= int(path.stem.rsplit("-", 1)[-1]) <= SOURCE_END]


def build_page_map(source_root: Path) -> dict[int, Path]:
    page_map: dict[int, Path] = {}
    for path in sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1])):
        raw_text = read_text(path)
        left, right = parse_header_pages(raw_text)
        for num in {left, right}:
            if isinstance(num, int):
                page_map[num] = path
    return page_map


def extract_page_matches(text: str) -> list[tuple[str, int, int | None]]:
    matches: list[tuple[str, int, int | None]] = []
    for match in PAGE_TOKEN_RE.finditer(text):
        start = match.start()
        prefix = text[:start].rstrip()
        tail = prefix[-8:].lower()
        if tail.endswith("c.") or tail.endswith("cap.") or tail.endswith("p.") or tail.endswith("pp.") or tail.endswith("n.") or tail.endswith("nn."):
            continue
        value = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        matches.append((match.group(0).strip(), value, end))
    return matches


def remove_top_level_pages(text: str) -> str:
    value = text
    for raw, _, _ in extract_page_matches(text):
        value = value.replace(raw, " ", 1)
    return normalize_ws(value)


def split_alpha_segment(line: str) -> list[str]:
    if " — " in line:
        parts = [part.strip() for part in EM_DASH_SPLIT_RE.split(line) if part.strip()]
        if len(parts) > 1:
            return parts
    pieces = [part.strip() for part in re.split(r"(?<=\d\.)\s+(?=[A-ZÆŒ])", line) if part.strip()]
    return pieces if len(pieces) > 1 else [line.strip()]


def derive_lemma(segment: str) -> str | None:
    text = remove_top_level_pages(segment)
    if not text:
        return None
    if " ibid." in text.lower():
        text = text.split(" ibid.", 1)[0]
    text = text.strip(" ,;:.—–-")
    return text or None


def derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    items: list[str] = []
    base = re.sub(r"\s*\(.*?\)\s*$", "", lemma_raw).strip()
    for candidate in (base, lemma_raw, context_raw.split(".", 1)[0].strip()):
        candidate = normalize_ws(candidate)
        if candidate and candidate not in items:
            items.append(candidate)
    return items[:4]


def current_header_page(path: Path) -> int | None:
    left, right = parse_header_pages(read_text(path))
    return right or left


def build_section1(source_files: list[Path], page_map: dict[int, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    section_entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    entry_order = 0
    node_order = 0
    current_letter: str | None = None
    current_node_key: str | None = None
    started = False

    for path in source_files:
        raw_text = read_text(path)
        lines = split_text_blocks(raw_text)
        file_page = current_header_page(path)
        evidence_files.append(str(path))
        for line in lines:
            if not started:
                if TITLE_RE.search(line):
                    started = True
                continue
            if line.startswith("Digitized by Google"):
                continue
            if line.startswith("Revocatur Lector"):
                continue
            if line == "ORDO RERUM" or ORDO_RE.match(line):
                break
            if LETTER_RE.fullmatch(line):
                if current_letter != line:
                    node_order += 1
                    current_letter = line
                    current_node_key = f"{VOLUME_ID}:node:1:{node_order:03d}"
                    nodes.append(
                        {
                            "node_key": current_node_key,
                            "section_key": SECTION1_KEY,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path)},
                        }
                    )
                continue
            for segment in split_alpha_segment(line):
                if not segment:
                    continue
                if LEADING_INDEX_PAGE_RE.fullmatch(segment):
                    continue
                lemma = derive_lemma(segment)
                if not lemma:
                    continue
                page_matches = extract_page_matches(segment)
                page_refs = [item for item in page_matches if item[1] is not None]
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:1:{entry_order:04d}"
                entry_kind = "cross_reference" if re.search(r"\b(?:vid\.|vide|voir|cf\.|id\.)\b", segment, re.IGNORECASE) and not page_refs else "lemma"
                inferred_page = page_refs[0][1] if page_refs else file_page
                target_file_best = str(page_map.get(inferred_page)) if inferred_page in page_map else str(path)
                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION1_KEY,
                    "parent_node_key": current_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": normalize_ws(lemma),
                    "lemma_sort": lowercase_sort(lemma),
                    "entry_raw": segment,
                    "context_raw": line,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(source_files[0]),
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_file_best,
                    "confidence": 0.86 if page_refs else 0.72,
                    "raw_json": {
                        "source_file": str(path),
                        "page_matches": [
                            {
                                "ref_raw": raw,
                                "page_ref_int": page,
                                "page_ref_end": end,
                            }
                            for raw, page, end in page_matches
                        ],
                    },
                }
                section_entries.append(entry)
                if page_refs:
                    for ref_order, (raw, page_ref, end_ref) in enumerate(page_refs, start=1):
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_range" if end_ref is not None else "editorial_page",
                                "ref_raw": raw,
                                "page_ref_raw": raw,
                                "page_ref_int": page_ref,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": str(page_ref) if end_ref is not None else None,
                                "range_end_raw": str(end_ref) if end_ref is not None else None,
                                "target_file": str(page_map[page_ref]) if page_ref in page_map else None,
                                "target_file_probability": 0.98 if page_ref in page_map else 0.5,
                                "section_start_file": str(source_files[0]),
                                "editorial_anchor_file": str(path),
                                "confidence": 0.9 if page_ref in page_map else 0.6,
                                "raw_json": {
                                    "source_file": str(path),
                                    "locator_tail": remove_top_level_pages(segment),
                                },
                            }
                        )
                    if len(page_refs) > 1 or re.search(r"\b(?:c|cap|p|pp|n|nn)\.\s*\d+", segment, re.IGNORECASE) or "ibid." in segment.lower():
                        helper_entries.append(
                            {
                                "entry_id": entry_key,
                                "lemma_raw": lemma,
                                "query_names": derive_query_names(lemma, segment),
                                "page_hints": [str(page_ref) for _, page_ref, _ in page_refs],
                                "page_hint_ints": [page_ref for _, page_ref, _ in page_refs],
                                "context_raw": line,
                            }
                        )

    section = {
        "section_key": SECTION1_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX ALPHABETICUS FIDELISSIMUS IN ISIDORUM MERCATOREM.",
        "heading_norm": "INDEX ALPHABETICUS FIDELISSIMUS IN ISIDORUM MERCATOREM.",
        "heading_letter": None,
        "page_start": 1224,
        "page_end": 1254,
        "file_start": str(source_files[0]),
        "file_end": str(source_files[-1]),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Alphabetical subject index headed by INDEX ALPHABETICUS FIDELISSIMUS IN ISIDORUM MERCATOREM.",
        },
    }
    return section, section_entries, refs, evidence_files, helper_entries


def build_section2(source_files: list[Path], page_map: dict[int, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    entry_order = 0
    started = False

    for path in source_files:
        raw_text = read_text(path)
        lines = split_text_blocks(raw_text)
        evidence_files.append(str(path))
        for line in lines:
            if not started:
                if line == "ORDO RERUM" or ORDO_RE.match(line) or "ORDO RERUM" in line:
                    started = True
                continue
            if line.startswith("Digitized by Google"):
                continue
            if line == "ORDO RERUM" or ORDO_RE.match(line) or line == "QUÆ IN HOC TOMO CONTINENTUR.":
                continue
            if line.startswith("FINIS TOMI"):
                continue
            fragments = [frag.strip() for frag in EM_DASH_SPLIT_RE.split(line) if frag.strip()] if " — " in line else [line]
            for fragment in fragments:
                if not fragment:
                    continue
                lemma = derive_lemma(fragment)
                if not lemma:
                    continue
                page_matches = extract_page_matches(fragment)
                page_refs = [item for item in page_matches if item[1] is not None]
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:2:{entry_order:04d}"
                inferred_page = page_refs[0][1] if page_refs else current_header_page(path)
                target_file_best = str(page_map.get(inferred_page)) if inferred_page in page_map else str(path)
                entry_kind = "cross_reference" if re.search(r"\b(?:ibid\.|vid\.|vide|voir|cf\.|id\.)\b", fragment, re.IGNORECASE) and not page_refs else "lemma"
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": SECTION2_KEY,
                        "parent_node_key": None,
                        "entry_order": entry_order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma,
                        "lemma_display": lemma,
                        "lemma_norm": normalize_ws(lemma),
                        "lemma_sort": lowercase_sort(lemma),
                        "entry_raw": fragment,
                        "context_raw": line,
                        "heading_letter": None,
                        "inferred_printed_page": inferred_page,
                        "section_start_file": str(source_files[0]),
                        "editorial_anchor_file": str(path),
                        "target_file_best": target_file_best,
                        "confidence": 0.9 if page_refs else 0.76,
                        "raw_json": {
                            "source_file": str(path),
                            "page_matches": [
                                {
                                    "ref_raw": raw,
                                    "page_ref_int": page,
                                    "page_ref_end": end,
                                }
                                for raw, page, end in page_matches
                            ],
                        },
                    }
                )
                if page_refs:
                    for ref_order, (raw, page_ref, end_ref) in enumerate(page_refs, start=1):
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_range" if end_ref is not None else "editorial_page",
                                "ref_raw": raw,
                                "page_ref_raw": raw,
                                "page_ref_int": page_ref,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": str(page_ref) if end_ref is not None else None,
                                "range_end_raw": str(end_ref) if end_ref is not None else None,
                                "target_file": str(page_map[page_ref]) if page_ref in page_map else None,
                                "target_file_probability": 0.98 if page_ref in page_map else 0.5,
                                "section_start_file": str(source_files[0]),
                                "editorial_anchor_file": str(path),
                                "confidence": 0.9 if page_ref in page_map else 0.6,
                                "raw_json": {
                                    "source_file": str(path),
                                    "locator_tail": remove_top_level_pages(fragment),
                                },
                            }
                        )
                    if len(page_refs) > 1 or re.search(r"\b(?:c|cap|p|pp|n|nn)\.\s*\d+", fragment, re.IGNORECASE) or "ibid." in fragment.lower():
                        helper_entries.append(
                            {
                                "entry_id": entry_key,
                                "lemma_raw": lemma,
                                "query_names": derive_query_names(lemma, fragment),
                                "page_hints": [str(page_ref) for _, page_ref, _ in page_refs],
                                "page_hint_ints": [page_ref for _, page_ref, _ in page_refs],
                                "context_raw": line,
                            }
                        )

    section = {
        "section_key": SECTION2_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_letter": None,
        "page_start": 1231,
        "page_end": 1256,
        "file_start": str(source_files[0]),
        "file_end": str(source_files[-1]),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Closing contents table printed as ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        },
    }
    return section, entries, refs, evidence_files, helper_entries


def build_helper_request(volume_id: str, source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if helper_output_json.exists():
        return json.loads(helper_output_json.read_text(encoding="utf-8"))
    return {}


def write_todo(intermediate_dir: Path, helper_status: str) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PL130 alphabetical payload and preserve the closing ORDO RERUM section.",
        "completed": [
            "OCR tail inspected",
            "INDEX ALPHABETICUS and ORDO RERUM sections segmented",
            "helper request generated",
            "helper run completed",
        ],
        "pending": [
            "validate the final JSON payload",
        ],
        "blocked": [],
        "notes": [
            "The alphabetical section spans files 641-654.",
            "The closing ORDO RERUM section spans files 655-657.",
            f"Helper status: {helper_status}.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL130 alphabetical-index payload.")
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--helper-request-json", required=True, type=Path)
    ap.add_argument("--helper-output-json", required=True, type=Path)
    ap.add_argument("--intermediate-dir", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    args = ap.parse_args()

    source_root = args.source_root.resolve()
    files = discover_files(source_root)
    page_map = build_page_map(source_root)

    section1, entries1, refs1, evidence1, helper1 = build_section1(files[:-3], page_map)
    section2, entries2, refs2, evidence2, helper2 = build_section2(files[-3:], page_map)

    helper_entries = helper1 + helper2
    helper_request = build_helper_request(VOLUME_ID, source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json) if helper_entries else {}

    helper_status = helper_output.get("status", "not_run")
    write_todo(args.intermediate_dir, helper_status)

    helper_lookup: dict[str, dict[str, Any]] = {}
    for result in helper_output.get("entries", []) if isinstance(helper_output.get("entries"), list) else []:
        entry_id = result.get("entry_id")
        if isinstance(entry_id, str):
            helper_lookup[entry_id] = result

    def merge_helper_evidence(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            hit = helper_lookup.get(entry["entry_key"])
            if not hit:
                continue
            entry["raw_json"]["helper"] = {
                "status": helper_status,
                "candidate_role": hit.get("candidate_role"),
                "reason_summary": hit.get("reason_summary"),
                "best_candidate": hit.get("best_candidate"),
                "candidates": hit.get("candidates", [])[:5],
            }
            if entry.get("target_file_best") is None and hit.get("best_candidate", {}).get("file"):
                entry["target_file_best"] = hit["best_candidate"]["file"]

    merge_helper_evidence(entries1)
    merge_helper_evidence(entries2)

    entries = entries1 + entries2
    refs = refs1 + refs2
    evidence_files = list(dict.fromkeys(evidence1 + evidence2))

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section1, section2],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the PL130 alphabetical index and the closing ORDO RERUM contents table from OCR, preserving literal page anchors and OCR corruptions where the material locator remained ambiguous.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "The main alphabetical section is the INDEX ALPHABETICUS FIDELISSIMUS IN ISIDORUM MERCATOREM block.",
            "The closing ORDO RERUM contents table is preserved as a separate editorial-closure section.",
            f"Helper status: {helper_status}.",
        ],
    }

    write_json(args.intermediate_dir / "sections.json", [section1, section2])
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "payload.json", payload)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

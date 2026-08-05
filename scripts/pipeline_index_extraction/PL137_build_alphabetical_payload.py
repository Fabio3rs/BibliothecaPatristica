#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/PL137_build_alphabetical_payload.py
Build the PL137 alphabetical-index payload from the OCR tail, run the local
index target locator helper, persist intermediate checkpoints, and write the
final canonical JSON payload.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL137"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 137"
SOURCE_ROOT = ROOT / "teste/PL137/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL137_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL137_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL137_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL137"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_START_SEQ = 601
SECTION_END_SEQ = 612

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
HEADER_LINE_RE = re.compile(r"^\d{4}\s+INDEX\s+LATINITATIS\b", re.IGNORECASE)
SECTION_HEAD_RE = re.compile(r"^INDEX\s+LATINITATIS(?:\s+IN\s+HROTSUITH[ÆA]?\s+OPERA\.?)?$", re.IGNORECASE)
SECTION_HEAD_2_RE = re.compile(r"^IN\s+HROTSUITH[ÆA]\.?M?\.?\s+OPERA\.?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})([a-z])?")
WEAK_CONTINUATION_RE = re.compile(
    r"^(?:Apud|Ecce|Namque|Aera|Aerae|Aere|Nam|NB\.|Vide|Vid\.|Voir)\b"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_segments(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        for segment in SENTENCE_SPLIT_RE.split(text):
            segment = norm(segment)
            if segment:
                lines.append(segment)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = extract_segments(path)[:8]
        header_blob = " ".join(lines)
        for match in PAGE_REF_RE.finditer(header_blob):
            page = int(match.group(1))
            if page >= 10:
                page_map.setdefault(page, str(path))
    return page_map


def is_section_heading(line: str) -> bool:
    if HEADER_LINE_RE.search(line):
        return True
    if line.startswith("INDEX LATINITATIS."):
        return True
    if line.startswith("Revocatur Lector ad cifras crassiores textui insertas."):
        return True
    if line.startswith("IN HROTSUITHÆ OPERA."):
        return True
    if SECTION_HEAD_RE.search(line):
        return True
    if SECTION_HEAD_2_RE.search(line):
        return True
    return False


def lemma_from_entry(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    if "," in text:
        return text.split(",", 1)[0].strip(" .;:")
    if "." in text and len(text.split()) <= 4:
        return text.split(".", 1)[0].strip(" .;:")
    return text.strip(" .;:")


def parse_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None]] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        page_ref_int = int(match.group(1))
        page_ref_col = match.group(2) or None
        ref_raw = match.group(0)
        key = (ref_raw.lower(), page_ref_int, page_ref_col)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": page_ref_col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def cleaned_line_is_start(line: str) -> bool:
    return bool(line and (not line[0].islower()) and not WEAK_CONTINUATION_RE.match(line))


def load_index_lines(files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        for line in extract_segments(path):
            items.append({"file": str(path), "line": line})
    return items


def segment_entries(items: list[dict[str, Any]], section_start_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    nodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    current_letter: str | None = None
    current_entry: dict[str, Any] | None = None
    entry_order = 0
    node_order = 0

    def finalize_current() -> None:
        nonlocal current_entry
        if current_entry is not None:
            entries.append(current_entry)
            current_entry = None

    for item in items:
        line = item["line"]
        source_file = item["file"]

        if is_section_heading(line):
            continue
        if line == "Digitized by Google":
            continue

        letter_prefix = re.match(r"^([A-ZÆŒ])\s+(.*)$", line)
        if letter_prefix and len(letter_prefix.group(2).split()) >= 1:
            current_letter = letter_prefix.group(1)
            line = letter_prefix.group(2).strip()
            if current_letter not in nodes:
                node_order += 1
                nodes[current_letter] = {
                    "node_key": f"{VOLUME_ID}:node:{node_order:03d}",
                    "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": current_letter,
                    "label_norm": current_letter.lower(),
                    "label_sort": current_letter.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": source_file},
                }

        if LETTER_RE.fullmatch(line):
            finalize_current()
            current_letter = line
            if line not in nodes:
                node_order += 1
                nodes[line] = {
                    "node_key": f"{VOLUME_ID}:node:{node_order:03d}",
                    "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": line.lower(),
                    "label_sort": line.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": source_file},
                }
            continue

        if current_entry is None:
            entry_order += 1
            current_entry = {
                "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
                "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
                "parent_node_key": nodes[current_letter]["node_key"] if current_letter in nodes else None,
                "entry_order": entry_order,
                "entry_kind": "lemma",
                "lemma_raw": None,
                "lemma_display": None,
                "lemma_norm": None,
                "lemma_sort": None,
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": current_letter,
                "inferred_printed_page": None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.80,
                "raw_json": {
                    "source_file": source_file,
                    "parser": "line_grouped_index_tail",
                    "line_count": 1,
                },
            }
            continue

        if line and (line[0].islower() or WEAK_CONTINUATION_RE.match(line)):
            current_entry["entry_raw"] = f"{current_entry['entry_raw']} {line}".strip()
            current_entry["context_raw"] = current_entry["entry_raw"]
            current_entry["raw_json"]["line_count"] = int(current_entry["raw_json"].get("line_count", 1)) + 1
            current_entry["raw_json"].setdefault("continuation_files", [])
            if source_file not in current_entry["raw_json"]["continuation_files"]:
                current_entry["raw_json"]["continuation_files"].append(source_file)
        else:
            finalize_current()
            entry_order += 1
            current_entry = {
                "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
                "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
                "parent_node_key": nodes[current_letter]["node_key"] if current_letter in nodes else None,
                "entry_order": entry_order,
                "entry_kind": "lemma",
                "lemma_raw": None,
                "lemma_display": None,
                "lemma_norm": None,
                "lemma_sort": None,
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": current_letter,
                "inferred_printed_page": None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.80,
                "raw_json": {
                    "source_file": source_file,
                    "parser": "line_grouped_index_tail",
                    "line_count": 1,
                },
            }

    finalize_current()
    return entries, list(nodes.values())


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    helper_index = 0
    for entry in entries:
        refs = parse_refs(entry["entry_raw"])
        if not refs:
            continue
        if len(refs) == 1 and refs[0]["page_ref_col"] is None and not re.search(
            r"\b(?:ibid\.?|id\.?|vide|vid\.?|voir|v\.|seqq?\.?)\b",
            entry["entry_raw"],
            re.IGNORECASE,
        ):
            continue
        helper_index += 1
        lemma_raw = lemma_from_entry(entry["entry_raw"]) or entry["entry_raw"]
        query_names = [lemma_raw]
        if len(lemma_raw.split()) >= 2:
            query_names.append(" ".join(lemma_raw.split()[:2]))
        page_hint_ints = sorted({ref["page_ref_int"] for ref in refs})
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{helper_index:04d}",
                "lemma_raw": lemma_raw,
                "query_names": query_names,
                "page_hints": [str(page) for page in page_hint_ints],
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
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
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        out[item["entry_id"]] = item
    return out


def compress_helper(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    candidates = []
    for cand in (item.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", []) if ev.get("kind")],
            }
        )
    return {
        "status": item.get("status"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(item.get("candidates") or []),
        "candidates": candidates,
    }


def build_payload(
    all_files: list[Path],
    files: list[Path],
    page_map: dict[int, str],
    helper_output: dict[str, Any],
    entries_raw: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    helper_by_id = helper_map(helper_output)
    section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
    section_start_file = str(files[0])
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    helper_index = 0
    for raw_entry in entries_raw:
        entry_raw = raw_entry["entry_raw"]
        page_refs = parse_refs(entry_raw)
        if not page_refs:
            entry_kind = "cross_reference" if re.match(r"^(?:Vide|Vid\.|Voir|v\.)\b", entry_raw, re.IGNORECASE) else "lemma"
        else:
            entry_kind = "lemma"
        lemma_raw = lemma_from_entry(entry_raw)
        lemma_display = lemma_raw
        lemma_norm = norm(lemma_raw).lower() if lemma_raw else None
        lemma_sort = sort_norm(lemma_raw)
        heading_letter = None
        if lemma_raw:
            for ch in lemma_raw:
                if ch.isalpha():
                    heading_letter = ch.upper()
                    break

        helper_info = None
        if page_refs:
            helper_index += 1
            helper_info = helper_by_id.get(f"{VOLUME_ID.lower()}_{helper_index:04d}")

        first_page = page_refs[0]["page_ref_int"] if page_refs else None
        target_file_best = None
        target_file_probability = None
        if helper_info:
            best = helper_info.get("best_candidate") or {}
            target_file_best = best.get("file")
            target_file_probability = best.get("probability")
        elif first_page is not None:
            target_file_best = page_map.get(first_page)
            target_file_probability = 0.66 if target_file_best else None

        if target_file_best is None and first_page is not None:
            target_file_best = page_map.get(first_page)
        editorial_anchor_file = raw_entry["editorial_anchor_file"]

        entry = {
            "entry_key": raw_entry["entry_key"],
            "section_key": section_key,
            "parent_node_key": raw_entry["parent_node_key"],
            "entry_order": raw_entry["entry_order"],
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_sort,
            "entry_raw": entry_raw,
            "context_raw": raw_entry["context_raw"],
            "heading_letter": heading_letter,
            "inferred_printed_page": first_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": target_file_best,
            "confidence": 0.92 if page_refs else 0.80,
            "raw_json": {
                "source_file": raw_entry["raw_json"]["source_file"],
                "parser": raw_entry["raw_json"].get("parser"),
                "line_count": raw_entry["raw_json"].get("line_count"),
                "section_kind": "alphabetical_general",
                "helper": compress_helper(helper_info) if helper_info else None,
            },
        }
        if raw_entry["raw_json"].get("continuation_files"):
            entry["raw_json"]["continuation_files"] = raw_entry["raw_json"]["continuation_files"]
        entries.append(entry)

        for idx, ref in enumerate(page_refs, start=1):
            ref_target = None
            ref_prob = None
            if helper_info:
                best = helper_info.get("best_candidate") or {}
                ref_target = best.get("file")
                ref_prob = best.get("probability")
            if ref_target is None:
                ref_target = page_map.get(ref["page_ref_int"])
                ref_prob = 0.66 if ref_target else None
            refs.append(
                {
                    "entry_key": raw_entry["entry_key"],
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": ref_target,
                    "target_file_probability": ref_prob,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": editorial_anchor_file,
                    "confidence": 0.90 if ref_target else 0.72,
                    "raw_json": {
                        "source_file": raw_entry["raw_json"]["source_file"],
                        "helper": compress_helper(helper_info) if helper_info else None,
                    },
                }
            )

    sections = [
        {
            "section_key": section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX LATINITATIS IN HROTSUITHÆ OPERA.",
            "heading_norm": "index latinitatis in hrotsuithae opera",
            "heading_letter": None,
            "page_start": 1197,
            "page_end": 1220,
            "file_start": str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.99,
            "raw_json": {
                "source_files": [str(path) for path in files],
                "section_kind_reason": "Alphabetical index of Latin usages and phrases in Hrotsvitha's opera, organized by letter groups A-V; not an ordo rerum block.",
                "observed_heading_lines": [
                    "INDEX LATINITATIS.",
                    "IN HROTSUITHÆ OPERA.",
                ],
                "helper_status": helper_output.get("status"),
            },
        }
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the alphabetical latinitas index from the OCR tail with line-group heuristics and page-target helper resolution for material references.",
        "evidence_files": [str(path) for path in files],
    }
    notes = [
        "Letter-group nodes were recovered from the standalone A-V markers in the OCR tail.",
        "OCR file suffixes were kept distinct from printed page references; helper evidence was preserved in raw_json where available.",
        "The tail begins on file 601 and ends on file 612; file 612 contains the final index header before FINIS TOMI.",
    ]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_LABEL,
        "notes": "Alphabetical latinitas index from the end of volume 137.",
    }
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
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL137 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=OUTPUT_FILE)
    args = ap.parse_args()

    all_files = discover_text_files(args.source_root)
    files = [path for path in all_files if SECTION_START_SEQ <= file_num(path) <= SECTION_END_SEQ]
    if not files:
        raise SystemExit("No OCR files found for the PL137 alphabetical index tail.")

    page_map = build_page_map(all_files)
    index_items = load_index_lines(files)
    entries_raw, nodes = segment_entries(index_items, str(files[0]))

    helper_request = build_helper_request(entries_raw)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(all_files, files, page_map, helper_output, entries_raw, nodes)

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.intermediate_dir / "volume.json",
        payload["volume"],
    )
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "source_root": str(args.source_root),
            "helper_request_json": str(args.helper_request_json),
            "helper_output_json": str(args.helper_output_json),
            "output_file": str(args.output_file),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate the PL137 alphabetical latinitas payload and preserve helper anchors in raw_json.",
            "completed": [
                "OCR tail files 601-612 inspected",
                "helper request generated",
                "helper output integrated",
                "intermediate payload fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals distinct from editorial page anchors and OCR file suffixes.",
            ],
        },
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

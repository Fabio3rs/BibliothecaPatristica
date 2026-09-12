#!/usr/bin/env python3
"""Usage: build the PL072 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl072_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL072/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL072_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL072_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL072 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL072_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


SECTION_1_START = 568
SECTION_1_END = 575
SECTION_2_START = 576
SECTION_2_END = 577

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
HEADING_1_RE = re.compile(
    r"^INDEX\s+RERUM\s+ET\s+VERBORUM(?:\s+QU[ÆAE]\s+IN\s+LIBRIS\s+LITURGIA\s+GALLICANA\s+CONTINENTUR\.?)?$",
    re.IGNORECASE,
)
HEADING_2_RE = re.compile(r"^ORDO\s+RERUM\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip(" \t\r\n,;:")


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = extract_lines(path)[:5]
        header_blob = " ".join(lines)
        for match in PAGE_RE.finditer(header_blob):
            page = int(match.group(1))
            if page >= 10:
                page_map.setdefault(page, str(path))
    return page_map


def in_section(path: Path, start: int, end: int) -> bool:
    num = file_num(path)
    return start <= num <= end


def clean_line(text: str) -> str:
    text = norm(text) or ""
    text = text.replace("\u00a0", " ")
    return text


def is_heading(line: str) -> bool:
    return bool(HEADING_1_RE.fullmatch(line) or HEADING_2_RE.fullmatch(line))


def is_letter_line(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def parse_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last = last_page
    ref_order = 1
    segments = re.split(r"(?<=[.;])\s+", entry_raw)
    for segment in segments:
        seg = segment.strip()
        if not seg:
            continue
        if IBID_RE.fullmatch(seg):
            if current_last is not None:
                refs.append(
                    {
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": seg,
                        "page_ref_raw": seg,
                        "page_ref_int": current_last,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                    }
                )
                ref_order += 1
            continue
        for match in PAGE_RE.finditer(seg):
            start = int(match.group(1))
            end = match.group(2)
            ref_raw = match.group(0).strip()
            if end is not None:
                refs.append(
                    {
                        "ref_order": ref_order,
                        "ref_kind": "editorial_range",
                        "ref_raw": ref_raw,
                        "page_ref_raw": ref_raw,
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(start),
                        "range_end_raw": str(int(end)),
                    }
                )
            else:
                refs.append(
                    {
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref_raw,
                        "page_ref_raw": ref_raw,
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                    }
                )
            current_last = start
            ref_order += 1
    return refs, current_last


def lemma_from_entry(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^[A-ZÆŒ]\s+", "", text)
    marker = text.find(",")
    if marker > 0:
        prefix = text[:marker]
    else:
        prefix = text
    prefix = re.split(r"(?<!\d)\d{1,4}(?:\s*-\s*\d{1,4})?", prefix)[0]
    prefix = prefix.strip(" .;:")
    return prefix or None


def build_section_1(files: list[Path], page_map: dict[int, str], helper_output: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section = {
        "section_key": "PL072:alpha:alphabetical_general:001",
        "volume_id": "PL072",
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX RERUM ET VERBORUM QUÆ IN LIBRIS LITURGIA GALLICANA CONTINENTUR.",
        "heading_norm": "index rerum et verborum quae in libris liturgia gallicana continentur",
        "heading_letter": None,
        "page_start": 1127,
        "page_end": 1153,
        "file_start": str(next(path for path in files if file_num(path) == SECTION_1_START)),
        "file_end": str(next(path for path in files if file_num(path) == SECTION_1_END)),
        "confidence": 0.96,
        "raw_json": {
            "source_files": [str(path) for path in files if in_section(path, SECTION_1_START, SECTION_1_END)],
            "section_kind_reason": "Alphabetical index of rerum/verborum, with letter-group nodes A-S and a heading variant on the later P-S pages.",
            "heading_variant_files": [
                str(next(path for path in files if file_num(path) == 575)),
            ],
        },
    }
    if helper_output is not None:
        section["raw_json"]["helper_locator_note"] = helper_output.get("status")

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    letter_node_key: str | None = None
    node_order = 0
    entry_order = 0
    entry_index = 0
    current_letter: str | None = None
    last_page: int | None = None

    section_start_file = str(next(path for path in files if file_num(path) == SECTION_1_START))

    def emit_entry(entry_raw: str) -> None:
        nonlocal entry_order, entry_index, last_page
        entry_raw = norm(entry_raw) or ""
        if not entry_raw:
            return
        if (
            entry_raw.startswith("INDEX ")
            or entry_raw.startswith("Digitized by Google")
            or re.fullmatch(r"\d{1,4}\.?$", entry_raw)
            or re.fullmatch(r"\d{1,4}\s+INDEX\b.*", entry_raw)
            or re.fullmatch(r"\d{1,4}\s+QU[ÆAE]\b.*", entry_raw)
            or re.fullmatch(r"\d{1,4}\s+ORDO\b.*", entry_raw)
        ):
            return
        if re.fullmatch(r"[A-ZÆŒ](?:\.)?", entry_raw):
            return
        entry_index += 1
        entry_order += 1
        entry_key = f"PL072:entry:{entry_order:06d}"
        lemma_raw = lemma_from_entry(entry_raw)
        entry_kind = "cross_reference" if re.match(r"^(?:Vide|Vid\.|Voir|v\.)\b", entry_raw, re.IGNORECASE) else "lemma"
        editorial_anchor_file = page_map.get(last_page) if last_page is not None else None
        entry = {
            "entry_key": entry_key,
            "section_key": section["section_key"],
            "parent_node_key": letter_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": norm(lemma_raw).lower() if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter or (lemma_raw[0] if lemma_raw else None),
            "inferred_printed_page": last_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": editorial_anchor_file,
            "confidence": 0.84 if entry_kind == "lemma" else 0.76,
            "raw_json": {
                "source_entry_index": entry_index,
                "source_section": "PL072 alphabetical index",
            },
        }
        if helper_output is not None and entry_index <= len(helper_output.get("entries", [])):
            entry["raw_json"]["helper_echo"] = helper_output["entries"][entry_index - 1]
        entries.append(entry)
        parsed_refs, last_page_after = parse_refs(entry_raw, last_page)
        last_page = last_page_after
        for ref in parsed_refs:
            refs.append(
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
                    "target_file": editorial_anchor_file,
                    "target_file_probability": 0.62 if editorial_anchor_file else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": editorial_anchor_file,
                    "confidence": 0.78 if ref["ref_kind"] == "editorial_page" else 0.74,
                    "raw_json": {
                        "source_entry_index": entry_index,
                    },
                }
            )

    def emit_segmented_line(line: str) -> None:
        segments = [part.strip() for part in re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", line) if part.strip()]
        for segment in segments:
            emit_entry(segment)

    for path in files:
        if not in_section(path, SECTION_1_START, SECTION_1_END):
            continue
        for raw_line in extract_lines(path):
            line = clean_line(raw_line)
            if not line:
                continue
            if is_heading(line) or line.startswith("INDEX ") or line.startswith("QUÆ IN LIBRIS") or line.startswith("EORUM QUÆ") or line.startswith("ORDO RERUM"):
                continue
            if is_letter_line(line):
                current_letter = line
                node_order += 1
                letter_node_key = f"PL072:node:{node_order:06d}"
                nodes.append(
                    {
                        "node_key": letter_node_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": line.lower(),
                        "label_sort": line.lower(),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path)},
                    }
                )
                continue
            if line.startswith("Digitized by Google"):
                continue
            emit_segmented_line(line)

    return [section], nodes, entries, refs, scripture_refs


def build_section_2(files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": "PL072:alpha:ordo_rerum:002",
            "volume_id": "PL072",
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1437,
            "page_end": 1439,
            "file_start": str(next(path for path in files if file_num(path) == SECTION_2_START)),
            "file_end": str(next(path for path in files if file_num(path) == SECTION_2_END)),
            "confidence": 0.97,
            "raw_json": {
                "source_files": [str(path) for path in files if in_section(path, SECTION_2_START, SECTION_2_END)],
                "section_kind_reason": "Editorial contents block after the alphabetical index.",
            },
        }
    ]


def build_helper_request(source_root: Path) -> dict[str, Any]:
    sample_file = source_root / "4c9f5b92-0be6-41fe-a1a1-534425bde8d3-568.txt"
    return {
        "volume_id": "PL072",
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "pl072_cerei_benedictio_140",
                "lemma_raw": "Cerei benedictio in Sabbato sancto",
                "query_names": [
                    "Cerei benedictio in Sabbato sancto",
                    "cerei paschalis",
                    "lucerna paschalis",
                ],
                "page_hints": ["140", "141", "142"],
                "page_hint_ints": [140, 141, 142],
                "context_raw": "Cerei benedictio in Sabbato sancto, 140, 141, 142.",
                "expected_manual": {
                    "target_file": str(sample_file),
                    "printed_page_anchor": 140,
                    "reason": "Sample page-locator request for the index tail; used only as a sanity check."
                },
            },
            {
                "entry_id": "pl072_lucerna_benedictio_140",
                "lemma_raw": "Lucernae benedictio in Sabbato sancto",
                "query_names": [
                    "Lucernae benedictio in Sabbato sancto",
                    "lucernae benedictio",
                    "lucerna paschalis",
                ],
                "page_hints": ["140", "141", "142"],
                "page_hint_ints": [140, 141, 142],
                "context_raw": "Lucernae benedictio in Sabbato sancto, 140, 141, 142.",
            },
        ],
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any] | None:
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
    if helper_output_json.exists():
        return read_json(helper_output_json)
    return None


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    page_map = build_page_map(files)
    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    sections_1, nodes_1, entries_1, refs_1, scripture_refs_1 = build_section_1(files, page_map, helper_output)
    sections_2 = build_section_2(files)

    volume = {
        "volume_id": "PL072",
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina, volume 72",
        "notes": "Alphabetical index and closing contents block recovered from the OCR tail.",
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical index and the closing ORDO RERUM block from the OCR tail using conservative line-based grouping. OCR line wraps and inherited page labels were preserved rather than normalized away.",
        "evidence_files": [
            str(next(path for path in files if file_num(path) == num))
            for num in [568, 569, 570, 571, 572, 573, 574, 575, 576, 577]
        ],
    }
    notes = [
        "Section 1 covers the alphabetical index from file 568 through file 575.",
        "Section 2 covers the closing ORDO RERUM block in files 576-577.",
        "Helper request was run only as a local locator sanity check.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections_1 + sections_2)
    write_json(intermediate_dir / "nodes.json", nodes_1)
    write_json(intermediate_dir / "entries.json", entries_1)
    write_json(intermediate_dir / "refs.json", refs_1)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs_1)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": "PL072",
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": "/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL072_alphabetical_indices.json",
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": "PL072",
            "updated_at": now_iso(),
            "current_focus": "Finalize PL072 alphabetical payload and validate the closing contents block.",
            "completed": [
                "section boundaries recovered",
                "helper request generated",
                "intermediate payload fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Use OCR literals as printed; do not collapse the OCR file suffix with the printed page numbers.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections_1 + sections_2,
        "nodes": nodes_1,
        "entries": entries_1,
        "refs": refs_1,
        "scripture_refs": scripture_refs_1,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL072 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

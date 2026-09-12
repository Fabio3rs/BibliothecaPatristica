#!/usr/bin/env python3
"""Usage: build the PL123 closing ORDO RERUM payload and helper request.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl123_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL123/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL123_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL123_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL123 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL123_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL123"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 123"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
MANUAL_TARGET_OVERRIDES = {
    147: "/homessddata/Projects/pdfocr/teste/PL123/text/9504ebd7-107c-44ed-aaa6-4a6a15859963-078.txt",
    151: "/homessddata/Projects/pdfocr/teste/PL123/text/9504ebd7-107c-44ed-aaa6-4a6a15859963-080.txt",
    153: "/homessddata/Projects/pdfocr/teste/PL123/text/9504ebd7-107c-44ed-aaa6-4a6a15859963-081.txt",
    157: "/homessddata/Projects/pdfocr/teste/PL123/text/9504ebd7-107c-44ed-aaa6-4a6a15859963-083.txt",
    159: "/homessddata/Projects/pdfocr/teste/PL123/text/9504ebd7-107c-44ed-aaa6-4a6a15859963-084.txt",
    161: "/homessddata/Projects/pdfocr/teste/PL123/text/556276be-36d6-4a4b-b94b-b1b64d5d8d79-152.txt",
    163: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-087.txt",
    167: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-088.txt",
    169: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-089.txt",
    173: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-091.txt",
    175: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-092.txt",
    201: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-105.txt",
    203: "/homessddata/Projects/pdfocr/teste/PL123/text/10dba593-0ed3-4d31-89b3-1ab0a5b59c64-107.txt",
    295: "/homessddata/Projects/pdfocr/teste/PL123/text/556276be-36d6-4a4b-b94b-b1b64d5d8d79-152.txt",
    449: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-229.txt",
    451: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-230.txt",
    453: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-246.txt",
    459: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-248.txt",
    467: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-238.txt",
    489: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-249.txt",
    495: "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-252.txt",
    501: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-255.txt",
    507: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-258.txt",
    519: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-264.txt",
    531: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-270.txt",
    537: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-276.txt",
    543: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-276.txt",
    554: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-281.txt",
    561: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-285.txt",
    569: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-289.txt",
    573: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-291.txt",
    577: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-293.txt",
    579: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-294.txt",
    583: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-296.txt",
    587: "/homessddata/Projects/pdfocr/teste/PL123/text/6795d98f-b851-48d7-ab40-d53ad0368cdc-298.txt",
    601: "/homessddata/Projects/pdfocr/teste/PL123/text/968f90a7-48cf-4ce2-8726-b1494b8a3589-305.txt",
    717: "/homessddata/Projects/pdfocr/teste/PL123/text/cd8fe941-b052-4fed-a2d4-ec96f71c7c45-362.txt",
    807: "/homessddata/Projects/pdfocr/teste/PL123/text/cd8fe941-b052-4fed-a2d4-ec96f71c7c45-398.txt",
    863: "/homessddata/Projects/pdfocr/teste/PL123/text/b1bc6c9e-c88d-4890-973d-879abad6bcd1-436.txt",
}


@dataclass(slots=True)
class TocItem:
    entry_order: int
    source_file: str
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    query_names: list[str]
    group_key: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def fold(text: str | None) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", text)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = fold(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def page_sort_key(path: Path) -> tuple[int, str]:
    m = re.search(r"-(\d+)\.txt$", path.name)
    return (int(m.group(1)) if m else 10**9, path.name)


def extract_body_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo = re.search(r'tipo="([^"]+)"', attrs)
        if (tipo.group(1).strip().lower() if tipo else "") != "texto_principal":
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def locate_section_file(source_root: Path) -> Path:
    for path in sorted(source_root.glob("*.txt"), key=page_sort_key):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "ORDO RERUM" in text and "QUÆ IN HOC TOMO CONTINENTUR." in text:
            return path
        if "ORDO RERUM" in text and "QUAE IN HOC TOMO CONTINENTUR." in text:
            return path
    raise SystemExit(f"Could not locate ORDO RERUM page in {source_root}")


def combine_hyphenated_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.endswith("-") and i + 1 < len(lines):
            line = f"{line[:-1]}{lines[i + 1].lstrip()}"
            i += 2
            out.append(line)
            continue
        out.append(line)
        i += 1
    return out


def strip_page_ref(line: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$", line)
    if not match:
        return line, None, None
    lemma = normalize(match.group("lemma").rstrip(" ,;:."))
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def clean_query_variant(text: str) -> str:
    value = normalize(text)
    value = re.sub(r"\s*—\s*", " ", value)
    value = re.sub(r"\bcult\s+us\b", "cultus", value, flags=re.I)
    value = value.replace("Precaiio", "Precatio")
    value = value.replace("Roswekdus", "Rosweydus")
    value = value.replace("Colices", "Codices")
    value = value.replace("Velnensem", "Velinensem")
    value = value.replace("Heriniensi", "Hereniensi")
    value = value.replace("m die", "in die")
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value


def make_query_names(lemma_raw: str) -> list[str]:
    candidates = [lemma_raw]
    if "—" in lemma_raw:
        after_dash = lemma_raw.split("—", 1)[1].strip()
        if after_dash:
            candidates.append(after_dash)
    stripped = clean_query_variant(lemma_raw)
    if stripped:
        candidates.append(stripped)
    folded = clean_query_variant(fold(lemma_raw))
    if folded and folded not in candidates:
        candidates.append(folded)
    # Extra OCR-tolerant variants for recurring errors in this page.
    extra = {
        "Precaiio": "Precatio",
        "Roswekdus": "Rosweydus",
        "Colices": "Codices",
        "cult us": "cultus",
    }
    for bad, good in extra.items():
        if bad in lemma_raw and good not in candidates:
            candidates.append(lemma_raw.replace(bad, good))
    return list(dict.fromkeys(candidates))


def is_group_heading(line: str) -> bool:
    return strip_page_ref(line)[1] is None


def parse_items(section_file: Path) -> list[TocItem]:
    raw_lines = combine_hyphenated_lines(extract_body_lines(section_file))
    items: list[TocItem] = []
    current_group: str | None = None
    order = 0
    started = False

    for raw_line in raw_lines:
        lemma_raw, page_raw, page_int = strip_page_ref(raw_line)
        if not started:
            if lemma_raw == "SANCTUS ADO, ARCHIEPISCOPUS VIENNENSIS.":
                started = True
            else:
                continue
        if is_group_heading(raw_line):
            current_group = lemma_raw
        order += 1
        items.append(
            TocItem(
                entry_order=order,
                source_file=section_file.as_posix(),
                entry_raw=raw_line,
                lemma_raw=lemma_raw,
                page_ref_raw=page_raw,
                page_ref_int=page_int,
                query_names=make_query_names(lemma_raw) if page_raw else [],
                group_key=current_group,
            )
        )
    return items


def build_helper_request(source_root: Path, items: list[TocItem]) -> dict[str, Any]:
    entries = []
    for item in items:
        if item.page_ref_raw is None:
            continue
        entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.entry_order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names,
                "page_hints": [item.page_ref_raw],
                "page_hint_ints": [item.page_ref_int],
                "context_raw": item.entry_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            "scripts/index_target_locator.py",
            "--input",
            helper_request_json.as_posix(),
            "--output",
            helper_output_json.as_posix(),
            "--pretty",
        ],
        check=True,
    )
    return read_json(helper_output_json)


def build_payload(
    source_root: Path,
    section_file: Path,
    items: list[TocItem],
    helper_output: dict[str, Any],
    intermediate_dir: Path,
) -> dict[str, Any]:
    helper_by_entry_id = {entry["entry_id"]: entry for entry in helper_output.get("entries", [])}

    section_pages = [item.page_ref_int for item in items if item.page_ref_int is not None]
    page_start = min(section_pages) if section_pages else None
    page_end = max(section_pages) if section_pages else None

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": section_file.as_posix(),
        "file_end": section_file.as_posix(),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "final_volume_ordo_rerum_closure",
            "evidence_files": [section_file.as_posix()],
            "source_note": "fallback_internal filtered pages confirmed the section heading in file 500.",
        },
    }

    nodes: list[dict[str, Any]] = []
    node_map: dict[str, str] = {}
    node_order = 0
    for item in items:
        if item.page_ref_raw is not None:
            continue
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        node_map[item.lemma_raw] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "rubric_group",
                "label_raw": item.lemma_raw,
                "label_norm": sort_norm(item.lemma_raw),
                "label_sort": sort_norm(item.lemma_raw),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source_file": section_file.as_posix(), "role": "major_heading"},
            }
        )

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    current_group_node: str | None = None
    entry_order = 0
    for item in items:
        if item.page_ref_raw is None:
            current_group_node = node_map.get(item.lemma_raw)
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        helper_entry_id = f"{VOLUME_ID.lower()}_{item.entry_order:03d}"
        helper_result = helper_by_entry_id.get(helper_entry_id)
        if item.page_ref_raw is None:
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": current_group_node,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group",
                    "lemma_raw": item.lemma_raw,
                    "lemma_display": item.lemma_raw,
                    "lemma_norm": sort_norm(item.lemma_raw),
                    "lemma_sort": sort_norm(item.lemma_raw),
                    "entry_raw": item.entry_raw,
                    "context_raw": item.entry_raw,
                    "heading_letter": None,
                    "inferred_printed_page": None,
                    "section_start_file": section_file.as_posix(),
                    "editorial_anchor_file": section_file.as_posix(),
                    "target_file_best": section_file.as_posix(),
                    "confidence": 0.8,
                    "raw_json": {
                        "source_file": section_file.as_posix(),
                        "section_kind": "ordo_rerum",
                        "entry_kind_reason": "top_level_toc_heading",
                    },
                }
            )
            continue

        best_candidate = (helper_result or {}).get("best_candidate") or {}
        target_file = MANUAL_TARGET_OVERRIDES.get(item.page_ref_int, best_candidate.get("file") or section_file.as_posix())
        target_probability = best_candidate.get("probability")
        helper_status = (helper_result or {}).get("status")
        confidence = 0.9 if helper_status == "resolved" else 0.82
        target_override_reason = None
        if item.page_ref_int in MANUAL_TARGET_OVERRIDES:
            target_override_reason = "manual_target_override_from_direct_ocr_inspection"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": current_group_node,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": sort_norm(item.lemma_raw),
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": section_file.as_posix(),
                "editorial_anchor_file": target_file,
                "target_file_best": target_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": section_file.as_posix(),
                    "section_kind": "ordo_rerum",
                    "query_names": item.query_names,
                    "helper": helper_result,
                    **({"target_override_reason": target_override_reason} if target_override_reason else {}),
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": item.page_ref_raw,
                "page_ref_raw": item.page_ref_raw,
                "page_ref_int": item.page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": target_probability,
                "section_start_file": section_file.as_posix(),
                "editorial_anchor_file": target_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": section_file.as_posix(),
                    "section_kind": "ordo_rerum",
                    "helper": helper_result,
                    **({"target_override_reason": target_override_reason} if target_override_reason else {}),
                },
            }
        )

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
        "notes": "Final ORDO RERUM closure for the volume.",
    }
    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved page-bearing entries to target OCR files.",
        "evidence_files": [section_file.as_posix()],
    }
    notes = [
        "This volume ends with an ORDO RERUM contents table rather than an alphabetical index proper.",
        "The printed pages cited inside the contents table are preserved literally, even when the sequence is not monotonic across OCR columns.",
        "Three major headings without page numbers are kept as heading_group entries and mirrored in nodes.",
    ]
    scripture_refs: list[dict[str, Any]] = []
    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
    }

    fragments = {
        "manifest.json": manifest,
        "volume.json": volume,
        "sections.json": [section],
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": scripture_refs,
        "coverage.json": coverage,
        "notes.json": notes,
    }
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

    payload = {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }
    return payload


def update_todo(intermediate_dir: Path, *, finished: bool) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Assemble PL123 ORDO RERUM payload",
        "completed": ["section identified", "helper request built", "entries and refs assembled"] if finished else ["section identified"],
        "pending": [] if finished else ["run helper", "assemble final payload"],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "The table of contents is the only recovered section in this tail window.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL123 closing ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    update_todo(args.intermediate_dir, finished=False)

    section_file = locate_section_file(args.source_root)
    items = parse_items(section_file)
    helper_request = build_helper_request(args.source_root, items)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(args.source_root, section_file, items, helper_output, args.intermediate_dir)

    update_todo(args.intermediate_dir, finished=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output_file.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()

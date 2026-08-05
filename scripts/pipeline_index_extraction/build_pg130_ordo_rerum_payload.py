#!/usr/bin/env python3
"""Usage: build the PG130 ORDO RERUM alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg130_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG130/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG130_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG130_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG130 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG130_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG130"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 130"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_KIND_REASON = (
    "Closing ORDO RERUM contents table with hierarchical work headings and page-bearing lines; "
    "this is editorial contents structure rather than an alphabetical lemma index."
)
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
SCRIPT_READ_OCR = ROOT / "scripts/read_ocr_page_text.py"
SECTION_FILE_START_SEQ = 688
SECTION_FILE_END_SEQ = 692
SECTION_PAGE_START = 1363
SECTION_PAGE_END = 1372

HEADING_RE = re.compile(r"^(EUTHYMIUS ZIGABENUS\.|PANOPLIA DOGMATICA\.|APPENDIX\.)$")
TITULUS_RE = re.compile(r"^TITULUS\s+([IVXLCDM]+)\.\s*(.*)$", re.IGNORECASE)
PAGE_END_RE = re.compile(r"^(?P<text>.*?)(?:\s+)(?P<page>\d{1,4})(?:[)\].,'’]*)?$")
HEADER_LINE_RE = re.compile(
    r"^(?:\d{3,4}\s+)?(?:ORDO RERUM(?:\s+QU(?:AE|Æ|E)\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU(?:AE|Æ|E)\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)(?:\s+\d{3,4})?$",
    re.IGNORECASE,
)
WS_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def fold_for_sort(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
        .replace("Ĳ", "IJ")
        .replace("ĳ", "ij")
    )
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def extract_lines(path: Path) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_READ_OCR),
            "--json",
            "--view",
            "body",
            path.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"read_ocr_page_text.py failed for {path}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    payload = json.loads(proc.stdout)
    body_text = payload.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body_text.splitlines():
        line = normalize(raw_line)
        if not line or line == "Digitized by Google":
            continue
        lines.append(line)
    return lines


def discover_files(source_root: Path) -> list[Path]:
    selected: list[Path] = []
    for path in sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1])):
        seq = int(path.stem.rsplit("-", 1)[-1])
        if SECTION_FILE_START_SEQ <= seq <= SECTION_FILE_END_SEQ:
            selected.append(path)
    return selected


def extract_sections_lines(source_root: Path) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit("No OCR files found in the PG130 tail window.")
    lines: list[tuple[str, str]] = []
    for path in files:
        for line in extract_lines(path):
            lines.append((path.as_posix(), line))
    return lines, {
        "files": [p.as_posix() for p in files],
        "section_start_file": files[0].as_posix(),
        "section_end_file": files[-1].as_posix(),
    }


def repair_midline_page_breaks(lines_with_source: list[tuple[str, str]]) -> list[tuple[str, str]]:
    repaired: list[tuple[str, str]] = []
    idx = 0
    while idx < len(lines_with_source):
        source_file, line = lines_with_source[idx]
        match = PAGE_END_RE.match(line)
        if (
            match
            and match.group("text")
            and normalize(match.group("text"))[-1:].isalnum()
            and idx + 1 < len(lines_with_source)
        ):
            next_source, next_line = lines_with_source[idx + 1]
            if (
                next_source == source_file
                and next_line
                and not HEADER_LINE_RE.fullmatch(next_line)
                and not TITULUS_RE.match(next_line)
                and next_line != "APPENDIX."
                and not PAGE_END_RE.match(next_line)
            ):
                repaired.append((source_file, normalize(match.group("text"))))
                repaired.append((next_source, normalize(f"{next_line} {match.group('page')}")))
                idx += 2
                continue
        repaired.append((source_file, line))
        idx += 1
    return repaired


def strip_page(text: str) -> tuple[str, str | None, int | None]:
    cleaned = normalize(text)
    match = PAGE_END_RE.match(cleaned)
    if not match:
        return cleaned, None, None
    lemma = normalize(match.group("text")).rstrip(" ,;:.")
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def query_names(lemma_raw: str) -> list[str]:
    candidates = [normalize(lemma_raw)]
    if candidates[0].startswith(("Item ", "Ejusdem ", "Ex ", "De ", "Aliterius ", "Ejusdem,", "Item,", "Ex,")):
        tail = re.sub(
            r"^(?:Item|Ejusdem|Ex|De|Aliterius)(?:,)?\s+",
            "",
            candidates[0],
            flags=re.IGNORECASE,
        )
        tail = normalize(tail)
        if tail:
            candidates.append(tail)
    quoted = re.findall(r"[«\"]([^»\"]+)[»\"]", lemma_raw)
    for item in quoted:
        item = normalize(item)
        if item:
            candidates.append(item)
    candidates.append(normalize(lemma_raw).replace("Æ", "AE").replace("æ", "ae"))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = normalize(item)
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:5]


def helper_compact(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates: list[dict[str, Any]] = []
    for candidate in (helper_entry.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in candidate.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            }
        )
    return {
        "status": helper_entry.get("status"),
        "candidate_role": helper_entry.get("candidate_role"),
        "reason_summary": helper_entry.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper_entry.get("candidates") or []),
        "candidates": candidates,
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    lines_with_source, evidence = extract_sections_lines(source_root)
    lines_with_source = repair_midline_page_breaks(lines_with_source)

    # Section / node hierarchy.
    nodes: list[dict[str, Any]] = []
    root_node_key = f"{VOLUME_ID}:node:001"
    panoplia_node_key = f"{VOLUME_ID}:node:002"
    nodes.append(
        {
            "node_key": root_node_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "EUTHYMIUS ZIGABENUS.",
            "label_norm": normalize("EUTHYMIUS ZIGABENUS.").lower(),
            "label_sort": fold_for_sort("EUTHYMIUS ZIGABENUS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "source_file": evidence["section_start_file"],
                "note": "First work heading in the contents table.",
            },
        }
    )
    nodes.append(
        {
            "node_key": panoplia_node_key,
            "section_key": SECTION_KEY,
            "parent_node_key": root_node_key,
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "PANOPLIA DOGMATICA.",
            "label_norm": normalize("PANOPLIA DOGMATICA.").lower(),
            "label_sort": fold_for_sort("PANOPLIA DOGMATICA."),
            "node_level": 2,
            "confidence": 0.98,
            "raw_json": {
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "source_file": evidence["section_start_file"],
                "note": "Main work title under the author heading.",
            },
        }
    )

    node_keys: dict[str, str] = {"ROOT": root_node_key, "PANOPLIA": panoplia_node_key}
    node_order = 2

    # Parse entries.
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_parent_node_key = panoplia_node_key
    buffer: list[str] = []
    buffer_source_file: str | None = None
    current_entry_prefix: str | None = None
    current_entry_page_raw: str | None = None
    current_entry_page_int: int | None = None

    def flush_entry(source_file: str) -> None:
        nonlocal buffer, buffer_source_file, current_entry_prefix, current_entry_page_raw, current_entry_page_int
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        if not entry_raw:
            buffer = []
            buffer_source_file = None
            current_entry_prefix = None
            current_entry_page_raw = None
            current_entry_page_int = None
            return
        lemma_raw, page_raw, page_int = strip_page(entry_raw)
        if current_entry_prefix is not None and not lemma_raw.startswith(current_entry_prefix):
            lemma_raw = current_entry_prefix + lemma_raw
        entry_order = len(entries) + 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": current_parent_node_key,
            "entry_order": entry_order,
            "entry_kind": "lemma",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize(lemma_raw).lower(),
            "lemma_sort": fold_for_sort(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": evidence["section_start_file"],
            "editorial_anchor_file": source_file,
            "target_file_best": None,
            "confidence": 0.78 if page_int is not None else 0.9,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "entry_kind_reason": "contents-table line item under the current ordinal heading.",
            },
        }
        entries.append(entry)
        if page_raw is not None and page_int is not None:
            helper_id = f"{VOLUME_ID.lower()}_{entry_order:04d}"
            helper_entries.append(
                {
                    "entry_id": helper_id,
                    "lemma_raw": lemma_raw,
                    "query_names": query_names(lemma_raw),
                    "page_hints": [page_raw],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": evidence["section_start_file"],
                    "editorial_anchor_file": source_file,
                    "confidence": 0.74,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "helper_entry_id": helper_id,
                        "page_ref_source": "ocr_contents_table",
                    },
                }
            )
        buffer = []
        buffer_source_file = None
        current_entry_prefix = None
        current_entry_page_raw = None
        current_entry_page_int = None

    for source_file, line in lines_with_source:
        if line in {"EUTHYMIUS ZIGABENUS.", "PANOPLIA DOGMATICA."}:
            if line == "PANOPLIA DOGMATICA.":
                current_parent_node_key = panoplia_node_key
            continue
        if HEADER_LINE_RE.fullmatch(line):
            continue
        if line.startswith("FINIS TOMI") or line.startswith("PARISIIS."):
            continue

        titulus_match = TITULUS_RE.match(line)
        if titulus_match:
            # Start a structural node and treat any text after the label as the first entry in the node.
            if buffer:
                flush_entry(source_file)
            roman = titulus_match.group(1).upper()
            remainder = normalize(titulus_match.group(2))
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            node_keys[roman] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": panoplia_node_key,
                    "node_order": node_order,
                    "node_kind": "ordinal_group",
                    "label_raw": f"TITULUS {roman}.",
                    "label_norm": f"titulus {roman.lower()}.",
                    "label_sort": fold_for_sort(f"TITULUS {roman}."),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "section_kind_reason": SECTION_KIND_REASON,
                        "source_file": source_file,
                        "node_scope": "contents-table titulary section",
                    },
                }
            )
            current_parent_node_key = node_key
            if remainder:
                buffer = [remainder]
                buffer_source_file = source_file
                current_entry_prefix = None
                if remainder.isdigit() or PAGE_END_RE.match(remainder):
                    flush_entry(source_file)
            continue

        if line == "APPENDIX.":
            if buffer:
                flush_entry(source_file)
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            node_keys["APPENDIX"] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": panoplia_node_key,
                    "node_order": node_order,
                    "node_kind": "rubric_group",
                    "label_raw": "APPENDIX.",
                    "label_norm": "appendix.",
                    "label_sort": fold_for_sort("APPENDIX."),
                    "node_level": 1,
                    "confidence": 0.97,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "section_kind_reason": SECTION_KIND_REASON,
                        "source_file": source_file,
                        "node_scope": "closing appendix heading",
                    },
                }
            )
            current_parent_node_key = node_key
            continue

        if not buffer:
            buffer = [line.replace("toti- humanae", "toti humanae")]
            buffer_source_file = source_file
            current_entry_prefix = None
            if line.isdigit() or PAGE_END_RE.match(normalize(line)):
                flush_entry(source_file)
            continue

        buffer.append(line.replace("toti- humanae", "toti humanae"))
        if line.isdigit() or PAGE_END_RE.match(normalize(line)):
            flush_entry(source_file)

    if buffer:
        flush_entry(buffer_source_file or evidence["section_start_file"])

    # Build helper request from the entries that carry printed page numbers.
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)

    if helper_entries:
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
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(
                f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
    else:
        write_json(helper_output_json, {"volume_id": VOLUME_ID, "status": "empty", "entries": []})

    helper_output = read_json(helper_output_json, {})
    helper_by_id = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }

    for entry in entries:
        helper_id = f"{VOLUME_ID.lower()}_{entry['entry_order']:04d}"
        helper = helper_by_id.get(helper_id)
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        best_file = best.get("file")
        if best_file:
            entry["target_file_best"] = best_file
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))
        entry["raw_json"]["helper"] = helper_compact(helper)

    for ref in refs:
        helper = helper_by_id.get(ref["raw_json"]["helper_entry_id"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref["confidence"] = max(ref["confidence"], float(best.get("probability") or 0.0))
        ref["raw_json"]["helper"] = helper_compact(helper)

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": evidence["section_start_file"],
            "file_end": evidence["section_end_file"],
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "evidence_files": evidence["files"],
                "heading_variants": [
                    "ORDO RERUM QUE IN HOC TOMO CONTINENTUR.",
                    "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
                ],
            },
        }
    ]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM table of contents from OCR files 688-692 and "
            "resolved the page-linked lines with the local helper."
        ),
        "evidence_files": evidence["files"],
    }

    notes = [
        "The closing material is an ORDO RERUM contents table, not an alphabetical lemma index.",
        "OCR file suffixes were kept distinct from printed page citations in every ref.",
        "The helper request includes every page-linked line item in the section, including split lines and entries with repeated page citations.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(intermediate_dir / "manifest.json", payload)

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG130 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve the closing ORDO RERUM table and keep OCR page citations literal.",
        "completed": [
            "identified the closing ORDO RERUM section",
            "bounded the OCR window to files 688-692",
        ],
        "pending": [
            "run index_target_locator on the page-linked lines",
            "assemble and validate the final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR suffixes distinct from printed page citations.",
            "Treat TITULUS labels and APPENDIX as structure, not as lemma entries.",
        ],
    }
    write_json(todo_path, todo)

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

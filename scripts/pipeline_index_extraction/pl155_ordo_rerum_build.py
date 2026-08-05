#!/usr/bin/env python3
"""Usage: build the PL155 closing ORDO RERUM payload and helper request.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl155_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL155/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL155_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL155_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL155 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL155_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


VOLUME_ID = "PL155"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 155"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 2119
SECTION_PAGE_END = 2132
SECTION_FILE_START_SEQ = 1063
SECTION_FILE_END_SEQ = 1069


@dataclass(slots=True)
class TocEntry:
    entry_order: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    source_file: str
    page_ref_source: str | None
    query_names: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    return value.lower() if value else None


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize_space(raw_line)
            if line:
                lines.append(line)
    return lines


def page_suffix_to_int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def strip_trailing_page(line: str) -> tuple[str, str | None, int | None]:
    m = re.search(r"(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$", line)
    if not m:
        return line, None, None
    return m.group("lemma").strip(), m.group("page"), int(m.group("page"))


def strip_page_from_entry(entry_raw: str) -> str:
    return re.sub(r"\s+\d{1,4}$", "", entry_raw).strip()


def make_query_names(lemma_raw: str) -> list[str]:
    value = normalize_space(lemma_raw)
    if not value:
        return []
    stripped = re.sub(r"^\s*(?:[IVXLCDM]+\.|[A-Z]\.)\s*—\s*", "", value)
    stripped = re.sub(r"^\s*(?:Cap\.|CAP\.)\s*[IVXLCDM]+\.\s*—\s*", "", stripped)
    stripped = re.sub(r"\s+\b(?:sive|sive)\b\s+", " ", stripped)
    candidates = [value]
    if stripped and stripped != value:
        candidates.append(stripped)
    if "—" in value:
        candidates.append(value.split("—", 1)[1].strip())
    return [q for q in dict.fromkeys(normalize_space(q) for q in candidates if normalize_space(q))]


def is_noise_line(line: str) -> bool:
    return line == "Digitized by Google"


def is_section_heading_line(line: str) -> bool:
    if line in {
        "2119 ORDO RERUM 2120",
        "2123 ORDO RERUM 2124",
        "2127 ORDO RERUM 2128",
        "2131 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 2132",
        "ORDO RERUM",
        "QUÆ IN HOC TOMO CONTINENTUR.",
        "QUAE IN HOC TOMO CONTINENTUR.",
    }:
        return True
    return bool(re.search(r"\bORDO RERUM\b", line))


def likely_standalone_heading(line: str) -> bool:
    if not line:
        return False
    if is_section_heading_line(line):
        return False
    if re.fullmatch(r"\d{3,4}", line):
        return False
    if re.search(r"\d{1,4}$", line):
        return False
    if line.endswith((".", ":")):
        return True
    if line.isupper() and len(line.split()) <= 10:
        return True
    if len(line) <= 40 and len(line.split()) <= 5 and not line.endswith((",", ";", "—", "-")):
        return True
    if len(line) <= 30 and line[0].isupper() and " " not in line:
        return True
    return False


def parse_entries(source_root: Path) -> list[TocEntry]:
    files = [source_root / f"cb87cccc-68a7-46ba-ba6f-775519cbfdba-{seq}.txt" for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)]
    lines_with_source: list[tuple[str, str]] = []
    for path in files:
        for line in extract_lines(path):
            lines_with_source.append((line, str(path)))

    entries: list[TocEntry] = []
    buffer: list[str] = []
    buffer_source: str | None = None
    entry_order = 0

    def flush_buffer(page_ref_raw: str | None = None, page_ref_int: int | None = None, source_file: str | None = None) -> None:
        nonlocal entry_order, buffer, buffer_source
        if not buffer:
            return
        entry_order += 1
        entry_raw = normalize_space(" ".join(buffer))
        lemma_raw = strip_page_from_entry(entry_raw) if page_ref_raw else entry_raw
        entries.append(
            TocEntry(
                entry_order=entry_order,
                entry_raw=entry_raw,
                lemma_raw=lemma_raw,
                page_ref_raw=page_ref_raw,
                page_ref_int=page_ref_int,
                source_file=source_file or buffer_source or "",
                page_ref_source=source_file,
                query_names=make_query_names(lemma_raw),
            )
        )
        buffer = []
        buffer_source = None

    for line, source_file in lines_with_source:
        if is_noise_line(line):
            continue
        if is_section_heading_line(line):
            continue
        if re.fullmatch(r"\d{3,4}", line):
            if buffer:
                flush_buffer(page_ref_raw=line, page_ref_int=int(line), source_file=source_file)
            continue
        lemma_no_page, page_ref_raw, page_ref_int = strip_trailing_page(line)
        if page_ref_raw is not None:
            if buffer:
                buffer.append(lemma_no_page)
                flush_buffer(page_ref_raw=page_ref_raw, page_ref_int=page_ref_int, source_file=source_file)
            else:
                entry_order += 1
                entries.append(
                    TocEntry(
                        entry_order=entry_order,
                        entry_raw=line,
                        lemma_raw=lemma_no_page,
                        page_ref_raw=page_ref_raw,
                        page_ref_int=page_ref_int,
                        source_file=source_file,
                        page_ref_source=source_file,
                        query_names=make_query_names(lemma_no_page),
                    )
                )
            continue
        if likely_standalone_heading(line):
            if buffer:
                flush_buffer(source_file=source_file)
            entry_order += 1
            entries.append(
                TocEntry(
                    entry_order=entry_order,
                    entry_raw=line,
                    lemma_raw=line,
                    page_ref_raw=None,
                    page_ref_int=None,
                    source_file=source_file,
                    page_ref_source=None,
                    query_names=make_query_names(line),
                )
            )
            continue
        buffer.append(line)
        if buffer_source is None:
            buffer_source = source_file

    if buffer:
        flush_buffer()

    return entries


def build_helper_request(source_root: Path, entries: list[TocEntry]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in entries:
        if item.page_ref_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.entry_order:04d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names or [item.lemma_raw],
                "page_hints": [item.page_ref_raw] if item.page_ref_raw else [],
                "page_hint_ints": [item.page_ref_int] if item.page_ref_int is not None else [],
                "context_raw": item.entry_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
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
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(source_root: Path, entries: list[TocEntry], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_by_id = helper_index(helper_output)
    section_start_file = str(source_root / f"cb87cccc-68a7-46ba-ba6f-775519cbfdba-{SECTION_FILE_START_SEQ}.txt")
    section_end_file = str(source_root / f"cb87cccc-68a7-46ba-ba6f-775519cbfdba-{SECTION_FILE_END_SEQ}.txt")

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
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "closing_ordo_rerum_table_of_contents",
                "evidence_files": [
                    str(source_root / f"cb87cccc-68a7-46ba-ba6f-775519cbfdba-{seq}.txt")
                    for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)
                ],
            },
        }
    ]

    out_entries: list[dict[str, Any]] = []
    out_refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for item in entries:
        entry_key = f"{VOLUME_ID}:entry:{item.entry_order:04d}"
        helper_entry = helper_by_id.get(f"{VOLUME_ID.lower()}_{item.entry_order:04d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        helper_status = helper_entry.get("status")
        target_file_best = best.get("file") if best else None
        target_prob = best.get("probability") if best else None
        if target_file_best is None:
            target_file_best = item.source_file
        confidence = 0.88 if item.page_ref_int is not None else 0.82
        if helper_status == "ambiguous":
            confidence = min(confidence, 0.76)
        if helper_status == "unresolved":
            confidence = min(confidence, 0.68)

        out_entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": item.entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": normalize_space(item.lemma_raw).lower() if item.lemma_raw else None,
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "source_file": item.source_file,
                    "page_ref_source": item.page_ref_source,
                    "entry_kind_reason": "closing_ordo_rerum_contents_line" if item.page_ref_int is not None else "closing_ordo_rerum_heading",
                    "helper_status": helper_status,
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
                    "helper_candidates_top": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in candidates[:3]
                    ],
                },
            }
        )

        if item.page_ref_raw is None:
            continue

        out_refs.append(
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
                "target_file": target_file_best,
                "target_file_probability": target_prob,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": item.source_file,
                    "page_ref_source": item.page_ref_source,
                    "helper_status": helper_status,
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
                    "helper_candidates_top": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in candidates[:3]
                    ],
                },
            }
        )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM contents table from OCR files 1063-1069, "
            "preserving page-number lines and standalone editorial headings."
        ),
        "evidence_files": [
            str(source_root / f"cb87cccc-68a7-46ba-ba6f-775519cbfdba-{seq}.txt")
            for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)
        ],
    }

    notes = [
        "The volume closes with an ORDO RERUM contents table, not an alphabetical author index.",
        "OCR page numbers are kept literally; several are isolated on their own line and were attached to the preceding TOC line.",
        "Standalone headings without page refs were preserved as entries because they structure the contents table.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": out_entries,
        "refs": out_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL155 ORDO RERUM alphabetical payload.")
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
        "current_focus": "Resolve the closing ORDO RERUM table and preserve OCR page-number line breaks literally.",
        "completed": [
            "identified the final ORDO RERUM section",
            "parsed OCR lines from files 1063-1069",
        ],
        "pending": [
            "run index_target_locator on page-linked contents lines",
            "assemble and validate the final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR file suffixes separate from printed page refs.",
            "Use the helper only to choose the best OCR file for each page-linked contents line.",
        ],
    }
    write_json(todo_path, todo)

    entries = parse_entries(args.source_root)
    write_json(args.intermediate_dir / "entries.json", [asdict(item) for item in entries])

    helper_request = build_helper_request(args.source_root, entries)
    write_json(args.helper_request_json, helper_request)
    write_json(args.intermediate_dir / "helper_request.json", helper_request)

    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    write_json(args.intermediate_dir / "helper_output.json", helper_output)

    payload = build_payload(args.source_root, entries, helper_output)
    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "payload.json", payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

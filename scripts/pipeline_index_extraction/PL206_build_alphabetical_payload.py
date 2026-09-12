#!/usr/bin/env python3
"""Usage: build the PL206 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/PL206_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL206/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL206_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL206_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL206 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL206_alphabetical_indices.json
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


VOLUME_ID = "PL206"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 206"

SOURCE_SEQ_START = 650
SOURCE_SEQ_END = 655
SECTION_1_END_SEQ = 654
SECTION_2_SEQ = 655

SECTION_1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:analytic_subject:002"

SECTION_1_HEADING_RAW = (
    "TABULA RERUM ET VERBORUM IN COMMENTARIIS IN CANTICA CANTICORUM VENERANDI "
    "PATRIS THOMÆ MONACHI CISTERCIENSIS ADNOTANDORUM."
)
SECTION_1_HEADING_NORM = (
    "Tabula rerum et verborum in commentariis in cantica canticorum venerandi "
    "Patris Thomae monachi Cisterciensis adnotandorum."
)
SECTION_2_HEADING_RAW = (
    "TABELLA NON MINUS COMPENDIOSA AC SUCCINCTA IN EMUNCTISSIMAM EXPLANATIONEM "
    "MAGNI THEOLOGI MAGISTRI JOANNIS ALGRINI AB ABBATISVILLA CARDINALIS."
)
SECTION_2_HEADING_NORM = (
    "Tabella non minus compendiosa ac succincta in emunctissimam explanationem "
    "magni theologi magistri Joannis Algrini ab Abbatisvilla cardinalis."
)

NOISE_LINES = {
    "Digitized by Google",
    "PATROL. CCVI. 41",
}

SECTION_1_SKIP_RE = re.compile(
    r"^(?:\d{3,4}\s+)?(?:INDEX IN THOM[ÆAE]\s+CISTERC\.?|TABULA RERUM ET VERBORUM|"
    r"In Commentariis in Cantica canticorum|De commentariis enim reverendi Patris|"
    r"Finis Tabell[æae] compendios[æae].*|Sequitur Tabella.*)$",
    re.IGNORECASE,
)
SECTION_2_SKIP_RE = re.compile(
    r"^(?:Finis Tabell[æae] compendios[æae].*|Sequitur Tabella.*)$",
    re.IGNORECASE,
)
HEADER_ONLY_RE = re.compile(r"^\d{3,4}$")
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒ]$")
TRAILING_TERMINAL_RE = re.compile(r"(?:[.!?:;]|\bibid\.?|\bid\.?|\bet\s+seq\.?|\bet\s+seqq\.?)\s*$", re.IGNORECASE)
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    return value.lower() if value else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        line = normalize_space(raw)
        if not line or line in NOISE_LINES:
            continue
        lines.append(line)
    return lines


def is_heading_like(line: str) -> bool:
    return bool(
        SECTION_1_SKIP_RE.fullmatch(line)
        or SECTION_2_SKIP_RE.fullmatch(line)
        or HEADER_ONLY_RE.fullmatch(line)
        or line.startswith("INDEX IN JOAN")
        or line.startswith("INDEX IN THOM")
    )


def line_is_letter(line: str) -> bool:
    return bool(LETTER_ONLY_RE.fullmatch(line))


def line_ends_chunk(line: str) -> bool:
    return bool(TRAILING_TERMINAL_RE.search(line))


def split_section_lines(
    files: list[Path],
    stop_before_text: str | None = None,
) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for path in files:
        for line in extract_lines(path):
            if stop_before_text and stop_before_text in line:
                return items
            items.append((path, line))
    return items


def group_lines(items: list[tuple[Path, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    current_lines: list[str] = []
    current_file: Path | None = None
    last_page: int | None = None
    entry_order = 0
    node_order = 0
    letter_nodes: list[dict[str, Any]] = []
    current_letter: str | None = None
    current_letter_node_key: str | None = None

    def flush() -> None:
        nonlocal current_lines, current_file, entry_order, last_page
        if not current_lines or current_file is None:
            current_lines = []
            current_file = None
            return
        source_file = current_file
        entry_raw = normalize_space(" ".join(current_lines))
        current_lines = []
        current_file = None
        if not entry_raw:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:s{current_section}:entry:{entry_order:06d}"
        lemma_raw = lemma_from_entry(entry_raw)
        page_refs, current_last_page = parse_refs(entry_raw, last_page, str(source_file))
        for idx, ref in enumerate(page_refs, start=1):
            ref["entry_key"] = entry_key
            ref["ref_order"] = idx
        inferred_printed_page = page_refs[0]["page_ref_int"] if page_refs else None
        target_file_best = str(source_file)
        entry_kind = "lemma"
        if not page_refs and re.match(r"^(?:Finis|Sequitur)\b", entry_raw, re.IGNORECASE):
            entry_kind = "editorial_note"
        elif not page_refs and re.match(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", entry_raw, re.IGNORECASE):
            entry_kind = "cross_reference"
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_1_KEY if current_section == 1 else SECTION_2_KEY,
            "parent_node_key": current_letter_node_key if current_section == 1 else None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": section_1_start_file if current_section == 1 else section_2_start_file,
            "editorial_anchor_file": str(source_file),
            "target_file_best": target_file_best,
            "confidence": 0.86 if page_refs else 0.72,
            "raw_json": {
                "source_file": str(source_file),
                "section_kind": "analytic_subject",
                "page_refs_found": [ref["page_ref_raw"] for ref in page_refs],
                "page_ref_ints": [ref["page_ref_int"] for ref in page_refs],
            },
        }
        entries.append(entry)
        refs.extend(page_refs)
        helper_entries.append(
            {
                "entry_id": helper_entry_id(entry_key),
                "lemma_raw": lemma_raw or entry_raw,
                "query_names": helper_query_names(entry_raw, lemma_raw),
                "page_hints": [str(ref["page_ref_int"]) for ref in page_refs],
                "page_hint_ints": [ref["page_ref_int"] for ref in page_refs],
                "context_raw": entry_raw,
            }
        )
        last_page = current_last_page

    def helper_entry_id(entry_key: str) -> str:
        return entry_key.replace(":entry:", "_").lower()

    def helper_query_names(entry_raw: str, lemma_raw: str | None) -> list[str]:
        values = []
        if lemma_raw:
            values.append(lemma_raw)
        raw = normalize_space(entry_raw)
        if raw:
            values.append(raw)
            prefix = raw.split(",", 1)[0].strip()
            if prefix and prefix != raw:
                values.append(prefix)
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            value = normalize_space(value)
            if value and value not in seen:
                out.append(value)
                seen.add(value)
        return out[:4]

    def lemma_from_entry(entry_raw: str) -> str | None:
        value = normalize_space(entry_raw)
        if not value:
            return None
        if value.startswith(("Finis ", "Sequitur ")):
            return value
        ref_match = PAGE_REF_RE.search(value)
        if ref_match:
            prefix = value[: ref_match.start()]
        else:
            prefix = value
        prefix = prefix.strip(" .;:")
        prefix = re.sub(r"\s+[A-ZÆŒ]$", "", prefix)
        return prefix or None

    def parse_refs(entry_raw: str, current_last_page: int | None, source_file: str) -> tuple[list[dict[str, Any]], int | None]:
        refs_local: list[dict[str, Any]] = []
        last = current_last_page
        for match in PAGE_REF_RE.finditer(entry_raw):
            start = int(match.group(1))
            end = match.group(2)
            raw = match.group(0).strip().rstrip(".,;:")
            if end is not None:
                refs_local.append(
                    {
                        "ref_order": len(refs_local) + 1,
                        "ref_kind": "editorial_range",
                        "ref_raw": raw,
                        "page_ref_raw": raw,
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(start),
                        "range_end_raw": str(int(end)),
                        "target_file": source_file,
                        "target_file_probability": 0.5,
                        "section_start_file": section_1_start_file if current_section == 1 else section_2_start_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.78,
                        "raw_json": {},
                    }
                )
            else:
                refs_local.append(
                    {
                        "ref_order": len(refs_local) + 1,
                        "ref_kind": "editorial_page",
                        "ref_raw": raw,
                        "page_ref_raw": raw,
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": source_file,
                        "target_file_probability": 0.5,
                        "section_start_file": section_1_start_file if current_section == 1 else section_2_start_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.8,
                        "raw_json": {},
                    }
                )
            last = start
        if not refs_local and re.search(r"\bibid\.?\b", entry_raw, re.IGNORECASE) and current_last_page is not None:
            refs_local.append(
                {
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": "ibid.",
                    "page_ref_raw": "ibid.",
                    "page_ref_int": current_last_page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": source_file,
                    "target_file_probability": 0.5,
                    "section_start_file": section_1_start_file if current_section == 1 else section_2_start_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.65,
                    "raw_json": {"ibid_resolved_from_previous_page": current_last_page},
                }
            )
            last = current_last_page
        return refs_local, last

    for path, line in items:
        evidence_files.append(str(path))
        if line in NOISE_LINES or is_heading_like(line):
            continue
        if line_is_letter(line):
            if current_lines:
                flush()
            if current_section == 1:
                node_order += 1
                node_key = f"{VOLUME_ID}:node:{node_order:04d}"
                current_letter = line
                current_letter_node_key = node_key
                letter_nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": SECTION_1_KEY,
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
        if current_file is None:
            current_file = path
        current_lines.append(line)
        if line_ends_chunk(line):
            flush()
    if current_lines:
        flush()
    return entries, refs, helper_entries, evidence_files, letter_nodes


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    section_1_files = [path for path in files if SOURCE_SEQ_START <= file_seq(path) <= SECTION_1_END_SEQ]
    section_2_files = [path for path in files if file_seq(path) == SECTION_2_SEQ]
    if not section_1_files or not section_2_files:
        raise SystemExit("Could not locate the PL206 index tail files.")

    global current_section, section_1_start_file, section_2_start_file, current_letter_node_key
    current_section = 1
    section_1_start_file = str(section_1_files[0])
    section_2_start_file = str(section_2_files[0])
    current_letter_node_key = None

    section_1_items = split_section_lines(section_1_files, stop_before_text="Finis Tabellæ compendiosæ")
    section_2_lines = extract_lines(section_2_files[0])
    section_2_start_idx = 0
    for idx, line in enumerate(section_2_lines):
        if SECTION_2_SKIP_RE.search(line):
            section_2_start_idx = idx + 1
            break
    section_2_items = [(section_2_files[0], line) for line in section_2_lines[section_2_start_idx:]]

    section_1_entries, section_1_refs, section_1_helpers, section_1_evidence, section_1_nodes = group_lines(section_1_items)
    current_section = 2
    current_letter_node_key = None
    section_2_entries, section_2_refs, section_2_helpers, section_2_evidence, _ = group_lines(section_2_items)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": section_1_helpers + section_2_helpers,
    }
    write_json(helper_request_json, helper_request)

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    helper_output = read_json(helper_output_json, default={}) or {}

    helper_map_local: dict[str, Any] = {}
    for item in helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if isinstance(entry_id, str):
            helper_map_local[entry_id.lower()] = item

    def enrich(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> None:
        by_key = {entry["entry_key"].replace(":entry:", "_").lower(): entry for entry in entries}
        ref_by_entry: dict[str, list[dict[str, Any]]] = {}
        for ref in refs:
            ref_by_entry.setdefault(ref["entry_key"].replace(":entry:", "_").lower(), []).append(ref)
        for entry_id, entry in by_key.items():
            helper = helper_map_local.get(entry_id)
            if helper:
                entry["raw_json"]["helper"] = {
                    "status": helper.get("status"),
                    "candidate_role": (helper.get("best_candidate") or {}).get("candidate_role"),
                    "reason_summary": (helper.get("best_candidate") or {}).get("reason_summary"),
                    "top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in (helper.get("candidates") or [])[:3]
                    ],
                }
                best = helper.get("best_candidate") or {}
                if best.get("file"):
                    entry["target_file_best"] = best["file"]
                if entry_id in ref_by_entry:
                    for ref in ref_by_entry[entry_id]:
                        if best.get("file"):
                            ref["target_file"] = best["file"]
                        if isinstance(best.get("probability"), (int, float)):
                            ref["target_file_probability"] = float(best["probability"])
            else:
                entry["raw_json"]["helper"] = {"status": "missing"}

    enrich(section_1_entries, section_1_refs)
    enrich(section_2_entries, section_2_refs)

    sections = [
        {
            "section_key": SECTION_1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_1_HEADING_RAW,
            "heading_norm": SECTION_1_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1279,
            "page_end": 1290,
            "file_start": str(section_1_files[0]),
            "file_end": str(section_2_files[0]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": (
                    "Analytical alphabetical subject index of Thomas Cisterciensis with "
                    "letter dividers and printed-page locators; the OCR headers on files "
                    "652-653 drift in the right-hand page number but the sequential tail "
                    "confirms the 1279-1290 span."
                ),
                "evidence_files": [str(path) for path in section_1_files],
                "helper_request": str(helper_request_json),
                "helper_output": str(helper_output_json),
            },
        },
        {
            "section_key": SECTION_2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_2_HEADING_RAW,
            "heading_norm": SECTION_2_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1290,
            "page_end": 1290,
            "file_start": str(section_2_files[0]),
            "file_end": str(section_2_files[0]),
            "confidence": 0.88,
            "raw_json": {
                "section_kind_reason": (
                    "Supplementary tabella introduced after the closing sentence of the "
                    "first index; the OCR preserves the Joannes Algrinus material as a "
                    "separate analytical table on the same tail page."
                ),
                "evidence_files": [str(section_2_files[0])],
                "helper_request": str(helper_request_json),
                "helper_output": str(helper_output_json),
            },
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": (
            "PL206 tail index with a large analytical alphabetical table for Thomas "
            "Cisterciensis and a shorter supplementary tabella for Joannes Algrinus."
        ),
    }
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": (
            "Recovered the analytical index chunks from OCR files 650-655 and preserved "
            "the closure/intro sentence that divides the Thomas and Joannes tables."
        ),
        "evidence_files": [str(path) for path in section_1_files + section_2_files],
    }
    notes = [
        "OCR page headers around files 652-653 contain visible drift in the right-hand page number; the tail sequence still supports the 1279-1290 span.",
        "The final file 655 contains both the closing sentence for the Thomas table and the opening sentence for the Joannes Algrinus supplement.",
        "Helper output is embedded in each entry's raw_json where the locator was materially useful.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": section_1_nodes,
        "entries": section_1_entries + section_2_entries,
        "refs": section_1_refs + section_2_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", section_1_nodes)
    write_json(intermediate_dir / "entries.json", section_1_entries + section_2_entries)
    write_json(intermediate_dir / "refs.json", section_1_refs + section_2_refs)
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
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PL206 alphabetical payload and validate helper-resolved target files.",
            "completed": [
                "Section boundaries recovered",
                "Helper request generated and executed",
                "Intermediate payload fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact; do not collapse the printed page locator with the OCR file suffix.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL206 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
    )
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

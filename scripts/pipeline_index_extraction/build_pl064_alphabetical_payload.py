#!/usr/bin/env python3
"""Usage: build the PL064 alphabetical-index payload from OCR tail files.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl064_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL064/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL064_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL064_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL064 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL064_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


OCR_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
ROMAN_PAIR_RE = re.compile(r"^[IVXLCDM1]\s*,\s*\d{1,4}$", re.IGNORECASE)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADING_RE = re.compile(
    r"(INDEX GENERALIS|IN OPERA BOETII|ORDO RERUM|TABLE DES MATIERES|TABLE DE LA SECONDE PARTIE)",
    re.IGNORECASE,
)
FOOTER_RE = re.compile(r"Digitized by Google", re.IGNORECASE)
CONTINUATION_RE = re.compile(r"^[a-zà-ÿ(,.;:]")
IBID_RE = re.compile(r"\b(?:ibid\.?|ibid|id\.?)\b", re.IGNORECASE)


@dataclass
class EntryRecord:
    entry_key: str
    section_key: str
    parent_node_key: str | None
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str
    editorial_anchor_file: str | None
    target_file_best: str | None
    confidence: float
    raw_json: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().strip(" ,;:.")


def lower_norm(text: str | None) -> str | None:
    text = norm(text)
    return text.lower() if text is not None else None


def sort_norm(text: str | None) -> str | None:
    text = norm(text)
    return text.lower() if text is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_text_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.search(text):
            break
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = extract_text_lines(path)
        header_blob = " ".join(lines[:3])
        for number in OCR_PAGE_RE.findall(header_blob):
            page = int(number)
            if page >= 10:
                page_map.setdefault(page, str(path))
    return page_map


def is_letter_line(text: str) -> bool:
    return bool(SINGLE_LETTER_RE.fullmatch(text))


def is_section_heading(text: str) -> bool:
    return bool(HEADING_RE.search(text))


def is_major_ordo_heading(text: str) -> bool:
    upper = text.upper()
    return (
        upper.startswith("LIBER ")
        or upper.startswith("CAP.")
        or upper.startswith("CAPUT ")
        or upper.startswith("INTERPRETATIO ")
        or upper.startswith("DE ")
        or upper.startswith("APPENDIX ")
        or upper.startswith("OPERUM ")
    )


def looks_like_new_entry(text: str) -> bool:
    if not text:
        return False
    if is_letter_line(text) or is_section_heading(text):
        return False
    return bool(re.search(r"\d|\bibid\b|\bid\b", text, re.IGNORECASE))


def split_entry_and_refs(text: str) -> tuple[str | None, list[str]]:
    working = norm(text) or ""
    parts = [part.strip(" .") for part in working.split(",")]
    if len(parts) == 1:
        return working, []

    refs: list[str] = []
    i = len(parts) - 1
    while i > 0:
        part = parts[i].strip()
        prev = parts[i - 1].strip()
        if not part:
            i -= 1
            continue
        if IBID_RE.fullmatch(part):
            refs.insert(0, "ibid.")
            i -= 1
            continue
        if re.fullmatch(r"\d{1,4}(?:\s*(?:seq\.?|seqq\.?|et suiv\.?|et seq\.?|à\s*\d{1,4}))?", part, re.IGNORECASE):
            if re.fullmatch(r"[IVXLCDM1]", prev, re.IGNORECASE):
                refs.insert(0, f"{prev}, {part}")
                i -= 2
                continue
            refs.insert(0, part)
            i -= 1
            continue
        if ROMAN_PAIR_RE.fullmatch(part):
            refs.insert(0, part)
            i -= 1
            continue
        if re.fullmatch(r"[IVXLCDM]+", part, re.IGNORECASE):
            refs.insert(0, part)
            i -= 1
            continue
        break

    if not refs:
        return working, []
    lemma = ", ".join(parts[: i + 1]).strip()
    lemma = lemma.rstrip(" ,;:.")
    return lemma or None, refs


def extract_pages_from_ref(ref_raw: str, previous_page: int | None) -> tuple[int | None, str | None]:
    ref_raw = norm(ref_raw) or ""
    if IBID_RE.fullmatch(ref_raw):
        return previous_page, ref_raw
    match = OCR_PAGE_RE.search(ref_raw)
    if match:
        return int(match.group(1)), ref_raw
    return previous_page, ref_raw


def derive_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    candidates = []
    if lemma_raw:
        candidates.append(lemma_raw)
        first_clause = re.split(r"\s*[;,]\s*", lemma_raw, maxsplit=1)[0].strip()
        if first_clause:
            candidates.append(first_clause)
    candidates.append(entry_raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = norm(item) or ""
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[:4]


def parse_files(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    page_map = build_page_map(files)

    alpha_files = [path for path in files if 804 <= file_num(path) <= 815]
    ordo_files = [path for path in files if 816 <= file_num(path) <= 818]

    sections = [
        {
            "section_key": "PL064:alpha:alphabetical_general:001",
            "volume_id": "PL064",
            "work_key": "boethii_opera",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX GENERALIS IN OPERA BOETII.",
            "heading_norm": "index generalis in opera boetii",
            "heading_letter": None,
            "page_start": 1603,
            "page_end": 1622,
            "file_start": str(alpha_files[0]),
            "file_end": str(alpha_files[-1]),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Alphabetical general index of Boethius' works.",
                "evidence_files": [str(alpha_files[0]), str(alpha_files[-1])],
            },
        },
        {
            "section_key": "PL064:alpha:ordo_rerum:002",
            "volume_id": "PL064",
            "work_key": "boethii_opera",
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1623,
            "page_end": 1628,
            "file_start": str(ordo_files[0]),
            "file_end": str(ordo_files[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Editorial closure / contents block after the alphabetical index.",
                "evidence_files": [str(ordo_files[0]), str(ordo_files[-1])],
            },
        },
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[EntryRecord] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    current_letter: str | None = None
    current_section = sections[0]
    buffer = ""
    buffer_file: str | None = None
    entry_order = 0
    node_order = 0
    last_explicit_page: int | None = None

    def flush() -> None:
        nonlocal buffer, buffer_file, entry_order, last_explicit_page
        text = norm(buffer)
        buffer = ""
        buffer_file = None
        if not text:
            return
        if is_section_heading(text) and not looks_like_new_entry(text):
            return
        if is_letter_line(text):
            return

        lemma_raw, ref_parts = split_entry_and_refs(text)
        entry_kind = "lemma"
        if lemma_raw is None and ref_parts:
            entry_kind = "cross_reference"
        elif lemma_raw is not None and IBID_RE.search(text) and not ref_parts:
            entry_kind = "cross_reference"
        elif current_section["section_kind"] == "ordo_rerum" and (
            is_major_ordo_heading(text) or text.startswith("TABLE ") or text.startswith("INDEX ")
        ):
            entry_kind = "heading_group"

        entry_order += 1
        entry_key = f"PL064:entry:{entry_order:04d}"
        page_hint: int | None = None
        target_file_best: str | None = buffer_file
        entry_refs: list[dict[str, Any]] = []

        if ref_parts:
            ref_order = 1
            for ref_raw in ref_parts:
                page_hint, raw = extract_pages_from_ref(ref_raw, last_explicit_page)
                if page_hint is not None:
                    last_explicit_page = page_hint
                if page_hint is not None and page_hint in page_map:
                    target_file_best = page_map[page_hint]
                ref_obj = {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page_hint,
                    "page_ref_col": "I" if re.fullmatch(r"[IVXLCDM1]\s*,\s*\d{1,4}", raw, re.IGNORECASE) else None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": page_map.get(page_hint) if page_hint is not None else None,
                    "target_file_probability": 0.9 if page_hint is not None and page_hint in page_map else None,
                    "section_start_file": current_section["file_start"],
                    "editorial_anchor_file": buffer_file,
                    "confidence": 0.86 if page_hint is not None else 0.72,
                    "raw_json": {},
                }
                refs.append(ref_obj)
                entry_refs.append(ref_obj)
                ref_order += 1
        elif IBID_RE.search(text) and last_explicit_page is not None:
            page_hint = last_explicit_page
            target_file_best = page_map.get(page_hint, target_file_best)
            ref_obj = {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": page_hint,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": page_map.get(page_hint),
                "target_file_probability": 0.74 if page_map.get(page_hint) else None,
                "section_start_file": current_section["file_start"],
                "editorial_anchor_file": buffer_file,
                "confidence": 0.7,
                "raw_json": {"ibid_resolved_from_previous_page": True},
            }
            refs.append(ref_obj)
            entry_refs.append(ref_obj)

        inferred_page = entry_refs[0]["page_ref_int"] if entry_refs else None
        lemma_display = lemma_raw
        rec = EntryRecord(
            entry_key=entry_key,
            section_key=current_section["section_key"],
            parent_node_key=None,
            entry_order=entry_order,
            entry_kind=entry_kind,
            lemma_raw=lemma_raw,
            lemma_display=lemma_display,
            lemma_norm=lower_norm(lemma_raw),
            lemma_sort=sort_norm(lemma_raw),
            entry_raw=text,
            context_raw=text,
            heading_letter=current_letter,
            inferred_printed_page=inferred_page,
            section_start_file=current_section["file_start"],
            editorial_anchor_file=buffer_file,
            target_file_best=target_file_best,
            confidence=0.9 if entry_refs else 0.72,
            raw_json={
                "source_file": buffer_file,
                "section_kind": current_section["section_kind"],
            },
        )
        if entry_kind == "cross_reference" and not entry_refs:
            rec.raw_json["ibid"] = bool(IBID_RE.search(text))
        entries.append(rec)
        if ref_parts:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or text,
                    "query_names": derive_query_names(lemma_raw or text, text),
                    "page_hints": [str(r["page_ref_int"]) for r in entry_refs if r["page_ref_int"] is not None],
                    "page_hint_ints": [r["page_ref_int"] for r in entry_refs if r["page_ref_int"] is not None],
                    "context_raw": text,
                }
            )

    for path in alpha_files + ordo_files:
        lines = extract_text_lines(path)
        current_section = sections[0] if path in alpha_files else sections[1]
        for line in lines:
            if is_section_heading(line):
                flush()
                continue
            if current_section["section_kind"] == "alphabetical_general" and is_letter_line(line):
                flush()
                current_letter = line
                node_order += 1
                nodes.append(
                    {
                        "node_key": f"PL064:node:{node_order:03d}",
                        "section_key": current_section["section_key"],
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": line,
                        "label_sort": line,
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source_file": str(path), "section_kind": current_section["section_kind"]},
                    }
                )
                continue
            if buffer and (CONTINUATION_RE.match(line) or (not looks_like_new_entry(line) and not is_major_ordo_heading(line))):
                buffer = f"{buffer} {line}"
                continue
            if buffer:
                flush()
            buffer = line
            buffer_file = str(path)
        flush()

    helper_request = {
        "volume_id": "PL064",
        "source_root": str(alpha_files[0].parent),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries[:8],
    }
    return sections, nodes, [rec.__dict__ for rec in entries], refs, helper_request


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
    proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL064 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    sections, nodes, entries, refs, helper_request = parse_files(files)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    by_entry = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}
    for entry in entries:
        helper = by_entry.get(entry["entry_key"])
        if helper:
            entry.setdefault("raw_json", {})["helper"] = {
                "status": helper.get("status"),
                "candidate_role": helper.get("candidate_role"),
                "reason_summary": helper.get("reason_summary"),
            }

    volume = {
        "volume_id": "PL064",
        "collection": "PL",
        "source_root": str(args.source_root),
        "volume_label": "Patrologia Latina 64",
        "notes": [
            "Alphabetical index and contents block recovered from the OCR tail.",
            "OCR literals were preserved; ambiguous ibid. references were resolved conservatively.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical index block and the final ORDO RERUM block conservatively from the OCR tail. Several entries are line-wrapped or OCR-corrupted, so the payload preserves the visible text rather than normalizing aggressively.",
        "evidence_files": [sections[0]["file_start"], sections[0]["file_end"], sections[1]["file_start"], sections[1]["file_end"]],
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
        "notes": [
            "Section 1 is the main alphabetical index for Boethius' works.",
            "Section 2 is the concluding ORDO RERUM block.",
            "Helper output is only advisory and was not allowed to override OCR reading.",
        ],
    }

    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "todo.json", {
        "volume_id": "PL064",
        "updated_at": now_iso(),
        "current_focus": "Finalize PL064 alphabetical payload after helper calibration",
        "completed": ["OCR tail section boundaries identified", "helper request generated and resolved"],
        "pending": ["validate payload shape", "inspect sample refs if needed"],
        "blocked": [],
        "notes": ["Keep OCR literals intact.", "Do not collapse distinct numbering systems."],
    })
    write_json(args.intermediate_dir / "manifest.json", {
        "volume_id": "PL064",
        "updated_at": now_iso(),
        "helper_entry_count": len(helper_request["entries"]),
    })
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

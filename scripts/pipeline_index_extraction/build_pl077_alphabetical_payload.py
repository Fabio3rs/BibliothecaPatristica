#!/usr/bin/env python3
"""Usage: build the PL077 alphabetical-index payload from the OCR volume.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl077_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL077/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL077_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL077_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL077 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL077_alphabetical_indices.json
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


VOLUME_ID = "PL077"
COLLECTION = "PL"

ANALYTIC_HEADING_RE = re.compile(r"^INDEX RERUM ET SENTENTIARUM\.?$", re.IGNORECASE)
ORDO_HEADING_RE = re.compile(r"^ORDO RERUM(?: QU[ÆAE] IN HOC TOMO CONTINENTUR\.)?\.?$", re.IGNORECASE)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
META_SKIP_RE = re.compile(r"^(?:Digitized by Google|.*UNIV\.?\s+OF\s+MICHIGAN.*|.*OF MICHIGAN.*)$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")


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
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


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


def build_page_maps(files: list[Path]) -> tuple[dict[int, str], dict[str, int]]:
    page_to_file: dict[int, str] = {}
    file_to_page: dict[str, int] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = norm(parsed.get("header_text")) or ""
        candidates: list[int] = []
        for match in re.finditer(r"\b\d{1,4}\b", header):
            value = int(match.group(0))
            if value < 3000:
                candidates.append(value)
        if not candidates:
            continue
        file_to_page[str(path)] = min(candidates)
        for value in candidates:
            page_to_file.setdefault(value, str(path))
    return page_to_file, file_to_page


def split_entry_text(text: str) -> list[str]:
    text = norm(text) or ""
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.;])\s+(?=[A-ZÆŒ])", text) if part.strip()]
    return parts or [text]


def infer_lemma(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^[A-ZÆŒ]\s+", "", text)
    text = re.sub(r"^\d+\s+", "", text)
    if text.lower().startswith(("vide ", "vid. ", "voir ", "v. ")):
        return None
    if "," in text:
        text = text.split(",", 1)[0]
    if "." in text and not text.startswith("S. "):
        left = text.split(".", 1)[0]
        if len(left) > 2:
            text = left
    return text.strip(" .;:") or None


def entry_kind_for(section_kind: str, entry_raw: str) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    if re.match(r"^(?:Vide|Vid\.|Voir|V\.)\b", entry_raw, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def is_metadata_line(text: str) -> bool:
    return bool(META_SKIP_RE.fullmatch(text))


def parse_refs(entry_raw: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last = last_page
    ref_order = 1
    for match in PAGE_REF_RE.finditer(entry_raw):
        page_ref_int = int(match.group(1))
        range_end = match.group(2)
        ref_raw = match.group(0).strip()
        if range_end is not None:
            ref_kind = "editorial_range"
            range_start_raw = str(page_ref_int)
            range_end_raw = str(int(range_end))
        else:
            ref_kind = "editorial_page"
            range_start_raw = None
            range_end_raw = None
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": ref_kind,
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
            }
        )
        current_last = page_ref_int
        ref_order += 1
    if not refs and IBID_RE.search(entry_raw) and current_last is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": current_last,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs, current_last


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl077_aaron_rationale_015",
                "lemma_raw": "Aaron rationale in pectore vitlis ligatum gerens quid significet",
                "query_names": [
                    "Aaron rationale in pectore",
                    "Aaron coram Domino judicium filiorum Israel",
                    "Aaron",
                ],
                "page_hints": ["15", "14"],
                "page_hint_ints": [15, 14],
                "context_raw": "Aaron rationale in pectore vitlis ligatum gerens quid significet, 15. Aaron coram Domino judicium filiorum Israel in pectore gestare quid sit, 14.",
            },
            {
                "entry_id": "pl077_abbas_modo_monasterio_1091",
                "lemma_raw": "Abbas quomodo in monasterio se gerat",
                "query_names": [
                    "Abbas quomodo in monasterio se gerat",
                    "Abbas lapsus ab ordine sacro",
                    "Abbati verato et oppresso",
                ],
                "page_hints": ["1091", "1098", "729"],
                "page_hint_ints": [1091, 1098, 729],
                "context_raw": "Abbas quomodo in monasterio se gerat, 1091, 1098 et seqq. Abbas lapsus ab ordine sacro irrevocabiliter deponitur, sed post poenitentiam inter monachos priorem locum obtinet, 729, 730.",
            },
            {
                "entry_id": "pl077_defensorum_schola_1127",
                "lemma_raw": "Defensorum schola erat",
                "query_names": [
                    "Defensorum schola erat",
                    "Defensoribus dignitas in presbyterio concessa",
                    "Defensoris instituendi formula",
                ],
                "page_hints": ["1127", "756", "941"],
                "page_hint_ints": [1127, 756, 941],
                "context_raw": "Defensorum schola erat, 1127. Defensoribus dignitas in presbyterio concessa, 593. Defensoris instituendi formula, 756, 941, 1120.",
            },
        ],
    }


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


def make_section_payload(
    *,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    file_start: Path,
    file_end: Path,
    page_start: int | None,
    page_end: int | None,
    section_reason: str,
    helper_output: dict[str, Any] | None,
    volume_id: str,
    source_root: Path,
) -> dict[str, Any]:
    payload = {
        "section_key": section_key,
        "volume_id": volume_id,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": sort_norm(heading_raw.rstrip(".")),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": str(file_start),
        "file_end": str(file_end),
        "confidence": 0.95 if section_kind == "ordo_rerum" else 0.92,
        "raw_json": {
            "section_kind_reason": section_reason,
            "source_root": str(source_root),
            "source_files": [],
        },
    }
    if helper_output is not None:
        payload["raw_json"]["helper_locator_status"] = helper_output.get("status")
    return payload


def flush_buffer(
    *,
    buffer_lines: list[str],
    file_path: Path,
    section_key: str,
    section_kind: str,
    current_letter: str | None,
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    entry_order: int,
    page_to_file: dict[int, str],
    file_to_page: dict[str, int],
    section_start_file: str,
    last_page: int | None,
    seen_segments: set[str],
    helper_output: dict[str, Any] | None,
) -> tuple[int, int | None]:
    if not buffer_lines:
        return entry_order, last_page

    blob = norm(" ".join(buffer_lines)) or ""
    buffer_lines.clear()
    if not blob:
        return entry_order, last_page

    for segment in split_entry_text(blob):
        cleaned = norm(segment) or ""
        if not cleaned:
            continue
        if cleaned.upper().startswith(("INDEX RERUM ET SENTENTIARUM", "ORDO RERUM")):
            continue
        if is_metadata_line(cleaned):
            continue
        if cleaned in seen_segments:
            continue
        if not PAGE_REF_RE.search(cleaned) and not IBID_RE.search(cleaned) and len(cleaned) < 8:
            continue
        seen_segments.add(cleaned)

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        refs_for_entry, last_page = parse_refs(cleaned, last_page)
        entry_kind = entry_kind_for(section_kind, cleaned)
        lemma_raw = infer_lemma(cleaned)
        target_file = str(file_path)
        entry_payload = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_raw.lower() if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": cleaned,
            "context_raw": cleaned,
            "heading_letter": current_letter,
            "inferred_printed_page": None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": str(file_path),
            "target_file_best": target_file,
            "confidence": 0.82 if section_kind == "ordo_rerum" else 0.78,
            "raw_json": {
                "source_file": str(file_path),
                "section_kind": section_kind,
            },
        }
        if helper_output is not None:
            entry_payload["raw_json"]["helper_locator_status"] = helper_output.get("status")
        entries.append(entry_payload)

        for ref in refs_for_entry:
            ref_copy = dict(ref)
            ref_copy.update(
                {
                    "entry_key": entry_key,
                    "target_file": page_to_file.get(ref["page_ref_int"]),
                    "target_file_probability": 0.98 if page_to_file.get(ref["page_ref_int"]) else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(file_path),
                    "confidence": 0.76 if page_to_file.get(ref["page_ref_int"]) else 0.52,
                    "raw_json": {
                        "source_file": str(file_path),
                        "section_kind": section_kind,
                    },
                }
            )
            if helper_output is not None:
                ref_copy["raw_json"]["helper_locator_status"] = helper_output.get("status")
            refs.append(ref_copy)

    return entry_order, last_page


def collect_sections(
    files: list[Path],
    page_to_file: dict[int, str],
    file_to_page: dict[str, int],
    helper_output: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    analytic_section_key = f"{VOLUME_ID}:alpha:analytic_subject:001"
    ordo_section_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"
    section_payloads = {
        "analytic_subject": make_section_payload(
            section_key=analytic_section_key,
            section_order=1,
            section_kind="analytic_subject",
            heading_raw="INDEX RERUM ET SENTENTIARUM.",
            file_start=next(path for path in files if file_num(path) == 741),
            file_end=next(path for path in files if file_num(path) == 803),
            page_start=file_to_page.get(str(next(path for path in files if file_num(path) == 741))),
            page_end=file_to_page.get(str(next(path for path in files if file_num(path) == 803))),
            section_reason="Alphabetical subject index with letter-group nodes and dense remissive entries.",
            helper_output=helper_output,
            volume_id=VOLUME_ID,
            source_root=files[0].parent,
        ),
        "ordo_rerum": make_section_payload(
            section_key=ordo_section_key,
            section_order=2,
            section_kind="ordo_rerum",
            heading_raw="ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            file_start=next(path for path in files if file_num(path) == 804),
            file_end=next(path for path in files if file_num(path) == 821),
            page_start=file_to_page.get(str(next(path for path in files if file_num(path) == 804))),
            page_end=file_to_page.get(str(next(path for path in files if file_num(path) == 821))),
            section_reason="Closing table of contents for the volume.",
            helper_output=helper_output,
            volume_id=VOLUME_ID,
            source_root=files[0].parent,
        ),
    }
    analytic_files = [str(path) for path in files if 741 <= file_num(path) <= 803]
    ordo_files = [str(path) for path in files if 804 <= file_num(path) <= 821]
    section_payloads["analytic_subject"]["raw_json"]["source_files"] = analytic_files
    section_payloads["ordo_rerum"]["raw_json"]["source_files"] = ordo_files
    if helper_output is not None:
        helper_status_counts: dict[str, int] = {}
        for item in helper_output.get("entries") or []:
            status = item.get("status")
            if isinstance(status, str):
                helper_status_counts[status] = helper_status_counts.get(status, 0) + 1
        section_payloads["analytic_subject"]["raw_json"]["helper_locator_summary"] = helper_status_counts
        section_payloads["ordo_rerum"]["raw_json"]["helper_locator_summary"] = helper_status_counts

    active_section = None
    started = False
    current_letter: str | None = None
    last_page: int | None = None
    entry_order = 0
    seen_segments: set[str] = set()

    section_start_file = str(files[0])

    for path in files:
        num = file_num(path)
        if num < 741 or num > 821:
            continue
        lines = extract_lines(path)
        buffer_lines: list[str] = []
        page_path = str(path)

        for line in lines:
            text = norm(line) or ""
            if not text:
                continue

            if active_section == "ordo_rerum" and ORDO_HEADING_RE.fullmatch(text):
                continue

            if is_metadata_line(text):
                continue

            if not started:
                if ANALYTIC_HEADING_RE.fullmatch(text):
                    started = True
                    active_section = "analytic_subject"
                    section_start_file = page_path
                continue

            if active_section == "analytic_subject" and ORDO_HEADING_RE.fullmatch(text):
                entry_order, last_page = flush_buffer(
                    buffer_lines=buffer_lines,
                    file_path=path,
                    section_key=analytic_section_key,
                    section_kind="analytic_subject",
                    current_letter=current_letter,
                    entries=entries,
                    refs=refs,
                    entry_order=entry_order,
                    page_to_file=page_to_file,
                    file_to_page=file_to_page,
                    section_start_file=section_start_file,
                    last_page=last_page,
                    seen_segments=seen_segments,
                    helper_output=helper_output,
                )
                active_section = "ordo_rerum"
                section_start_file = page_path
                current_letter = None
                continue

            if active_section is None:
                continue

            if LETTER_RE.fullmatch(text):
                node_key = f"{VOLUME_ID}:node:{len(nodes)+1:06d}"
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": analytic_section_key if active_section == "analytic_subject" else ordo_section_key,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": text,
                        "label_norm": text.lower(),
                        "label_sort": text.lower(),
                        "node_level": 1,
                        "confidence": 0.97,
                        "raw_json": {"source_file": page_path, "section_kind": active_section},
                    }
                )
                entry_order, last_page = flush_buffer(
                    buffer_lines=buffer_lines,
                    file_path=path,
                    section_key=analytic_section_key if active_section == "analytic_subject" else ordo_section_key,
                    section_kind=active_section,
                    current_letter=current_letter,
                    entries=entries,
                    refs=refs,
                    entry_order=entry_order,
                    page_to_file=page_to_file,
                    file_to_page=file_to_page,
                    section_start_file=section_start_file,
                    last_page=last_page,
                    seen_segments=seen_segments,
                    helper_output=helper_output,
                )
                current_letter = text
                continue

            buffer_lines.append(text)

        if active_section is not None:
            entry_order, last_page = flush_buffer(
                buffer_lines=buffer_lines,
                file_path=path,
                section_key=analytic_section_key if active_section == "analytic_subject" else ordo_section_key,
                section_kind=active_section,
                current_letter=current_letter,
                entries=entries,
                refs=refs,
                entry_order=entry_order,
                page_to_file=page_to_file,
                file_to_page=file_to_page,
                section_start_file=section_start_file,
                last_page=last_page,
                seen_segments=seen_segments,
                helper_output=helper_output,
            )

    sections.extend([section_payloads["analytic_subject"], section_payloads["ordo_rerum"]])
    return sections, nodes, entries, refs


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL077 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = discover_text_files(args.source_root)
    page_to_file, file_to_page = build_page_maps(files)

    helper_request = build_helper_request(args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    sections, nodes, entries, refs = collect_sections(files, page_to_file, file_to_page, helper_output)

    scripture_refs: list[dict[str, Any]] = []
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": "Patrologia Latina, volume 77",
        "notes": "Alphabetical subject index followed by the closing Ordo Rerum from the OCR volume tail.",
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical subject index and the closing Ordo Rerum from the OCR volume using a conservative line-chunk parser; OCR noise, split columns, and page drift remain possible in individual entries.",
        "evidence_files": [
            str(next(path for path in files if file_num(path) == num))
            for num in [741, 748, 760, 804, 820]
        ],
    }
    notes = [
        "Section 1 is the main Index Rerum et Sententiarum.",
        "Section 2 is the closing Ordo Rerum.",
        "Helper locator run on a small set of ambiguous target-resolution samples; the final payload uses direct OCR page-header resolution for material refs when possible.",
    ]

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(args.source_root),
            "helper_request_json": str(args.helper_request_json),
            "helper_output_json": str(args.helper_output_json),
            "output_file": str(args.output_file),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate PL077 alphabetical extraction and fix any lingering OCR-page target ambiguities.",
            "completed": [
                "page map built from OCR headers",
                "helper request written and executed",
                "alphabetical index and closing Ordo Rerum parsed into intermediate fragments",
            ],
            "pending": [
                "validate the final JSON payload structure",
                "inspect any unresolved refs if import validation fails",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not conflate OCR file suffixes with editorial page numbers.",
            ],
        },
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL089 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl089_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL089/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL089_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL089_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL089 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL089_alphabetical_indices.json
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


VOLUME_ID = "PL089"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 89"

ORDO_RE = re.compile(r"ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)?", re.IGNORECASE)
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(
    r"(?<!\d)(?P<start>\d{1,4})(?:\s*(?:[-–—]|à)\s*(?P<end>\d{1,4}))?(?=[\s\.,;:\)]|$)",
    re.IGNORECASE,
)
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
ALL_CAPS_RE = re.compile(r"^[A-ZÆŒ0-9][A-ZÆŒ0-9 .,'’()\-/:;]*\.?$")
SHORT_HEADING_RE = re.compile(r"^(?:S\.\s*)?[A-ZÆŒ][A-ZÆŒ0-9 .,'’()\-/:;]{0,60}\.?$")
PERSON_HEADING_RE = re.compile(r"^(?:S\.\s*)?[A-ZÆŒ][A-ZÆŒ0-9 .,'’()\-/:;]*\.$")
LOWER_CONT_RE = re.compile(r"^[a-zà-ÿ]|^[,.;:)\]]")
TOC_START_RE = re.compile(
    r"^(?:SERGIUS I\.|JOANNES VI\.|JOANNES VII\.|S\. ALDHELMUS\.|TRACTATUS de Laudibus virginitatis\.|LIBER de Septenario et de Metris\.|POEMATA\.|CONSTANTINUS PAPA\.|CEOLFRIDUS\.|FELIX\.|BENEDICTUS CRISPUS\.|PŒNITENTIALIS LIBER TERTIUS\.|DIALOGUS de institutione catholica\.|CANONES de remediis peccatorum\.|GREGORIUS II\.|S\. VILLIBRORDUS\.|GREGORIUS III\.|S\. BONIFACIUS\.|S\. ZACHARIAS\.|STEPHANUS II\.|S\. PIRMINIUS\.|ALANUS\.|STEPHANUS III\.|S\. STURMIUS\.|S\. AMBROSIUS AUTPERTUS\.)",
    re.IGNORECASE,
)
TITLE_PREFIX_RE = re.compile(
    r"^(?:Notitia historica|EPISTOLA|Epist\.|CAP\.|CAPUT|LIBER|VITA|TRACTATUS|DIALOGUS|CANONES|EXCERPTA|EXCERPTUM|S\.|REGULA|Sermo|Homilia|Poema|Diploma|Concilium|Mansi|Præfatio|Praefatio|Appendix|Respondet|Gratias|Privilegium|Revelatio|Responsa|Confirmatio|Alia)",
    re.IGNORECASE,
)


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
    value = value.strip(" \t\r\n")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def is_ordo_file(path: Path) -> bool:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    return bool(ORDO_RE.search(parsed["all_text"]))


def extract_header_numbers(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = parsed["header_text"] or ""
    return [int(match.group(1)) for match in HEADER_NUM_RE.finditer(header) if int(match.group(1)) >= 10]


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for number in extract_header_numbers(path)[:3]:
            page_map.setdefault(number, str(path))
    return page_map


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text or FOOTER_RE.fullmatch(text):
            continue
        if re.fullmatch(r"[A-ZÆŒ]", text):
            continue
        if text.isdigit():
            continue
        lines.append(text)
    return lines


def split_segments(line: str) -> list[str]:
    tokens = (line or "").split()
    if not tokens:
        return []
    segments: list[str] = []
    current: list[str] = [tokens[0]]

    def is_page_token(token: str) -> bool:
        return bool(PAGE_REF_RE.fullmatch(token.rstrip(",;:.)")))

    def is_ibid_token(token: str) -> bool:
        return bool(IBID_RE.fullmatch(token.rstrip(",;:.)")))

    for token in tokens[1:]:
        prev = current[-1]
        starts_new = (is_page_token(prev) or is_ibid_token(prev)) and (token[:1].isupper() or token.startswith("S."))
        if starts_new:
            segments.append(" ".join(current).strip())
            current = [token]
        else:
            current.append(token)
    segments.append(" ".join(current).strip())
    return [part for part in segments if part]


def is_heading_line(line: str) -> bool:
    if not line:
        return False
    if ORDO_RE.search(line):
        return True
    if PERSON_HEADING_RE.fullmatch(line):
        return True
    if SHORT_HEADING_RE.fullmatch(line) and not PAGE_REF_RE.search(line):
        return True
    if ALL_CAPS_RE.fullmatch(line) and not PAGE_REF_RE.search(line) and len(line) <= 70:
        return True
    return False


def split_page_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last = last_page
    ref_order = 1
    text = norm(text) or ""
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group("start"))
        end_raw = match.group("end")
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "entry_key": None,
                "ref_order": ref_order,
                "ref_kind": "editorial_range" if end_raw is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end_raw is not None else None,
                "range_end_raw": str(int(end_raw)) if end_raw is not None else None,
            }
        )
        current_last = start
        ref_order += 1
    if not refs and IBID_RE.search(text) and current_last is not None:
        refs.append(
            {
                "entry_key": None,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "Ibid.",
                "page_ref_raw": "Ibid.",
                "page_ref_int": current_last,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs, current_last


def lemma_from_entry(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^[0-9]+\s+", "", text)
    text = re.sub(r"\s+\d{1,4}(?:\s*(?:[-–—]|à)\s*\d{1,4})?\s*$", "", text)
    text = re.sub(r"\s+Ibid\.?$", "", text, flags=re.IGNORECASE)
    text = text.strip(" .;:")
    if " — " in text:
        text = text.split(" — ", 1)[0].strip()
    if text.endswith(".") and len(text) <= 70 and not PAGE_REF_RE.search(text):
        return text
    if PAGE_REF_RE.search(text):
        text = PAGE_REF_RE.split(text, maxsplit=1)[0].strip()
    return text or None


def looks_like_continuation(current_buffer: str, line: str) -> bool:
    if not current_buffer:
        return False
    if line[:1].isdigit():
        return True
    if LOWER_CONT_RE.match(line):
        return True
    if current_buffer.endswith("-") or current_buffer.endswith("—"):
        return True
    return False


def parse_entries(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:001"
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    entry_key_for_current_node: str | None = None
    current_buffer: str | None = None
    current_kind = "heading_group"
    last_page: int | None = None
    current_file: str | None = None
    started_toc = False

    def flush_buffer() -> None:
        nonlocal current_buffer, current_kind, entry_order, entry_key_for_current_node, node_order, last_page, current_file
        if not current_buffer:
            return
        entry_raw = norm(current_buffer) or ""
        if not entry_raw:
            current_buffer = None
            current_kind = "heading_group"
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entry_refs, updated_last = split_page_refs(entry_raw, last_page)
        inferred_page = entry_refs[0]["page_ref_int"] if entry_refs else None
        lemma_raw = lemma_from_entry(entry_raw)
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else None
        confidence = 0.93 if entry_refs else 0.87
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": entry_key_for_current_node,
                "entry_order": entry_order,
                "entry_kind": current_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": inferred_page,
                "section_start_file": str(files[0]) if files else None,
                "editorial_anchor_file": current_file,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "entry_classification_reason": "ordo_rerum content line" if entry_refs else "ordo_rerum standalone heading",
                    "page_hints": [ref["page_ref_int"] for ref in entry_refs if ref.get("page_ref_int") is not None],
                    "entry_refs_preview": [ref["ref_raw"] for ref in entry_refs],
                    "source_file": current_file,
                },
            }
        )
        for ref in entry_refs:
            ref = dict(ref)
            ref["entry_key"] = entry_key
            ref["section_start_file"] = str(files[0]) if files else None
            ref["editorial_anchor_file"] = current_file
            ref["target_file"] = target_file_best
            ref["target_file_probability"] = 0.93 if target_file_best else None
            ref["confidence"] = 0.92 if target_file_best else 0.74
            ref["raw_json"] = {
                "source": "toc_parse",
                "inferred_from_last_page": last_page if ref["ref_raw"].lower().startswith("ibid") else None,
            }
            refs.append(ref)
        if entry_refs:
            last_page = updated_last
        current_buffer = None
        current_kind = "heading_group"

    for path in files:
        current_file = str(path)
        in_ordo = False
        finished_ordo = False
        for line in clean_lines(path):
            if finished_ordo:
                break
            if re.search(r"FINIS TOMI", line, re.IGNORECASE):
                flush_buffer()
                finished_ordo = True
                continue
            if re.match(r"^Parisiis\.\s*—\s*Ex Typis", line, re.IGNORECASE):
                flush_buffer()
                finished_ordo = True
                continue
            if ORDO_RE.search(line) and line.upper().startswith("ORDO RERUM"):
                in_ordo = True
                flush_buffer()
                continue
            if not in_ordo:
                continue
            if line.startswith("Digitized by Google"):
                continue
            for segment in split_segments(line):
                if not started_toc:
                    if not TOC_START_RE.match(segment):
                        continue
                    started_toc = True
                if current_buffer is not None and looks_like_continuation(current_buffer, segment):
                    current_buffer = f"{current_buffer} {segment}"
                    if PAGE_REF_RE.search(segment) or IBID_RE.search(segment):
                        flush_buffer()
                    continue
                if current_buffer is not None:
                    buffer_text = current_buffer.strip()
                    if is_heading_line(buffer_text) and not PAGE_REF_RE.search(buffer_text) and not IBID_RE.search(buffer_text):
                        flush_buffer()
                    else:
                        if PAGE_REF_RE.search(buffer_text) or IBID_RE.search(buffer_text):
                            flush_buffer()
                        elif not is_heading_line(segment):
                            flush_buffer()
                if is_heading_line(segment) and not PAGE_REF_RE.search(segment):
                    current_buffer = segment
                    current_kind = "heading_group"
                    continue
                current_buffer = segment
                current_kind = "heading_group"
                if PAGE_REF_RE.search(segment) or IBID_RE.search(segment):
                    flush_buffer()
        flush_buffer()

    # Remove duplicate heading-only ORDO lines if they were emitted multiple times.
    deduped_entries: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()
    for entry in entries:
        key = (entry["lemma_raw"], entry["entry_raw"])
        if key in seen:
            continue
        seen.add(key)
        deduped_entries.append(entry)
    return deduped_entries, refs, nodes


def resolve_helper_request(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
    return read_json(helper_output_json, default={}) or {}


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        page_hints = [str(page) for page in entry["raw_json"].get("page_hints") or []]
        if not page_hints and entry.get("inferred_printed_page") is not None:
            page_hints = [str(entry["inferred_printed_page"])]
        query_names = [entry["lemma_raw"] or entry["entry_raw"], entry["entry_raw"]]
        helper_entries.append(
            {
                "entry_id": f"pl089_{idx:04d}",
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": [name for name in query_names if name],
                "page_hints": page_hints,
                "page_hint_ints": [int(page) for page in page_hints if str(page).isdigit()],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in helper_output.get("entries", []) or []:
        lookup[str(item.get("entry_id"))] = item
    return lookup


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    header_pages = []
    for path in files:
        for page in extract_header_numbers(path):
            header_pages.append(page)
    page_start = min(header_pages) if header_pages else None
    page_end = max(header_pages) if header_pages else None
    return [
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": page_start,
            "page_end": page_end,
            "file_start": str(files[0]) if files else None,
            "file_end": str(files[-1]) if files else None,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Final contents table printed as ORDO RERUM / ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.; not alphabetical, but an editorial closure/contents section allowed by the contract.",
                "source_files": [str(path) for path in files],
                "header_pages": header_pages,
                "heading_variants": ["ORDO RERUM", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."],
            },
        }
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL089 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Extract PL089 ORDO RERUM and resolve target files for TOC entries",
            "completed": [],
            "pending": [
                "parse ORDO RERUM entries",
                "run helper target locator",
                "assemble final payload",
            ],
            "blocked": [],
            "notes": [
                "Printed page headers are non-monotonic across the relevant OCR files.",
                "Use page hints from the TOC lines rather than OCR file suffixes.",
            ],
        },
    )

    files = discover_text_files(args.source_root)
    tail_start = max(0, len(files) - 32)
    tail_files = files[tail_start:]
    relevant_files = [path for path in tail_files if is_ordo_file(path)]
    if not relevant_files:
        raise SystemExit("No ORDO RERUM files detected for PL089.")

    page_map = build_page_map(files)
    entries, refs, nodes = parse_entries(relevant_files, page_map)

    entries_intermediate = args.intermediate_dir / "entries.json"
    refs_intermediate = args.intermediate_dir / "refs.json"
    sections_intermediate = args.intermediate_dir / "sections.json"
    write_json(entries_intermediate, entries)
    write_json(refs_intermediate, refs)
    sections = build_sections(relevant_files)
    write_json(sections_intermediate, sections)

    helper_request = build_helper_request(args.source_root, [entry for entry in entries if entry["inferred_printed_page"] is not None])
    write_json(args.helper_request_json, helper_request)
    helper_output = resolve_helper_request(args.helper_request_json, args.helper_output_json)
    helper_index = helper_lookup(helper_output)

    # Enrich entries and refs with helper evidence when available.
    for entry in entries:
        entry_id = f"pl089_{entry['entry_order']:04d}"
        helper_item = helper_index.get(entry_id)
        if helper_item:
            entry["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": helper_item.get("top_candidates", [])[:3],
            }
            best = (helper_item.get("top_candidates") or [{}])[0]
            entry["target_file_best"] = best.get("file") or entry["target_file_best"]
            if best.get("file"):
                entry["raw_json"]["helper_best_file"] = best["file"]
                entry["raw_json"]["helper_best_probability"] = best.get("probability")
        if not entry.get("target_file_best") and entry.get("inferred_printed_page") is not None:
            fallback_target = page_map.get(entry["inferred_printed_page"])
            if fallback_target:
                entry["target_file_best"] = fallback_target
                entry["raw_json"]["page_map_fallback"] = fallback_target

    for ref in refs:
        entry = next((item for item in entries if item["entry_key"] == ref["entry_key"]), None)
        if entry is None:
            continue
        entry_id = f"pl089_{entry['entry_order']:04d}"
        helper_item = helper_index.get(entry_id)
        if helper_item:
            best = (helper_item.get("top_candidates") or [{}])[0]
            if best.get("file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")
                ref["raw_json"]["helper_best_file"] = best["file"]
                ref["raw_json"]["helper_best_probability"] = best.get("probability")
                ref["confidence"] = max(ref["confidence"], 0.84 if best.get("probability") else ref["confidence"])
            ref["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": helper_item.get("top_candidates", [])[:3],
            }
        if not ref.get("target_file") and ref.get("page_ref_int") is not None:
            fallback_target = page_map.get(ref["page_ref_int"])
            if fallback_target:
                ref["target_file"] = fallback_target
                ref["target_file_probability"] = ref.get("target_file_probability") or 0.75
                ref["raw_json"]["page_map_fallback"] = fallback_target

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Final volume tail is an ORDO RERUM contents section rather than a subject index.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "TOC entries were recoverable from the OCR tail and mapped to page hints and helper candidates.",
            "evidence_files": [str(path) for path in relevant_files],
        },
        "notes": [
            {
                "note_key": "pl089_ordo_rerum",
                "note_raw": "The final volume material is a contents section (ORDO RERUM). OCR file suffix order does not match the printed page header values, so the payload keeps that distinction explicit.",
                "confidence": 0.92,
            }
        ],
    }

    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "manifest.json", {"sections": sections, "entries_count": len(entries), "refs_count": len(refs), "generated_at": payload["generated_at"]})
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(todo_path, {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Complete and validate PL089 payload",
        "completed": [
            "parsed ORDO RERUM entries",
            "ran helper target locator",
            "assembled final payload",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Retained OCR literals and TOC page references as separate concepts.",
        ],
    })


if __name__ == "__main__":
    main()

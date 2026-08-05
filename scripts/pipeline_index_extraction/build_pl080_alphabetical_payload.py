#!/usr/bin/env python3
"""Usage: build the PL080 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl080_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL080/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL080_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL080_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL080 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL080_alphabetical_indices.json
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

VOLUME_ID = "PL080"
COLLECTION = "PL"
TAIL_START = 522
TAIL_END = 553

TOC_MARKERS = (
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR",
    "QUE IN HOC TOMO CONTINENTUR",
    "ADDENDA",
    "FINIS TOMI",
)

PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
BARE_REMISSION_RE = re.compile(r"^(?:Ibid\.?|Id\.?)$", re.IGNORECASE)
SECTION_HEADING_RE = re.compile(
    r"^(?:ORDO RERUM|QUE IN HOC TOMO CONTINENTUR\.?|QUÆ IN HOC TOMO CONTINENTUR\.?|ADDENDA\.?|FINIS TOMI OCTOGESIMI\.?)$",
    re.IGNORECASE,
)
ENTRY_START_RE = re.compile(
    r"^(?:S\.|CAP\.|CAPUT|VITA|EPIST|EPISTOLA|EPISTOLÆ|NOTITIA|DECRETUM|CHARTA|REGULA|SERMO|LIBER|FUNDATIO|PRIVILEGIUM|CONCILIUM|APPENDIX|DISSERTATIO|PREFATIO|PRÆFATIO|PRAEFATIO|INCIPIT|ACTA|BULGARANUS|DAGOBERTUS|ETHELBERTUS|SISBUTHUS|S\. [A-ZÆŒ]|[A-ZÆŒ][A-ZÆŒ ]+\.)",
    re.IGNORECASE,
)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


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


def first_nonempty_line(text: str) -> str | None:
    for raw in (text or "").splitlines():
        value = norm(raw)
        if value:
            return value
    return None


def extract_header_numbers(header_text: str) -> list[int]:
    line = first_nonempty_line(header_text)
    if not line:
        return []
    return [int(match.group(0)) for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", line)]


def extract_min_explicit_page(text: str) -> int | None:
    pages = [int(match.group(1)) for match in PAGE_REF_RE.finditer(text or "") if match.group(2) is None]
    return min(pages) if pages else None


def extract_block_texts(raw_text: str, block_type: str) -> list[str]:
    pattern = re.compile(rf'<bloco tipo="{re.escape(block_type)}"[^>]*>(.*?)</bloco>', re.DOTALL)
    return [match.group(1) for match in pattern.finditer(raw_text or "")]


def extract_first_block_text(raw_text: str, block_type: str) -> str:
    blocks = extract_block_texts(raw_text, block_type)
    return blocks[0] if blocks else ""


def clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        text = norm(raw)
        if not text:
            continue
        if text == "Digitized by Google":
            continue
        if SINGLE_LETTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def merge_wrapped_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            merged.append(buffer.strip())
            buffer = ""

    for line in lines:
        if not buffer:
            buffer = line
            continue

        if BARE_REMISSION_RE.fullmatch(line):
            buffer = f"{buffer} {line}"
            continue

        if buffer.endswith("-"):
            buffer = f"{buffer[:-1]}{line.lstrip()}"
            continue

        last_pages = [match.group(1) for match in PAGE_REF_RE.finditer(buffer) if match.group(2) is None]
        if last_pages and line.startswith(last_pages[-1] + " "):
            remainder = line[len(last_pages[-1]) :].lstrip()
            if remainder:
                buffer = f"{buffer} {remainder}"
                continue

        if (
            line and line[0].islower()
            and not PAGE_REF_RE.search(line)
            and not buffer.endswith(".")
        ):
            buffer = f"{buffer} {line}"
            continue

        if (
            not PAGE_REF_RE.search(line)
            and PAGE_REF_RE.search(buffer) is None
            and buffer.endswith(",")
            and line[0].isupper()
            and len(line) <= 60
        ):
            buffer = f"{buffer} {line}"
            continue

        flush()
        buffer = line

    flush()
    return merged


def infer_lemma(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^[—-]\s*", "", text)
    if "," in text:
        candidate = text.split(",", 1)[0].strip()
    else:
        candidate = text
    candidate = re.sub(r"\s+\d+.*$", "", candidate).strip(" .;:")
    return candidate or None


def parse_page_refs(text: str) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    first_page: int | None = None
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = match.group(2)
        if first_page is None:
            first_page = start
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": match.group(0).strip(),
                "page_ref_raw": match.group(0).strip(),
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(int(end)) if end is not None else None,
            }
        )
    return refs, first_page


def section_heading_for_file(header_text: str, body_lines: list[str]) -> str | None:
    lines = [line for line in (first_nonempty_line(header_text), *body_lines[:6]) if line]
    for line in lines:
        if any(marker in line for marker in TOC_MARKERS):
            return line
    return None


def page_sort_key(page: dict[str, Any]) -> tuple[int, int]:
    min_page = page.get("min_page")
    return (min_page if min_page is not None else 10**9, page["file_num"])


def extract_pages(source_root: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for path in discover_text_files(source_root):
        num = file_num(path)
        if num < TAIL_START or num > TAIL_END:
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        header_text = extract_first_block_text(raw, "cabecalho")
        body_text = "\n".join(extract_block_texts(raw, "texto_principal"))
        body_lines = clean_lines(body_text)
        heading = section_heading_for_file(header_text, body_lines)
        if heading is None:
            continue
        header_nums = extract_header_numbers(header_text)
        min_page = extract_min_explicit_page(body_text)
        pages.append(
            {
                "file_num": num,
                "path": str(path),
                "heading": heading,
                "header_numbers": header_nums,
                "body_lines": body_lines,
                "min_page": min_page,
            }
        )
    pages.sort(key=page_sort_key)
    return pages


def build_page_map_from_source(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in discover_text_files(source_root):
        raw = path.read_text(encoding="utf-8", errors="replace")
        header_text = extract_first_block_text(raw, "cabecalho")
        header_nums = extract_header_numbers(header_text)
        for num in header_nums:
            page_map.setdefault(num, str(path))
    return page_map


def split_sections(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordo_pages: list[dict[str, Any]] = []
    closure_pages: list[dict[str, Any]] = []
    for page in pages:
        ordo_pages.append(page)
        if any(line.startswith("ADDENDA") for line in page["body_lines"]):
            closure_pages.append(page)
    return ordo_pages, closure_pages


def build_entries(
    pages: list[dict[str, Any]],
    *,
    section_key: str,
    section_kind: str,
    section_start_file: str,
    page_map: dict[int, str],
    helper_output: dict[str, Any] | None,
    starting_entry_index: int = 1,
    stop_at_addenda: bool = False,
    start_after_addenda: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_index = starting_entry_index
    last_explicit_page: int | None = None
    last_target_file: str | None = None
    helper_entries = {item.get("entry_id"): item for item in (helper_output or {}).get("entries", [])}

    for page in pages:
        file_path = page["path"]
        lines = merge_wrapped_lines(page["body_lines"])
        in_closure = not start_after_addenda
        for raw_line in lines:
            line = norm(raw_line) or ""
            if not line:
                continue
            if start_after_addenda and not in_closure:
                if line.startswith("ADDENDA"):
                    in_closure = True
                continue
            if SECTION_HEADING_RE.fullmatch(line):
                if line.startswith("ADDENDA") and stop_at_addenda:
                    return entries, refs, entry_index
                continue
            if line == "FINIS TOMI OCTOGESIMI.":
                continue
            if line == "Digitized by Google":
                continue
            if line == "ADDENDA.":
                if stop_at_addenda:
                    return entries, refs, entry_index
                continue

            entry_id = f"{VOLUME_ID.lower()}_{section_kind}_{entry_index:04d}"
            lemma_raw = infer_lemma(line)
            page_refs, first_page = parse_page_refs(line)

            if not page_refs and BARE_REMISSION_RE.fullmatch(line):
                if last_explicit_page is not None:
                    page_refs = [
                        {
                            "ref_kind": "editorial_page",
                            "ref_raw": line,
                            "page_ref_raw": line,
                            "page_ref_int": last_explicit_page,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                        }
                    ]
                    first_page = last_explicit_page

            if not page_refs and first_page is None and last_explicit_page is not None and (
                line.endswith("Ibid.") or line.endswith("Id.")
            ):
                page_refs = [
                    {
                        "ref_kind": "editorial_page",
                        "ref_raw": line,
                        "page_ref_raw": line,
                        "page_ref_int": last_explicit_page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                    }
                ]
                first_page = last_explicit_page

            inferred_printed_page = first_page
            target_file_best = page_map.get(first_page) if first_page is not None else last_target_file
            editorial_anchor_file = file_path

            entry_kind = "heading_group"
            if line in {"ORDO RERUM", "ADDENDA."}:
                continue

            helper_data = helper_entries.get(entry_id)
            helper_best_file = None
            if helper_data and isinstance(helper_data.get("best_candidate"), dict):
                helper_best_file = helper_data["best_candidate"].get("file")
            raw_json: dict[str, Any] = {
                "source": "direct_ocr",
                "section_kind": section_kind,
                "source_file": file_path,
            }
            if helper_data is not None:
                raw_json["helper"] = {
                    "entry_id": helper_data.get("entry_id"),
                    "status": helper_data.get("status"),
                    "candidate_role": helper_data.get("candidate_role"),
                    "reason_summary": helper_data.get("reason_summary"),
                    "best_candidate": helper_data.get("best_candidate"),
                }
                raw_json["source"] = "helper_and_ocr"

            entry_payload = {
                "entry_key": entry_id,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_index,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": None,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "target_file_best": target_file_best or helper_best_file,
                "confidence": 0.9 if page_refs else 0.74,
                "raw_json": raw_json,
            }
            entries.append(entry_payload)

            for ref_order, ref in enumerate(page_refs, start=1):
                target_file = page_map.get(ref["page_ref_int"]) or helper_best_file
                refs.append(
                    {
                        "entry_key": entry_id,
                        "ref_order": ref_order,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": ref["page_ref_col"],
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": target_file,
                        "target_file_probability": 0.98 if target_file else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": editorial_anchor_file,
                        "confidence": 0.94 if target_file else 0.7,
                        "raw_json": {
                            "source": "direct_ocr",
                            "section_kind": section_kind,
                        },
                    }
                )
                if ref["page_ref_int"] is not None:
                    last_explicit_page = ref["page_ref_int"]
                    if target_file is not None:
                        last_target_file = target_file

            if not page_refs and last_target_file is not None and inferred_printed_page is not None:
                entry_payload["target_file_best"] = last_target_file

            if page_refs and page_refs[-1]["page_ref_int"] is not None:
                last_explicit_page = page_refs[-1]["page_ref_int"]
                if refs[-1]["target_file"] is not None:
                    last_target_file = refs[-1]["target_file"]

            entry_index += 1

    return entries, refs, entry_index


def select_helper_entries(entries: list[dict[str, Any]], max_entries: int | None = None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for entry in entries:
        if entry["inferred_printed_page"] is None:
            continue
        selected.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": [
                    entry["lemma_raw"] or entry["entry_raw"],
                    entry["entry_raw"],
                ],
                "page_hints": [str(entry["inferred_printed_page"])],
                "page_hint_ints": [entry["inferred_printed_page"]],
                "context_raw": entry["entry_raw"],
            }
        )
        if max_entries is not None and len(selected) >= max_entries:
            break
    return selected


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
    return read_json(helper_output_json)


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    pages = extract_pages(source_root)
    page_map = build_page_map_from_source(source_root)
    ordo_pages, closure_pages = split_sections(pages)

    # Build helper input from the parsed OCR lines before final assembly.
    temp_section_key = f"{VOLUME_ID}:alpha:ordo_rerum:001"
    temp_entries, _, _ = build_entries(
        ordo_pages,
        section_key=temp_section_key,
        section_kind="ordo_rerum",
        section_start_file=ordo_pages[0]["path"],
        page_map=page_map,
        helper_output=None,
        starting_entry_index=1,
        stop_at_addenda=True,
    )
    temp_closure_entries, _, _ = build_entries(
        closure_pages,
        section_key=f"{VOLUME_ID}:alpha:editorial_closure:002",
        section_kind="editorial_closure",
        section_start_file=closure_pages[0]["path"] if closure_pages else ordo_pages[-1]["path"],
        page_map=page_map,
        helper_output=None,
        starting_entry_index=len(temp_entries) + 1,
    )
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": select_helper_entries(temp_entries + temp_closure_entries, max_entries=None),
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    ordo_section_key = f"{VOLUME_ID}:alpha:ordo_rerum:001"
    closure_section_key = f"{VOLUME_ID}:alpha:editorial_closure:002"

    ordo_entries, ordo_refs, next_index = build_entries(
        ordo_pages,
        section_key=ordo_section_key,
        section_kind="ordo_rerum",
        section_start_file=ordo_pages[0]["path"],
        page_map=page_map,
        helper_output=helper_output,
        starting_entry_index=1,
        stop_at_addenda=True,
    )
    closure_entries, closure_refs, _ = build_entries(
        closure_pages,
        section_key=closure_section_key,
        section_kind="editorial_closure",
        section_start_file=closure_pages[0]["path"] if closure_pages else ordo_pages[-1]["path"],
        page_map=page_map,
        helper_output=helper_output,
        starting_entry_index=next_index,
        start_after_addenda=True,
    )

    sections = [
        {
            "section_key": ordo_section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum que in hoc tomo continentur",
            "heading_letter": None,
            "page_start": min((n for page in ordo_pages for n in page["header_numbers"]), default=None),
            "page_end": max((n for page in ordo_pages for n in page["header_numbers"]), default=None),
            "file_start": ordo_pages[0]["path"] if ordo_pages else None,
            "file_end": ordo_pages[-1]["path"] if ordo_pages else None,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Contents table of the volume; the OCR pages are shuffled relative to the cited printed pages, so the entry order was reconstructed from the page references.",
                "ocr_headers": [page["heading"] for page in ordo_pages],
                "evidence_files": [page["path"] for page in ordo_pages],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
            },
        },
        {
            "section_key": closure_section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "editorial_closure",
            "heading_raw": "ADDENDA.",
            "heading_norm": "addenda",
            "heading_letter": None,
            "page_start": min((n for page in closure_pages for n in page["header_numbers"]), default=None),
            "page_end": max((n for page in closure_pages for n in page["header_numbers"]), default=None),
            "file_start": closure_pages[0]["path"] if closure_pages else None,
            "file_end": closure_pages[-1]["path"] if closure_pages else None,
            "confidence": 0.94,
            "raw_json": {
                "section_kind_reason": "Editorial closure at the end of the table of contents, covering addenda and the appended dissertation on Honorius I.",
                "ocr_headers": [page["heading"] for page in closure_pages],
                "evidence_files": [page["path"] for page in closure_pages],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
            },
        },
    ]

    entries = ordo_entries + closure_entries
    refs = ordo_refs + closure_refs
    global_key_map: dict[str, str] = {}
    for idx, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        new_key = f"{VOLUME_ID}:entry:{idx:06d}"
        global_key_map[old_key] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = idx

    for ref in refs:
        ref["entry_key"] = global_key_map.get(ref["entry_key"], ref["entry_key"])

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina 80",
        "notes": [
            "The volume ends with a long contents table (ORDO RERUM) followed by ADDENDA and the dissertation on Honorius I.",
            "OCR file suffixes are not the same thing as the cited printed pages; target files were resolved from OCR page headers and a small helper pass.",
            "Bare remissions such as Ibid. were kept in the entry text and only mapped to a ref when a preceding printed page made the locator explicit.",
        ],
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the closing contents block conservatively from the OCR tail. The file order is shuffled against the printed-page order, and some lines are wrapped or abbreviated as Ibid., so the payload preserves the literals and records the inferred locators explicitly.",
        "evidence_files": [page["path"] for page in ordo_pages + closure_pages],
    }

    notes = [
        "The contents table is a single editorial block split across multiple OCR files and mixed printed-page headers.",
        "The addenda section was separated as editorial closure.",
        "Entry order follows the OCR line order after local page-drift correction, not the suffix order of the OCR files.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
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
            "current_focus": "Validate PL080 contents extraction and keep OCR literals intact.",
            "completed": [
                "closing contents block identified",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "review the final payload for ref drift",
                "confirm the addenda split and page-header map",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR file suffix, printed page, and cited reference separate.",
                "Do not invent placeholder refs for bare remissions.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL080 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

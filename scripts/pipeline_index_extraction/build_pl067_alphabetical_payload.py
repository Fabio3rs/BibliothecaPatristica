#!/usr/bin/env python3
"""Usage: build the PL067 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl067_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL067/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL067_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL067_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL067 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL067_alphabetical_indices.json
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

VOLUME_ID = "PL067"
COLLECTION = "PL"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_ORDER = 1
SECTION_KIND = "ordo_rerum"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 1290
SECTION_PAGE_END = 1295
SECTION_FILE_START = 654
SECTION_FILE_END = 657
VOLUME_LABEL = "Patrologia Latina 67"

PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|BUILDING USE ONLY|\d{1,4}\s*)?$", re.IGNORECASE)
BLOCK_RE = re.compile(r"<bloco[^>]*>(?P<content>.*?)</bloco>", re.IGNORECASE | re.DOTALL)
CAPTION_RE = re.compile(r"^(?:LIBER|CAP\.?|CAPUT|EPISTOLA|EPISTOLÆ|NOTITIA|VITA|APPENDIX|HOMILIÆ|OPUSCULA|REGULA|BREVIATIO|TROJANUS|PONTIANUS|FERRANDUS|VIVENTIOLUS|S\. CÆSARIUS|S\. JUSTUS URGELLENSIS|RUSTICUS|FACUNDUS|DIONYSIUS EXIGUUS|ORDO RERUM)\b", re.IGNORECASE)
UPPER_HEAD_RE = re.compile(r"^[A-ZÆŒ0-9\s,.'’\-()]+\.?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm_text(text)
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


def zone_lines(text: str) -> list[str]:
    return [norm_text(line) for line in text.splitlines() if norm_text(line)]


def page_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        content = match.group("content") or ""
        for line in zone_lines(content):
            if NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for line in page_lines(path)[:6]:
            for match in PAGE_TOKEN_RE.finditer(line):
                page = int(match.group(1))
                if 1 <= page <= 9999:
                    page_map.setdefault(page, str(path))
    return page_map


def looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if PAGE_TOKEN_RE.search(stripped):
        return False
    if UPPER_HEAD_RE.fullmatch(stripped) and len(stripped) <= 80:
        return True
    return bool(CAPTION_RE.match(stripped)) and stripped.endswith(".")


def split_segments(line: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s+—\s+", line) if part.strip()]
    return parts if parts else [line]


def should_continue_buffer(buffer: str, line: str) -> bool:
    if not buffer:
        return False
    if buffer.endswith(("-", "—", ":", ";", ",")):
        return True
    if "(" in buffer and ")" not in buffer:
        return True
    if buffer.lower().endswith(("autem", "et", "vel", "sed", "quia", "quod")):
        return True
    if line[:1].islower():
        return True
    if line.startswith(("Patrologi", "coll.", "tom.", "lib.", "epist.", "gendae")):
        return True
    return False


def should_buffer_segment(segment: str) -> bool:
    if not segment:
        return False
    if looks_like_heading(segment):
        return False
    if PAGE_TOKEN_RE.search(segment) or IBID_RE.search(segment):
        return False
    if segment[:1].islower():
        return True
    if "(" in segment and ")" not in segment:
        return True
    if segment.endswith(("-", "—", ":", ";")):
        return True
    return False


def extract_ref_tokens(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1

    if IBID_RE.search(text) and not PAGE_TOKEN_RE.search(text):
        if last_page is not None:
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": "Ibid.",
                    "page_ref_raw": "Ibid.",
                    "page_ref_int": last_page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            page_hints.append(last_page)
            return refs, page_hints, last_page

    for match in PAGE_TOKEN_RE.finditer(text):
        raw = match.group(0).strip()
        start = int(match.group(1))
        end = match.group(2)
        if end is not None:
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_range",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(start),
                    "range_end_raw": str(int(end)),
                }
            )
            page_hints.append(start)
            ref_order += 1
            last_page = start
            continue
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(start)
        ref_order += 1
        last_page = start
    if not refs and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)
    return refs, page_hints, last_page


def derive_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    values = [lemma_raw, entry_raw]
    if lemma_raw:
        values.append(lemma_raw.split(".", 1)[0])
    cleaned: list[str] = []
    for value in values:
        if not value:
            continue
        value = norm_text(value)
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def parse_or_build_entries(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    section_started = False
    ordo_seen = 0
    last_page: int | None = None
    buffer = ""
    buffer_file: str | None = None

    def emit(segment: str, source_file: str) -> None:
        nonlocal entry_order, last_page
        text = norm_text(segment)
        if not text:
            return
        refs_local, page_hints, last_page_local = extract_ref_tokens(text, last_page)
        if last_page_local is not None:
            last_page = last_page_local
        if not refs_local and not looks_like_heading(text):
            # Keep unnumbered editorial fragments as conservative headings.
            entry_kind = "editorial_note" if text.lower().startswith(("notitia", "præfatio", "prefatio", "synopsis")) else "heading_group"
        elif not refs_local:
            entry_kind = "heading_group"
        else:
            entry_kind = "lemma"

        cut = None
        for ref in refs_local:
            pos = text.find(ref["ref_raw"])
            if pos >= 0 and (cut is None or pos < cut):
                cut = pos
        lemma_raw = text if cut is None else norm_text(text[:cut]) or text
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        inferred_printed_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_printed_page) if inferred_printed_page is not None else source_file
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": None,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.74 if not refs_local else 0.9,
            "raw_json": {
                "source_file": source_file,
                "section_kind": SECTION_KIND,
                "page_hints": page_hints,
            },
        }
        entries.append(entry)
        helper_entries.append(
            {
                "entry_id": entry_key,
                "lemma_raw": lemma_raw or text,
                "query_names": derive_query_names(lemma_raw or text, text),
                "page_hint_ints": page_hints,
                "context_raw": text,
            }
        )
        for ref in refs_local:
            target_file = page_map.get(ref["page_ref_int"])
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
                    "target_file": target_file,
                    "target_file_probability": 0.9 if target_file else None,
                    "section_start_file": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
                    "editorial_anchor_file": source_file,
                    "confidence": 0.86 if target_file else 0.68,
                    "raw_json": {},
                }
            )

    for path in files:
        num = file_num(path)
        if num < SECTION_FILE_START or num > SECTION_FILE_END:
            continue
        for raw_line in page_lines(path):
            line = norm_text(raw_line)
            if not line:
                continue
            if not section_started:
                if num == SECTION_FILE_START and "ORDO RERUM" in line:
                    ordo_seen += 1
                    if ordo_seen >= 2:
                        section_started = True
                        emit("ORDO RERUM", str(path))
                continue
            if (
                "Digitized by Google" in line
                or "BUILDING USE ONLY" in line
                or line.startswith("FINIS TOMI")
                or "Ex Typis J.-P. MIGNE" in line
                or line.startswith("UNIV.")
                or line.startswith("Parisiis.")
                or line.startswith("JAN ")
            ):
                continue
            if buffer:
                if should_continue_buffer(buffer, line):
                    buffer = f"{buffer} {line}"
                    continue
                emit(buffer, buffer_file or str(path))
                buffer = ""
                buffer_file = None

            if "—" in line:
                for segment in split_segments(line):
                    if PAGE_TOKEN_RE.search(segment) or IBID_RE.search(segment) or looks_like_heading(segment):
                        emit(segment, str(path))
                    elif should_buffer_segment(segment):
                        buffer = segment
                        buffer_file = str(path)
                    elif segment:
                        emit(segment, str(path))
                continue
            if PAGE_TOKEN_RE.search(line) or IBID_RE.search(line) or looks_like_heading(line):
                emit(line, str(path))
            elif should_buffer_segment(line):
                buffer = line
                buffer_file = str(path)
            else:
                emit(line, str(path))

    if buffer:
        emit(buffer, buffer_file or str(next((p for p in files if file_num(p) == SECTION_FILE_END), files[-1])))

    return entries, refs, helper_entries


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
    proc = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL067 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    page_map = build_page_map(files)
    entries, refs, helper_entries = parse_or_build_entries(files, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_entry = {
        item.get("entry_id"): item
        for item in (helper_output.get("entries") or [])
        if isinstance(item, dict)
    }

    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        top_candidates = []
        for cand in helper.get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            top_candidates.append(
                {
                    "rank": cand.get("rank"),
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                    "inferred_printed_page": cand.get("inferred_printed_page"),
                    "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or []) if isinstance(ev, dict)],
                }
            )
        entry.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": (helper.get("best_candidate") or {}).get("candidate_role"),
            "reason_summary": (helper.get("best_candidate") or {}).get("reason_summary"),
            "top_candidates": top_candidates,
        }
        best = helper.get("best_candidate") or {}
        if best.get("file") and entry.get("target_file_best") is None:
            entry["target_file_best"] = best["file"]
        if best.get("probability") is not None and entry.get("inferred_printed_page") is not None:
            entry["confidence"] = max(float(entry.get("confidence") or 0.0), 0.9 if best.get("status") == "resolved" else 0.74)

    for ref in refs:
        helper = helper_by_entry.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        ref.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if ref.get("target_file") is None and best.get("file"):
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.68 if helper.get("status") != "resolved" else 0.86)

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": "tomus_lxvii_contents",
            "section_order": SECTION_ORDER,
            "section_kind": SECTION_KIND,
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
            "file_end": str(next((p for p in reversed(files) if file_num(p) <= SECTION_FILE_END), files[-1])),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial contents/closure block at the end of the volume.",
                "evidence_files": [
                    str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
                    str(next((p for p in reversed(files) if file_num(p) <= SECTION_FILE_END), files[-1])),
                ],
            },
        }
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The tail OCR preserves the closing ORDO RERUM contents block for the whole tome.",
            "Page refs are editorial page numbers in the contents, not OCR file suffixes.",
            "Helper output is preserved in raw_json as locator evidence only.",
        ],
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents block with conservative line and fragment grouping; the earliest footer spillover on the opening page is preserved only where OCR made the boundary explicit.",
        "evidence_files": [
            str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
            str(next((p for p in files if file_num(p) == SECTION_FILE_START + 1), files[0])),
            str(next((p for p in files if file_num(p) == SECTION_FILE_END - 1), files[-1])),
            str(next((p for p in files if file_num(p) == SECTION_FILE_END), files[-1])),
        ],
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            "This volume ends with an editorial contents block rather than a subject alphabetical index.",
            "Unnumbered heading fragments from the opening footer are retained conservatively when isolated by OCR.",
        ],
    }

    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": now_iso(), "output_file": str(args.output_file)})
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL067 contents block extracted and validated",
            "completed": [
                "section recovered",
                "helper request generated",
                "target locator run",
                "final payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Page refs in the contents should not be collapsed into OCR file suffixes.",
            ],
        },
    )


if __name__ == "__main__":
    main()

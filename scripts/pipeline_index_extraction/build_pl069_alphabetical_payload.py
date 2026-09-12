#!/usr/bin/env python3
"""Usage: build the PL069 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl069_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL069/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL069_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL069_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL069 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL069_alphabetical_indices.json
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

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map

VOLUME_ID = "PL069"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 69"

SECTION_1 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX RERUM, VERBORUM ET SENTENTIARUM, QUAE IN HOC TOMO CONTINENTUR (a).",
    "heading_norm": "index rerum verborum et sententiarum quae in hoc tomo continentur",
    "page_start": 1295,
    "page_end": 1332,
    "file_start": 654,
    "file_end": 672,
}

SECTION_2 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "page_start": 1333,
    "page_end": 1364,
    "file_start": 673,
    "file_end": 688,
}

BLOCK_RE = re.compile(r"<bloco[^>]*>(?P<content>.*?)</bloco>", re.IGNORECASE | re.DOTALL)
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|-\s*|\d{1,4}\s*)?$", re.IGNORECASE)
SECTION1_MARKER_RE = re.compile(r"INDEX RERUM, VERBORUM ET SENTENTIARUM", re.IGNORECASE)
SECTION2_MARKER_RE = re.compile(r"ORDO RERUM QU[AEÆ] IN HOC TOMO CONTINENTUR", re.IGNORECASE)
STANDALONE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
UPPER_HEAD_RE = re.compile(r"^[A-ZÆŒ0-9\s,.'’\-()]+\.?$")
SECTION_HEADING_RE = re.compile(
    r"^(?:INDEX RERUM|ORDO RERUM|VIGILIUS PAPA\.|GILDAS SAPIENS\.|PELAGIUS PAPA I|CASSIODORUS\.|VARIARUM LIBRI XII\.|PELAGII EPISTOLÆ\.|DE EXCIDIO BRITANNIÆ LIBER QUERULUS\.)",
    re.IGNORECASE,
)
FOOTER_RE = re.compile(r"^(?:Digitized by Google|Transcrição|Texto em latino|Transcrição literal|Notas?)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:")
    return value or None


def norm_sort(text: str | None) -> str | None:
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


def extract_blocks(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        content = match.group("content") or ""
        for line in content.splitlines():
            value = norm_text(line)
            if not value:
                continue
            if NOISE_RE.fullmatch(value):
                continue
            if FOOTER_RE.match(value):
                continue
            blocks.append(value)
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for line in extract_blocks(path)[:6]:
            for match in PAGE_HEADER_RE.finditer(line):
                page = int(match.group(1))
                if 1 <= page <= 9999 and page not in page_map:
                    page_map[page] = str(path)
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def derive_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    values = [lemma_raw, entry_raw]
    if lemma_raw:
        values.append(lemma_raw.split(".", 1)[0])
    cleaned: list[str] = []
    for value in values:
        value = norm_text(value)
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def split_segments(line: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s+—\s+", line) if part.strip()]
    return parts or [line]


def section_start(file_num_value: int) -> dict[str, Any]:
    if file_num_value <= SECTION_1["file_end"]:
        return SECTION_1
    return SECTION_2


def make_ref_objs(entry_key: str, text: str, last_page: int | None, source_file: str, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1
    last_seen_page = last_page

    if IBID_RE.search(text) and not PAGE_HEADER_RE.search(text) and last_seen_page is not None:
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": last_seen_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": page_map.get(last_seen_page),
                "target_file_probability": 0.65,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.66,
                "raw_json": {"source_token": "ibid.", "inherited_page": last_seen_page},
            }
        )
        page_hints.append(last_seen_page)
        return refs, page_hints, last_seen_page

    for match in PAGE_HEADER_RE.finditer(text):
        raw = match.group(0).strip()
        page_int = int(match.group(1))
        end = match.group(2)
        if end is not None:
            ref_kind = "editorial_range"
            ref = {
                "ref_order": ref_order,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page_int),
                "range_end_raw": str(int(end)),
                "target_file": page_map.get(page_int),
                "target_file_probability": 0.72 if page_map.get(page_int) else None,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.74 if page_map.get(page_int) else 0.62,
                "raw_json": {"source_token": raw},
            }
            refs.append(ref)
            page_hints.append(page_int)
            ref_order += 1
            last_seen_page = page_int
            continue
        ref = {
            "ref_order": ref_order,
            "ref_kind": "editorial_page",
            "ref_raw": raw,
            "page_ref_raw": raw,
            "page_ref_int": page_int,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": page_map.get(page_int),
            "target_file_probability": 0.75 if page_map.get(page_int) else None,
            "section_start_file": source_file,
            "editorial_anchor_file": source_file,
            "confidence": 0.78 if page_map.get(page_int) else 0.6,
            "raw_json": {"source_token": raw},
        }
        refs.append(ref)
        page_hints.append(page_int)
        ref_order += 1
        last_seen_page = page_int

    return refs, page_hints, last_seen_page


def looks_like_entry(line: str) -> bool:
    if not line:
        return False
    if STANDALONE_LETTER_RE.fullmatch(line):
        return True
    if SECTION_HEADING_RE.match(line):
        return True
    if PAGE_HEADER_RE.search(line):
        return True
    if IBID_RE.search(line):
        return True
    return bool(UPPER_HEAD_RE.fullmatch(line) and len(line) <= 120)


def should_continue_buffer(buffer: str, line: str) -> bool:
    if not buffer:
        return False
    if buffer.endswith(("-", "—", ":", ";", ",")):
        return True
    if line[:1].islower():
        return True
    if buffer.lower().endswith(("et", "sed", "aut", "quia", "quod", "ubi")):
        return True
    return False


def build_section(
    *,
    files: list[Path],
    page_map: dict[int, str],
    section_meta: dict[str, Any],
    start_marker: re.Pattern[str],
    stop_marker: re.Pattern[str] | None,
    section_start_file_num: int,
    section_end_file_num: int,
    section_start_page: int,
    section_end_page: int,
    entry_start_order: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_node_key: str | None = None
    node_order = 0
    entry_order = entry_start_order
    started = False
    buffer = ""
    buffer_file: str | None = None
    last_page: int | None = None
    current_heading_letter: str | None = None
    section_start_file = str(next((p for p in files if file_num(p) == section_start_file_num), files[0]))

    def emit(text: str, source_file: str) -> None:
        nonlocal entry_order, node_order, current_node_key, current_heading_letter, last_page
        value = norm_text(text)
        if not value:
            return
        if STANDALONE_LETTER_RE.fullmatch(value):
            node_order += 1
            current_heading_letter = value
            current_node_key = f"{VOLUME_ID}:node:{section_meta['section_key'].split(':')[-1]}:{value}:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_node_key,
                    "section_key": section_meta["section_key"],
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": value,
                    "label_norm": value.lower(),
                    "label_sort": value.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": source_file},
                }
            )
            return
        refs_local, page_hints, last_page_local = make_ref_objs(
            f"{VOLUME_ID}:entry:pending", value, last_page, source_file, page_map
        )
        if last_page_local is not None:
            last_page = last_page_local
        if refs_local:
            entry_kind = "cross_reference" if " V. " in f" {value} " and len(refs_local) == 0 else "lemma"
            if value.lower().startswith(("v. ", "vid.", "vide", "voir", "cf.", "id.")):
                entry_kind = "cross_reference"
        else:
            entry_kind = "cross_reference" if " V. " in f" {value} " else ("heading_group" if looks_like_entry(value) else "editorial_note")
        if refs_local and entry_kind == "cross_reference":
            entry_kind = "lemma"

        cut = None
        for ref in refs_local:
            pos = value.find(ref["ref_raw"])
            if pos >= 0 and (cut is None or pos < cut):
                cut = pos
        lemma_raw = value if cut is None else norm_text(value[:cut]) or value
        if entry_kind == "cross_reference" and lemma_raw and lemma_raw == value:
            # For bare cross-references, preserve the OCR literal as the lemma.
            lemma_raw = lemma_raw

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        inferred_printed_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_printed_page) if inferred_printed_page is not None else source_file
        entry = {
            "entry_key": entry_key,
            "section_key": section_meta["section_key"],
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": norm_sort(lemma_raw),
            "lemma_sort": norm_sort(lemma_raw),
            "entry_raw": value,
            "context_raw": value,
            "heading_letter": current_heading_letter,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.9 if refs_local else 0.72,
            "raw_json": {
                "source_file": source_file,
                "section_kind": section_meta["section_kind"],
                "page_hints": page_hints,
            },
        }
        entries.append(entry)
        if refs_local:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or value,
                    "query_names": derive_query_names(lemma_raw or value, value),
                    "page_hints": [str(page) for page in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": value,
                }
            )
        for ref in refs_local:
            ref["entry_key"] = entry_key
            refs.append(ref)

    for path in files:
        num = file_num(path)
        if num < section_start_file_num or num > section_end_file_num:
            continue
        for line in extract_blocks(path):
            if not started:
                if start_marker.search(line):
                    started = True
                continue
            if stop_marker and stop_marker.search(line):
                break
            if FOOTER_RE.match(line):
                continue
            if buffer:
                if should_continue_buffer(buffer, line):
                    buffer = f"{buffer} {line}"
                    continue
                emit(buffer, buffer_file or str(path))
                buffer = ""
                buffer_file = None
            if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                emit(line, str(path))
                continue
            if SECTION_HEADING_RE.match(line):
                emit(line, str(path))
                continue
            if "—" in line:
                segments = split_segments(line)
                for segment in segments:
                    if should_continue_buffer(segment, segment):
                        buffer = segment
                        buffer_file = str(path)
                        continue
                    emit(segment, str(path))
                continue
            if PAGE_HEADER_RE.search(line) or IBID_RE.search(line) or line[:1].isupper():
                if line[:1].isupper() and not PAGE_HEADER_RE.search(line) and not IBID_RE.search(line) and len(line) < 3:
                    emit(line, str(path))
                    continue
                if PAGE_HEADER_RE.search(line) or IBID_RE.search(line) or line[:1].isupper():
                    emit(line, str(path))
                    continue
            if line[:1].islower() or line.endswith(("-", "—", ":", ";", ",")):
                buffer = line
                buffer_file = str(path)
                continue
            emit(line, str(path))

    if buffer:
        emit(buffer, buffer_file or str(next((p for p in files if file_num(p) == section_end_file_num), files[-1])))

    section = {
        **section_meta,
        "file_start": section_start_file,
        "file_end": str(next((p for p in reversed(files) if file_num(p) == section_end_file_num), files[-1])),
        "confidence": 0.97,
        "raw_json": {
            "source_heading_file": section_start_file,
            "section_kind_reason": (
                "Analytical subject index block with alphabetic lemmata and page citations."
                if section_meta["section_kind"] == "analytic_subject"
                else "Editorial contents block after the analytical index."
            ),
        },
    }
    return [section], nodes, entries, refs, helper_entries


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
    ap = argparse.ArgumentParser(description="Build PL069 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    page_map = build_page_map(files)

    sections_1, nodes_1, entries_1, refs_1, helper_entries_1 = build_section(
        files=files,
        page_map=page_map,
        section_meta=SECTION_1,
        start_marker=SECTION1_MARKER_RE,
        stop_marker=SECTION2_MARKER_RE,
        section_start_file_num=SECTION_1["file_start"],
        section_end_file_num=SECTION_1["file_end"],
        section_start_page=SECTION_1["page_start"],
        section_end_page=SECTION_1["page_end"],
    )
    sections_2, nodes_2, entries_2, refs_2, helper_entries_2 = build_section(
        files=files,
        page_map=page_map,
        section_meta=SECTION_2,
        start_marker=SECTION2_MARKER_RE,
        stop_marker=None,
        section_start_file_num=SECTION_2["file_start"],
        section_end_file_num=SECTION_2["file_end"],
        section_start_page=SECTION_2["page_start"],
        section_end_page=SECTION_2["page_end"],
        entry_start_order=len(entries_1),
    )

    sections = sections_1 + sections_2
    nodes = nodes_1 + nodes_2
    entries = entries_1 + entries_2
    refs = refs_1 + refs_2

    helper_entries = helper_entries_1 + helper_entries_2
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
        best = helper.get("best_candidate") or {}
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
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "top_candidates": top_candidates,
        }
        if best.get("file") and not entry.get("target_file_best"):
            entry["target_file_best"] = best["file"]

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
        if best.get("file") and not ref.get("target_file"):
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "PL069 combines an analytical index block and an editorial ordo rerum block in the OCR tail.",
            "Editorial page numbers are preserved separately from OCR file suffixes.",
        ],
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the analytical index and the closing ordo rerum block from the OCR tail with conservative line-level grouping; some OCR lines still contain wrapped or inherited text and are preserved as-is.",
        "evidence_files": [
            str(next((p for p in files if file_num(p) == SECTION_1["file_start"]), files[0])),
            str(next((p for p in files if file_num(p) == SECTION_1["file_end"]), files[-1])),
            str(next((p for p in files if file_num(p) == SECTION_2["file_start"]), files[0])),
            str(next((p for p in files if file_num(p) == SECTION_2["file_end"]), files[-1])),
        ],
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
            "Helper locator evidence is stored in raw_json for entries and refs with material page hints.",
            "The payload intentionally keeps OCR literals and does not normalize page references beyond basic parsing.",
        ],
    }

    # Persist checkpoints for reruns.
    write_json(args.intermediate_dir / "todo.json", {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL069 alphabetical payload after helper resolution",
        "completed": [
            "OCR tail inspected",
            "section candidates identified",
            "helper request generated",
            "helper output applied to entries and refs",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Keep OCR literals and the two numbering systems distinct.",
        ],
    })
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PG043 alphabetical-index payload from OCR tail files.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/PG043_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG043/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG043_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG043_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG043 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG043_alphabetical_indices.json
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


HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip()


def lower_norm(text: str | None) -> str | None:
    text = norm(text)
    return text.lower().strip(" ,;:.") if text else None


def sort_norm(text: str | None) -> str | None:
    text = norm(text)
    return text.lower().strip(" ,;:.") if text else None


def read_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if "Digitized by Google" in text:
            break
        lines.append(text)
    return lines


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = read_lines(path)
        header = " ".join(lines[:4])
        nums = [int(m) for m in HEADER_PAGE_RE.findall(header)]
        for page in nums:
            if page >= 1:
                page_map.setdefault(page, str(path))
    return page_map


def header_pages_for_file(path: Path) -> list[int]:
    lines = read_lines(path)
    header = " ".join(lines[:4])
    nums = [int(m) for m in HEADER_PAGE_RE.findall(header)]
    return nums[:2] if len(nums) >= 2 else nums


def is_noise_line(text: str) -> bool:
    upper = text.upper()
    if upper in {"INDEX RERUM", "INDEX ANALYTICUS", "INDEX ANALYTICUS.", "ORDO RERUM", "MONITUM", "EXPLICIT EPIPHANIUS."}:
        return True
    if upper.startswith("VERSIO ANTIQUA") or upper.startswith("S. EPIPHANII"):
        return True
    if upper.startswith("IN SCRIPTA S EPIPHANII GENUINA"):
        return True
    if upper.startswith("MONITUM IN FRAGMENTUM SUBSEQUENS"):
        return True
    if upper.startswith("CAPUT ") or upper.startswith("CAP. ") or upper.startswith("LIBER "):
        return True
    if re.fullmatch(r"\d{1,4}", text):
        return True
    return False


def normalize_roman_token(token: str) -> int | None:
    token = token.strip().rstrip(".")
    if not token or not ROMAN_RE.fullmatch(token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(token.upper()):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def extract_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    # keep the literal OCR tokens but only collect obvious locator tokens
    for m in re.finditer(r"\b([IVXLCDM]+|\d{1,4})(?:\s*(?:et\s+alib\.?|et\s+alibi|alib\.?|alibi|ibid\.?|ibid|ib\.|seq\.?|seqq\.?))?", text, re.IGNORECASE):
        raw = m.group(0).strip()
        core = m.group(1)
        if raw.lower() in {"ib.", "ibid.", "ibid", "alib.", "alibi"}:
            continue
        page_int = None
        if core.isdigit():
            page_int = int(core)
        else:
            page_int = normalize_roman_token(core)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
            }
        )
    return refs


def split_lemma_and_entry(line: str) -> tuple[str | None, str]:
    cleaned = norm(line) or ""
    # Prefer the part before the first comma; fall back to the first period.
    if "," in cleaned:
        lemma = cleaned.split(",", 1)[0].strip()
    elif "." in cleaned:
        lemma = cleaned.split(".", 1)[0].strip()
    else:
        lemma = cleaned
    lemma = lemma.rstrip(" ,;:.")
    return (lemma or None), cleaned


def derive_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    candidates: list[str] = []
    if lemma_raw:
        candidates.append(lemma_raw)
        prefix = re.split(r"\s*[;,]\s*", lemma_raw, maxsplit=1)[0].strip()
        if prefix:
            candidates.append(prefix)
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


def line_is_section_marker(text: str) -> bool:
    upper = text.upper()
    return upper in {
        "INDEX RERUM",
        "INDEX ANALYTICUS",
        "INDEX ANALYTICUS.",
        "ORDO RERUM",
        "MONITUM IN FRAGMENTUM SUBSEQUENS.",
        "EXPLICIT EPIPHANIUS.",
    } or upper.startswith("INDEX ANALYTICUS.")


def collect_section_lines(files: list[Path], section: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    buffer: str | None = None
    current_file: Path | None = None
    current_page: int | None = None
    started = False
    for path in files:
        lines = read_lines(path)
        header_pages = header_pages_for_file(path)
        current_page = header_pages[0] if header_pages else None
        for raw_line in lines:
            line = norm(raw_line) or ""
            upper = line.upper()
            if section == "index_rerum":
                if upper.startswith("LIBRI DE XII GEMMIS"):
                    continue
                if upper.startswith("MONITUM IN FRAGMENTUM SUBSEQUENS"):
                    started = False
                    break
            elif section == "index_analyticus":
                if upper.startswith("IN SCRIPTA S EPIPHANII GENUINA"):
                    continue
                if upper.startswith("EXPLICIT EPIPHANIUS"):
                    started = False
                    break
            if line_is_section_marker(line):
                started = True
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                records.append(
                    {
                        "kind": "node",
                        "label": line,
                        "file": str(path),
                        "page": current_page,
                    }
                )
                continue
            if not started:
                # section 1 starts immediately; section 2 waits for the first letter marker.
                if section == "index_rerum":
                    if not is_noise_line(line):
                        started = True
                    else:
                        continue
                else:
                    continue
            if is_noise_line(line):
                continue
            if buffer is not None:
                if line and (line[0].islower() or line[0] in ",.;:)]"):
                    buffer = buffer.rstrip("-") + (" " if not buffer.endswith("-") else "") + line
                    if not buffer.endswith("-"):
                        records.append(
                            {
                                "kind": "entry",
                                "line": buffer,
                                "file": str(current_file),
                                "page": current_page,
                            }
                        )
                        buffer = None
                    continue
                records.append(
                    {
                        "kind": "entry",
                        "line": buffer,
                        "file": str(current_file),
                        "page": current_page,
                    }
                )
                buffer = None
            if line.endswith("-") and len(line) > 12:
                buffer = line
                current_file = path
                continue
            records.append(
                {
                    "kind": "entry",
                    "line": line,
                    "file": str(path),
                    "page": current_page,
                }
            )
    if buffer is not None:
        records.append(
            {
                "kind": "entry",
                "line": buffer,
                "file": str(current_file) if current_file else None,
                "page": current_page,
            }
        )
    return records


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict[str, Any]], path: Path) -> None:
    helper_entries: list[dict[str, Any]] = []
    seen = set()
    for idx, item in enumerate(entries, start=1):
        if item["kind"] != "entry":
            continue
        line = item["line"]
        lemma_raw, entry_raw = split_lemma_and_entry(line)
        refs = extract_refs(line)
        page_hints = [str(r["page_ref_int"]) for r in refs if r["page_ref_int"] is not None]
        if not page_hints:
            continue
        entry_id = f"pg043_{idx:04d}"
        if entry_id in seen:
            continue
        seen.add(entry_id)
        helper_entries.append(
            {
                "entry_id": entry_id,
                "lemma_raw": lemma_raw or entry_raw,
                "query_names": derive_query_names(lemma_raw, entry_raw),
                "page_hints": page_hints,
                "page_hint_ints": [int(p) for p in page_hints],
                "context_raw": entry_raw,
            }
        )
    payload = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_helper(helper_request: Path, helper_output: Path) -> None:
    helper_output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request),
            "--output",
            str(helper_output),
            "--pretty",
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")


def helper_output_map(helper_output: Path) -> dict[str, Any]:
    if not helper_output.exists():
        return {}
    data = json.loads(helper_output.read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for item in data.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(
    volume_id: str,
    source_root: Path,
    page_map: dict[int, str],
    helper_map: dict[str, Any],
    section_id: str,
    section_key: str,
    section_order: int,
    heading_raw: str,
    page_start: int,
    page_end: int,
    file_start: Path,
    file_end: Path,
    section_lines: list[dict[str, Any]],
    section_kind_reason: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections = [
        {
            "section_key": section_key,
            "volume_id": volume_id,
            "work_key": None,
            "section_order": section_order,
            "section_kind": "analytic_subject",
            "heading_raw": heading_raw,
            "heading_norm": lower_norm(heading_raw),
            "heading_letter": None,
            "page_start": page_start,
            "page_end": page_end,
            "file_start": str(file_start),
            "file_end": str(file_end),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": section_kind_reason,
            },
        }
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    node_for_letter: dict[str, str] = {}
    node_order = 1
    entry_order = 1
    entry_count = 1
    current_letter = None

    for rec in section_lines:
        if rec["kind"] == "node":
            continue

        line = rec["line"]
        lemma_raw, entry_raw = split_lemma_and_entry(line)
        ref_items = extract_refs(line)
        letter = None
        if lemma_raw:
            m = re.search(r"[A-ZÆŒἈ-῾Α-Ω]", lemma_raw)
            if m:
                letter = m.group(0)
        if letter and letter != current_letter:
            node_key = f"{volume_id}:node:{section_id}:{node_order:03d}"
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.92,
                    "raw_json": {"derived_from": "entry_initial_letter"},
                }
            )
            node_for_letter[letter] = node_key
            current_letter = letter
            node_order += 1
        entry_id = f"{volume_id}:entry:{entry_count:04d}"
        entry_count += 1
        helper_key = f"pg043_{entry_count-1:04d}"
        helper_item = helper_map.get(helper_key)
        target_best = rec["file"]
        best_candidate = helper_item.get("best_candidate") if helper_item else None
        if isinstance(best_candidate, dict) and best_candidate.get("file"):
            target_best = best_candidate["file"]
        parent_node_key = node_for_letter.get(current_letter) if current_letter else None
        entry_payload = {
            "entry_key": entry_id,
            "section_key": section_key,
            "parent_node_key": parent_node_key,
            "entry_order": entry_order,
            "entry_kind": "cross_reference" if lemma_raw and lemma_raw.lower().startswith(("vid.", "vide", "voir")) and not ref_items else "lemma",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lower_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": current_letter,
            "inferred_printed_page": rec["page"],
            "section_start_file": str(file_start),
            "editorial_anchor_file": rec["file"],
            "target_file_best": target_best,
            "confidence": 0.82 if ref_items else 0.72,
            "raw_json": {
                "source_file": rec["file"],
                "source_page": rec["page"],
                "section_kind": "analytic_subject",
                "helper_status": helper_item.get("status") if helper_item else None,
                "helper_best_candidate": best_candidate,
            },
        }
        entries.append(entry_payload)
        for i, ref in enumerate(ref_items, start=1):
            target_file = None
            if ref["page_ref_int"] is not None:
                target_file = page_map.get(ref["page_ref_int"])
            refs.append(
                {
                    "entry_key": entry_id,
                    "ref_order": i,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.99 if target_file else 0.5,
                    "section_start_file": str(file_start),
                    "editorial_anchor_file": rec["file"],
                    "confidence": 0.9 if target_file else 0.6,
                    "raw_json": {
                        "source_file": rec["file"],
                        "source_page": rec["page"],
                        "page_map_hit": bool(target_file),
                    },
                }
            )
        entry_order += 1

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": volume_id,
            "collection": "PG",
            "source_root": str(source_root),
            "volume_label": volume_id,
            "notes": [
                "PG043 contains an index rerum for the version antiqua material and a later INDEX ANALYTICUS for the Epiphanius corpus.",
                "The non-alphabetic ORDO RERUM closure was inspected but not serialized as a section in this payload.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "partial_recovery",
            "entries_status_reason": "Conservative recovery of the visible alphabetical index blocks from OCR files 196-199 and 342-345; the closing ORDO RERUM was inspected separately and excluded as non-alphabetic closure material.",
            "evidence_files": [
                str(file_start),
                str(file_end),
            ],
        },
        "notes": [
            "The section on files 196-199 is treated as an alphabetical subject index with letter-group nodes.",
            "The section on files 342-345 is treated as a second alphabetical subject index block under INDEX ANALYTICUS.",
            "Helper evidence is preserved in raw_json where available; target files were resolved against the local editorial page map.",
        ],
    }
    return payload, sections, entries, refs


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PG043 alphabetical index payload.")
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--helper-request-json", required=True, type=Path)
    ap.add_argument("--helper-output-json", required=True, type=Path)
    ap.add_argument("--intermediate-dir", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    args = ap.parse_args()

    source_root: Path = args.source_root
    files = sorted(source_root.glob("*.txt"), key=file_seq)
    if not files:
        raise SystemExit(f"No OCR files found under {source_root}")

    page_map = build_page_map(files)
    todo_path = args.intermediate_dir / "todo.json"
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": "PG043",
        "updated_at": now_iso(),
        "current_focus": "Recover PG043 alphabetical index sections and resolve material target files.",
        "completed": [
            "located the index rerum section in files 196-199",
            "located the INDEX ANALYTICUS section in files 342-345",
            "separated the non-alphabetic ORDO RERUM closure",
        ],
        "pending": [
            "validate the helper output",
            "write the final payload",
        ],
        "blocked": [],
        "notes": [
            "Work is intentionally scoped to the provided OCR source_root only.",
        ],
    }
    todo_path.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    section1_files = [p for p in files if 196 <= file_seq(p) <= 199]
    section2_files = [p for p in files if 342 <= file_seq(p) <= 345]
    section1_lines = collect_section_lines(section1_files, "index_rerum")
    section2_lines = collect_section_lines(section2_files, "index_analyticus")

    combined_for_helper = section1_lines + section2_lines
    build_helper_request("PG043", source_root, combined_for_helper, args.helper_request_json)
    run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_output_map(args.helper_output_json)

    payload1, *_ = build_payload(
        "PG043",
        source_root,
        page_map,
        helper_map,
        "index_rerum",
        "PG043:alpha:analytic_subject:001",
        1,
        "INDEX RERUM.",
        365,
        372,
        section1_files[0],
        section1_files[-1],
        section1_lines,
        "Alphabetical subject index of the version antiqua material, with letter-group dividers and local remissions.",
    )

    payload2, *_ = build_payload(
        "PG043",
        source_root,
        page_map,
        helper_map,
        "index_analyticus",
        "PG043:alpha:analytic_subject:002",
        2,
        "INDEX ANALYTICUS.",
        657,
        664,
        section2_files[0],
        section2_files[-1],
        section2_lines,
        "Alphabetical subject index for the Epiphanius corpus; the preface line before the A-group was inspected and preserved as non-entry context.",
    )

    final_payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": payload1["volume"],
        "sections": payload1["sections"] + payload2["sections"],
        "nodes": payload1["nodes"] + payload2["nodes"],
        "entries": payload1["entries"] + payload2["entries"],
        "refs": payload1["refs"] + payload2["refs"],
        "scripture_refs": [],
        "coverage": payload1["coverage"],
        "notes": payload1["notes"],
    }
    final_payload["coverage"]["evidence_files"] = [
        str(section1_files[0]),
        str(section1_files[-1]),
        str(section2_files[0]),
        str(section2_files[-1]),
    ]
    final_payload["notes"].append("The later ORDO RERUM closure on files 678-679 was inspected and excluded from serialization because it is not an alphabetical index.")

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

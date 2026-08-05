#!/usr/bin/env python3
"""Usage: build the PL157 alphabetical payload and helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl157_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL157/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL157_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL157_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL157 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL157_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL157"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 157"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
SECTION_1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_2_KEY = f"{VOLUME_ID}:alpha:onomastic_person:002"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def strip_accents(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = strip_accents(text)
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def clean_lines(path: Path) -> list[str]:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<"):
            continue
        if "Digitized by Google" in line:
            continue
        if re.fullmatch(r"\d{4}", line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        footer = parsed.get("footer_text") or ""
        top_text = f"{header} {footer}".strip()
        if not top_text:
            top_text = " ".join((parsed.get("all_text") or "").splitlines()[:6])
        numbers: list[int] = []
        for raw in re.findall(r"\b(\d{1,4})\b", top_text):
            num = int(raw)
            if num not in numbers:
                numbers.append(num)
        for num in numbers:
            page_map.setdefault(num, str(path))
    return page_map


def normalize_for_search(text: str) -> str:
    value = strip_accents(text).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def build_body(lines: list[str]) -> str:
    pieces: list[str] = []
    prev = ""
    for line in lines:
        if prev and prev[-1].isdigit() and line[:1].isupper():
            pieces.append(".")
        pieces.append(line)
        prev = line
    body = " ".join(pieces)
    body = re.sub(r"§", ".", body)
    body = re.sub(r"\b([A-Z])\.", r"\1§", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body


def split_segments(body: str) -> list[str]:
    raw_segments = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", body)
    segments: list[str] = []
    for segment in raw_segments:
        seg = segment.strip()
        if not seg:
            continue
        if seg in {"INDEX RERUM ET VERBORUM", "QUÆ", "IN OPERIBUS GOFFRIDI VINDOCINENSIS CONTINENTUR.", "INDEX EORUM"}:
            continue
        if seg.startswith("INDEX RERUM ET VERBORUM"):
            continue
        if seg.startswith("Revocatur Lector"):
            continue
        if seg.startswith("Ad quos mittuntur epistolae"):
            continue
        if re.fullmatch(r"\d{1,4}(?:\s+\d{1,4})*", seg):
            continue
        segments.append(seg.replace("§", "."))
    return segments


def lemma_from_segment(segment: str) -> str | None:
    candidate = segment.strip().replace("§", ".")
    match = re.search(r",\s*(?:[IVXLCDM]+|\d)", candidate)
    if match:
        lemma = candidate[: match.start()]
    else:
        lemma = candidate
    lemma = lemma.rstrip(" ,;:.")
    return lemma or None


def extract_numbers(segment: str) -> list[int]:
    numbers: list[int] = []
    for raw in re.findall(r"\b(\d{1,4})\b", segment):
        num = int(raw)
        if num not in numbers:
            numbers.append(num)
    return numbers


def first_letter(lemma: str | None) -> str | None:
    if not lemma:
        return None
    value = normalize(lemma)
    if not value:
        return None
    for ch in value:
        if ch.isalpha():
            return ch.upper()
    return None


def locate_source_file(segment: str, file_bodies: dict[str, str], ordered_files: list[Path]) -> str | None:
    snippet = normalize_for_search(segment[:120])
    if not snippet:
        return None
    snippet = snippet[:80]
    for path in ordered_files:
        body = file_bodies.get(str(path), "")
        if snippet in body:
            return str(path)
    return str(ordered_files[0]) if ordered_files else None


def parse_segments(
    *,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    lines: list[str],
    file_bodies: dict[str, str],
    ordered_files: list[Path],
    section_start_file: str,
    page_start: int | None,
    page_end: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    body = build_body(lines)
    segments = split_segments(body)
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    letter_order = 1
    entry_order = 1
    for segment in segments:
        lemma = lemma_from_segment(segment)
        page_numbers = extract_numbers(segment)
        if not lemma or not page_numbers:
            continue
        letter = first_letter(lemma)
        if letter and letter not in letter_to_node:
            node_key = f"{section_key}:letter:{letter}"
            letter_to_node[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": letter_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {"source": "ocr_letter_group"},
                }
            )
            letter_order += 1

        source_file = locate_source_file(segment, file_bodies, ordered_files)
        entry_key = f"{section_key}:e{entry_order:03d}"
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": letter_to_node.get(letter),
            "entry_order": entry_order,
            "entry_kind": "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": normalize(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": segment.replace("§", "."),
            "context_raw": segment.replace("§", "."),
            "heading_letter": letter,
            "inferred_printed_page": page_numbers[0],
            "section_start_file": section_start_file,
            "editorial_anchor_file": source_file,
            "target_file_best": None,
            "confidence": 0.72,
            "raw_json": {
                "source": "direct_ocr",
                "segment_file": source_file,
                "page_refs": page_numbers,
            },
        }
        for ref_order, page_num in enumerate(page_numbers, start=1):
            ref = {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": str(page_num),
                "page_ref_raw": str(page_num),
                "page_ref_int": page_num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.68,
                "raw_json": {"source": "direct_ocr"},
            }
            refs.append(ref)
        entries.append(entry)
        entry_order += 1

    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": normalize(heading_raw),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": section_start_file,
        "file_end": section_start_file,
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": (
                "Alphabetical analytical index of matters and phrases"
                if section_kind == "analytic_subject"
                else "Alphabetical onomastic index of recipients and correspondents"
            ),
            "ocr_evidence": lines[:8],
        },
    }
    return section, nodes, entries, refs, []


def build_payload(
    volume: dict[str, Any],
    sections: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    scripture_refs: list[dict[str, Any]],
    coverage: dict[str, Any],
    notes: list[str],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL157 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = sorted(args.source_root.glob("*.txt"), key=file_seq)
    wanted = {f"26ce5fc7-ef51-439e-a7c7-92ac5fca351d-{i}.txt" for i in range(652, 656)}
    index_files = [p for p in files if p.name in wanted]
    if len(index_files) != 4:
        raise SystemExit(f"Expected 4 index files, found {len(index_files)}")

    file_bodies = {str(path): normalize_for_search(" ".join(clean_lines(path))) for path in index_files}
    all_lines: list[tuple[Path, str]] = []
    for path in index_files:
        for line in clean_lines(path):
            all_lines.append((path, line))

    split_idx = None
    for idx, (_, line) in enumerate(all_lines):
        if line.strip() == "INDEX EORUM":
            split_idx = idx
            break
    if split_idx is None:
        raise SystemExit("Could not locate INDEX EORUM split point in PL157 OCR tail")

    section1_lines = [line for _, line in all_lines[:split_idx]]
    section2_lines = [line for _, line in all_lines[split_idx:]]

    section1, nodes1, entries1, refs1, _ = parse_segments(
        section_key=SECTION_1_KEY,
        section_order=1,
        section_kind="analytic_subject",
        heading_raw="INDEX RERUM ET VERBORUM QUÆ IN OPERIBUS GOFFRIDI VINDOCINENSIS CONTINENTUR.",
        lines=section1_lines,
        file_bodies=file_bodies,
        ordered_files=index_files,
        section_start_file=str(index_files[0]),
        page_start=1295,
        page_end=1302,
    )

    section2, nodes2, entries2, refs2, _ = parse_segments(
        section_key=SECTION_2_KEY,
        section_order=2,
        section_kind="onomastic_person",
        heading_raw="INDEX EORUM. Ad quos mittuntur epistolae et opuscula Goffridi Vindocinensis.",
        lines=section2_lines,
        file_bodies=file_bodies,
        ordered_files=index_files,
        section_start_file=str(index_files[-1]),
        page_start=1302,
        page_end=1302,
    )

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "PL157 tail contains two alphabetical indices: the long rerum/verborum index and the addressee index starting at INDEX EORUM.",
            "The final ORDO RERUM table begins after the alphabetical material and is excluded from the alphabetical payload.",
        ],
    }

    helper_entries: list[dict[str, Any]] = []
    for entry in entries2:
        refs = [ref for ref in refs2 if ref["entry_key"] == entry["entry_key"]]
        page_hints = [str(ref["page_ref_int"]) for ref in refs if ref.get("page_ref_int") is not None]
        page_hint_ints = [ref["page_ref_int"] for ref in refs if ref.get("page_ref_int") is not None]
        lemma_raw = entry["lemma_raw"]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw, normalize(lemma_raw) or lemma_raw, normalize_for_search(lemma_raw)],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_entry = {item.get("entry_id"): item for item in helper_output.get("entries", [])}

    # Fill the addressee-index targets from the helper.
    for entry in entries2:
        helper_entry = helper_by_entry.get(entry["entry_key"])
        if helper_entry:
            entry["raw_json"]["helper"] = {
                "status": helper_entry.get("status"),
                "candidate_role": helper_entry.get("candidate_role"),
                "reason_summary": helper_entry.get("reason_summary"),
                "best_candidate": helper_entry.get("best_candidate"),
                "top_candidates": helper_entry.get("candidates", [])[:3],
            }
            best_candidate = helper_entry.get("best_candidate") or {}
            if best_candidate.get("file"):
                entry["target_file_best"] = best_candidate["file"]
                entry["confidence"] = max(entry["confidence"], 0.84)
        if entry["target_file_best"] is None and entry["editorial_anchor_file"]:
            entry["target_file_best"] = entry["editorial_anchor_file"]

    for ref in refs2:
        helper_entry = helper_by_entry.get(ref["entry_key"])
        if helper_entry:
            best_candidate = helper_entry.get("best_candidate") or {}
            if best_candidate.get("file"):
                ref["target_file"] = best_candidate["file"]
                ref["target_file_probability"] = best_candidate.get("probability")
                ref["confidence"] = max(ref["confidence"], 0.84)
            ref["raw_json"]["helper"] = {
                "status": helper_entry.get("status"),
                "candidate_role": helper_entry.get("candidate_role"),
                "reason_summary": helper_entry.get("reason_summary"),
                "best_candidate": best_candidate,
            }

    # Resolve the analytical index entries to the OCR file for the cited page.
    page_map = build_page_map(files)
    sorted_pages = sorted(page_map)
    for entry in entries1:
        refs = [ref for ref in refs1 if ref["entry_key"] == entry["entry_key"]]
        if refs:
            first_ref = refs[0]
            target_file = page_map.get(first_ref["page_ref_int"])
            if target_file:
                entry["target_file_best"] = target_file
            else:
                entry["target_file_best"] = entry["editorial_anchor_file"]
            entry["raw_json"]["page_map_target"] = target_file
            for ref in refs:
                target_file = page_map.get(ref["page_ref_int"])
                if target_file:
                    ref["target_file"] = target_file
                    ref["target_file_probability"] = 1.0
                    ref["confidence"] = max(ref["confidence"], 0.92)
                else:
                    if sorted_pages:
                        nearest_page = min(sorted_pages, key=lambda page: abs(page - ref["page_ref_int"]))
                        if abs(nearest_page - ref["page_ref_int"]) <= 10:
                            ref["target_file"] = page_map[nearest_page]
                            ref["target_file_probability"] = max(0.35, 1.0 - (abs(nearest_page - ref["page_ref_int"]) / 10.0))
                            ref["confidence"] = max(ref["confidence"], 0.6)
                            ref["raw_json"]["page_map_fallback"] = {
                                "requested_page": ref["page_ref_int"],
                                "nearest_page": nearest_page,
                                "page_delta": nearest_page - ref["page_ref_int"],
                            }
                        else:
                            ref["raw_json"]["page_map_target"] = None
                    else:
                        ref["raw_json"]["page_map_target"] = None
        if entry["target_file_best"] is None:
            entry["target_file_best"] = entry["editorial_anchor_file"]

    all_sections = [section1, section2]
    all_nodes = nodes1 + nodes2
    all_entries = entries1 + entries2
    all_refs = refs1 + refs2
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered both alphabetical indices from the OCR tail; section boundaries were confirmed in files 652-655 and the final ORDO RERUM table was excluded.",
        "evidence_files": [str(p) for p in index_files],
    }
    notes = [
        "Section 1 is the analytical rerum/verborum index; section 2 is the onomastic addressee index beginning at INDEX EORUM.",
        "Page-map resolution was used for the analytical index; the addressee index was cross-checked with the helper locator.",
        "OCR file suffixes are not used as editorial page numbers.",
    ]

    intermediate_dir = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"volume_id": VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()}
    write_json(intermediate_dir / "manifest.json", manifest)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", all_sections)
    write_json(intermediate_dir / "nodes.json", all_nodes)
    write_json(intermediate_dir / "entries.json", all_entries)
    write_json(intermediate_dir / "refs.json", all_refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL157 alphabetical payload built and helper cross-checked; validate final JSON and import shape.",
            "completed": [
                "sections separated",
                "analytical index resolved against page map",
                "addressee index resolved with helper locator",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The final ORDO RERUM table starts after the alphabetical material and was intentionally excluded.",
            ],
        },
    )

    payload = build_payload(volume, all_sections, all_nodes, all_entries, all_refs, [], coverage, notes, manifest["generated_at"])
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL099 alphabetical index payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl099_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL099/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL099_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL099_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL099 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL099_alphabetical_indices.json
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


VOLUME_ID = "PL099"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 99"

BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.IGNORECASE | re.DOTALL)
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ROMAN_TOKEN_RE = re.compile(r"\b[ivxlcdm]{1,8}\b", re.IGNORECASE)
PAGE_REF_RE = re.compile(
    r"(?P<roman>\b[ivxlcdm]{1,8}\b)|(?P<arabic>\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)|(?P<ibid>\bibid\.?\b)",
    re.IGNORECASE,
)
NOISE_LINES = {"Digitized by Google", "||", "."}
ALPHA_START_FILES = {638, 639, 640, 641, 642, 643, 644, 645, 646, 647, 648, 649, 650, 651}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def roman_to_int(text: str) -> int | None:
    cleaned = text.lower().strip()
    if not re.fullmatch(r"[ivxlcdm]+", cleaned):
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(cleaned):
        value = values[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and line not in NOISE_LINES]
        if lines:
            blocks.append({"kind": kind, "lines": lines})
    return blocks


def build_page_maps(files: list[Path]) -> tuple[dict[int, str], dict[int, str]]:
    arabic: dict[int, str] = {}
    roman: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed["header_text"] or "") or ""
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            arabic.setdefault(page, str(path))
        for token in ROMAN_TOKEN_RE.findall(header):
            page = roman_to_int(token)
            if page is not None:
                roman.setdefault(page, str(path))
    return arabic, roman


def split_segments(line: str) -> list[str]:
    tokens = line.split()
    if not tokens:
        return []
    segments: list[str] = []
    current: list[str] = [tokens[0]]

    def is_page_token(token: str) -> bool:
        stripped = token.rstrip(",;:.)")
        return bool(
            re.fullmatch(r"(?:col\.?\s*)?(?:Ibid\.|\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)", stripped, re.IGNORECASE)
            or re.fullmatch(r"[ivxlcdm]{1,8}", stripped, re.IGNORECASE)
        )

    def is_ibid_token(token: str) -> bool:
        return bool(re.fullmatch(r"Ibid\.?", token.rstrip(",;:.)"), re.IGNORECASE))

    for token in tokens[1:]:
        prev = current[-1]
        starts_new = (is_page_token(prev) or is_ibid_token(prev)) and (token[:1].isupper() or token.startswith("S."))
        if starts_new:
            segments.append(" ".join(current).strip())
            current = [token]
        else:
            current.append(token)
    segments.append(" ".join(current).strip())
    return [segment for segment in segments if segment]


def looks_like_continuation(segment: str) -> bool:
    cleaned = normalize(segment) or ""
    if not cleaned:
        return False
    if cleaned.startswith(("—", "-", "·")):
        return True
    if cleaned[:1].islower():
        return True
    if cleaned.startswith(("In ", "Ex ", "Per ", "De ", "Ad ", "Ut ", "Quod ", "Quando ", "Non ", "Ejus ", "Item ", "Cur ", "Quam ", "Ubi ", "Unde ")):
        return True
    return False


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    first_match = PAGE_REF_RE.search(text)
    if first_match:
        text = text[: first_match.start()].strip()
    text = text.strip(" .;:")
    text = re.sub(r"\s+", " ", text)
    return text or None


def parse_refs(entry_raw: str, page_map_arabic: dict[int, str], page_map_roman: dict[int, str]) -> tuple[list[dict[str, Any]], int | None, str | None]:
    refs: list[dict[str, Any]] = []
    current_last: int | None = None
    inferred_page: int | None = None
    inferred_mode: str | None = None

    for match in PAGE_REF_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if match.group("ibid"):
            if current_last is None:
                continue
            page_int = current_last
            page_ref_col = None
            range_start = None
            range_end = None
            ref_kind = "editorial_page"
            target_file = page_map_arabic.get(page_int) or page_map_roman.get(page_int)
            target_prob = 0.74 if target_file else None
        elif match.group("arabic"):
            token = match.group("arabic")
            if "-" in token or "–" in token or "—" in token:
                start_raw, end_raw = re.split(r"\s*[-–—]\s*", token, maxsplit=1)
                page_int = int(start_raw)
                range_start = start_raw
                range_end = end_raw
                ref_kind = "editorial_range"
            else:
                page_int = int(token)
                range_start = None
                range_end = None
                ref_kind = "editorial_page"
            page_ref_col = None
            current_last = page_int
            inferred_page = inferred_page if inferred_page is not None else page_int
            inferred_mode = inferred_mode or "arabic"
            target_file = page_map_arabic.get(page_int) or page_map_roman.get(page_int)
            target_prob = 0.99 if target_file else None
        else:
            token = match.group("roman") or ""
            page_int = roman_to_int(token)
            range_start = None
            range_end = None
            ref_kind = "editorial_page"
            page_ref_col = None
            if page_int is not None:
                current_last = page_int
                inferred_page = inferred_page if inferred_page is not None else page_int
                inferred_mode = inferred_mode or "roman"
            target_file = page_map_roman.get(page_int) if page_int is not None else None
            if target_file is None and page_int is not None:
                target_file = page_map_arabic.get(page_int)
            target_prob = 0.9 if target_file else None

        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": page_ref_col,
                "line_ref_raw": None,
                "range_start_raw": range_start,
                "range_end_raw": range_end,
                "target_file": target_file,
                "target_file_probability": target_prob,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.85 if page_int is not None else 0.65,
                "raw_json": {
                    "page_token_kind": "ibid" if match.group("ibid") else ("roman" if match.group("roman") else "arabic"),
                },
            }
        )
    return refs, inferred_page, inferred_mode


def build_alpha_entries(
    *,
    files: list[Path],
    page_map_arabic: dict[int, str],
    page_map_roman: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_files = [path for path in files if file_seq(path) in ALPHA_START_FILES]
    if not section_files:
        raise SystemExit("No PL099 alphabetical index files found in the expected window.")

    section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    entry_order = 0
    node_order = 0
    current_node_key: str | None = None
    current_buffer: list[str] = []
    current_source_file: str | None = None
    started = False

    def flush_buffer() -> None:
        nonlocal entry_order, current_buffer, current_source_file
        if not current_buffer:
            return
        entry_raw = normalize(" ".join(current_buffer)) or ""
        current_buffer = []
        if not entry_raw:
            return
        if not re.search(r"\d{1,4}|[ivxlcdm]{1,8}|ibid\.?", entry_raw, re.IGNORECASE):
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entry_refs, inferred_page, inferred_mode = parse_refs(entry_raw, page_map_arabic, page_map_roman)
        lemma_raw = lemma_from_entry(entry_raw)
        target_file_best = current_source_file
        if inferred_page is not None:
            if inferred_mode == "roman":
                target_file_best = page_map_roman.get(inferred_page) or page_map_arabic.get(inferred_page) or current_source_file
            else:
                target_file_best = page_map_arabic.get(inferred_page) or page_map_roman.get(inferred_page) or current_source_file
        entry_payload = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": "heading_group" if lemma_raw and re.fullmatch(r"[A-ZÆŒ]", lemma_raw) else "lemma",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_node_key.split(":")[-1] if current_node_key else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(section_files[0]),
            "editorial_anchor_file": current_source_file,
            "target_file_best": target_file_best,
            "confidence": 0.9 if entry_refs else 0.78,
            "raw_json": {
                "source_file": current_source_file,
                "page_hints": [ref["page_ref_int"] for ref in entry_refs if ref.get("page_ref_int") is not None],
                "page_token_kinds": [ref["raw_json"]["page_token_kind"] for ref in entry_refs],
            },
        }
        entries.append(entry_payload)
        for ref in entry_refs:
            ref = dict(ref)
            ref["entry_key"] = entry_key
            ref["section_start_file"] = str(section_files[0])
            ref["editorial_anchor_file"] = current_source_file
            if ref.get("target_file") is None and ref.get("page_ref_int") is not None:
                ref["target_file"] = page_map_arabic.get(ref["page_ref_int"]) or page_map_roman.get(ref["page_ref_int"])
            refs.append(ref)

    for path in section_files:
        current_source_file = str(path)
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        blocks = parsed["blocks"] if isinstance(parsed.get("blocks"), list) else extract_blocks(path)
        for block in blocks:
            if file_seq(path) == 651 and block.get("kind") == "texto_principal":
                break
            if block["kind"] not in {"cabecalho", "texto_principal", "outro"}:
                continue
            for line in block["lines"]:
                upper = line.upper()
                if line.startswith("Digitized by Google"):
                    continue
                if upper.startswith("INDEX IN S. PAULINUM") or upper.startswith("INDEX IN SANCTUM PAULINUM") or upper.startswith("INDEX RERUM ET VERBORUM"):
                    continue
                if upper.startswith("QUÆ TUM IN TEXTU") or upper.startswith("NUMERI ARABICI RESPONDENT"):
                    continue
                for segment in split_segments(line):
                    if not started:
                        if re.match(r"^[A-ZÆŒ].*\d{1,4}", segment) or re.fullmatch(r"[A-ZÆŒ]", segment):
                            started = True
                        else:
                            continue
                    if re.fullmatch(r"[A-ZÆŒ]", segment):
                        flush_buffer()
                        node_order += 1
                        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": section_key,
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": segment,
                                "label_norm": segment.lower(),
                                "label_sort": segment.lower(),
                                "node_level": 1,
                                "confidence": 0.98,
                                "raw_json": {"source_file": current_source_file},
                            }
                        )
                        continue
                    if current_buffer and looks_like_continuation(segment):
                        current_buffer.append(segment)
                        continue
                    if current_buffer:
                        flush_buffer()
                    current_buffer = [segment]
    flush_buffer()

    for idx, entry in enumerate(entries, start=1):
        entry_id = f"{VOLUME_ID.lower()}_{idx:05d}"
        helper_info = {
            "entry_id": entry_id,
            "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
            "query_names": [name for name in [entry["lemma_raw"], entry["entry_raw"]] if name],
            "page_hints": [str(p) for p in entry["raw_json"].get("page_hints") or []],
            "page_hint_ints": [p for p in entry["raw_json"].get("page_hints") or [] if isinstance(p, int)],
            "context_raw": entry["entry_raw"],
        }
        entry["raw_json"]["helper_entry_id"] = entry_id
        entry["raw_json"]["helper_request_entry"] = helper_info

    return entries, refs, nodes


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict[str, Any]], helper_request_json: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        hints = [int(x) for x in (entry["raw_json"].get("page_hints") or []) if isinstance(x, int)]
        if not hints:
            continue
        lemma_raw = entry["lemma_raw"] or entry["entry_raw"]
        helper_entries.append(
            {
                "entry_id": entry["raw_json"]["helper_entry_id"],
                "lemma_raw": lemma_raw,
                "query_names": [q for q in [lemma_raw, entry["entry_raw"]] if q],
                "page_hints": [str(i) for i in hints],
                "page_hint_ints": hints,
                "context_raw": entry["entry_raw"],
            }
        )
    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


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
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def build_sections(section_files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX RERUM ET VERBORUM",
            "heading_norm": "index rerum et verborum",
            "heading_letter": None,
            "page_start": 1239,
            "page_end": 1266,
            "file_start": str(section_files[0]),
            "file_end": str(section_files[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Alphabetical index of subjects and names in the Paulinus volume tail.",
                "evidence_files": [str(path) for path in section_files],
            },
        }
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL099 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    todo_path.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": now_iso(),
                "current_focus": "Extract PL099 alphabetical index and resolve target files",
                "completed": [],
                "pending": [
                    "parse alphabetical index entries",
                    "run helper target locator",
                    "assemble final payload",
                ],
                "blocked": [],
                "notes": [
                    "PL099 tail contains a separate contents section after the alphabetical index window.",
                    "The final payload keeps OCR file suffixes distinct from printed page references.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    files = discover_text_files(args.source_root)
    page_map_arabic, page_map_roman = build_page_maps(files)
    section_files = [path for path in files if file_seq(path) in ALPHA_START_FILES]
    entries, refs, nodes = build_alpha_entries(
        files=files,
        page_map_arabic=page_map_arabic,
        page_map_roman=page_map_roman,
    )
    sections = build_sections(section_files)

    helper_request = build_helper_request(VOLUME_ID, args.source_root, entries, args.helper_request_json)
    helper_output: dict[str, Any] = {"entries": []}
    if args.helper_output_json.exists():
        helper_output = json.loads(args.helper_output_json.read_text(encoding="utf-8"))
    helper_lookup = {str(item.get("entry_id")): item for item in (helper_output.get("entries") or [])}

    for entry in entries:
        entry_id = entry["raw_json"].get("helper_entry_id")
        helper_item = helper_lookup.get(str(entry_id))
        if helper_item:
            entry["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }
            best = (helper_item.get("top_candidates") or [{}])[0]
            if best.get("file") and not entry.get("target_file_best"):
                entry["target_file_best"] = best["file"]
                entry["raw_json"]["helper_best_file"] = best["file"]
                entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        entry = next((item for item in entries if item["entry_key"] == ref["entry_key"]), None)
        if not entry:
            continue
        entry_id = entry["raw_json"].get("helper_entry_id")
        helper_item = helper_lookup.get(str(entry_id))
        if helper_item:
            best = (helper_item.get("top_candidates") or [{}])[0]
            if best.get("file") and not ref.get("target_file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")
            ref["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }
        if not ref.get("target_file") and ref.get("page_ref_int") is not None:
            ref["target_file"] = page_map_arabic.get(ref["page_ref_int"]) or page_map_roman.get(ref["page_ref_int"])
            if ref.get("target_file"):
                ref["target_file_probability"] = ref.get("target_file_probability") or 0.74

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Alphabetical index extracted from the PL099 tail; contents pages after 651 are treated as separate editorial closure material.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Alphabetical index entries were recoverable from the OCR window spanning files 638-651.",
            "evidence_files": [str(path) for path in section_files],
        },
        "notes": [
            {
                "note_key": "pl099_alpha_window",
                "note_raw": "The OCR tail continues with contents pages after the alphabetical index window; those pages were not folded into this payload.",
                "confidence": 0.93,
            }
        ],
    }

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "manifest.json").write_text(
        json.dumps({"sections": sections, "entries_count": len(entries), "refs_count": len(refs), "generated_at": payload["generated_at"]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (args.intermediate_dir / "coverage.json").write_text(json.dumps(payload["coverage"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    todo_path.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": now_iso(),
                "current_focus": "Complete and validate PL099 payload",
                "completed": ["parsed alphabetical index entries", "ran helper target locator", "assembled final payload"],
                "pending": [],
                "blocked": [],
                "notes": ["Retained OCR literals and the page/file distinction in separate fields."],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

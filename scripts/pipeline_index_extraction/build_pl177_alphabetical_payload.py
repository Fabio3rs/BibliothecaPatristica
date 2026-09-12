#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl177_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL177/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL177_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL177_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL177 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL177_alphabetical_indices.json

Build the PL177 alphabetical-index payload from the OCR tail, including the
`INDEX RERUM ANALYTICUS` section and the closing `ORDO RERUM` contents table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL177"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 177"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PL177_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL177_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL177"
DEFAULT_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL177_alphabetical_indices.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_SEQ = list(range(616, 624))
ORDO_SEQ = list(range(624, 646))

INDEX_SECTION_KEY = f"{VOLUME_ID}:section:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:section:ordo_rerum:002"

INDEX_HEADING_RAW = "INDEX RERUM ANALYTICUS."
ORDO_HEADING_RAW = "ORDO RERUM"

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_RE = re.compile(r"\b\d{1,4}\b")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ])")
NOISE_RE = re.compile(
    r"^(?:Digitized by Google|PATROL\.\s*CLXXVII\.?|INDEX RERUM ANALYTICUS\.?|ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)$",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value).lower()
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    def seq(path: Path) -> int:
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            return 10**9
        return int(m.group(1))

    return sorted(source_root.glob("*.txt"), key=seq)


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse sequence from {path}")
    return int(m.group(1))


def extract_header_page(path: Path) -> int | None:
    parsed = parse_ocr_page_xml(read_text(path))
    header = normalize(parsed.get("header_text") or "")
    matches = [int(m.group(0)) for m in PAGE_RE.finditer(header)]
    if not matches:
        return None
    return matches[0]


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        printed = extract_header_page(path)
        if printed is not None:
            page_map.setdefault(printed, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def page_hints(text: str) -> list[int]:
    seen: set[int] = set()
    hints: list[int] = []
    for match in PAGE_RE.finditer(text):
        value = int(match.group(0))
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def target_for_page(page: int | None, page_map: dict[int, str], source_root: Path) -> str | None:
    if page is None:
        return None
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(read_text(path))
        if needle.search(parsed.get("header_text") or "") or needle.search(parsed.get("body_text") or ""):
            return str(path)
    return None


def entry_lemma_from_fragment(fragment: str) -> str | None:
    clean = normalize(fragment)
    if not clean:
        return None
    m = PAGE_RE.search(clean)
    if m:
        clean = clean[: m.start()].strip(" ,;:.")
    clean = clean.strip(" ,;:.")
    return clean or None


def split_index_fragments(page_text: str) -> list[str]:
    page_text = re.sub(r"(?<=\w)-\s+", "", page_text)
    page_text = re.sub(r"\s+", " ", page_text).strip()
    if not page_text:
        return []
    return [frag.strip() for frag in SENTENCE_SPLIT_RE.split(page_text) if frag.strip()]


def make_index_section(
    files: list[Path],
    page_map: dict[int, str],
    source_root: Path,
    resolve_target,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    section_start_file = str(files[0]) if files else None
    current_letter: str | None = None
    letter_nodes: dict[str, str] = {}
    entry_order = 0

    for path in files:
        lines = extract_lines(path)
        page_parts: list[str] = []
        for line in lines:
            if NOISE_RE.fullmatch(line):
                continue
            if LETTER_RE.fullmatch(line):
                current_letter = line
                node_key = letter_nodes.get(line)
                if node_key is None:
                    node_key = f"{VOLUME_ID}:node:letter:{line}"
                    letter_nodes[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": INDEX_SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path), "kind": "alphabetic divider"},
                        }
                    )
                continue
            if re.fullmatch(r"[A-ZÆŒ]\s+", line):
                current_letter = line.strip()[0]
                continue
            if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                current_letter = line
                continue
            if line.startswith("INDEX RERUM ANALYTICUS") or line.startswith("PATROL. CLXXVII."):
                continue
            page_parts.append(line)

        page_text = " ".join(page_parts)
        fragments = split_index_fragments(page_text)
        for fragment in fragments:
            cleaned = normalize(fragment)
            if not cleaned:
                continue
            if NOISE_RE.fullmatch(cleaned):
                continue
            hints = page_hints(cleaned)
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            lemma_raw = entry_lemma_from_fragment(cleaned)
            entry_kind = "heading_group" if (not hints and len(cleaned) <= 3) else "lemma"
            inferred_page = hints[0] if hints else None
            target_file_best = resolve_target(inferred_page) if inferred_page else str(path)
            entry = {
                "entry_key": entry_key,
                "section_key": INDEX_SECTION_KEY,
                "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
                "inferred_printed_page": inferred_page,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": 0.82 if hints else 0.63,
                "raw_json": {
                    "source_file": str(path),
                    "page_hints": hints,
                    "section_kind": "analytic_subject",
                    "split_strategy": "sentence_boundary",
                },
            }
            entries.append(entry)
            if hints:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or cleaned[:120],
                        "query_names": [name for name in [lemma_raw, cleaned.split(",", 1)[0]] if name],
                        "page_hints": [str(h) for h in hints[:3]],
                        "page_hint_ints": hints[:3],
                        "context_raw": cleaned,
                    }
                )
            for ref_order, page in enumerate(hints, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(page),
                        "page_ref_raw": str(page),
                        "page_ref_int": page,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": resolve_target(page),
                        "target_file_probability": 0.99 if resolve_target(page) else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(path),
                        "confidence": 0.95 if resolve_target(page) else 0.7,
                        "raw_json": {
                            "source_file": str(path),
                            "locator_method": "header_page_map" if resolve_target(page) else "fallback_search",
                        },
                    }
                )

    return entries, refs, nodes, helper_entries


def make_ordo_section(
    files: list[Path],
    page_map: dict[int, str],
    source_root: Path,
    resolve_target,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    section_start_file = str(files[0]) if files else None
    entry_order = 0
    current_entry: dict[str, Any] | None = None

    def flush_entry(entry_text: str, source_path: Path) -> None:
        nonlocal entry_order, current_entry
        cleaned = normalize(entry_text)
        if not cleaned:
            return
        if NOISE_RE.fullmatch(cleaned):
            return
        hints = page_hints(cleaned)
        if not hints:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:ordo:{entry_order:04d}"
        lemma_raw = cleaned
        if "—" in cleaned:
            lemma_raw = normalize(cleaned.split("—", 1)[1].strip()) or cleaned
        elif "CAP." in cleaned:
            lemma_raw = normalize(re.sub(r"^CAP\.\s*[IVXLCDM]+\.\s*—?\s*", "", cleaned)) or cleaned
        entry = {
            "entry_key": entry_key,
            "section_key": ORDO_SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": "heading_group",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": cleaned,
            "context_raw": cleaned,
            "heading_letter": None,
            "inferred_printed_page": hints[0],
            "section_start_file": section_start_file,
            "editorial_anchor_file": str(source_path),
            "target_file_best": resolve_target(hints[0]),
            "confidence": 0.9,
            "raw_json": {
                "source_file": str(source_path),
                "page_hints": hints,
                "section_kind": "ordo_rerum",
                "split_strategy": "toc_line",
            },
        }
        entries.append(entry)
        for ref_order, page in enumerate(hints, start=1):
            ref_target = resolve_target(page)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page),
                    "page_ref_raw": str(page),
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": ref_target,
                    "target_file_probability": 0.99 if ref_target else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(source_path),
                    "confidence": 0.96 if ref_target else 0.72,
                    "raw_json": {
                        "source_file": str(source_path),
                        "locator_method": "header_page_map" if ref_target else "fallback_search",
                    },
                }
            )

    for path in files:
        lines = extract_lines(path)
        buffer: list[str] = []
        for line in lines:
            if NOISE_RE.fullmatch(line) or LETTER_RE.fullmatch(line):
                continue
            if line.startswith("ORDO RERUM") or line.startswith("QUAE IN HOC TOMO CONTINENTUR") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR"):
                continue
            if line.startswith("APPENDIX AD HUGONIS OPERA MYSTICA") or line.startswith("LIBER") or line.startswith("Praefatio") or line.startswith("Sermo ") or line.startswith("— "):
                if buffer:
                    flush_entry(" ".join(buffer), path)
                    buffer = []
                buffer.append(line)
                continue
            if line.startswith("CAP.") or re.match(r"^[IVXLCDM]+\.", line):
                if buffer:
                    flush_entry(" ".join(buffer), path)
                    buffer = []
                buffer.append(line)
                continue
            if buffer:
                buffer.append(line)
            else:
                buffer.append(line)
        if buffer:
            flush_entry(" ".join(buffer), path)

    return entries, refs


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    if not helper_request_json.exists():
        return {"status": "missing_request", "entries": []}
    request = json.loads(helper_request_json.read_text(encoding="utf-8"))
    if not request.get("entries"):
        helper_output_json.write_text(json.dumps({"status": "empty", "entries": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"status": "empty", "entries": []}
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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        if isinstance(item, dict) and item.get("entry_id"):
            result[str(item["entry_id"])] = item
    return result


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = helper_map(helper_output)
    for entry in entries:
        helper = by_id.get(entry["entry_key"])
        if not helper:
            continue
        raw_json = entry.setdefault("raw_json", {})
        best = helper.get("best_candidate") or {}
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in helper.get("candidates", [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    index_files = [p for p in files if file_seq(p) in INDEX_SEQ]
    ordo_files = [p for p in files if file_seq(p) in ORDO_SEQ]

    target_cache: dict[int, str | None] = {}

    def resolve_target(page: int | None) -> str | None:
        if page is None:
            return None
        if page not in target_cache:
            target_cache[page] = target_for_page(page, page_map, source_root)
        return target_cache[page]

    index_entries, index_refs, nodes, helper_entries = make_index_section(index_files, page_map, source_root, resolve_target)
    ordo_entries, ordo_refs = make_ordo_section(ordo_files, page_map, source_root, resolve_target)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries[:12],
    }
    write_json(helper_request_json, helper_request)
    helper_output = {"status": "skipped", "entries": []}
    if helper_output_json.exists():
        try:
            helper_output = json.loads(helper_output_json.read_text(encoding="utf-8"))
        except Exception:
            helper_output = {"status": "invalid", "entries": []}

    all_entries = index_entries + ordo_entries
    all_refs = index_refs + ordo_refs
    attach_helper(all_entries, helper_output)

    entry_by_key = {entry["entry_key"]: entry for entry in all_entries}
    for ref in all_refs:
        entry = entry_by_key.get(ref["entry_key"])
        if not entry:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    sections = [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": INDEX_HEADING_RAW,
            "heading_norm": sort_norm(INDEX_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1221,
            "page_end": 1238,
            "file_start": str(index_files[0]) if index_files else None,
            "file_end": str(index_files[-1]) if index_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytical index headed INDEX RERUM ANALYTICUS with letter-group dividers A-N and page-locator entries.",
                "evidence_files": [str(index_files[0]) if index_files else None, str(index_files[-1]) if index_files else None],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": sort_norm(ORDO_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1243,
            "page_end": 1264,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table for the tomus, separate from the alphabetical index.",
                "evidence_files": [str(ordo_files[0]) if ordo_files else None, str(ordo_files[-1]) if ordo_files else None],
            },
        },
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytical index and the closing ORDO RERUM table from the OCR tail. File 620 was kept with the index section because its body continues the L-M-N alphabetic run despite the malformed header OCR.",
        "evidence_files": [
            str(index_files[0]) if index_files else None,
            str(index_files[-1]) if index_files else None,
            str(ordo_files[0]) if ordo_files else None,
            str(ordo_files[-1]) if ordo_files else None,
        ],
    }

    notes = [
        "The OCR tail contains two editorial structures: the analytical index and the table of contents.",
        "Page-suffix order is not the same as editorial order; helper evidence was only attached to a small sample of entries.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", all_entries)
    write_json(intermediate_dir / "refs.json", all_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT),
            "entry_count": len(all_entries),
            "ref_count": len(all_refs),
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL170 ORDO RERUM alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl170_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL170/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL170_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL170_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL170 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL170_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL170"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 170"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL170/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL170_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL170_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL170_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL170"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUE IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum que in hoc tomo continentur"
SECTION_KIND_REASON = "Editorial contents table (ordo rerum) covering the volume's internal works and chapter listings; separate from alphabetical subject or onomastic index material."
SECTION_FILE_START_SEQ = 694
SECTION_FILE_END_SEQ = 704
SECTION_PAGE_START = 1379
SECTION_PAGE_END = 1400

NOISE_EXACT = {
    "Digitized by Google",
}
NOISE_PREFIXES = (
    "PATROL.",
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
)

PAGE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LEADING_PAGE_ONLY_RE = re.compile(r"^\d{3,4}(?:\s+\d{3,4})?$")
HEADING_LIKE_RE = re.compile(
    r"^(?:"
    r"LIBER\b|"
    r"PROLOGUS\b|"
    r"PRO[ŒE]MIUM\b|"
    r"OBSERVATIO\b|"
    r"NOTITIA\b|"
    r"ANNULUS\b|"
    r"VITA\b|"
    r"DE\b|"
    r"HISTORIA\b|"
    r"CARMEN\b|"
    r"MUNIO\b|"
    r"UDASCALCUS\b|"
    r"NARRATIO\b|"
    r"CAP\.\b|"
    r"§\s*[IVXLCDM0-9]+\b|"
    r"[IVXLCDM]+\.\s+—"
    r")",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def page_header_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in PAGE_NUMBER_RE.finditer(header):
            token = match.group(1)
            if token.startswith("0"):
                continue
            mapping.setdefault(int(token), str(path))
    return mapping


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if line in NOISE_EXACT:
            continue
        if any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        if LEADING_PAGE_ONLY_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(read_text(path))
        if needle.search(parsed.get("header_text") or ""):
            return str(path)
    return None


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_NUMBER_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_fragment(fragment: str) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if re.search(r"(?<!\d)\d{1,4}(?!\d)\s*\.?$", text):
        text = re.sub(r"\s+\d{1,4}\.?\s*$", "", text).strip()
    return text.strip(" ,;:.") or None


def is_heading_like(fragment: str) -> bool:
    text = normalize(fragment) or ""
    if not text:
        return False
    if any(text.startswith(prefix) for prefix in ("LIBER ", "PROLOGUS", "PROŒMIUM", "PROE MIUM", "OBSERVATIO", "NOTITIA", "ANNULUS", "VITA ", "HISTORIA ", "CARMEN ", "MUNIO ", "UDASCALCUS ", "NARRATIO ", "DE ", "§ ")):
        return True
    if re.match(r"^CAP\.\s", text):
        return True
    if re.match(r"^[IVXLCDM]+\.\s+—", text):
        return True
    if text.isupper() and len(text.split()) <= 8:
        return True
    return False


def split_fragments(buffer_text: str) -> list[str]:
    normalized = re.sub(r"(\d{1,4}\.)\s+(?=[A-Z§IVXLCDM])", r"\1\n", buffer_text)
    normalized = re.sub(r"(\d{1,4})\s+(?=[A-Z§IVXLCDM])", r"\1\n", normalized)
    pieces = [frag.strip() for frag in normalized.splitlines() if frag.strip()]
    return pieces or ([buffer_text.strip()] if buffer_text.strip() else [])


def parse_ordo_entries(files: list[Path], page_map: dict[int, str], source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    section_start_file = str(files[0]) if files else None

    page_chunks: list[str] = []
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        body = parsed.get("body_text") or ""
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if line in NOISE_EXACT:
                continue
            if line.startswith("PATROL."):
                continue
            page_chunks.append(line)

    full_text = normalize(" ".join(page_chunks)) or ""
    ordo_pos = full_text.find("ORDO RERUM")
    if ordo_pos >= 0:
        full_text = full_text[ordo_pos + len("ORDO RERUM"):].lstrip(" .")

    full_text = re.sub(r"(\d{1,4})\.?\s+(?=[A-Z§IVXLCDM])", r"\1.\n", full_text)
    full_text = re.sub(
        r"\.\s+(?=(?:LIBER\b|PROLOGUS\b|PRO[ŒE]MIUM\b|OBSERVATIO\b|NOTITIA\b|ANNULUS\b|VITA\b|DE\b|HISTORIA\b|CARMEN\b|MUNIO\b|UDASCALCUS\b|NARRATIO\b|CAP\.|§\s|[IVXLCDM]+\.\s+—))",
        ".\n",
        full_text,
    )

    fragments = [frag.strip() for frag in full_text.splitlines() if frag.strip()]
    entry_order = 0
    for fragment in fragments:
        cleaned = normalize(fragment) or ""
        if not cleaned:
            continue
        if cleaned in NOISE_EXACT:
            continue
        if any(cleaned.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        page_hints = extract_page_hints(cleaned)
        lemma_raw = lemma_from_fragment(cleaned)
        if not lemma_raw:
            continue
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        anchor_file = str(files[0]) if files else None
        target_file_best = target_for_page(page_hints[0], page_map, source_root) if page_hints else anchor_file
        entry_kind = "heading_group" if page_hints or is_heading_like(cleaned) else "editorial_note"
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
            "entry_raw": cleaned,
            "context_raw": cleaned,
            "heading_letter": None,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": anchor_file,
            "target_file_best": target_file_best,
            "confidence": 0.94 if page_hints else 0.78,
            "raw_json": {
                "page_hints": page_hints,
                "section_kind": "ordo_rerum",
                "split_strategy": "volume_text_sentence_and_page_boundary",
            },
        }
        entries.append(entry)
        if page_hints:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": [lemma_raw],
                    "page_hints": [str(page) for page in page_hints[:3]],
                    "page_hint_ints": page_hints[:3],
                    "context_raw": cleaned,
                }
            )
            for ref_order, page in enumerate(page_hints, start=1):
                target_file = target_for_page(page, page_map, source_root)
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
                        "target_file": target_file,
                        "target_file_probability": 0.99 if target_file else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": anchor_file,
                        "confidence": 0.95 if target_file else 0.68,
                        "raw_json": {
                            "locator_method": "header_page_map" if target_file else "unresolved",
                        },
                    }
                )
        else:
            notes.append(
                {
                    "entry_key": entry_key,
                    "reason": "heading without explicit editorial page number",
                    "source_file": anchor_file,
                }
            )

    return entries, refs, helper_entries, notes


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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = page_header_map(files)

    section_files = [path for path in files if SECTION_FILE_START_SEQ <= file_seq(path) <= SECTION_FILE_END_SEQ]
    entries, refs, helper_seed, notes = parse_ordo_entries(section_files, page_map, source_root)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_seed,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}

    helper_by_id = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict)
    }
    entry_map = {entry["entry_key"]: entry for entry in entries}
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": best if best else None,
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

    for ref in refs:
        helper = helper_by_id.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref.setdefault("raw_json", {})["helper_best_file"] = best.get("file")
            ref["raw_json"]["helper_best_probability"] = best.get("probability")

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": str(section_files[0]) if section_files else None,
            "file_end": str(section_files[-1]) if section_files else None,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "evidence_files": [
                    str(section_files[0]) if section_files else None,
                    str(section_files[-1]) if section_files else None,
                ],
            },
        }
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and serialized the page-numbered lines as editorial headings with material targets resolved from the page headers.",
        "evidence_files": [
            str(section_files[0]) if section_files else None,
            str(section_files[-1]) if section_files else None,
        ],
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            "PL170 ends with a closing ORDO RERUM / table-of-contents block rather than a separate alphabetical subject index.",
            f"Helper status: {helper_output.get('status', 'unknown')}.",
            "OCR file suffixes were treated separately from printed page numbers; target files were resolved from the page headers when possible.",
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PL170 ORDO RERUM payload and validate the contents-table entries against page headers.",
            "completed": [
                "confirmed the final OCR region is an ORDO RERUM contents table",
                "confirmed files 694-704 cover the editorial closure block",
                "built and ran the helper request",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page numbers.",
                "Only preserve helper evidence where it materially changes a locator choice.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL170 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL167 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl167_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL167/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL167_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL167_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL167 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL167_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL167"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 167"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL167/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL167_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL167_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL167_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL167"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
ASSEMBLE_SCRIPT = ROOT / "scripts/pipeline_index_extraction/assemble_alphabetical_payload.py"


INDEX_RANGE = (921, 940)
ORDO_RANGE = (941, 966)

HEADER_INDEX_RE = re.compile(r"INDEX RERUM AC VERBORUM", re.IGNORECASE)
HEADER_ORDO_RE = re.compile(r"^ORDO RERUM\b", re.IGNORECASE)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|PATROL\.\s*CLXVII\.?|ORDO RERUM(?:\s+QU[ÆAE\.£]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?)$", re.IGNORECASE)
REF_SPLIT_RE = re.compile(r"(?<=\d\.)\s+(?=[A-ZÆŒ])")
FALLBACK_TARGET_CACHE: dict[int, str | None] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value).replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def iter_ocr_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in iter_ocr_files(source_root):
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def discover_section_files(files: list[Path], start: int, end: int) -> list[Path]:
    return [path for path in files if start <= file_seq(path) <= end]


def fragment_is_complete(fragment: str) -> bool:
    cleaned = fragment.rstrip()
    return cleaned.endswith((".", "!", "?"))


def split_fragments(text: str, carry: str = "") -> tuple[list[str], str]:
    combined = normalize(f"{carry} {text}" if carry else text) or ""
    if not combined:
        return [], ""
    parts = [part.strip() for part in REF_SPLIT_RE.split(combined) if part.strip()]
    if not parts:
        return [], ""
    last = parts[-1]
    if fragment_is_complete(last):
        return parts, ""
    return parts[:-1], last


def entry_kind(section_kind: str, fragment: str, page_hints: list[int]) -> str:
    text = fragment.strip()
    if section_kind == "ordo_rerum":
        return "heading_group"
    if re.match(r"^(?:Vide|Vid\.|Voir|V\.|Cf\.|Id\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not page_hints and re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.|ibid\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not page_hints:
        return "editorial_note"
    return "lemma"


def lemma_from_fragment(fragment: str, kind: str) -> str | None:
    if kind in {"cross_reference", "editorial_note"}:
        return None
    text = normalize(fragment) or ""
    if not text:
        return None
    match = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
    if match:
        text = text[: match.start()].rstrip(" ,;:.")
    else:
        for marker in ("ibid.", "ibid", "id.", "id", "vide", "vid.", "voir", "cf."):
            pos = re.search(rf"\b{re.escape(marker)}\b", text, re.IGNORECASE)
            if pos:
                text = text[: pos.start()].rstrip(" ,;:.")
                break
    text = text.strip(" .;:")
    return text or None


def extract_page_hints(fragment: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", fragment):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def target_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    if page in FALLBACK_TARGET_CACHE:
        return FALLBACK_TARGET_CACHE[page]
    try:
        needle = re.compile(rf"(?<!\d){page}(?!\d)")
        for path in iter_ocr_files(source_root):
            raw = read_text(path)[:2000]
            if needle.search(raw):
                FALLBACK_TARGET_CACHE[page] = str(path)
                return str(path)
    except FileNotFoundError:
        FALLBACK_TARGET_CACHE[page] = None
        return None
    FALLBACK_TARGET_CACHE[page] = None
    return None


def make_helper_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
    page_hints = page_hints[:3]
    if not page_hints:
        return None
    lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:80]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma_raw,
        "query_names": [lemma_raw],
        "page_hints": [str(page) for page in page_hints],
        "page_hint_ints": page_hints,
        "context_raw": entry["entry_raw"],
    }


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    section1 = discover_section_files(files, *INDEX_RANGE)
    section2 = discover_section_files(files, *ORDO_RANGE)
    sections: list[dict[str, Any]] = []
    if section1:
        sections.append(
            {
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "analytic_subject",
                "heading_raw": "INDEX RERUM AC VERBORUM. QUÆ IN HOC PRIMO OPERUM RUPERTI VOLUMINE CONTINENTUR.",
                "heading_norm": sort_norm("INDEX RERUM AC VERBORUM. QUÆ IN HOC PRIMO OPERUM RUPERTI VOLUMINE CONTINENTUR."),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": str(section1[0]),
                "file_end": str(section1[-1]),
                "confidence": 0.99,
                "raw_json": {
                    "section_kind_reason": "Subject index headed INDEX RERUM AC VERBORUM; alphabetical subject entries with page locators.",
                    "evidence_files": [str(section1[0]), str(section1[-1])],
                },
            }
        )
    if section2:
        sections.append(
            {
                "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 2,
                "section_kind": "ordo_rerum",
                "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": sort_norm("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": str(section2[0]),
                "file_end": str(section2[-1]),
                "confidence": 0.99,
                "raw_json": {
                    "section_kind_reason": "Closing ORDO RERUM contents table at the end of the volume tail.",
                    "evidence_files": [str(section2[0]), str(section2[-1])],
                },
            }
        )
    return sections


def build_entries(
    files: list[Path],
    section_key: str,
    section_kind: str,
    page_map: dict[int, str],
    helper_map: dict[str, Any],
    source_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    carry = ""
    section_start_file = str(files[0]) if files else None

    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        body_text = normalize(parsed.get("body_text") or "") or ""
        fragments, carry = split_fragments(body_text, carry=carry)
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or NOISE_RE.fullmatch(cleaned):
                continue
            page_hints = extract_page_hints(cleaned)
            kind = entry_kind(section_kind, cleaned, page_hints)
            if kind == "editorial_note" and not page_hints and not re.search(r"\b(?:ibid\.?|vide|vid\.|voir|cf\.|id\.)\b", cleaned, re.IGNORECASE):
                continue

            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{section_key.split(':')[-1]}:{entry_order:04d}"
            lemma_raw = lemma_from_fragment(cleaned, kind)
            lemma_norm = sort_norm(lemma_raw)
            target_file_best = None
            if page_hints:
                target_file_best = target_for_page(page_hints[0], page_map, source_root)
            if target_file_best is None:
                target_file_best = str(path)

            helper_info = helper_map.get(entry_key)
            raw_json: dict[str, Any] = {
                "source_file": str(path),
                "page_hints": page_hints,
                "split_strategy": "page_ref_sentence_boundary",
            }
            confidence = 0.88 if page_hints else 0.66
            if helper_info:
                best = helper_info.get("best_candidate") or {}
                raw_json["helper"] = {
                    "status": helper_info.get("status"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                    "best_candidate": best if best else None,
                    "top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                        }
                        for cand in (helper_info.get("candidates") or [])[:3]
                    ],
                }
                if best.get("file") and best.get("file") != target_file_best:
                    raw_json["helper_disagrees_with_header_map"] = True
                    raw_json["helper_best_file"] = best.get("file")
                confidence = max(confidence, float(best.get("probability") or 0.0), 0.78)

            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_norm,
                "lemma_sort": lemma_norm,
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": round(confidence, 4),
                "raw_json": raw_json,
            }
            entries.append(entry)

            if page_hints:
                seen_pages: set[int] = set()
                for ref_order, page in enumerate(page_hints, start=1):
                    if page in seen_pages:
                        continue
                    seen_pages.add(page)
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
                            "target_file_probability": 0.98 if target_file else None,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": str(path),
                            "confidence": 0.94 if target_file else 0.66,
                            "raw_json": {
                                "source_file": str(path),
                                "locator_method": "header_page_map" if target_file else "unresolved",
                            },
                        }
                    )

    if carry.strip():
        cleaned = normalize(carry) or ""
        if cleaned and not NOISE_RE.fullmatch(cleaned):
            page_hints = extract_page_hints(cleaned)
            kind = entry_kind(section_kind, cleaned, page_hints)
            if kind != "editorial_note" or page_hints or re.search(r"\b(?:ibid\.?|vide|vid\.|voir|cf\.|id\.)\b", cleaned, re.IGNORECASE):
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{section_key.split(':')[-1]}:{entry_order:04d}"
                lemma_raw = lemma_from_fragment(cleaned, kind)
                lemma_norm = sort_norm(lemma_raw)
                target_file_best = str(files[-1]) if files else None
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": lemma_norm,
                    "lemma_sort": lemma_norm,
                    "entry_raw": cleaned,
                    "context_raw": cleaned,
                    "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
                    "inferred_printed_page": page_hints[0] if page_hints else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(files[-1]) if files else None,
                    "target_file_best": target_file_best,
                    "confidence": 0.62,
                    "raw_json": {
                        "source_file": str(files[-1]) if files else None,
                        "page_hints": page_hints,
                        "split_strategy": "carry_from_previous_file",
                    },
                }
                entries.append(entry)

    return entries, refs


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    sampled: list[dict[str, Any]] = []
    for entry in entries:
        helper_entry = make_helper_entry(entry)
        if helper_entry is None:
            continue
        sampled.append(helper_entry)
        if len(sampled) >= 28:
            break
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(DEFAULT_SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": sampled,
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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        mapping[item.get("entry_id")] = item
    return mapping


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = iter_ocr_files(source_root)
    section1_files = discover_section_files(files, *INDEX_RANGE)
    section2_files = discover_section_files(files, *ORDO_RANGE)
    page_map = extract_page_map(source_root)

    sections = build_sections(files)
    section1_entries, section1_refs = build_entries(section1_files, sections[0]["section_key"], sections[0]["section_kind"], page_map, {}, source_root)
    section2_entries, section2_refs = build_entries(section2_files, sections[1]["section_key"], sections[1]["section_kind"], page_map, {}, source_root)

    all_entries = section1_entries + section2_entries
    helper_request = build_helper_request(all_entries)
    write_json(helper_request_json, helper_request)

    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}
    helper_map = helper_map_by_id(helper_output)

    # Rebuild entries with helper evidence folded into raw_json.
    section1_entries, section1_refs = build_entries(section1_files, sections[0]["section_key"], sections[0]["section_kind"], page_map, helper_map, source_root)
    section2_entries, section2_refs = build_entries(section2_files, sections[1]["section_key"], sections[1]["section_kind"], page_map, helper_map, source_root)
    entries = section1_entries + section2_entries
    refs = section1_refs + section2_refs

    evidence_files = list(dict.fromkeys(
        [str(section1_files[0]), str(section1_files[-1]), str(section2_files[0]), str(section2_files[-1])]
    ))

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the closing INDEX RERUM AC VERBORUM subject index and the trailing ORDO RERUM table of contents from the OCR tail; page locators were mapped conservatively to the corresponding OCR files in the full volume.",
        "evidence_files": evidence_files,
    }
    notes = [
        "PL167 ends with a long subject index headed INDEX RERUM AC VERBORUM and then a closing ORDO RERUM table.",
        "OCR files 967-970 are garbage tail fragments and were not serialized because they do not add recoverable index content.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]

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
        "notes": notes,
    }

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
            "current_focus": "Finalize PL167 alphabetical payload and preserve OCR page anchors literally.",
            "completed": [
                "identified the INDEX RERUM AC VERBORUM section",
                "identified the closing ORDO RERUM section",
                "assembled helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals and page/file numbering separate.",
            ],
        },
    )

    # Re-run helper evidence attachment once more in case the first pass succeeded but the
    # second build used the helper map to annotate raw_json entries.
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL167 alphabetical payload.")
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

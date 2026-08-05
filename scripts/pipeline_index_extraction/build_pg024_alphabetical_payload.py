#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg024_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG024/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG024_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG024_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG024 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG024_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import bisect
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
VOLUME_ID = "PG024"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca XXIV"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG024/text"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG024_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG024_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG024"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG024_alphabetical_indices.json"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_ANALYTIC = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX RERUM ET VERBORUM.",
    "heading_norm": "index rerum et verborum",
    "heading_letter": None,
    "page_start": 979,
    "page_end": 1002,
    "file_start": None,
    "file_end": None,
    "confidence": 0.98,
    "raw_json": {
        "section_kind_reason": (
            "Final analytical subject index headed INDEX RERUM ET VERBORUM; "
            "the heading recurs across the OCR tail and the material closes before ORDO RERUM."
        ),
        "evidence": [
            "91 INDEX RERUM ET VERBORUM. 92",
            "INDEX RERUM ET VERBORUM.",
            "1001 INDEX RERUM ET VERBORUM. 1002",
        ],
    },
}

SECTION_ORDO = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "volume_id": VOLUME_ID,
    "work_key": None,
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": 1003,
    "page_end": 1004,
    "file_start": None,
    "file_end": None,
    "confidence": 0.99,
    "raw_json": {
        "section_kind_reason": "Editorial contents-order closure page, distinct from the alphabetical subject index.",
        "evidence": [
            "1003 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 1004",
            "ORDO RERUM",
        ],
    },
}

BLOCK_RE = re.compile(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
PAGE_REF_RE = re.compile(
    r"(?<!\d)(\d{1,4})(?:\s*(?:[-–—]|à)\s*(\d{1,4}))?(?:\s*(?:et\s+seqq?\.?|et\s+seq\.?|ibid\.?|id\.?))?",
    re.I,
)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ENTRY_HEADING_RE = re.compile(r"INDEX\s+RERUM\s+ET\s+VERBORUM|ORDO\s+RERUM", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_lines(path: Path) -> tuple[str, list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    header_parts: list[str] = []
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        tipo = (match.group(1) or "").strip().lower()
        body = match.group(2) or ""
        if tipo == "cabecalho":
            header_parts.append(normalize(re.sub(r"<[^>]+>", " ", body)))
            continue
        if tipo != "texto_principal":
            continue
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return normalize(" ".join(part for part in header_parts if part)) or "", lines


def extract_page_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", text):
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        header, _ = extract_lines(path)
        for page in extract_page_numbers(header):
            mapping.setdefault(page, path.as_posix())
    return mapping


def nearest_page_file(page: int, page_map: dict[int, str], sorted_pages: list[int]) -> tuple[str | None, str, int | None]:
    if page in page_map:
        return page_map[page], "exact", page
    if not sorted_pages:
        return None, "missing", None
    idx = bisect.bisect_left(sorted_pages, page)
    candidates: list[int] = []
    if idx < len(sorted_pages):
        candidates.append(sorted_pages[idx])
    if idx > 0:
        candidates.append(sorted_pages[idx - 1])
    if not candidates:
        return None, "missing", None
    best_page = min(candidates, key=lambda value: (abs(value - page), value))
    return page_map.get(best_page), "nearest", best_page


def count_page_refs(text: str) -> int:
    return sum(1 for _ in PAGE_REF_RE.finditer(text))


def split_short_line(line: str) -> list[str]:
    text = line
    text = re.sub(r";\s+", ";\n", text)
    text = re.sub(r"(?<=\d)\.\s+(?=[A-ZÆŒ(])", ".\n", text)
    text = re.sub(r"(?<=\d)\s+(?=[A-ZÆŒ(])", "\n", text)
    return [part.strip() for part in text.splitlines() if part.strip()]


def should_split_line(line: str) -> bool:
    return len(line) <= 150 and count_page_refs(line) <= 2


def infer_letter(text: str) -> str | None:
    cleaned = normalize(text)
    if not cleaned:
        return None
    for ch in cleaned:
        if ch.isalpha():
            upper = ch.upper()
            if upper == "Æ":
                return "A"
            if upper == "Œ":
                return "O"
            return upper
    return None


def extract_lemma(fragment: str) -> str | None:
    text = normalize(fragment)
    if not text:
        return None
    match = PAGE_REF_RE.search(text)
    if match:
        text = text[: match.start()]
    text = text.strip(" ,;:.")
    if not text:
        return None
    for tail in ("ibid.", "ibid", "id.", "id"):
        if text.lower().endswith(tail):
            text = text[: -len(tail)].strip(" ,;:.")
    return text or None


def extract_refs(fragment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None, str]] = set()
    for match in PAGE_REF_RE.finditer(fragment):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        raw = normalize(match.group(0))
        key = (start, end, raw)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_int": start,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
            }
        )
    return refs


def make_query_names(lemma_raw: str, entry_raw: str) -> list[str]:
    candidates = [
        lemma_raw,
        re.sub(r"\s+", " ", re.sub(r"[^\w\sÆŒæœ]", " ", lemma_raw)).strip(),
        entry_raw[:160],
    ]
    names: list[str] = []
    for candidate in candidates:
        candidate = normalize(candidate)
        if candidate and candidate not in names:
            names.append(candidate)
    return names[:4]


def source_files_for_section(source_root: Path) -> list[Path]:
    return [path for path in discover_files(source_root) if 494 <= file_seq(path) <= 505]


def build_payload(source_root: Path, helper_output_json: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    all_files = discover_files(source_root)
    page_map = build_page_map(all_files)
    sorted_pages = sorted(page_map)
    helper_output = read_json(helper_output_json, {"entries": []}) or {"entries": []}
    helper_map = {entry.get("entry_id"): entry for entry in helper_output.get("entries", []) if entry.get("entry_id")}

    section_files = source_files_for_section(source_root)
    section_start_file = section_files[0].as_posix() if section_files else None
    section_end_file = section_files[-1].as_posix() if section_files else None
    SECTION_ANALYTIC["file_start"] = section_start_file
    SECTION_ANALYTIC["file_end"] = section_end_file
    SECTION_ORDO["file_start"] = section_end_file
    SECTION_ORDO["file_end"] = section_end_file

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_request_entries: list[dict[str, Any]] = []
    node_keys: dict[str, str] = {}
    current_letter: str | None = None
    entry_order = 0
    node_order = 0
    line_counter = 0
    for path in section_files:
        header, lines = extract_lines(path)
        for line in lines:
            line_counter += 1
            if ENTRY_HEADING_RE.search(line) and not re.match(r"^[A-ZÆŒ]\b", line):
                continue
            if LETTER_RE.fullmatch(line):
                letter = line
                if letter not in node_keys:
                    node_order += 1
                    node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:node:{node_order:03d}:{letter}"
                    node_keys[letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": SECTION_ANALYTIC["section_key"],
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": letter,
                            "label_norm": letter.lower(),
                            "label_sort": letter.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": path.as_posix(), "line_kind": "explicit_letter"},
                        }
                    )
                current_letter = letter
                continue
            fragments = split_short_line(line) if should_split_line(line) else [line]
            if current_letter is None:
                current_letter = infer_letter(fragments[0]) or "A"
                if current_letter not in node_keys:
                    node_order += 1
                    node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:node:{node_order:03d}:{current_letter}"
                    node_keys[current_letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": SECTION_ANALYTIC["section_key"],
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter.lower(),
                            "label_sort": current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.95,
                            "raw_json": {"source_file": path.as_posix(), "line_kind": "inferred_letter"},
                        }
                    )

            pending_prefix: list[str] = []
            last_page_hint: int | None = None
            for frag_index, fragment in enumerate(fragments, start=1):
                fragment = normalize(fragment)
                if not fragment:
                    continue
                refs_for_fragment = extract_refs(fragment)
                if refs_for_fragment:
                    if pending_prefix:
                        fragment = normalize(" ".join([*pending_prefix, fragment]))
                        pending_prefix = []
                    # Recompute refs after prefix merge just in case spacing changed.
                    refs_for_fragment = extract_refs(fragment)
                    last_page_hint = refs_for_fragment[-1]["page_ref_int"] if refs_for_fragment else last_page_hint
                elif pending_prefix:
                    pending_prefix.append(fragment)
                    continue
                elif last_page_hint is None:
                    pending_prefix.append(fragment)
                    continue
                else:
                    refs_for_fragment = [
                        {
                            "ref_raw": str(last_page_hint),
                            "page_ref_int": last_page_hint,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "ref_kind": "editorial_page",
                            "inherited": True,
                        }
                    ]

                lemma_raw = extract_lemma(fragment) or fragment
                if not lemma_raw:
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:alpha:analytic_subject:001:entry:{entry_order:04d}"
                page_hints = [ref["page_ref_int"] for ref in refs_for_fragment if ref.get("page_ref_int") is not None]
                helper_entry = {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": make_query_names(lemma_raw, fragment),
                    "page_hints": [str(value) for value in page_hints[:6]],
                    "page_hint_ints": page_hints[:6],
                    "context_raw": fragment[:180],
                }
                helper_request_entries.append(helper_entry)

                helper_entry_result = helper_map.get(entry_key) or {}
                best = helper_entry_result.get("best_candidate") or {}
                if not best and helper_entry_result.get("candidates"):
                    best = helper_entry_result["candidates"][0] or {}
                helper_best_file = best.get("file")
                helper_best_prob = best.get("probability")
                helper_status = helper_entry_result.get("status")
                helper_reason = helper_entry_result.get("reason_summary")

                ref_records: list[dict[str, Any]] = []
                ref_source_info: list[dict[str, Any]] = []
                for ref_order, ref in enumerate(refs_for_fragment, start=1):
                    ref_raw = ref["ref_raw"]
                    page_int = ref["page_ref_int"]
                    target_file, mapping_kind, mapped_page = nearest_page_file(page_int, page_map, sorted_pages)
                    if ref.get("inherited"):
                        mapping_kind = "inherited"
                    confidence = 0.96 if mapping_kind == "exact" else 0.83
                    if mapping_kind == "inherited":
                        confidence = 0.78
                    ref_records.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref_raw,
                            "page_ref_raw": ref_raw,
                            "page_ref_int": page_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": target_file,
                            "target_file_probability": 0.96 if mapping_kind == "exact" else (0.78 if mapping_kind == "inherited" else 0.72),
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": confidence,
                            "raw_json": {
                                "mapping_kind": mapping_kind,
                                "mapped_page": mapped_page,
                                "inherited_from_previous_ref": bool(ref.get("inherited")),
                            },
                        }
                    )
                    ref_source_info.append(
                        {
                            "ref_order": ref_order,
                            "page_ref_int": page_int,
                            "target_file": target_file,
                            "mapping_kind": mapping_kind,
                        }
                    )

                if ref_records:
                    refs.extend(ref_records)

                target_best = helper_best_file or (ref_records[0]["target_file"] if ref_records else path.as_posix())
                target_prob = helper_best_prob if helper_best_prob is not None else (ref_records[0]["target_file_probability"] if ref_records else 0.6)
                entry_confidence = 0.93 if ref_records else 0.62
                if helper_status and helper_status != "ok":
                    entry_confidence -= 0.05
                if any(info["mapping_kind"] != "exact" for info in ref_source_info):
                    entry_confidence -= 0.03
                entry_confidence = round(max(0.55, min(entry_confidence, 0.98)), 2)
                entry_kind = "lemma"
                if frag_index > 1 and len(fragments) > 1:
                    entry_kind = "sublemma"
                if re.match(r"^(?:vide|vid\.|voir|cf\.|id\.)\b", lemma_raw, re.I):
                    entry_kind = "cross_reference"
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": SECTION_ANALYTIC["section_key"],
                        "parent_node_key": node_keys.get(current_letter),
                        "entry_order": entry_order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": normalize(lemma_raw).lower() or None,
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": fragment,
                        "context_raw": None,
                        "heading_letter": current_letter,
                        "inferred_printed_page": page_hints[0] if page_hints else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": path.as_posix(),
                        "target_file_best": target_best,
                        "confidence": entry_confidence,
                        "raw_json": {
                            "source_file": path.as_posix(),
                            "line_number": line_counter,
                            "fragment_index": frag_index,
                            "fragment_count": len(fragments),
                            "split_mode": "short_line" if should_split_line(line) else "whole_line",
                            "helper": {
                                "status": helper_status,
                                "candidate_role": best.get("candidate_role"),
                                "reason_summary": helper_reason,
                                "best_candidate": {
                                    "file": helper_best_file,
                                    "probability": helper_best_prob,
                                    "evidence_kinds": [
                                        ev.get("kind")
                                        for ev in (best.get("evidence") or [])
                                        if isinstance(ev, dict) and ev.get("kind")
                                    ][:8],
                                }
                                if best
                                else None,
                            },
                            "ref_source_info": ref_source_info,
                        },
                    }
                )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    return {
        "page_map": page_map,
        "sorted_pages": sorted_pages,
        "sections": [SECTION_ANALYTIC, SECTION_ORDO],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "helper_request": helper_request,
        "section_files": section_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PG024 alphabetical index payload.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract PG024 final analytic index and ORDO RERUM closure from the OCR tail.",
        "completed": [
            "tail OCR inspected",
            "index section boundaries confirmed",
        ],
        "pending": [
            "run index_target_locator on helper request",
            "assemble final payload",
            "validate JSON shape",
        ],
        "blocked": [],
        "notes": [
            "Use page-map lookups for exact page headers and nearest-header fallback for OCR drift.",
            "Preserve the OCR literal in entry_raw; split only short multi-entry lines.",
        ],
    }
    write_json(TODO_JSON, todo)

    built = build_payload(args.source_root, args.helper_output_json)
    write_json(args.helper_request_json, built["helper_request"])

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(args.helper_request_json),
            "--output",
            str(args.helper_output_json),
            "--pretty",
        ],
        check=True,
    )

    built = build_payload(args.source_root, args.helper_output_json)
    section_files = built["section_files"]
    sections = built["sections"]
    if section_files:
        sections[0]["file_start"] = section_files[0].as_posix()
        sections[0]["file_end"] = section_files[-2].as_posix() if len(section_files) > 1 else section_files[0].as_posix()
        sections[1]["file_start"] = section_files[-1].as_posix()
        sections[1]["file_end"] = section_files[-1].as_posix()

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Final analytic subject index recovered from the OCR tail pages and modeled conservatively from line-level fragments.",
            ],
        },
        "sections": sections,
        "nodes": built["nodes"],
        "entries": built["entries"],
        "refs": built["refs"],
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": (
                "Analytical subject index entries were recovered from the OCR tail (pages 494-504), "
                "with ORDO RERUM kept as a separate editorial closure section."
            ),
            "evidence_files": [path.as_posix() for path in section_files],
        },
        "notes": [
            "The OCR tail contains repeated INDEX RERUM ET VERBORUM headings and one ORDO RERUM closure page; both were kept distinct.",
            "Short multi-entry lines were split conservatively; long thematic lines were preserved as single fragments.",
            "Page anchors use exact header matches when available and nearest-header fallback when the OCR page number is missing from the header map.",
        ],
    }

    write_json(args.output_file, payload)

    final_check = json.loads(args.output_file.read_text(encoding="utf-8"))
    if final_check.get("sections") is None or final_check.get("entries") is None:
        raise SystemExit("Final payload missing required top-level keys")
    print(
        json.dumps(
            {
                "status": "ok",
                "volume_id": VOLUME_ID,
                "written_file": str(args.output_file),
                "entries": len(payload["entries"]),
                "refs": len(payload["refs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

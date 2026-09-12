#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl144_alphabetical_payload.py prepare \
    --source-root /homessddata/Projects/pdfocr/teste/PL144/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL144_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL144

  python scripts/pipeline_index_extraction/build_pl144_alphabetical_payload.py finalize \
    --source-root /homessddata/Projects/pdfocr/teste/PL144/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL144_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL144 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL144_alphabetical_indices.json

Builds the PL144 alphabetical-index helper request from the OCR tail and then
finalizes the canonical payload after helper-backed target resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml  # noqa: E402

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL144"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 144"
SOURCE_ROOT_DEFAULT = ROOT / "teste/PL144/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL144_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PL144_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL144_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL144"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

MAIN_FILES = list(range(523, 534))
ORDO_FILES = list(range(534, 538))

INDEX_HEADING = "INDEX RERUM NOTABILIUM"
ORDO_HEADING = "ORDO RERUM"

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
NUMBER_RE = re.compile(r"\b\d{1,4}\b")
SPACE_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "").replace("\xa0", " ")
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = SPACE_RE.sub(" ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize_text(text)
    return value.lower() if value else None


def page_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=page_seq)


def page_map_for_source(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in discover_files(source_root):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        for match in NUMBER_RE.finditer(header):
            value = int(match.group(0))
            if value not in mapping:
                mapping[value] = str(path)
    return mapping


def line_list(text: str) -> list[str]:
    out: list[str] = []
    for raw_line in (text or "").splitlines():
        line = normalize_text(raw_line)
        if line and line != "Digitized by Google":
            out.append(line)
    return out


def load_page_content(path: Path) -> dict[str, Any]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    notes = line_list(parsed.get("notes_text") or "")
    body = line_list(parsed.get("body_text") or "")
    header = normalize_text(parsed.get("header_text") or "")
    footer = normalize_text(parsed.get("footer_text") or "")
    return {
        "file": str(path),
        "seq": page_seq(path),
        "header": header,
        "notes": notes,
        "body": body,
        "footer": footer,
    }


def is_header_or_title(line: str) -> bool:
    if not line:
        return False
    norm = normalize_text(line)
    if INDEX_HEADING in norm or ORDO_HEADING in norm:
        return True
    if norm in {
        "QUAE IN DUOBUS PRIMIS B. PETRI DAMIANI OPERUM TOMIS SEU PARTIBUS CONTINENTUR.",
        "QUAE IN HOC TOMO CONTINENTUR.",
        "SANCTUS PETRUS DAMIANUS SANCTAE ROMANAE ECCLESIAE CARDINALIS.",
        "AD OPERA SANCTI PETRI DAMIANI PROLEGOMENA.",
        "EPISTOLARUM LIBRI OCTO.",
        "LIBER PRIMUS.",
        "LIBER SECUNDUS.",
        "LIBER TERTIUS.",
        "LIBER QUARTUS.",
        "LIBER QUINTUS.",
        "LIBER SEXTUS.",
        "LIBER SEPTIMUS.",
        "LIBER OCTAVUS.",
        "S. PETRI DAMIANI SERMONES.",
        "SANCTORUM HISTORIAE.",
        "SANTORUM HISTORIAE.",
    }:
        return True
    if norm.isupper() and len(norm) > 1 and not any(ch.isdigit() for ch in norm):
        return True
    return False


def extract_printed_pages(line: str, section_kind: str) -> list[int]:
    text = line.strip()
    pages: list[int] = []
    for match in NUMBER_RE.finditer(text):
        value = int(match.group(0))
        if value <= 0 or value >= 10000:
            continue
        start = match.start()
        end = match.end()
        prefix = text[:start]
        suffix = text[end:]
        if section_kind == "analytic_subject":
            if re.match(r"^\d+\.\s+[A-ZÆŒ]", text) and text.count(".") >= 2:
                first_number = NUMBER_RE.search(text)
                if first_number and first_number.start() == start:
                    continue
        if prefix.rstrip().endswith("t") and suffix.lstrip().startswith("."):
            continue
        if value not in pages:
            pages.append(value)
    return pages


def lemma_from_line(line: str) -> str:
    text = line.strip()
    if re.match(r"^\d+\.\s+[A-ZÆŒ]", text):
        text = re.sub(r"^\d+\.\s+", "", text, count=1)
    first = NUMBER_RE.search(text)
    if not first:
        return text.strip(" ,;:.")
    return SPACE_RE.sub(" ", text[: first.start()].strip(" ,;:."))


def query_names_for(lemma_raw: str, entry_raw: str) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned:
        candidates.append(cleaned)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    prefix = re.split(r"[;,]", cleaned, maxsplit=1)[0].strip()
    if prefix and prefix not in candidates:
        candidates.append(prefix)
    entry_prefix = re.split(r"[.;]", entry_raw, maxsplit=1)[0].strip()
    if entry_prefix and entry_prefix not in candidates:
        candidates.append(entry_prefix)
    return candidates[:4]


def make_entry_id(section_tag: str, counter: int) -> str:
    return f"{VOLUME_ID}:{section_tag}:{counter:04d}"


def build_todo(intermediate_dir: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PL144 alphabetical payload from OCR tail and helper output",
        "completed": [
            "inspected index tail pages",
            "separated INDEX RERUM NOTABILIUM from ORDO RERUM",
        ],
        "pending": [
            "run index_target_locator on helper request",
            "finalize payload with helper evidence",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals and page drift separate from printed page references.",
            "Main index section spans files 523-533; ORDO RERUM spans files 534-537.",
        ],
    }


def detect_section_for_seq(seq: int) -> str | None:
    if seq in MAIN_FILES:
        return "main"
    if seq in ORDO_FILES:
        return "ordo"
    return None


def extract_volume_state(source_root: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    pages = [load_page_content(path) for path in files if detect_section_for_seq(page_seq(path))]
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    current_node_key: str | None = None
    node_counter = 0
    entry_counter = {"main": 0, "ordo": 0}

    section_defs = {
        "main": {
            "section_key": f"{VOLUME_ID}:section:main",
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": INDEX_HEADING,
            "page_start": 1039,
            "page_end": 1056,
            "file_start": None,
            "file_end": None,
            "notes": "Main subject index recovered from files 523-533.",
        },
        "ordo": {
            "section_key": f"{VOLUME_ID}:section:ordo",
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING,
            "page_start": 1057,
            "page_end": 1064,
            "file_start": None,
            "file_end": None,
            "notes": "Ordo rerum recovered from files 534-537.",
        },
    }

    first_file_for_section: dict[str, str] = {}
    last_file_for_section: dict[str, str] = {}

    for page in pages:
        section = detect_section_for_seq(page["seq"])
        if not section:
            continue
        first_file_for_section.setdefault(section, page["file"])
        last_file_for_section[section] = page["file"]

        if section == "main":
            for note in page["notes"]:
                if LETTER_RE.fullmatch(note):
                    if note != current_letter:
                        node_counter += 1
                        current_letter = note
                        current_node_key = f"{VOLUME_ID}:node:{note}:{node_counter:03d}"
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": section_defs[section]["section_key"],
                                "parent_node_key": None,
                                "node_order": node_counter,
                                "node_kind": "letter_group",
                                "label_raw": note,
                                "label_norm": normalize_text(note),
                                "label_sort": sort_norm(note),
                                "node_level": 1,
                                "confidence": 0.93,
                                "raw_json": {"source": "marginal_note"},
                            }
                        )
        else:
            current_letter = None
            current_node_key = None

        for line in page["body"]:
            if is_header_or_title(line):
                continue
            if section == "main" and LETTER_RE.fullmatch(line):
                if line != current_letter:
                    node_counter += 1
                    current_letter = line
                    current_node_key = f"{VOLUME_ID}:node:{line}:{node_counter:03d}"
                    nodes.append(
                        {
                            "node_key": current_node_key,
                            "section_key": section_defs[section]["section_key"],
                            "parent_node_key": None,
                            "node_order": node_counter,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": normalize_text(line),
                            "label_sort": sort_norm(line),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source": "body_letter"},
                        }
                    )
                continue

            if section == "main" and not NUMBER_RE.search(line):
                if entries and (line[:1].islower() or line.startswith(("ibid", "ibid.", "et ", "ejus", "ejus", "quod", "quom", "quomodo", "quae", "qui "))):
                    prev = entries[-1]
                    prev["entry_raw"] = f"{prev['entry_raw']} {line}".strip()
                    prev["context_raw"] = f"{prev['context_raw']}\n{line}".strip()
                    prev["confidence"] = min(0.9, float(prev["confidence"]) + 0.02)
                    prev["raw_json"]["continuation_lines"].append(line)
                    prev["raw_json"]["line_count"] += 1
                continue

            if section == "ordo" and not NUMBER_RE.search(line) and not line.endswith("."):
                # Preserve only lines that look like structural headings.
                if line.isupper():
                    entry_counter[section] += 1
                    entry_key = make_entry_id("ordo", entry_counter[section])
                    entry = {
                        "entry_key": entry_key,
                        "section_key": section_defs[section]["section_key"],
                        "parent_node_key": None,
                        "entry_order": entry_counter[section],
                        "entry_kind": "heading_group",
                        "lemma_raw": line,
                        "lemma_display": line,
                        "lemma_norm": normalize_text(line),
                        "lemma_sort": sort_norm(line),
                        "entry_raw": line,
                        "context_raw": line,
                        "heading_letter": None,
                        "inferred_printed_page": None,
                        "section_start_file": first_file_for_section[section],
                        "editorial_anchor_file": page["file"],
                        "target_file_best": None,
                        "confidence": 0.72,
                        "raw_json": {
                            "section_kind": section_defs[section]["section_kind"],
                            "line_index": len(entries) + 1,
                            "source_page_seq": page["seq"],
                            "line_class": "heading_only",
                            "page_hints": [],
                            "continuation_lines": [],
                            "line_count": 1,
                        },
                    }
                    entries.append(entry)
                continue

            pages_for_line = extract_printed_pages(line, section_defs[section]["section_kind"])
            if not pages_for_line and section == "main" and entries and line[:1].islower():
                prev = entries[-1]
                prev["entry_raw"] = f"{prev['entry_raw']} {line}".strip()
                prev["context_raw"] = f"{prev['context_raw']}\n{line}".strip()
                prev["confidence"] = min(0.9, float(prev["confidence"]) + 0.02)
                prev["raw_json"]["continuation_lines"].append(line)
                prev["raw_json"]["line_count"] += 1
                continue

            entry_counter[section] += 1
            entry_key = make_entry_id(section, entry_counter[section])
            lemma_raw = lemma_from_line(line)
            entry_raw = line
            entry_kind = "lemma" if section == "main" else "heading_group"
            confidence = 0.78 if pages_for_line else 0.64
            entry = {
                "entry_key": entry_key,
                "section_key": section_defs[section]["section_key"],
                "parent_node_key": current_node_key if section == "main" else None,
                "entry_order": entry_counter[section],
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw if lemma_raw else None,
                "lemma_display": lemma_raw if lemma_raw else None,
                "lemma_norm": normalize_text(lemma_raw) if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
                "entry_raw": entry_raw,
                "context_raw": line,
                "heading_letter": current_letter if section == "main" else None,
                "inferred_printed_page": pages_for_line[0] if pages_for_line else None,
                "section_start_file": first_file_for_section[section],
                "editorial_anchor_file": page["file"],
                "target_file_best": None,
                "confidence": confidence,
                "raw_json": {
                    "section_kind": section_defs[section]["section_kind"],
                    "line_index": len(entries) + 1,
                    "source_page_seq": page["seq"],
                    "line_class": "entry_line",
                    "page_hints": pages_for_line,
                    "continuation_lines": [],
                    "line_count": 1,
                },
            }
            entries.append(entry)
            if pages_for_line:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw if lemma_raw else line,
                        "query_names": query_names_for(lemma_raw if lemma_raw else line, line),
                        "page_hints": [str(p) for p in pages_for_line],
                        "page_hint_ints": pages_for_line,
                        "context_raw": line,
                    }
                )

    for section_name, section_def in section_defs.items():
        section_def["file_start"] = first_file_for_section.get(section_name)
        section_def["file_end"] = last_file_for_section.get(section_name)
        sections.append(
            {
                "section_key": section_def["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section_def["section_order"],
                "section_kind": section_def["section_kind"],
                "heading_raw": section_def["heading_raw"],
                "heading_norm": normalize_text(section_def["heading_raw"]),
                "heading_letter": None,
                "page_start": section_def["page_start"],
                "page_end": section_def["page_end"],
                "file_start": section_def["file_start"],
                "file_end": section_def["file_end"],
                "confidence": 0.96 if section_name == "main" else 0.95,
                "raw_json": {
                    "section_kind_reason": section_def["notes"],
                    "file_range": [section_def["file_start"], section_def["file_end"]],
                },
            }
        )

    return {
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "helper_entries": helper_entries,
    }


def build_request(source_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": state["helper_entries"],
    }


def prepare(source_root: Path, helper_request_json: Path, intermediate_dir: Path) -> None:
    state = extract_volume_state(source_root)
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    })
    write_json(intermediate_dir / "sections.json", state["sections"])
    write_json(intermediate_dir / "nodes.json", state["nodes"])
    write_json(intermediate_dir / "entries.json", state["entries"])
    write_json(intermediate_dir / "refs.json", [])
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", {})
    write_json(intermediate_dir / "notes.json", [
        "Main analytical index and ORDO RERUM were extracted from the OCR tail.",
        "Helper request includes every entry line with material page hints.",
    ])
    write_json(intermediate_dir / "manifest.json", {"updated_at": now_iso(), "generated_at": now_iso()})
    write_json(TODO_JSON, build_todo(intermediate_dir))
    write_json(helper_request_json, build_request(source_root, state))


def helper_by_entry(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if entry_id:
            out[str(entry_id)] = item
    return out


def best_candidate_file(helper_entry: dict[str, Any] | None) -> tuple[str | None, float | None, dict[str, Any]]:
    if not helper_entry:
        return None, None, {}
    best = helper_entry.get("best_candidate") or {}
    file = best.get("file")
    prob = best.get("probability")
    helper_json = {
        "status": helper_entry.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "top_candidates": [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "inferred_printed_page": cand.get("inferred_printed_page"),
                "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or [])[:4]],
            }
            for cand in (helper_entry.get("candidates") or [])[:5]
        ],
        "debug": helper_entry.get("debug"),
    }
    return file, prob, helper_json


def finalize(source_root: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> None:
    helper_output = read_json(helper_output_json)
    if helper_output is None:
        raise SystemExit(f"helper output not found: {helper_output_json}")
    state = {
        "sections": read_json(intermediate_dir / "sections.json", []),
        "nodes": read_json(intermediate_dir / "nodes.json", []),
        "entries": read_json(intermediate_dir / "entries.json", []),
        "refs": read_json(intermediate_dir / "refs.json", []),
        "scripture_refs": read_json(intermediate_dir / "scripture_refs.json", []),
        "coverage": read_json(intermediate_dir / "coverage.json", {}),
        "notes": read_json(intermediate_dir / "notes.json", []),
        "volume": read_json(intermediate_dir / "volume.json", {}),
    }
    page_map = page_map_for_source(source_root)
    helper_lookup = helper_by_entry(helper_output)

    refs: list[dict[str, Any]] = []
    for entry in state["entries"]:
        helper_entry = helper_lookup.get(entry["entry_key"])
        best_file, best_prob, helper_json = best_candidate_file(helper_entry)
        page_hints = entry["raw_json"].get("page_hints") or []
        page_map_hits = [page_map.get(int(p)) for p in page_hints if page_map.get(int(p))]
        fallback_file = page_map_hits[0] if page_map_hits else None
        entry["target_file_best"] = best_file or fallback_file
        if best_prob is not None:
            entry["confidence"] = min(0.97, max(float(entry["confidence"]), float(best_prob)))
        entry["raw_json"]["helper"] = helper_json
        entry["raw_json"]["helper"]["page_map_hits"] = page_map_hits
        for idx, page_hint in enumerate(page_hints, start=1):
            target_file = page_map.get(int(page_hint)) or best_file or fallback_file
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_hint),
                    "page_ref_raw": str(page_hint),
                    "page_ref_int": int(page_hint),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": best_prob,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": entry["confidence"],
                    "raw_json": {
                        "helper": helper_json,
                        "page_map_hit": page_map.get(int(page_hint)),
                    },
                }
            )

    state["refs"] = refs
    state["coverage"] = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the main alphabetical index and ORDO RERUM from the OCR tail with conservative line grouping and helper-backed target resolution.",
        "evidence_files": [
            state["sections"][0]["file_start"],
            state["sections"][0]["file_end"],
            state["sections"][1]["file_start"],
            state["sections"][1]["file_end"],
        ],
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": state["volume"],
        "sections": state["sections"],
        "nodes": state["nodes"],
        "entries": state["entries"],
        "refs": state["refs"],
        "scripture_refs": state["scripture_refs"],
        "coverage": state["coverage"],
        "notes": state["notes"],
    }
    write_json(output_file, payload)
    write_json(intermediate_dir / "coverage.json", state["coverage"])
    write_json(TODO_JSON, {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload finalized",
        "completed": [
            "helper request built",
            "helper output integrated",
            "final payload written",
        ],
        "pending": [],
        "blocked": [],
        "notes": [],
    })


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL144 alphabetical index payload.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    prepare_ap = sub.add_parser("prepare")
    prepare_ap.add_argument("--source-root", type=Path, default=SOURCE_ROOT_DEFAULT)
    prepare_ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    prepare_ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)

    finalize_ap = sub.add_parser("finalize")
    finalize_ap.add_argument("--source-root", type=Path, default=SOURCE_ROOT_DEFAULT)
    finalize_ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    finalize_ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    finalize_ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)

    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.source_root, args.helper_request_json, args.intermediate_dir)
    elif args.cmd == "finalize":
        finalize(args.source_root, args.helper_output_json, args.intermediate_dir, args.output_file)


if __name__ == "__main__":
    main()

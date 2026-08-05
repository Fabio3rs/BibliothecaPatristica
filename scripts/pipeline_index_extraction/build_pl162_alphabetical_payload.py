#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl162_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL162/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL162 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_alphabetical_indices.json \
    --mode request

  python scripts/pipeline_index_extraction/build_pl162_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL162/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL162 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL162_alphabetical_indices.json \
    --mode final

Build the PL162 alphabetical index payload from the OCR tail window and a small
helper request used to resolve a few locator-sensitive entries.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PL162"
COLLECTION = "PL"
VOLUME_LABEL = "PL162"
SECTION_KEY = f"{VOLUME_ID}:section:1"
HEADING_RAW = "INDEX IN EPISTOLAS , SERMONES , ET CHRONICON IVONIS"
HELPER_ENTRY_ID_BY_LEMMA = {
    "A latere papae": "pl162_a_latere_papae",
    "Philippus Francorum rex": "pl162_philippus_francorum_rex",
    "Hugo Lugdunensis archiepiscopus": "pl162_hugo_lugdunensis_archiepiscopus",
    "Gaufridus episcopus Carnotensis depositus": "pl162_gaufridus_episcopus_carnotensis_depositus",
}

INDEX_START_RE = re.compile(
    r"INDEX\s+IN\s+EPISTOLAS\s*,\s*SERMONES\s*,\s*ET\s+CHRONICON\s+IVONIS",
    re.IGNORECASE,
)
STOP_RE = re.compile(r"ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-Z]$")
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])|(?<=\d)\s+(?=[A-ZÆŒ])")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
HELPER_CROSS_RE = re.compile(r"^(?:Vide|Vid\.|Voir|V\.|cf\.|id\.)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_files(source_root: Path) -> list[Path]:
    files = []
    for path in sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1])):
        suffix = int(path.stem.rsplit("-", 1)[-1])
        if 809 <= suffix <= 816:
            files.append(path)
    return files


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, re.I | re.S):
        block_type = match.group(1)
        body = re.sub(r"<[^>]+>", " ", match.group(2))
        body = body.replace("\r", "\n")
        body = re.sub(r"\n{2,}", "\n", body)
        blocks.append((block_type, body))
    return blocks


def extract_index_lines(files: list[Path]) -> tuple[str, list[dict[str, Any]]]:
    index_started = False
    current: list[str] = []
    current_letter = None
    entries: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current, current_letter, entries
        if not current or current_letter is None:
            current = []
            return
        raw = norm(" ".join(current))
        current = []
        if not raw:
            return
        if raw == current_letter:
            return
        entries.append(
            {
                "letter": current_letter,
                "text": raw,
                "file": str(active_file),
            }
        )

    active_file = files[0]
    heading_raw = HEADING_RAW

    for path in files:
        active_file = path
        for block_type, body in extract_blocks(path):
            if block_type != "texto_principal":
                continue
            for raw_line in body.splitlines():
                line = norm(raw_line)
                if not line:
                    continue
                if STOP_RE.search(line):
                    flush()
                    return heading_raw, entries
                if line == "Digitized by Google":
                    continue
                if line == "INDEX":
                    continue
                if line == "-----------------------------------------------------------------------":
                    continue
                if LETTER_RE.fullmatch(line):
                    flush()
                    current_letter = line
                    index_started = True
                    continue
                if not index_started and INDEX_START_RE.search(line):
                    continue
                if not index_started:
                    continue
                current.append(line)
    flush()
    return heading_raw, entries


def split_segments(text: str) -> list[str]:
    parts = [part.strip() for part in SPLIT_RE.split(text) if part and part.strip()]
    merged: list[str] = []
    for part in parts:
        if merged:
            prev = merged[-1]
            prev_has_page = bool(PAGE_RE.search(prev))
            part_has_page = bool(PAGE_RE.search(part))
            if not prev_has_page and not part_has_page and HELPER_CROSS_RE.match(part):
                merged[-1] = f"{prev} {part}".strip()
                continue
            if not prev_has_page and not part_has_page and len(part) <= 18:
                merged[-1] = f"{prev} {part}".strip()
                continue
        merged.append(part)
    return merged


def parse_refs(text: str, entry_key: str, section_start_file: str, editorial_anchor_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    order = 0
    for match in PAGE_RE.finditer(text):
        ref_raw = match.group(0).strip()
        key = (ref_raw, int(match.group(1)), match.group(2))
        if key in seen:
            continue
        seen.add(key)
        order += 1
        start = int(match.group(1))
        end = match.group(2)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_kind": "editorial_range" if end else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end else None,
                "range_end_raw": str(int(end)) if end else None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": 0.82,
                "raw_json": {
                    "resolver_status": "unresolved",
                    "evidence": "material locator not resolved in this pass",
                },
            }
        )
    return refs


def parse_entries(files: list[Path]) -> dict[str, Any]:
    heading_raw, raw_lines = extract_index_lines(files)

    split_entries: list[dict[str, Any]] = []
    for line_info in raw_lines:
        for segment in split_segments(line_info["text"]):
            split_entries.append({"letter": line_info["letter"], "text": segment, "file": line_info["file"]})

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    current_letter = None

    for segment in split_entries:
        text = norm(segment["text"]) or ""
        if not text:
            continue
        letter = segment["letter"]
        if letter and letter != current_letter:
            current_letter = letter
            if letter not in letter_to_node:
                node_key = f"{VOLUME_ID}:section:1:node:{len(nodes)+1:03d}"
                letter_to_node[letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": letter,
                        "label_norm": letter.lower(),
                        "label_sort": letter.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": segment["file"]},
                    }
                )

        if text == letter:
            continue

        if text.startswith("INDEX") or text == "Digitized by Google":
            continue

        entry_key = f"{VOLUME_ID}:entry:{len(entries)+1:04d}"
        page_refs = [m.group(0).strip() for m in PAGE_RE.finditer(text)]
        entry_kind = "cross_reference" if HELPER_CROSS_RE.search(text) and not page_refs else "lemma"
        if not page_refs and HELPER_CROSS_RE.search(text):
            entry_kind = "cross_reference"
        if not page_refs and not HELPER_CROSS_RE.search(text):
            entry_kind = "editorial_note"

        lemma_raw = text
        if "," in lemma_raw:
            lemma_raw = lemma_raw.split(",", 1)[0].strip()
        elif "." in lemma_raw and (entry_kind == "cross_reference" or lemma_raw.count(".") <= 1):
            lemma_raw = lemma_raw.split(".", 1)[0].strip()
        lemma_raw = lemma_raw.strip(" ,;")
        if not lemma_raw:
            lemma_raw = text

        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": letter_to_node.get(letter),
            "entry_order": len(entries) + 1,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw if entry_kind != "editorial_note" else None,
            "lemma_display": lemma_raw if entry_kind != "editorial_note" else None,
            "lemma_norm": sort_norm(lemma_raw) if entry_kind != "editorial_note" else None,
            "lemma_sort": sort_norm(lemma_raw) if entry_kind != "editorial_note" else None,
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": letter,
            "inferred_printed_page": None,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": segment["file"],
            "target_file_best": None,
            "confidence": 0.84 if page_refs else 0.7,
            "raw_json": {
                "ocr_file": segment["file"],
                "segment_text": text,
                "page_refs": page_refs,
                "entry_kind_reason": "Cross-reference without material locator" if entry_kind == "cross_reference" and not page_refs else "Alphabetical index entry",
            },
        }
        entries.append(entry)
        refs.extend(parse_refs(text, entry_key, str(files[0]), segment["file"]))

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": heading_raw,
        "heading_norm": sort_norm(heading_raw),
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Alphabetical subject index covering epistles, sermons, and chronicle material.",
            "evidence_files": [str(path) for path in files],
            "observed_headings": [heading_raw],
            "notes": [
                "The alphabetical index starts in file 809 and continues through file 816.",
                "The ORDO RERUM material that follows in file 816 is editorial contents and is excluded from this alphabetical section.",
            ],
        },
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(files[0].parent),
        "volume_label": VOLUME_LABEL,
        "notes": "Alphabetical subject index for epistles, sermons, and chronicle material.",
    }

    return {
        "heading_raw": heading_raw,
        "section": section,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "volume": volume,
    }


def build_helper_request(parsed: dict[str, Any]) -> dict[str, Any]:
    helper_entries = [
        {
            "entry_id": "pl162_a_latere_papae",
            "lemma_raw": "A latere papae",
            "query_names": ["A latere papae", "Lateranensis", "papae"],
            "page_hints": ["17", "31", "137"],
            "page_hint_ints": [17, 31, 137],
            "context_raw": "A latere papae, 17, 31, 137.",
        },
        {
            "entry_id": "pl162_philippus_francorum_rex",
            "lemma_raw": "Philippus Francorum rex",
            "query_names": [
                "Philippus Francorum rex",
                "Lupus rapax dictus",
                "Ejus mores improbi graviter reprehensi",
            ],
            "page_hints": ["5", "6", "7", "60", "90", "567"],
            "page_hint_ints": [5, 6, 7, 60, 90, 567],
            "context_raw": "Philippus Francorum rex, 5, 6, 7, 60, 90, 567. Ejus mores improbi graviter reprehensi a papa Gregorio septimo 136 148. Lupus rapax dictus,",
        },
        {
            "entry_id": "pl162_hugo_lugdunensis_archiepiscopus",
            "lemma_raw": "Hugo Lugdunensis archiepiscopus",
            "query_names": [
                "Hugo Lugdunensis archiepiscopus",
                "Hugo Lugdunensis",
                "legatus papæ",
            ],
            "page_hints": ["135", "145", "148", "157"],
            "page_hint_ints": [135, 145, 148, 157],
            "context_raw": "Hugo Lugdunensis archiepiscopus, legatus papæ, 135. Ejus fastus et superbum imperium reprehensum, 135, 145, 148. A Victore papa excommunicatus, 157.",
        },
        {
            "entry_id": "pl162_gaufridus_episcopus_carnotensis_depositus",
            "lemma_raw": "Gaufridus episcopus Carnotensis depositus",
            "query_names": [
                "Gaufridus episcopus Carnotensis depositus",
                "Gaufridus Carnotensis",
                "depositus",
            ],
            "page_hints": ["1", "4", "5", "7", "22", "126"],
            "page_hint_ints": [1, 4, 5, 7, 22, 126],
            "context_raw": "Gaufridus episcopus Carnotensis depositus, 1, 4, 5, 7, 22, 126. Romæ accusatus, 126. Exepiscopus, 140.",
        },
    ]
    return {
        "volume_id": VOLUME_ID,
        "source_root": parsed["volume"]["source_root"],
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(parsed: dict[str, Any], helper_output: dict[str, Any] | None) -> dict[str, Any]:
    helper_by_entry_id: dict[str, Any] = {}
    if helper_output:
        for item in helper_output.get("entries", []):
            helper_by_entry_id[item.get("entry_id")] = item

    if helper_by_entry_id:
        for entry in parsed["entries"]:
            entry_id = HELPER_ENTRY_ID_BY_LEMMA.get(entry.get("lemma_raw") or "")
            if not entry_id:
                continue
            helper_item = helper_by_entry_id.get(entry_id)
            if not helper_item:
                continue
            best = helper_item.get("best_candidate") or {}
            entry["target_file_best"] = best.get("file")
            entry["confidence"] = max(float(entry.get("confidence") or 0.0), float(best.get("probability") or 0.0), 0.9)
            entry.setdefault("raw_json", {})["helper_resolution"] = {
                "entry_id": entry_id,
                "status": helper_item.get("status"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                },
            }

    notes = [
        "The alphabetical index is preserved as a single analytic_subject section; file 816 also contains excluded ORDO RERUM material.",
        "Material locators remain explicit in refs, but unresolved target files are left null where the OCR tail alone does not sustain a safe anchor.",
    ]
    if helper_output:
        notes.append("Helper output was consulted for locator-sensitive entries and retained in raw_json only where relevant.")

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": parsed["volume"],
        "sections": [parsed["section"]],
        "nodes": parsed["nodes"],
        "entries": parsed["entries"],
        "refs": parsed["refs"],
        "scripture_refs": parsed["scripture_refs"],
        "coverage": {
            "entries_status": "recovered_with_residual_ambiguity",
            "entries_status_reason": "Recovered the alphabetical subject index from OCR files 809-816; the line items and page references are serialized, while several material target files remain unresolved because the tail window alone does not safely identify every source page.",
            "evidence_files": [str(path) for path in discover_files(Path(parsed["volume"]["source_root"]))],
        },
        "notes": notes,
    }
    if helper_output:
        payload["notes"].append(f"Helper status: {helper_output.get('status', 'unknown')}.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--helper-request-json", required=True)
    parser.add_argument("--helper-output-json", required=True)
    parser.add_argument("--intermediate-dir", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--mode", choices=["request", "final"], required=True)
    args = parser.parse_args()

    source_root = Path(args.source_root)
    helper_request_json = Path(args.helper_request_json)
    helper_output_json = Path(args.helper_output_json)
    intermediate_dir = Path(args.intermediate_dir)
    output_file = Path(args.output_file)

    files = discover_files(source_root)
    parsed = parse_entries(files)

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract PL162 alphabetical index and keep unresolved locators explicit.",
        "completed": [
            "OCR tail window identified",
            "alphabetical section parsed",
            "helper request prepared",
        ],
        "pending": [
            "run index_target_locator.py",
            "assemble final payload",
        ],
        "blocked": [
            "some material target files remain unresolved from the tail window alone",
        ],
        "notes": [
            "Exclude the ORDO RERUM material appended inside file 816.",
            "Keep OCR literals and page references separate from file suffixes.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)
    write_json(intermediate_dir / "parsed.json", parsed)

    if args.mode == "request":
        write_json(helper_request_json, build_helper_request(parsed))
        return

    helper_output = load_json(helper_output_json) if helper_output_json.exists() else None
    payload = build_payload(parsed, helper_output)
    write_json(output_file, payload)


if __name__ == "__main__":
    main()

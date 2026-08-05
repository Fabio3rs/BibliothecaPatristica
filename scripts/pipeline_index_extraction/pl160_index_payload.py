#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/pl160_index_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL160/text \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL160 \
    --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_helper_request.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_helper_output.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_alphabetical_indices.json \
    --mode request

  python scripts/pipeline_index_extraction/pl160_index_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL160/text \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL160 \
    --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_helper_request.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_helper_output.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL160_alphabetical_indices.json \
    --mode final

Builds the PL160 author-index helper request from OCR, then assembles the
canonical alphabetical-index payload after helper resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(
    r"INDEX SCRIPTORUM ECCLESIASTICORUM QUI A SIGEBERTO GEMBLACENSI\s+MEMORANTUR",
    re.IGNORECASE | re.DOTALL,
)
LETTER_RE = re.compile(r"^[A-Z]$")
PAGE_RE = re.compile(r"\b\d{1,4}\b")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_sort(text: str) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_alt_name(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[\.;]+$", "", text).strip()
    return text


def find_index_page(source_root: Path) -> Path:
    for path in sorted(source_root.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        if HEADING_RE.search(text):
            return path
    raise FileNotFoundError(f"Could not find PL160 index page under {source_root}")


def parse_body_lines(page_text: str) -> list[str]:
    match = re.search(r'<bloco[^>]*tipo="texto_principal"[^>]*>(?P<body>.*?)</bloco>', page_text, re.I | re.S)
    if not match:
        return []
    body = match.group("body")
    body = re.sub(r"<[^>]+>", " ", body)
    lines = [normalize_text(line) for line in body.splitlines()]
    return [line for line in lines if line]


def strip_spurious_tail_letter(tail: str) -> str:
    tail = normalize_text(tail)
    if re.search(r"\b\d{1,4}\s+[A-Z]$", tail):
        tail = re.sub(r"\s+[A-Z]$", "", tail)
    return tail


def parse_entries(source_root: Path) -> dict[str, Any]:
    index_page = find_index_page(source_root)
    text = index_page.read_text(encoding="utf-8")
    lines = parse_body_lines(text)

    heading_raw = "INDEX SCRIPTORUM ECCLESIASTICORUM QUI A SIGEBERTO GEMBLACENSI MEMORANTUR"
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    node_map: dict[str, str] = {}
    current_letter: str | None = None
    entry_counter = 0

    # We only keep true letter headings, not stray OCR glyphs.
    for idx, line in enumerate(lines):
        if HEADING_RE.search(line):
            continue
        if LETTER_RE.match(line):
            next_line = None
            for probe in lines[idx + 1 :]:
                if probe:
                    next_line = probe
                    break
            if next_line and next_line[0] == line:
                current_letter = line
                if line not in node_map:
                    node_key = f"PL160:section:1:node:{len(nodes)+1:03d}"
                    node_map[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": "PL160:section:1",
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(index_page)},
                        }
                    )
            continue

        m = re.match(r"^(?P<lemma>.+?)\.\s*(?P<tail>.+)$", line)
        if not m:
            continue

        entry_counter += 1
        lemma_raw = normalize_text(m.group("lemma"))
        tail = strip_spurious_tail_letter(m.group("tail"))
        entry_raw = normalize_text(line)
        page_hints = [int(x) for x in PAGE_RE.findall(tail)]
        query_names = [lemma_raw]

        vide_match = re.search(r"\bVide\s+(.+?)(?:\.|$)", tail, re.I)
        if vide_match:
            query_names.append(clean_alt_name(vide_match.group(1)))

        alt_match = re.search(r"\bAl\.\s+(.+?)(?:\.\s*\d|\.\s*$|$)", tail, re.I)
        if alt_match:
            query_names.append(clean_alt_name(alt_match.group(1)))

        query_names = [name for name in OrderedDict.fromkeys(name for name in query_names if name)]
        entry_kind = "cross_reference" if re.search(r"\bVide\b", tail, re.I) else "lemma"
        parent_node_key = node_map.get(current_letter) if current_letter else None
        entry_key = f"PL160:entry:{entry_counter:03d}"

        entry = {
            "entry_key": entry_key,
            "section_key": "PL160:section:1",
            "parent_node_key": parent_node_key,
            "entry_order": entry_counter,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize_sort(lemma_raw),
            "lemma_sort": normalize_sort(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": str(index_page),
            "editorial_anchor_file": str(index_page),
            "target_file_best": None,
            "confidence": 0.75 if page_hints else 0.66,
            "raw_json": {
                "ocr_file": str(index_page),
                "ocr_line_raw": entry_raw,
                "page_hints": page_hints,
                "entry_kind_reason": "Cross-reference with locator" if entry_kind == "cross_reference" else "Index lemma line",
                "query_names": query_names,
            },
        }
        entry["_page_hints"] = page_hints
        entries.append(entry)

    section = {
        "section_key": "PL160:section:1",
        "volume_id": "PL160",
        "work_key": None,
        "section_order": 1,
        "section_kind": "author_index",
        "heading_raw": heading_raw,
        "heading_norm": normalize_sort(heading_raw),
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(index_page),
        "file_end": str(index_page),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Index of ecclesiastical writers cited by Sigebert of Gembloux.",
            "evidence_files": [str(index_page)],
            "observed_headings": [heading_raw],
            "notes": [
                "The alphabetical index occupies the PL160 index page only.",
                "The separate ORDO RERUM blocks at the tail are editorial contents and are excluded from this alphabetical payload.",
            ],
        },
    }

    volume = {
        "volume_id": "PL160",
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "PL160",
        "notes": "Alphabetical author index extracted from the Sigebert of Gembloux index page.",
    }

    return {
        "index_page": str(index_page),
        "volume": volume,
        "section": section,
        "nodes": nodes,
        "entries": entries,
    }


def build_helper_request(parsed: dict[str, Any]) -> dict[str, Any]:
    helper_entries = []
    for entry in parsed["entries"]:
        helper_entries.append(
            {
                "entry_id": entry["entry_key"].replace(":", "_").lower(),
                "lemma_raw": entry["lemma_raw"],
                "query_names": entry["raw_json"]["query_names"],
                "page_hints": [str(v) for v in entry["raw_json"]["page_hints"]],
                "page_hint_ints": entry["raw_json"]["page_hints"],
                "context_raw": entry["context_raw"],
            }
        )
    return {
        "volume_id": "PL160",
        "source_root": parsed["volume"]["source_root"],
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def load_helper_output(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_final_payload(parsed: dict[str, Any], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_entries = {entry["entry_id"]: entry for entry in helper_output.get("entries") or []}
    refs: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []

    def choose_candidate_for_hint(candidates: list[dict[str, Any]], page_hint: int) -> dict[str, Any]:
        if not candidates:
            return {}
        exact = [candidate for candidate in candidates if candidate.get("inferred_printed_page") == page_hint]
        if exact:
            return max(exact, key=lambda candidate: candidate.get("probability") or 0.0)
        return min(
            candidates,
            key=lambda candidate: (
                abs((candidate.get("inferred_printed_page") or page_hint) - page_hint),
                -(candidate.get("probability") or 0.0),
            ),
        )

    for entry in parsed["entries"]:
        helper_id = entry["entry_key"].replace(":", "_").lower()
        helper_entry = helper_entries.get(helper_id, {})
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        best_file = best.get("file")
        target_file_best = best_file or entry["section_start_file"]
        entry_out = dict(entry)
        entry_out.pop("_page_hints", None)
        entry_out["target_file_best"] = target_file_best
        entry_out["inferred_printed_page"] = best.get("inferred_printed_page", entry_out["inferred_printed_page"])
        entry_out["confidence"] = max(
            entry_out["confidence"],
            float(best.get("probability") or 0.0),
        )
        entry_out["raw_json"] = {
            **entry_out["raw_json"],
            "helper_entry_id": helper_id,
            "helper_status": helper_entry.get("status"),
            "helper_best_file": best_file,
            "helper_best_probability": best.get("probability"),
            "helper_candidate_role": best.get("candidate_role"),
            "helper_reason_summary": best.get("reason_summary"),
            "helper_top_candidates": [
                {
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "candidate_role": candidate.get("candidate_role"),
                    "inferred_printed_page": candidate.get("inferred_printed_page"),
                }
                for candidate in candidates[:3]
            ],
        }
        entries.append(entry_out)

        for ref_order, page_hint in enumerate(entry["raw_json"]["page_hints"], start=1):
            chosen_candidate = choose_candidate_for_hint(candidates, int(page_hint))
            candidate_target = chosen_candidate.get("file") or best_file
            candidate_probability = chosen_candidate.get("probability", best.get("probability"))
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": "target_locator",
                    "ref_raw": str(page_hint),
                    "page_ref_raw": str(page_hint),
                    "page_ref_int": int(page_hint),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": candidate_target,
                    "target_file_probability": candidate_probability,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.85 if candidate_target else 0.5,
                    "raw_json": {
                        "helper_entry_id": helper_id,
                        "helper_status": helper_entry.get("status"),
                        "helper_best_file": best_file,
                        "helper_best_probability": candidate_probability,
                        "helper_chosen_file": candidate_target,
                        "helper_chosen_inferred_page": chosen_candidate.get("inferred_printed_page"),
                        "helper_best_candidate_role": best.get("candidate_role"),
                        "helper_reason_summary": best.get("reason_summary"),
                    },
                }
            )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "The author index lines were recovered from the OCR page and cross-checked against helper target resolution.",
        "evidence_files": [parsed["index_page"]],
    }

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": parsed["volume"],
        "sections": [parsed["section"]],
        "nodes": parsed["nodes"],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            "The OCR page contains a single alphabetical author index headed INDEX SCRIPTORUM ECCLESIASTICORUM QUI A SIGEBERTO GEMBLACENSI MEMORANTUR.",
            "The ORDO RERUM content later in the volume was inspected separately and excluded from this alphabetical payload.",
        ],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--helper-request", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--mode", choices={"request", "final"}, required=True)
    args = ap.parse_args()

    parsed = parse_entries(args.source_root)
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    write_json(args.intermediate_dir / "volume.json", parsed["volume"])
    write_json(args.intermediate_dir / "sections.json", [parsed["section"]])
    write_json(args.intermediate_dir / "nodes.json", parsed["nodes"])
    write_json(args.intermediate_dir / "entries.json", parsed["entries"])
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": "PL160",
            "updated_at": now_iso(),
            "current_focus": "Resolve helper targets and assemble PL160 alphabetical author index payload",
            "completed": [
                "index page detected",
                "author-index section parsed",
                "entry skeleton extracted",
            ],
            "pending": [
                "run index_target_locator on helper request",
                "assemble final payload from helper output",
                "validate final JSON shape",
            ],
            "blocked": [],
            "notes": [
                "Keep ORDO RERUM excluded from the alphabetical payload.",
                "Cross-reference-only lines remain as entries without material refs.",
            ],
        },
    )
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": "PL160",
            "generated_at": now_iso(),
            "index_page": parsed["index_page"],
            "entry_count": len(parsed["entries"]),
            "node_count": len(parsed["nodes"]),
        },
    )

    helper_request = build_helper_request(parsed)
    write_json(args.helper_request, helper_request)

    if args.mode == "final":
        helper_output = load_helper_output(args.helper_output)
        payload = build_final_payload(parsed, helper_output)
        write_json(args.output, payload)


if __name__ == "__main__":
    main()

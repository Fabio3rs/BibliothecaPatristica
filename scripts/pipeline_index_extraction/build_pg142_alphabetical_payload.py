#!/usr/bin/env python3
"""Usage: build the PG142 alphabetical payload from OCR tail pages and helper output.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg142_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG142/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG142_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG142_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG142 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG142_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG142"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 142"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS AD NICEPHORI BLEMMIDAE EPITOMEN LOGICAM.",
        "page_start": 1621,
        "page_end": 1626,
        "file_start_seq": 817,
        "file_end_seq": 820,
        "section_kind_reason": "Analytical alphabetical index for the logical epitome, beginning with the printed INDEX ANALYTICUS heading.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS AD NICEPHORI BLEMMIDAE EPITOMEN PHYSICAM.",
        "page_start": 1627,
        "page_end": 1634,
        "file_start_seq": 820,
        "file_end_seq": 823,
        "section_kind_reason": "Analytical alphabetical index for the physical epitome, continuing from the mid-file transition on OCR file 820.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        "page_start": 1635,
        "page_end": 1643,
        "file_start_seq": 824,
        "file_end_seq": 828,
        "section_kind_reason": "Closing ORDO RERUM contents table for the tomus, distinct from the analytical index sections.",
    },
]

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", flags=re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
PAGE_HINT_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?:\s*et\s+seqq?\.?)?", re.IGNORECASE)
PAGE_HINT_INT_RE = re.compile(r"\d{1,4}")
STRIP_PAGE_TRAIL_RE = re.compile(r"(?P<body>.*?)(?:\s+|\s*[,.])(?P<page>\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*et\s+seqq?\.?)?)\s*$", re.IGNORECASE)
SPLIT_CLAUSE_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÆŒÀ-ÝΑ-Ω])")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
STRUCTURAL_RE = re.compile(r"^(?:INDEX|ORDO|CAP\.|ELENCHUS|TABLE|INDICES)\b", re.IGNORECASE)
NOISE_LINES = {
    "Digitized by Google",
    "PATROL. CXLII. 52",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, list[str]]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = (attrs.get("tipo") or "").strip().lower()
        if block_type not in {"cabecalho", "texto_principal", "nota_marginal", "outro"}:
            continue
        lines: list[str] = []
        for raw_line in (match.group("content") or "").splitlines():
            line = normalize(raw_line)
            if line and line not in NOISE_LINES:
                lines.append(line)
        if lines:
            blocks.append((block_type, lines))
    return blocks


def helper_query_names(lemma_raw: str, context_raw: str | None) -> list[str]:
    candidates: list[str] = []
    lemma = normalize(lemma_raw)
    if lemma:
        candidates.append(lemma)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    if context_raw:
        first_clause = normalize(context_raw).split(".", 1)[0].strip()
        if first_clause and first_clause not in candidates:
            candidates.append(first_clause)
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned and cleaned not in candidates:
        candidates.append(cleaned)
    return candidates[:4]


def derive_lemma_raw(fragment: str) -> str | None:
    cleaned = normalize(fragment)
    if not cleaned:
        return None
    cleaned = re.split(r"\bVide\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.split(r"\bIbid\.?\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    match = STRIP_PAGE_TRAIL_RE.match(cleaned)
    if match:
        cleaned = match.group("body")
    cleaned = cleaned.rstrip(" ,;:.—-")
    if not cleaned:
        return None
    return cleaned


def extract_page_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in PAGE_HINT_RE.finditer(text):
        token = match.group(0).strip().rstrip(" ,;:.")
        if token and token not in hints:
            hints.append(token)
    return hints


def split_fragments(line: str) -> list[str]:
    text = normalize(line)
    if not text:
        return []
    return [text]


def is_heading_only(fragment: str) -> bool:
    return bool(LETTER_RE.fullmatch(fragment))


def is_structural(fragment: str) -> bool:
    value = normalize(fragment)
    return bool(value and STRUCTURAL_RE.match(value))


def fragment_is_entry(fragment: str, section_kind: str) -> bool:
    value = normalize(fragment)
    if not value:
        return False
    if re.fullmatch(r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*et\s+seqq?\.?)?\.?", value):
        return False
    if value in NOISE_LINES:
        return False
    if is_heading_only(value):
        return False
    if section_kind != "ordo_rerum" and is_structural(value):
        return False
    if section_kind == "ordo_rerum" and value.upper().startswith("ORDO RERUM"):
        return False
    return bool(PAGE_HINT_RE.search(value) or value.upper().startswith(("A ", "B ", "C ", "D ", "E ", "F ", "G ", "H ", "I ", "J ", "K ", "L ", "M ", "N ", "O ", "P ", "Q ", "R ", "S ", "T ", "U ", "V ", "W ", "X ", "Y ", "Z ", "CAP.", "EPIST.", "ORATIO", "EPISTOLA", "DE ", "IN ", "EX ", "AD ")))


def build_entries_for_section(section: dict[str, Any], files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    current_letter: str | None = None
    current_entry: dict[str, Any] | None = None
    current_buffer: list[str] = []

    def flush() -> None:
        nonlocal current_entry, current_buffer
        if current_entry is None:
            current_buffer = []
            return
        entry_raw = normalize(" ".join(current_buffer or [current_entry["entry_raw"]]))
        if not entry_raw:
            current_entry = None
            current_buffer = []
            return
        current_entry["entry_raw"] = entry_raw
        lemma_raw = derive_lemma_raw(entry_raw) or entry_raw
        current_entry["lemma_raw"] = lemma_raw
        current_entry["lemma_display"] = lemma_raw
        current_entry["lemma_norm"] = sort_norm(lemma_raw)
        current_entry["lemma_sort"] = current_entry["lemma_norm"]
        page_hints = extract_page_hints(entry_raw)
        first_hint = page_hints[0] if page_hints else ""
        hint_match = PAGE_HINT_INT_RE.search(first_hint)
        current_entry["inferred_printed_page"] = int(hint_match.group(0)) if hint_match else None
        current_entry["raw_json"]["page_hints"] = page_hints
        current_entry["raw_json"]["section_kind"] = section["section_kind"]
        current_entry["raw_json"]["source_file"] = current_entry["raw_json"].get("source_file")
        current_entry["raw_json"]["section_kind_reason"] = section["section_kind_reason"]
        entries.append(current_entry)
        current_entry = None
        current_buffer = []

    for path in files:
        seq = file_seq(path)
        if seq < section["file_start_seq"] or seq > section["file_end_seq"]:
            continue
        for block_type, lines in extract_blocks(path):
            if block_type == "cabecalho":
                continue
            for line in lines:
                if line in NOISE_LINES:
                    continue
                if section["section_kind"] != "ordo_rerum" and normalize(line).startswith("INDEX ANALYTICUS"):
                    continue
                if section["section_kind"] == "ordo_rerum" and normalize(line).startswith("ORDO RERUM"):
                    continue
                for fragment in split_fragments(line):
                    fragment = normalize(fragment)
                    if not fragment:
                        continue
                    if re.fullmatch(r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*et\s+seqq?\.?)?\.?", fragment):
                        if current_entry is not None:
                            current_buffer.append(fragment)
                        continue
                    if is_heading_only(fragment):
                        flush()
                        current_letter = fragment
                        node_order += 1
                        nodes.append(
                            {
                                "node_key": f"{VOLUME_ID}:node:{section['section_order']:03d}:{node_order:03d}",
                                "section_key": section["section_key"],
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": fragment,
                                "label_norm": fragment.lower(),
                                "label_sort": fragment.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {
                                    "source_file": str(path),
                                    "section_kind_reason": "Explicit single-letter grouping in the index.",
                                },
                            }
                        )
                        continue
                    if not fragment_is_entry(fragment, section["section_kind"]):
                        continue
                    if current_entry is not None and fragment and fragment[0].islower():
                        current_buffer.append(fragment)
                        continue
                    if current_entry is not None:
                        flush()
                    entry_order += 1
                    current_entry = {
                        "entry_key": f"{VOLUME_ID}:entry:{section['section_order']:03d}:{entry_order:04d}",
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "entry_order": entry_order,
                        "entry_kind": "lemma",
                        "lemma_raw": None,
                        "lemma_display": None,
                        "lemma_norm": None,
                        "lemma_sort": None,
                        "entry_raw": fragment,
                        "context_raw": None,
                        "heading_letter": current_letter,
                        "inferred_printed_page": None,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "target_file_best": str(path),
                        "confidence": 0.74,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section["section_kind"],
                        },
                    }
                    current_buffer = [fragment]
                    if fragment.upper().startswith(("VIDE ", "VIDE.", "VID.", "CF.", "ID.", "VOIR ")):
                        current_entry["entry_kind"] = "cross_reference"
                    if fragment.upper().startswith(("CAP.", "EPIST.", "ORATIO", "EPISTOLA", "PROLOGUS", "EXPOSITIO")):
                        current_entry["entry_kind"] = "heading_group"
                    if current_letter is None and section["section_kind"] == "ordo_rerum" and fragment.upper().startswith("CAP."):
                        current_entry["entry_kind"] = "heading_group"

    flush()

    for entry in entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        refs_for_entry: list[dict[str, Any]] = []
        seen_ref_tokens: set[str] = set()
        for ref_order, token in enumerate(page_hints, start=1):
            norm_token = normalize(token)
            if not norm_token or norm_token in seen_ref_tokens:
                continue
            seen_ref_tokens.add(norm_token)
            start = re.match(r"\d{1,4}", norm_token)
            page_int = int(start.group(0)) if start else None
            ref_kind = "editorial_range" if re.search(r"[-–—]", norm_token) else "editorial_page"
            refs_for_entry.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": ref_kind,
                    "ref_raw": norm_token,
                    "page_ref_raw": norm_token,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.68,
                    "raw_json": {
                        "source_file": entry["raw_json"].get("source_file"),
                        "page_hint_from_entry": True,
                    },
                }
            )
        refs.extend(refs_for_entry)

    return entries, refs, nodes


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict[str, Any]], helper_request_json: Path) -> dict[str, Any]:
    request_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        if not page_hints:
            continue
        request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": helper_query_names(entry["lemma_raw"] or entry["entry_raw"], entry["entry_raw"]),
                "page_hints": page_hints,
                "page_hint_ints": [int(m.group(0)) for hint in page_hints if (m := PAGE_HINT_INT_RE.search(hint))],
                "context_raw": entry["entry_raw"],
            }
        )
    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": request_entries,
    }
    write_json(helper_request_json, request)
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
    )
    return read_json(helper_output_json, {})


def update_entries_with_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_entries = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for entry in entries:
        helper_item = helper_entries.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        candidate_file = best.get("file")
        probability = best.get("probability")
        entry["target_file_best"] = candidate_file or entry["target_file_best"]
        if probability is not None:
            entry["confidence"] = max(entry["confidence"], float(probability) * 0.9)
        entry["raw_json"]["helper"] = helper_item
        for ref in refs_by_entry.get(entry["entry_key"], []):
            ref["target_file"] = candidate_file or ref["target_file"]
            ref["target_file_probability"] = probability
            ref["confidence"] = max(ref["confidence"], float(probability) * 0.85) if probability is not None else ref["confidence"]
            ref["raw_json"]["helper"] = helper_item
            ref["raw_json"]["helper_candidate_file"] = candidate_file


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG142 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root.resolve()
    files = discover_files(source_root)
    intermediate_dir = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PG142 alphabetical payload from OCR tail and helper output",
        "completed": [
            "OCR tail files inspected",
            "sections identified",
        ],
        "pending": [
            "resolve helper targets for extracted entries",
            "write final payload",
        ],
        "blocked": [],
        "notes": [
            "Section 820 contains the transition from the logic index to the physics index.",
            "OCR headers in the tail are noisy; keep editorial and physical numbering separate.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The tail contains two analytical indexes for Blemmidas (logic and physics) followed by ORDO RERUM.",
            "OCR file suffix, printed page, and cited reference are handled as separate numbering systems.",
        ],
    }
    write_json(intermediate_dir / "volume.json", volume)

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    for section_def in SECTION_DEFS:
        section = {
            "section_key": section_def["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": section_def["section_order"],
            "section_kind": section_def["section_kind"],
            "heading_raw": section_def["heading_raw"],
            "heading_norm": sort_norm(section_def["heading_raw"]),
            "heading_letter": None,
            "page_start": section_def["page_start"],
            "page_end": section_def["page_end"],
            "file_start": str((source_root / f"placeholder-{section_def['file_start_seq']:03d}.txt")),
            "file_end": str((source_root / f"placeholder-{section_def['file_end_seq']:03d}.txt")),
            "confidence": 0.94 if section_def["section_kind"] != "ordo_rerum" else 0.98,
            "raw_json": {
                "section_kind_reason": section_def["section_kind_reason"],
                "evidence_files": [],
            },
            "_file_start_seq": section_def["file_start_seq"],
            "_file_end_seq": section_def["file_end_seq"],
        }
        section_files = [path for path in files if section_def["file_start_seq"] <= file_seq(path) <= section_def["file_end_seq"]]
        if section_files:
            section["file_start"] = str(section_files[0])
            section["file_end"] = str(section_files[-1])
            section["raw_json"]["evidence_files"] = [str(section_files[0]), str(section_files[-1])]
        sections.append(section)
        section_entries, section_refs, section_nodes = build_entries_for_section(section_def, files)
        entries.extend(section_entries)
        refs.extend(section_refs)
        nodes.extend(section_nodes)

    for entry in entries:
        if not entry["lemma_raw"]:
            entry["lemma_raw"] = entry["entry_raw"]
            entry["lemma_display"] = entry["entry_raw"]
            entry["lemma_norm"] = sort_norm(entry["entry_raw"])
            entry["lemma_sort"] = entry["lemma_norm"]

    helper_request = build_helper_request(VOLUME_ID, source_root, entries, args.helper_request_json)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json) if helper_request["entries"] else {"entries": []}
    update_entries_with_helper(entries, refs, helper_output)

    # Recompute a few fields after helper resolution.
    for entry in entries:
        entry["context_raw"] = None if entry["context_raw"] == entry["entry_raw"] else entry["context_raw"]
        if entry["entry_kind"] == "lemma" and entry["entry_raw"].upper().startswith("CAP."):
            entry["entry_kind"] = "heading_group"

    notes = [
        "PG142 has two analytical index sections for Blemmidas (logic and physics) before the final ORDO RERUM.",
        "The tail OCR carries noisy printed page headers; helper output was used to stabilize target-file anchors where possible.",
    ]
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Analytical and contents sections were recovered from OCR tail pages; some ibid.-only fragments remain conservative cross-reference or unrefined entries.",
        "evidence_files": [str(path) for path in files if 817 <= file_seq(path) <= 828],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": [
            {k: v for k, v in section.items() if not k.startswith("_")}
            for section in sections
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(intermediate_dir / "manifest.json", {"generated_at": payload["generated_at"], "updated_at": payload["generated_at"], "volume_id": VOLUME_ID})

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

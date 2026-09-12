#!/usr/bin/env python3
"""Usage: build the PG100 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG100_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG100/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG100_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG100_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG100 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG100_alphabetical_indices.json
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
VOLUME_ID = "PG100"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 100"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG100_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG100_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG100_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG100"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM."
SECTION_HEADING_NORM = "ordo rerum"

TARGET_SEQS = [777, 778]

PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?")
SPLIT_RE = re.compile(r"(?<=\.)\s+(?=(?:[A-ZΑ-Ω]|Ἀ|Ἐ|Ἰ|Ὁ|Ὑ|Ὤ|Ὄ))")
LETTER_RE = re.compile(r"^[A-Z]$")
HEADING_RE = re.compile(r"^(?:III\. INDEX IN VITAM S\. STEPHANI JUNIORIS\.|ORDO RERUM\.?)$", re.I)
NOISE_RE = re.compile(r"^(?:Digitized by Google|1541\s+ORDO RERUM\.\s+1542\s+III\. INDEX IN VITAM S\. STEPHANI JUNIORIS\.|1543\s+ORDO RERUM\.\s+1544)$")
ROMAN_HEAD_RE = re.compile(r"^(?P<roman>[IVXLCDM]+)\.\s+(?P<label>.+)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if not match:
            continue
        files.append((int(match.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"Cannot parse file seq from {path}")
    return int(match.group(1))


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header):
            page = int(token)
            if page and page not in mapping:
                mapping[page] = path.as_posix()
    return mapping


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("body_text") or "").splitlines():
        line = normalize(raw)
        if not line:
            continue
        if NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def split_fragments(text: str) -> list[str]:
    parts = [normalize(part) for part in SPLIT_RE.split(text) if normalize(part)]
    return [part for part in parts if part]


def line_is_heading(line: str) -> bool:
    return bool(HEADING_RE.fullmatch(line))


def line_is_letter(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def derive_lemma(fragment: str) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if text.startswith(("V. ", "v. ", "vide ", "vid. ", "cf. ", "id. ", "ibid.")):
        return None
    match = PAGE_REF_RE.search(text)
    if match:
        text = text[: match.start()].rstrip(" ,;:.")
    if text.startswith(("A ", "B ", "C ", "D ", "E ", "F ", "G ", "H ", "I ", "J ", "L ", "M ", "N ", "O ", "P ", "R ", "S ", "T ", "V ", "X ", "Z ")):
        text = text[2:].strip()
    text = text.strip(" ,;:.")
    return text or None


def extract_refs(fragment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for idx, match in enumerate(PAGE_REF_RE.finditer(fragment), start=1):
        start_raw = match.group(1)
        end_raw = match.group(2)
        if end_raw:
            refs.append(
                {
                    "ref_order": idx,
                    "ref_kind": "editorial_range",
                    "ref_raw": f"{start_raw}-{end_raw}",
                    "page_ref_raw": start_raw,
                    "page_ref_int": int(start_raw),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": start_raw,
                    "range_end_raw": end_raw,
                }
            )
        else:
            refs.append(
                {
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": start_raw,
                    "page_ref_raw": start_raw,
                    "page_ref_int": int(start_raw),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
    return refs


def build_helper_request(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    source_root: Path,
) -> dict[str, Any]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(str(ref.get("entry_key")), []).append(ref)
    helper_entries: list[dict[str, Any]] = []
    for entry in entries[:5]:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if not entry_refs:
            continue
        page_hints = [str(ref["page_ref_int"]) for ref in entry_refs[:3]]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:80],
                "query_names": [q for q in dict.fromkeys(
                    [
                        normalize(entry.get("lemma_raw") or ""),
                        normalize((entry.get("lemma_raw") or entry["entry_raw"]).split(",", 1)[0]),
                        normalize(entry["entry_raw"].split(",", 1)[0]),
                    ]
                ) if q],
                "page_hints": page_hints,
                "page_hint_ints": [int(p) for p in page_hints],
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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
    return read_json(helper_output_json, default={"entries": []})


def helper_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        mapping[str(item.get("entry_id"))] = item
    return mapping


def parse_entries(
    files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_node_key: str | None = None
    current_letter_key: str | None = None
    node_order = 0
    entry_order = 0

    section_anchor = files[0].as_posix() if files else None
    subsection_key = f"{VOLUME_ID}:node:001"
    nodes.append(
        {
            "node_key": subsection_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "ordinal_group",
            "label_raw": "III.",
            "label_norm": "iii",
            "label_sort": "iii",
            "node_level": 1,
            "confidence": 0.96,
            "raw_json": {
                "source_file": files[0].as_posix() if files else None,
                "note": "Ordinal heading printed before the alphabetical index in the ORDO RERUM block.",
            },
        }
    )
    node_order = 1
    current_node_key = subsection_key

    letter_map: dict[str, str] = {}

    def ensure_letter(letter: str, source_file: str) -> str:
        nonlocal node_order
        if letter in letter_map:
            return letter_map[letter]
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        letter_map[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": subsection_key,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 2,
                "confidence": 0.98,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": "ordo_rerum",
                },
            }
        )
        return node_key

    for path in files:
        for raw_line in extract_lines(path):
            line = raw_line.strip()
            if not line or line in {"Digitized by Google"}:
                continue
            if line_is_heading(line):
                continue
            if line_is_letter(line):
                current_letter_key = ensure_letter(line, path.as_posix())
                current_node_key = current_letter_key
                continue

            fragments = split_fragments(line)
            for fragment in fragments:
                if not fragment:
                    continue
                if fragment in {"Digitized by Google", "ORDO RERUM.", "III. INDEX IN VITAM S. STEPHANI JUNIORIS."}:
                    continue
                entry_order += 1
                lemma_raw = derive_lemma(fragment)
                refs_for_fragment = extract_refs(fragment)
                entry_kind = "heading_group"
                if lemma_raw is None and refs_for_fragment:
                    entry_kind = "editorial_note"
                elif lemma_raw and any(token in fragment for token in ("V.", "v.", "vide", "vid.", "cf.", "id.")) and not refs_for_fragment:
                    entry_kind = "cross_reference"
                elif lemma_raw is None:
                    entry_kind = "editorial_note"
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                inferred_page = refs_for_fragment[0]["page_ref_int"] if refs_for_fragment else None
                target_best = None
                if inferred_page is not None and inferred_page in page_map:
                    target_best = page_map[inferred_page]
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": SECTION_KEY,
                        "parent_node_key": current_node_key,
                        "entry_order": entry_order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": fragment,
                        "context_raw": fragment if len(fragment) < 240 else fragment[:237] + "...",
                        "heading_letter": current_letter_key,
                        "inferred_printed_page": inferred_page,
                        "section_start_file": section_anchor,
                        "editorial_anchor_file": path.as_posix(),
                        "target_file_best": target_best,
                        "confidence": 0.88 if refs_for_fragment else 0.72,
                        "raw_json": {
                            "source_file": path.as_posix(),
                            "section_kind": "ordo_rerum",
                            "fragment_role": "index_entry" if refs_for_fragment else "heading_or_note",
                            "page_refs": [ref["page_ref_int"] for ref in refs_for_fragment],
                        },
                    }
                )
                for ref in refs_for_fragment:
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref["ref_order"],
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": page_map.get(ref["page_ref_int"]),
                            "target_file_probability": 0.94 if page_map.get(ref["page_ref_int"]) else None,
                            "section_start_file": section_anchor,
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": 0.9 if page_map.get(ref["page_ref_int"]) else 0.76,
                            "raw_json": {
                                "source_file": path.as_posix(),
                                "section_kind": "ordo_rerum",
                            },
                        }
                    )
    return entries, refs, nodes


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG100 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = [path for path in discover_files(args.source_root) if file_seq(path) in TARGET_SEQS]
    if not files:
        raise SystemExit("No target OCR files found for PG100.")

    page_map = build_page_map(discover_files(args.source_root))
    entries, refs, nodes = parse_entries(files, page_map)
    helper_request = build_helper_request(entries, refs, args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_by_id(helper_output)

    helper_sample: dict[str, Any] = {}
    for entry in entries[:5]:
        helper_entry = helper_map.get(entry["entry_key"])
        if helper_entry:
            helper_sample[entry["entry_key"]] = {
                "status": helper_entry.get("status"),
                "candidate_role": helper_entry.get("candidate_role"),
                "reason_summary": helper_entry.get("reason_summary"),
                "top_candidates": helper_entry.get("candidates", [])[:3],
            }

    if helper_sample:
        for entry in entries[:5]:
            if entry["entry_key"] in helper_sample:
                entry["raw_json"]["helper"] = helper_sample[entry["entry_key"]]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": args.source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [
            {
                "section_key": SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": SECTION_HEADING_RAW,
                "heading_norm": SECTION_HEADING_NORM,
                "heading_letter": None,
                "page_start": 1541,
                "page_end": 1544,
                "file_start": files[0].as_posix(),
                "file_end": files[-1].as_posix(),
                "confidence": 0.94,
                "raw_json": {
                    "section_kind_reason": (
                        "Closing editorial contents table for the tome, with an explicit ordinal "
                        "subheading and an alphabetical index to S. Stephani Junioris."
                    ),
                    "heading_sources": [path.as_posix() for path in files],
                    "helper_used": bool(helper_sample),
                },
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the closing ORDO RERUM block as a structured editorial index section and preserved its alphabetical subentries.",
            "evidence_files": [path.as_posix() for path in files],
        },
        "notes": [
            {
                "note_key": f"{VOLUME_ID}:note:001",
                "note_type": "extraction",
                "text": "PG100 closes with ORDO RERUM and the alphabetical index to S. Stephani Junioris; OCR page headers and cited page numbers are preserved as distinct fields.",
            }
        ],
    }

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG100 ORDO RERUM payload assembled",
            "completed": [
                "tail OCR files inspected",
                "helper request generated",
                "helper run completed",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The volume also contains earlier analytical index pages, but the extracted payload is centered on the explicit ORDO RERUM tail block present in the filtered pages.",
            ],
        },
    )

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

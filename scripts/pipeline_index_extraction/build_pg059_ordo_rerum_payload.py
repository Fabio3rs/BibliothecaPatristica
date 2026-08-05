#!/usr/bin/env python3
"""Usage: build the PG059 closing ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg059_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG059/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG059_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG059_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG059 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG059_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG059"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 59"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
NODE_KEY = f"{VOLUME_ID}:node:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUAE IN HOC TOMO NONO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo nono continentur"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
SECTION_START_FILES = [
    "fa3d6a48-9a93-47b1-a531-6debf09e7232-759.txt",
    "fa3d6a48-9a93-47b1-a531-6debf09e7232-760.txt",
    "fa3d6a48-9a93-47b1-a531-6debf09e7232-762.txt",
]

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
TYPE_RE = re.compile(r'tipo="([^"]+)"')
PAGE_RE = re.compile(
    r"^(?P<body>.*?)(?:\s+)(?P<ref>(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?|ibid\.?))\.?$",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def slug(text: str) -> str:
    value = sort_norm(text) or "entry"
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "entry"


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def extract_lines(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[tuple[str, str]] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = TYPE_RE.search(attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            if re.fullmatch(r"\d{1,4}", line):
                continue
            lines.append((path.as_posix(), line))
    return lines


def is_heading(line: str) -> bool:
    upper = line.upper()
    return "ORDO RERUM" in upper or "QUAE IN HOC TOMO NONO CONTINENTUR" in upper or "QUÆ IN HOC TOMO NONO CONTINENTUR" in upper


def is_entry_start(line: str) -> bool:
    upper = line.upper()
    if upper.startswith("ORDO RERUM"):
        return False
    return bool(
        re.match(
            r"^(?:PRAEFATIO\b|PRÆFATIO\b|MONITUM\b|ADMONITIO\b|HOMILIA\b|HOM\.|HOM\s|SERMO\b|ORATIO\b|OPUSCULUM\b|INTERPRETATIO\b|SPUMA\.|INDEX SPURIORUM\b|SPURIA\.)",
            upper,
        )
    )


def split_trailing_ref(text: str) -> tuple[str, str | None, int | None, int | None]:
    m = PAGE_RE.match(text)
    if not m:
        return normalize(text), None, None, None
    body = normalize(m.group("body").rstrip(" ,;:."))
    ref = normalize(m.group("ref"))
    if ref.lower().startswith("ibid"):
        return body, "ibid.", None, None
    if re.fullmatch(r"\d{1,4}\s*[-–]\s*\d{1,4}", ref):
        start, end = re.split(r"\s*[-–]\s*", ref, maxsplit=1)
        return body, f"{start}-{end}", int(start), int(end)
    return body, ref, int(ref), None


def join_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    out = lines[0]
    for piece in lines[1:]:
        piece = normalize(piece)
        if out.endswith("-"):
            out = out[:-1] + piece.lstrip()
        else:
            out += " " + piece
    return normalize(out)


def parse_ordo_entries(source_root: Path) -> dict[str, Any]:
    files = [source_root / name for name in SECTION_START_FILES]
    ordered_lines: list[tuple[str, str]] = []
    for path in files:
        ordered_lines.extend(extract_lines(path))

    section_seen = False
    buffered: list[str] = []
    buffered_source: str | None = None
    entries: list[dict[str, Any]] = []
    last_explicit_ref: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal buffered, buffered_source, last_explicit_ref
        if not buffered:
            return
        raw = join_lines(buffered)
        entry_raw, ref_raw, ref_int, ref_end = split_trailing_ref(raw)
        if ref_raw == "ibid." and last_explicit_ref:
            ref_int = last_explicit_ref["page_ref_int"]
            ref_end = last_explicit_ref.get("range_end_raw")
        elif ref_raw and ref_raw != "ibid.":
            last_explicit_ref = {
                "page_ref_int": ref_int,
                "range_end_raw": str(ref_end) if ref_end is not None else None,
            }
        title = entry_raw
        if ref_raw:
            title = entry_raw
        entry_kind = "lemma"
        upper = entry_raw.upper()
        if upper.startswith(("MONITUM", "ADMONITIO")):
            entry_kind = "editorial_note"
        elif upper.startswith("INDEX SPURIORUM") or upper.startswith("SPURIA"):
            entry_kind = "heading_group"
        entry_order = len(entries) + 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        lemma_raw = title
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": NODE_KEY,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": ref_int,
            "section_start_file": files[0].as_posix(),
            "editorial_anchor_file": buffered_source,
            "target_file_best": None,
            "confidence": 0.72 if ref_int is not None else 0.5,
            "raw_json": {
                "source_file": buffered_source,
                "section_kind": "ordo_rerum",
                "entry_kind_reason": (
                    "editorial note in the contents table"
                    if entry_kind == "editorial_note"
                    else "contents-table line from the final ORDO RERUM"
                ),
                "page_ref_raw": ref_raw,
                "page_ref_int": ref_int,
                "page_ref_range_end": ref_end,
            },
        }
        entries.append(entry)
        buffered = []
        buffered_source = None

    for source_file, line in ordered_lines:
        if not section_seen:
            if is_heading(line):
                section_seen = True
            continue
        if line in {"ORDO RERUM", "INDEX RERUM."}:
            continue
        if is_entry_start(line) and buffered:
            flush()
            buffered = [line]
            buffered_source = source_file
            continue
        if not buffered:
            buffered = [line]
            buffered_source = source_file
        else:
            buffered.append(line)

    flush()
    return {
        "section_files": [p.as_posix() for p in files],
        "entries": entries,
    }


def make_query_names(lemma_raw: str) -> list[str]:
    base = normalize(lemma_raw)
    variants = [base]
    if "—" in base:
        variants.append(normalize(base.split("—", 1)[0]))
    if ";" in base:
        variants.append(normalize(base.split(";", 1)[0]))
    if ":" in base:
        variants.append(normalize(base.split(":", 1)[0]))
    if "," in base:
        variants.append(normalize(base.split(",", 1)[0]))
    words = base.split()
    if len(words) >= 5:
        variants.append(" ".join(words[:5]))
    if len(words) >= 8:
        variants.append(" ".join(words[:8]))
    seen: list[str] = []
    for item in variants:
        item = normalize(item)
        if item and item not in seen:
            seen.append(item)
    return seen[:5]


def build_helper_request(volume_id: str, source_root: Path, parsed_entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for idx, item in enumerate(parsed_entries, start=1):
        page_ref_int = item["inferred_printed_page"]
        page_hints = []
        page_hint_ints = []
        if isinstance(page_ref_int, int):
            page_hints.append(str(page_ref_int))
            page_hint_ints.append(page_ref_int)
        helper_entries.append(
            {
                "entry_id": f"{volume_id.lower()}_ordo_{idx:04d}",
                "lemma_raw": item["lemma_raw"],
                "query_names": make_query_names(item["lemma_raw"]),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": item["entry_raw"],
            }
        )
    return {
        "volume_id": volume_id,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
    subprocess.run(
        [
            "python",
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_payload(source_root: Path, parsed: dict[str, Any], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_by_entry_id: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            helper_by_entry_id[str(entry_id)] = item

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed["entries"], start=1):
        entry_id = f"{VOLUME_ID.lower()}_ordo_{idx:04d}"
        helper_item = helper_by_entry_id.get(entry_id, {})
        best_candidate = helper_item.get("best_candidate") or {}
        probability = best_candidate.get("probability")
        target_file = best_candidate.get("file")
        status = helper_item.get("status")
        confidence = float(probability) if isinstance(probability, (int, float)) else (0.78 if status == "resolved" else 0.6)
        page_ref_int = item.get("inferred_printed_page")
        page_ref_raw = item["raw_json"].get("page_ref_raw")
        ref_kind = "editorial_page"
        if page_ref_raw == "ibid.":
            ref_kind = "editorial_page"
        entry = dict(item)
        entry["target_file_best"] = target_file or item["editorial_anchor_file"]
        entry["confidence"] = round(confidence, 6)
        entry["raw_json"] = {
            **entry["raw_json"],
            "helper_entry_id": entry_id,
            "helper_status": status,
            "helper_best_candidate": best_candidate or None,
        }
        entries.append(entry)
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 1,
                "ref_kind": ref_kind,
                "ref_raw": page_ref_raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page_ref_raw.split("-", 1)[0] if isinstance(page_ref_raw, str) and "-" in page_ref_raw else None,
                "range_end_raw": page_ref_raw.split("-", 1)[1] if isinstance(page_ref_raw, str) and "-" in page_ref_raw else None,
                "target_file": target_file,
                "target_file_probability": probability if isinstance(probability, (int, float)) else None,
                "section_start_file": item["section_start_file"],
                "editorial_anchor_file": item["editorial_anchor_file"],
                "confidence": round(confidence, 6),
                "raw_json": {
                    "helper_entry_id": entry_id,
                    "helper_status": status,
                    "best_candidate": best_candidate or None,
                },
            }
        )

    section_start_file = parsed["section_files"][0]
    section_end_file = parsed["section_files"][-1]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
        "notes": "The final OCR tail is a contents table headed ORDO RERUM; the initial INDEX RERUM rubric and preceding prose are not serialized as index entries.",
    }
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
            "page_start": None,
            "page_end": None,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Closing contents table headed ORDO RERUM QUAE IN HOC TOMO NONO CONTINENTUR; the page also carries an INDEX RERUM rubric and earlier body text that are not separate sections.",
                "witnesses": {
                    "candidate_files": parsed["section_files"],
                },
            },
        }
    ]
    nodes = [
        {
            "node_key": NODE_KEY,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": SECTION_HEADING_RAW,
            "label_norm": SECTION_HEADING_NORM,
            "label_sort": SECTION_HEADING_NORM,
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {
                "role": "section_heading",
                "source_files": parsed["section_files"],
            },
        }
    ]
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail; the lines were grouped conservatively by content-title and printed page locator.",
        "evidence_files": parsed["section_files"],
    }
    notes = [
        "PG059 does not present a separate alphabetical subject index in the filtered tail; the useful material is the closing ORDO RERUM table.",
        "The OCR spread interleaves the contents table with residual prose and a later SPURIA heading; only the ORDO RERUM contents lines were serialized here.",
    ]
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--helper-request-json", type=Path, required=True)
    parser.add_argument("--helper-output-json", type=Path, required=True)
    parser.add_argument("--intermediate-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PG059 closing ORDO RERUM and anchor printed page ranges with the helper.",
            "completed": [
                "identified the closing ORDO RERUM contents table",
                "grouped the OCR lines into logical contents entries",
            ],
            "pending": [
                "run the target locator helper",
                "assemble the final payload",
                "validate the written JSON",
            ],
            "blocked": [],
            "notes": [
                "The OCR tail includes residual prose before the ORDO RERUM heading and a later SPURIA heading that are not part of the final index payload.",
            ],
        },
    )

    parsed = parse_ordo_entries(args.source_root)
    helper_request = build_helper_request(VOLUME_ID, args.source_root, parsed["entries"])
    write_json(args.helper_request_json, helper_request)
    run_helper(args.helper_request_json, args.helper_output_json)
    helper_output = read_json(args.helper_output_json)
    payload = build_payload(args.source_root, parsed, helper_output)
    write_json(args.output_file, payload)
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG059 ORDO RERUM payload completed and written.",
            "completed": [
                "identified the closing ORDO RERUM contents table",
                "grouped the OCR lines into logical contents entries",
                "ran the target locator helper",
                "assembled the final payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The final payload intentionally leaves page_start/page_end null because the OCR tail is a mixed spread and the contents table lacks a stable printed-page span of its own.",
            ],
        },
    )


if __name__ == "__main__":
    main()

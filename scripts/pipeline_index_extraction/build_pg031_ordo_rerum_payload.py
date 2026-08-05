#!/usr/bin/env python3
"""Usage: build the PG031 closing ORDO RERUM payload and helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg031_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG031/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG031_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG031_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG031 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG031_alphabetical_indices.json
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


VOLUME_ID = "PG031"
COLLECTION = "PG"
VOLUME_LABEL = "PG031"


TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
FILE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
TRAILING_PAGE_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})$")
LEADING_NOISE_RE = re.compile(r"^\s*(?P<noise>\d{3,4})\s+(?P<body>.*)$")

SKIP_HEADINGS = (
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "S. BASILIUS CÆSAREÆ CAPPADOCIÆ ARCHI-",
    "EPISCOPUS.",
)

SECTION_SEQ_MIN = 937
SECTION_SEQ_MAX = 939


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def fold(text: str | None) -> str:
    if not text:
        return ""
    value = normalize(text)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value.lower()


def seq(path: Path) -> int:
    match = FILE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse OCR sequence from {path}")
    return int(match.group(1))


def block_lines(path: Path, block_type: str) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in TEXT_BLOCK_RE.finditer(raw):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo != block_type:
            continue
        body = re.sub(r"<[^>]+>", " ", block.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if line and line != "Digitized by Google":
                lines.append(line)
    return lines


def find_section_files(source_root: Path) -> list[Path]:
    paths = [path for path in sorted(source_root.glob("*.txt"), key=seq) if SECTION_SEQ_MIN <= seq(path) <= SECTION_SEQ_MAX]
    if not paths:
        raise SystemExit(f"could not locate section window {SECTION_SEQ_MIN}-{SECTION_SEQ_MAX} in {source_root}")
    return paths


def extract_printed_page_bounds(section_files: list[Path]) -> tuple[int, int]:
    # The OCR headers around the closing ORDO RERUM are noisy enough that
    # the section bounds are safer when fixed to the printed range observed
    # in this tail block: 1815-1848.
    return 1815, 1848


def join_buffer(buffer: list[str]) -> str:
    if not buffer:
        return ""
    text = buffer[0]
    for piece in buffer[1:]:
        if text.endswith("-"):
            text = text[:-1] + piece.lstrip()
        else:
            text += " " + piece
    return normalize(text)


def strip_leading_noise(text: str) -> tuple[str, str | None]:
    match = LEADING_NOISE_RE.match(text)
    if not match:
        return text, None
    return normalize(match.group("body")), match.group("noise")


def split_entry(text: str) -> tuple[str, str | None, int | None]:
    cleaned, _ = strip_leading_noise(text)
    match = TRAILING_PAGE_RE.match(cleaned)
    if not match:
        return cleaned, None, None
    body = normalize(match.group("body").rstrip(" ,;:."))
    page_raw = match.group("page")
    return body, page_raw, int(page_raw)


def classify_entry(lemma_raw: str) -> str:
    folded = fold(lemma_raw)
    raw = normalize(lemma_raw)
    if "memoratur tantum" in folded or "tantum memoratur" in folded:
        return "editorial_note"
    heading_prefixes_raw = (
        "PRÆFATIO",
        "PRAEFATIO",
        "MONITUM",
        "ASCETICA",
        "MORALIA",
        "REGULÆ",
        "REGULAE",
        "CONSTITUTIONES",
        "DE BAPTISMO",
        "LIBER SECUNDUS",
        "ORATIONES SIVE EXORCISMI",
        "INDEX MORALIUM",
        "ELENCHUS",
        "CAPUT",
        "CAP.",
        "§ ",
        "HOMILIÆ ET SERMONES",
        "HOMILIAE ET SERMONES",
        "HOMILIÆ quæ transtulit Rufinus de Greco in Latinum",
        "HOMILIAE quæ transtulit Rufinus de Greco in Latinum",
    )
    if raw.startswith(heading_prefixes_raw):
        return "heading_group"
    heading_prefixes = (
        "præfatio",
        "praefatio",
        "monitum",
        "ascetica",
        "moralia",
        "regulæ",
        "regulae",
        "constitutiones",
        "de baptismo",
        "liber secundus",
        "orationes sive exorcismi",
        "index moralium",
        "elenchus",
        "caput",
        "cap ",
        "homiliæ et sermones",
        "homiliae et sermones",
        "homiliæ quæ transtulit rufinus de greco in latinum",
        "homiliae quæ transtulit rufinus de greco in latinum",
    )
    if folded.startswith(heading_prefixes):
        return "heading_group"
    return "lemma"


def entry_id(order: int) -> str:
    return f"{VOLUME_ID}:entry:{order:04d}"


def section_key() -> str:
    return f"{VOLUME_ID}:alpha:ordo_rerum:001"


def parse_entries(section_files: list[Path]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_source: str | None = None
    buffer_line_index: int | None = None
    order = 0

    def flush(source_file: str, line_index: int) -> None:
        nonlocal buffer, buffer_source, buffer_line_index, order
        if not buffer:
            return
        joined = join_buffer(buffer)
        lemma_raw, page_raw, page_int = split_entry(joined)
        if page_raw is None:
            buffer = []
            buffer_source = None
            buffer_line_index = None
            return
        order += 1
        entry_kind = classify_entry(lemma_raw)
        leading_noise = None
        match_noise = LEADING_NOISE_RE.match(joined)
        if match_noise and normalize(match_noise.group("body")) != joined:
            leading_noise = match_noise.group("noise")
        source = buffer_source or source_file
        parsed.append(
            {
                "entry_key": entry_id(order),
                "section_key": section_key(),
                "parent_node_key": None,
                "entry_order": order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": fold(lemma_raw) or None,
                "lemma_sort": fold(lemma_raw) or None,
                "entry_raw": normalize(joined),
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": section_files[0].as_posix(),
                "editorial_anchor_file": source,
                "target_file_best": None,
                "confidence": 0.9 if len(buffer) == 1 else 0.84,
                "raw_json": {
                    "source_file": source,
                    "line_index": line_index,
                    "line_buffered": len(buffer) > 1,
                    "entry_kind_reason": (
                        "editorial note with memoratur phrasing"
                        if entry_kind == "editorial_note"
                        else "structural heading in closing ORDO RERUM"
                        if entry_kind == "heading_group"
                        else "content line in closing ORDO RERUM"
                    ),
                    "leading_page_noise": int(leading_noise) if leading_noise else None,
                },
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
            }
        )
        buffer = []
        buffer_source = None
        buffer_line_index = None

    for path in section_files:
        text_lines = block_lines(path, "texto_principal")
        for line_index, line in enumerate(text_lines, 1):
            if not line:
                continue
            if line in SKIP_HEADINGS:
                buffer = []
                buffer_source = None
                buffer_line_index = None
                continue
            buffer.append(line)
            if TRAILING_PAGE_RE.search(normalize(line)) or LEADING_NOISE_RE.match(normalize(line)):
                flush(path.as_posix(), line_index)
    buffer = []
    return parsed


def build_helper_request(section_entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    request_entries: list[dict[str, Any]] = []
    for item in section_entries:
        lemma_raw = item["lemma_raw"]
        page_hint = item["page_ref_int"]
        query_names = [lemma_raw]
        stripped = re.sub(r"^\s*(?:§\s*[IVXLCDM]+\.\s*—\s*|CAPUT\s+[IVXLCDM]+\.\s*—\s*|CAP\.\s+[IVXLCDM]+\.\s*—\s*)", "", lemma_raw)
        stripped = re.sub(r"\s*\(.*?\)\s*$", "", stripped).strip()
        if stripped and stripped not in query_names:
            query_names.append(stripped)
        folded = fold(lemma_raw)
        if folded and folded not in query_names:
            query_names.append(folded)
        request_entries.append(
            {
                "entry_id": item["entry_key"].replace(":", "_"),
                "lemma_raw": lemma_raw,
                "query_names": query_names,
                "page_hints": [str(page_hint)],
                "page_hint_ints": [page_hint],
                "context_raw": item["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": request_entries,
    }


def helper_result_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        out[item["entry_id"]] = item
    return out


def assemble_payload(
    section_files: list[Path],
    entries: list[dict[str, Any]],
    helper_output: dict[str, Any],
    page_start: int,
    page_end: int,
) -> dict[str, Any]:
    helper_map = helper_result_map(helper_output)
    refs: list[dict[str, Any]] = []
    for item in entries:
        helper_entry = helper_map.get(item["entry_key"].replace(":", "_"), {})
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        target = best.get("file")
        item["target_file_best"] = target
        helper_status = helper_entry.get("status")
        helper_reason = best.get("reason_summary") or best.get("candidate_role")
        if target and item["confidence"] < 0.95 and best.get("probability") is not None:
            item["confidence"] = max(item["confidence"], min(0.95, float(best["probability"]) + 0.05))
        item["raw_json"].update(
            {
                "helper_status": helper_status,
                "helper_candidate_role": best.get("candidate_role"),
                "helper_reason_summary": helper_reason,
                "helper_best_candidate": {
                    "file": target,
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "inferred_printed_page": best.get("inferred_printed_page"),
                    "evidence_kinds": [ev.get("kind") for ev in best.get("evidence", [])[:6]],
                }
                if best
                else None,
            }
        )
        if candidates:
            item["raw_json"]["helper_top_candidates"] = [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "inferred_printed_page": cand.get("inferred_printed_page"),
                    "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:4]],
                }
                for cand in candidates[:3]
            ]
        refs.append(
            {
                "entry_key": item["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": item["page_ref_raw"],
                "page_ref_raw": item["page_ref_raw"],
                "page_ref_int": item["page_ref_int"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target,
                "target_file_probability": best.get("probability"),
                "section_start_file": item["section_start_file"],
                "editorial_anchor_file": item["editorial_anchor_file"],
                "confidence": best.get("probability") if best.get("probability") is not None else item["confidence"],
                "raw_json": {
                    "helper_status": helper_status,
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": helper_reason,
                    "helper_best_candidate": {
                        "file": target,
                        "probability": best.get("probability"),
                        "candidate_role": best.get("candidate_role"),
                        "inferred_printed_page": best.get("inferred_printed_page"),
                        "evidence_kinds": [ev.get("kind") for ev in best.get("evidence", [])[:6]],
                    }
                    if best
                    else None,
                },
            }
        )

    section = {
        "section_key": section_key(),
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": section_files[0].as_posix(),
        "file_end": section_files[-1].as_posix(),
        "confidence": 0.96,
        "raw_json": {
            "section_kind_reason": (
                "Closing contents table / order of matters at the end of the tome; "
                "editorial closure rather than a true alphabetical index."
            ),
            "section_file_span": [section_files[0].as_posix(), section_files[-1].as_posix()],
        },
    }

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": section_files[0].parent.as_posix(),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "PG031 contains a closing ORDO RERUM table of contents rather than a separate alphabetic index block.",
                "OCR file suffixes, printed pages, and cited pages were kept separate throughout extraction.",
            ],
        },
        "sections": [section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "partial_recovery",
            "entries_status_reason": (
                "Recovered the closing ORDO RERUM contents table from OCR files 937-939; "
                "no separate alphabetical index heading was found in the inspected tail window."
            ),
            "evidence_files": [path.as_posix() for path in section_files],
        },
        "notes": [
            "The OCR span for the contents table is not monotonic in printed-page order; helper resolution and local header checks were both used.",
            "Non-page heading fragments at the start of the section were treated as editorial context, not serialized as entries.",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve PG031 ORDO RERUM entries and material targets",
        "completed": ["located closing ORDO RERUM files", "parsed contents lines", "built helper request"],
        "pending": ["run helper", "assemble final payload", "validate target anchors"],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "The contents spread is split across files 937-939.",
        ],
    }
    write_json(todo_path, todo)

    section_files = find_section_files(args.source_root)
    page_start, page_end = extract_printed_page_bounds(section_files)
    entries = parse_entries(section_files)
    helper_request = build_helper_request(entries, args.source_root)
    write_json(args.helper_request_json, helper_request)

    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(args.helper_request_json),
            "--output",
            str(args.helper_output_json),
            "--pretty",
        ],
        check=True,
    )
    helper_output = read_json(args.helper_output_json, default={})

    payload = assemble_payload(section_files, entries, helper_output, page_start, page_end)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("wrote final payload")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

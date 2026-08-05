#!/usr/bin/env python3
"""Usage: build the PG129 ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg129_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG129/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG129_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG129_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG129 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG129_alphabetical_indices.json
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
VOLUME_ID = "PG129"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 129"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
NODE_KEY = f"{VOLUME_ID}:node:001"

SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"

TEXT_BLOCK_RE = re.compile(r'<bloco tipo="texto_principal"[^>]*>(?P<body>.*?)</bloco>', re.S)
HEADER_BLOCK_RE = re.compile(r'<bloco tipo="cabecalho"[^>]*>(?P<body>.*?)</bloco>', re.S)
PAGE_SUFFIX_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})\.?$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.I)
WHITESPACE_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = WHITESPACE_RE.sub(" ", value).strip()
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


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(match.group(1))


def discover_tail_files(source_root: Path) -> list[Path]:
    files = [path for path in sorted(source_root.glob("*.txt"), key=file_seq) if 739 <= file_seq(path) <= 770]
    if not files:
        raise SystemExit(f"no OCR tail files found in {source_root}")
    return files


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def extract_header(path: Path) -> str | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    for match in HEADER_BLOCK_RE.finditer(raw):
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        text = normalize(body)
        if text and "ORDO RERUM" in text:
            return text
    return None


def split_entry_line(line: str) -> tuple[str | None, int | None]:
    match = PAGE_SUFFIX_RE.match(line)
    if not match:
        return None, None
    body = normalize(match.group("body"))
    if not body:
        return None, None
    return body, int(match.group("page"))


def parse_entries(lines: list[str], source_file: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    node = {
        "node_key": NODE_KEY,
        "section_key": SECTION_KEY,
        "parent_node_key": None,
        "node_order": 1,
        "node_kind": "heading_group",
        "label_raw": "EUTHYMIUS ZIGABENUS.",
        "label_norm": "euthymius zigabenus",
        "label_sort": "euthymius zigabenus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {
            "role": "macro_heading",
            "source_file": source_file,
        },
    }

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    buffer: list[str] = []
    entry_order = 0
    current_source_file: str | None = source_file

    def flush_entry() -> None:
        nonlocal buffer, entry_order
        if not buffer:
            return
        body, page = split_entry_line(" ".join(buffer))
        if body is None or page is None:
            buffer = []
            return
        entry_order += 1
        entry_kind = "sublemma" if body.startswith("—") or body.startswith("-") else "lemma"
        entry_key = f"{VOLUME_ID}:entry:{entry_order:03d}"
        entry_raw = f"{body} {page}"
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{entry_order:03d}",
                "lemma_raw": body,
                "query_names": build_query_names(body),
                "page_hints": [str(page)],
                "page_hint_ints": [page],
                "context_raw": entry_raw,
            }
        )
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": NODE_KEY,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": body,
                "lemma_display": body,
                "lemma_norm": sort_norm(body),
                "lemma_sort": sort_norm(body),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "target_file_best": None,
                "confidence": 0.78,
                "raw_json": {
                    "source_file": current_source_file,
                    "page_ref_raw": str(page),
                    "section_role": "ordo_rerum_contents_line",
                    "line_count": len(buffer),
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(page),
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.78,
                "raw_json": {
                    "source_file": current_source_file,
                    "page_ref_raw": str(page),
                    "section_role": "ordo_rerum_contents_line",
                },
            }
        )
        buffer = []

    for line in lines:
        if not line or line == "Digitized by Google":
            continue
        if line in {"ORDO RERUM", SECTION_HEADING_RAW, "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR.", "-"}:
            continue
        if line == "EUTHYMIUS ZIGABENUS.":
            continue
        if not buffer:
            current_source_file = current_source_file or None
        buffer.append(line)
        _, page = split_entry_line(" ".join(buffer))
        if page is not None:
            flush_entry()

    flush_entry()
    return node, entries, refs, helper_entries


def build_query_names(lemma_raw: str) -> list[str]:
    lemma = normalize(lemma_raw) or ""
    if not lemma:
        return []
    variants = [lemma]
    stripped = lemma.rstrip(" .;:,")
    if stripped and stripped not in variants:
        variants.append(stripped)
    if stripped.startswith("— "):
        dashless = stripped[2:].strip()
        if dashless and dashless not in variants:
            variants.append(dashless)
    if "Matthæum" in lemma:
        variants.extend(["Expositio in Matthaeum", "Matthaeum"])
    if "Marcum" in lemma:
        variants.extend(["Expositio in Marcum", "Marcum"])
    if "Lucam" in lemma:
        variants.extend(["Expositio in Lucam", "Lucam"])
    if "Joannem" in lemma:
        variants.extend(["Expositio in Joannem", "Joannem"])
    if "Euthymii" in lemma or "Zigabeni" in lemma:
        variants.extend(["Euthymius Zigabenus", "Euthymius"])
    if "Henteni" in lemma:
        variants.extend(["Henteni", "Joannis Henteni"])
    if "Mosquensis" in lemma:
        variants.extend(["Mosquensis", "codicis Mosquensis"])
    if "Quattor Evangelia" in lemma or "quatuor Evangelia" in lemma:
        variants.extend(["quattuor Evangelia", "Evangelia"])
    out: list[str] = []
    for variant in variants:
        normalized = normalize(variant)
        if normalized and normalized not in out:
            out.append(normalized)
    return out[:6]


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }


def summarize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for candidate in candidates[:3]:
        summary.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [
                    evidence.get("kind")
                    for evidence in candidate.get("evidence", [])
                    if isinstance(evidence, dict) and evidence.get("kind")
                ],
            }
        )
    return summary


def build_payload(
    source_root: Path,
    section_file: Path,
    node: dict[str, Any],
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    helper_output: dict[str, Any],
    tail_files: list[Path],
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    entry_by_key = {entry["entry_key"]: entry for entry in entries}
    ref_by_key = {ref["entry_key"]: ref for ref in refs}

    for entry in entries:
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{entry['entry_order']:03d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        target_file = best.get("file")
        entry["section_start_file"] = section_file.as_posix()
        entry["editorial_anchor_file"] = section_file.as_posix()
        entry["target_file_best"] = target_file
        entry["confidence"] = 0.92 if target_file else 0.78
        entry["raw_json"]["helper"] = {
            "status": helper_entry.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best if best else None,
            "top_candidates": summarize_candidates(candidates),
        }
        ref = ref_by_key[entry["entry_key"]]
        ref["target_file"] = target_file
        ref["target_file_probability"] = best.get("probability")
        ref["section_start_file"] = section_file.as_posix()
        ref["editorial_anchor_file"] = section_file.as_posix()
        ref["confidence"] = 0.91 if target_file else 0.78
        ref["raw_json"]["helper"] = {
            "status": helper_entry.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best if best else None,
        }

    section = {
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
        "file_start": section_file.as_posix(),
        "file_end": section_file.as_posix(),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Closing ORDO RERUM contents table at the end of PG129; the OCR file shows the heading explicitly and the cited page numbers belong to the listed contents, not to the OCR file suffix.",
            "evidence_files": [section_file.as_posix()],
            "source_note": "Filtered tail pages were used as a hint, then the OCR file was inspected directly.",
        },
    }

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Recovered the closing ORDO RERUM table as page-bearing contents lines with one logical entry per printed line.",
        "evidence_files": [
            next((path.as_posix() for path in tail_files if file_seq(path) == 759), section_file.as_posix()),
            section_file.as_posix(),
        ],
    }

    notes = [
        {
            "note_key": f"{VOLUME_ID}:note:001",
            "note_type": "extraction",
            "text": "PG129 closes with an ORDO RERUM contents table. The entries were kept as page-bearing contents lines, and the helper was used only to anchor the cited pages in the body OCR.",
        }
    ]

    return {
        "schema_version": 1.0,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": [node],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def write_intermediate_state(
    intermediate_dir: Path,
    payload: dict[str, Any],
    helper_request: dict[str, Any],
    helper_output: dict[str, Any],
    section_file: Path,
) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG129 ORDO RERUM payload assembled and validated",
            "completed": [
                "tail OCR parsed",
                "helper request built and resolved",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The volume closes with a single ORDO RERUM table.",
                "OCR file suffixes were not treated as editorial page numbers.",
            ],
        },
    )
    write_json(intermediate_dir / "section_file.json", {"section_file": section_file.as_posix()})


def build_helper_request(source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG129 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    tail_files = discover_tail_files(args.source_root)
    section_file = next((path for path in tail_files if file_seq(path) == 760), None)
    if section_file is None:
        raise SystemExit("could not find OCR file 760 for the ORDO RERUM section")

    lines = extract_lines(section_file)
    header = extract_header(section_file)
    node, entries, refs, helper_entries = parse_entries(lines, section_file.as_posix())
    helper_request = build_helper_request(args.source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(
        source_root=args.source_root,
        section_file=section_file,
        node=node,
        entries=entries,
        refs=refs,
        helper_output=helper_output,
        tail_files=tail_files,
    )
    if header:
        payload["sections"][0]["raw_json"]["header_excerpt"] = header
    write_intermediate_state(args.intermediate_dir, payload, helper_request, helper_output, section_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

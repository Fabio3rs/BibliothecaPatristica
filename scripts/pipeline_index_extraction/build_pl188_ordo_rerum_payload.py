#!/usr/bin/env python3
"""Usage: build the PL188 closing ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl188_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL188/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL188_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL188_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL188 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL188_alphabetical_indices.json

The OCR tail contains the closing contents table for the tome. The script
extracts the page-bearing lines, preserves the non-page headings as entries,
builds a helper request for material target resolution, writes intermediate
checkpoints, and assembles the canonical JSON payload.
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

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL188"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 188"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
PAGE_ONLY_RE = re.compile(r"^\d{1,4}\.?$")
PAGE_TRAIL_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})\.?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def block_text(path: Path, block_type: str) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo != block_type:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google" or line == "-":
                continue
            out.append(line)
    return out


def header_numbers(path: Path) -> list[int]:
    nums: list[int] = []
    for line in block_text(path, "cabecalho"):
        for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", line):
            value = int(num)
            if 0 <= value <= 5000:
                nums.append(value)
    return nums


def section_files(source_root: Path) -> list[Path]:
    matches: list[Path] = []
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        header_lines = block_text(path, "cabecalho")
        header_text = " ".join(header_lines)
        if "ORDO RERUM" in header_text.upper():
            matches.append(path)
    if not matches:
        raise SystemExit(f"no ORDO RERUM OCR files found in {source_root}")
    return matches


def section_heading_raw(paths: list[Path]) -> str:
    first = paths[0]
    header_lines = block_text(first, "cabecalho")
    header_text = normalize(" ".join(header_lines))
    if "ORDO RERUM" in header_text.upper():
        return header_text
    return "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."


def page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        for num in header_numbers(path):
            mapping.setdefault(num, path.as_posix())
    for page, target in estimator_page_map(
        volume_id=VOLUME_ID,
        collection=COLLECTION,
        source_root=source_root,
    ).items():
        mapping.setdefault(page, target)
    return mapping


def page_line(line: str) -> tuple[str, int | None]:
    if PAGE_ONLY_RE.fullmatch(line):
        return "", int(line.rstrip("."))
    m = PAGE_TRAIL_RE.match(line)
    if not m:
        return line, None
    body = normalize(m.group("body").rstrip(" ,;:"))
    return body, int(m.group("page"))


def is_heading_start(line: str) -> bool:
    if not line:
        return False
    if line.startswith(
        (
            "PARS ",
            "LIBER ",
            "ANNO ",
            "APPENDIX ",
            "EPISTOLAE ",
            "SERMO ",
            "CAP. ",
            "CAPITULUM ",
            "ORDERICUS VITALIS",
            "HISTORIA ECCLESIASTICA",
            "ODO ABBAS",
            "FASTREDUS",
            "JOANNES CIRITA",
            "THEOBALDUS",
            "GAUFRIDUS",
            "GILBERTUS DE HOILLANDIA",
            "EPISTOLAE VARIORUM",
        )
    ):
        return True
    if re.fullmatch(r"[A-ZÆŒ0-9][A-ZÆŒ0-9 .,:;\-\(\)\[\]/]+", line):
        return True
    return False


def heading_cont(prev: str, cur: str) -> bool:
    prev = prev or ""
    if not prev:
        return False
    if prev.endswith(("-", ",", ";")):
        return True
    if not prev.endswith(".") and len(cur) <= 20 and not re.match(r"^[IVXLCDM]+\.", cur):
        return True
    if prev.endswith(".") and cur.isupper() and len(cur) <= 12 and not re.search(r"\d", cur):
        return True
    if cur and cur[0].islower() and not re.match(r"^[IVXLCDM]+\.", cur):
        return True
    return False


def parse_items(section_paths: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    buf: list[str] = []
    buf_kind: str | None = None
    entry_order = 0

    def flush(kind: str, *, page: int | None = None, source_file: str | None = None) -> None:
        nonlocal buf, buf_kind, entry_order
        if not buf and page is None:
            return
        text = normalize(" ".join(buf))
        if page is not None:
            entry_raw = f"{text} {page}".strip() if text else str(page)
            lemma_raw = text or None
        else:
            entry_raw = text
            lemma_raw = text or None
        entry_order += 1
        items.append(
            {
                "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": page,
                "section_start_file": section_paths[0].as_posix(),
                "editorial_anchor_file": source_file or section_paths[0].as_posix(),
                "target_file_best": None,
                "confidence": 0.8 if page is not None else 0.72,
                "raw_json": {
                    "source_file": source_file,
                    "entry_kind_reason": "contents-table line from closing ORDO RERUM",
                },
            }
        )
        buf = []
        buf_kind = None

    for path in section_paths:
        for line in block_text(path, "texto_principal"):
            body, page = page_line(line)
            if page is not None:
                if buf_kind == "heading":
                    flush("heading", source_file=path.as_posix())
                if buf_kind == "entry":
                    if body:
                        buf.append(body)
                    flush("entry", page=page, source_file=path.as_posix())
                else:
                    entry_order += 1
                    items.append(
                        {
                            "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
                            "entry_order": entry_order,
                            "entry_kind": "heading_group",
                            "lemma_raw": body or None,
                            "lemma_display": body or None,
                            "lemma_norm": sort_norm(body),
                            "lemma_sort": sort_norm(body),
                            "entry_raw": f"{body} {page}".strip() if body else str(page),
                            "context_raw": f"{body} {page}".strip() if body else str(page),
                            "heading_letter": None,
                            "inferred_printed_page": page,
                            "section_start_file": section_paths[0].as_posix(),
                            "editorial_anchor_file": path.as_posix(),
                            "target_file_best": None,
                            "confidence": 0.8,
                            "raw_json": {
                                "source_file": path.as_posix(),
                                "entry_kind_reason": "page-bearing line from closing ORDO RERUM",
                            },
                        }
                    )
                continue

            if is_heading_start(line):
                if buf_kind == "entry":
                    flush("entry", source_file=path.as_posix())
                if buf_kind == "heading" and heading_cont(" ".join(buf), line):
                    buf.append(line)
                else:
                    if buf_kind == "heading":
                        flush("heading", source_file=path.as_posix())
                    buf = [line]
                    buf_kind = "heading"
                continue

            if buf_kind == "heading":
                if heading_cont(" ".join(buf), line):
                    buf.append(line)
                    continue
                flush("heading", source_file=path.as_posix())

            if buf_kind == "entry":
                buf.append(line)
            else:
                buf = [line]
                buf_kind = "entry"

    if buf:
        flush(buf_kind or "entry", source_file=section_paths[-1].as_posix())
    return items


def helper_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    page_int = item.get("inferred_printed_page")
    if page_int is None:
        return None
    lemma = normalize(item.get("lemma_raw") or item.get("entry_raw"))
    query_names = [lemma]
    stripped = lemma.rstrip(" .;:,")
    if stripped and stripped not in query_names:
        query_names.append(stripped)
    return {
        "entry_id": item["entry_key"].replace(":", "_").lower(),
        "lemma_raw": item.get("lemma_raw") or item.get("entry_raw") or "",
        "query_names": [q for q in query_names if q],
        "page_hints": [str(page_int)],
        "page_hint_ints": [page_int],
        "context_raw": item.get("entry_raw") or item.get("context_raw") or "",
    }


def build_helper_request(section_paths: list[Path], items: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    entries = [entry for item in items if (entry := helper_entry(item)) is not None]
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
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


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            out[entry_id] = item
    return out


def build_payload(
    section_paths: list[Path],
    items: list[dict[str, Any]],
    helper_output: dict[str, Any],
    page_lookup: dict[int, str],
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    section_start_file = section_paths[0].as_posix()
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for item in items:
        entry_key = f"{VOLUME_ID}:entry:{item['entry_order']:04d}"
        helper_entry_id = entry_key.replace(":", "_").lower()
        helper_row = helper_map.get(helper_entry_id, {})
        best = helper_row.get("best_candidate") or {}
        candidates = helper_row.get("candidates") or []
        status = helper_row.get("status")
        page_int = item.get("inferred_printed_page")
        target_file = page_lookup.get(page_int) if page_int is not None else None
        target_prob = best.get("probability") if target_file else None
        if page_int is None:
            confidence = 0.82
            editorial_anchor_file = section_start_file
        elif target_file:
            confidence = 0.91 if status == "resolved" or not helper_row else 0.88
            editorial_anchor_file = target_file
        else:
            confidence = 0.64
            editorial_anchor_file = section_start_file
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": item["entry_order"],
            "entry_kind": item["entry_kind"],
            "lemma_raw": item.get("lemma_raw"),
            "lemma_display": item.get("lemma_display"),
            "lemma_norm": item.get("lemma_norm"),
            "lemma_sort": item.get("lemma_sort"),
            "entry_raw": item.get("entry_raw"),
            "context_raw": item.get("context_raw"),
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": target_file,
            "confidence": confidence,
            "raw_json": {
                **(item.get("raw_json") or {}),
                "helper_entry_id": helper_entry_id if page_int is not None else None,
                "helper_status": status,
                "helper_best_candidate": best or None,
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                    }
                    for cand in candidates[:3]
                ]
                if candidates
                else [],
                "page_lookup_resolved": bool(target_file),
            },
        }
        entries.append(entry)
        if page_int is not None:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_int),
                    "page_ref_raw": str(page_int),
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": editorial_anchor_file,
                    "confidence": confidence - 0.03 if confidence > 0.03 else confidence,
                    "raw_json": {
                        "helper_entry_id": helper_entry_id,
                        "helper_status": status,
                        "source_file": item.get("raw_json", {}).get("source_file"),
                    },
                }
            )

    section_page_start = min(header_numbers(section_paths[0]))
    section_page_end = max(header_numbers(section_paths[-1]))
    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": section_heading_raw(section_paths),
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": section_page_start,
        "page_end": section_page_end,
        "file_start": section_start_file,
        "file_end": section_paths[-1].as_posix(),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Editorial contents table at the end of the tome, listing works and printed-page anchors.",
            "evidence_files": [p.as_posix() for p in section_paths],
        },
    }

    page_refs_total = sum(1 for item in items if item.get("inferred_printed_page") is not None)
    unresolved_page_refs = sum(
        1 for item in items if item.get("inferred_printed_page") is not None and page_lookup.get(item["inferred_printed_page"]) is None
    )
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            f"Recovered the closing ORDO RERUM contents table from {len(section_paths)} OCR spreads; "
            f"{unresolved_page_refs} of {page_refs_total} page-bearing items point to pages outside the current OCR window."
        ),
        "evidence_files": [p.as_posix() for p in section_paths],
    }
    notes = [
        {
            "note_type": "extraction",
            "source": "build_pl188_ordo_rerum_payload.py",
            "status": "completed",
        },
        {
            "note_type": "helper",
            "source": "PL188_helper_output.json",
            "status": helper_output.get("status") if isinstance(helper_output, dict) else None,
        },
    ]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": section_paths[0].parent.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": [section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL188 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    section_paths = section_files(args.source_root)
    items = parse_items(section_paths)
    page_lookup = page_map(args.source_root)

    helper_request = build_helper_request(section_paths, items, args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(section_paths, items, helper_output, page_lookup)

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "helper_request_json": args.helper_request_json.as_posix(),
            "helper_output_json": args.helper_output_json.as_posix(),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PL188 ORDO RERUM payload and preserve unresolved page anchors.",
            "completed": [
                "section files identified",
                "closing contents lines extracted",
                "helper request generated",
                "helper run completed",
            ],
            "pending": [
                "review unresolved page anchors outside source_root",
                "assemble and validate final payload",
            ],
            "blocked": [],
            "notes": [
                "Preserve OCR literals, including uncertain year headings and inherited hyphens.",
                "Use null target_file for page anchors that do not exist in the current OCR window.",
            ],
        },
    )

    final_path = args.output_file
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

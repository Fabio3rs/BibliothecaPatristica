#!/usr/bin/env python3
"""Usage: build the PL121 closing ORDO RERUM payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl121_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL121/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL121_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL121_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL121 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL121_alphabetical_indices.json
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


VOLUME_ID = "PL121"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 121"
SECTION_KEY = f"{VOLUME_ID}:section:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
FILE_START_SEQ = 583
FILE_END_SEQ = 588


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_page_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo = re.search(r'tipo="([^"]+)"', attrs)
        if (tipo.group(1).strip().lower() if tipo else "") not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = norm(raw_line)
            if line:
                lines.append(line)
    return lines


def is_header_line(line: str) -> bool:
    return bool(
        re.fullmatch(r"\d+ ORDO RERUM(?: QU[ÆAE] IN HOC TOMO CONTINENTUR\.)?", line)
        or re.fullmatch(r"ORDO RERUM", line)
        or re.fullmatch(r"QU[ÆAE] IN HOC TOMO CONTINENTUR\.?", line)
        or re.fullmatch(r"PATROL\. CXXI\.", line)
        or re.fullmatch(r"Digitized by Google", line)
        or re.fullmatch(r"\d+\.", line)
    )


def extract_entries(source_root: Path) -> list[dict[str, Any]]:
    page_re = re.compile(r"\b\d{1,4}\b")
    split_after_page_re = re.compile(r"\b\d{1,4}\b(?=(?:\s+[A-ZÀ-ÝIVXLCDM]|$))")

    entries: list[dict[str, Any]] = []
    order = 0

    for seq in range(FILE_START_SEQ, FILE_END_SEQ + 1):
        source_file = source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{seq}.txt"
        lines = load_page_lines(source_file)
        pending: list[str] = []

        for line in lines:
            if is_header_line(line):
                continue

            if not page_re.search(line):
                pending.append(line)
                continue

            combined = " ".join([*pending, line]).strip()
            pending = []

            parts: list[str] = []
            start = 0
            for match in split_after_page_re.finditer(combined):
                end = match.end()
                chunk = combined[start:end].strip()
                if chunk:
                    parts.append(chunk)
                start = end
            tail = combined[start:].strip()
            if tail:
                if parts:
                    parts[-1] = f"{parts[-1]} {tail}".strip()
                else:
                    pending.append(tail)

            for part in parts:
                order += 1
                refs = [int(n) for n in re.findall(r"\b\d{1,4}\b", part)]
                lemma_raw = norm(re.sub(r"\b\d{1,4}\b", " ", part)) or part
                entry = {
                    "order": order,
                    "source_file": str(source_file),
                    "entry_text": part,
                    "lemma_raw": lemma_raw,
                    "refs": refs,
                }
                entries.append(entry)

        if pending:
            order += 1
            part = " ".join(pending).strip()
            refs = [int(n) for n in re.findall(r"\b\d{1,4}\b", part)]
            lemma_raw = norm(re.sub(r"\b\d{1,4}\b", " ", part)) or part
            entries.append(
                {
                    "order": order,
                    "source_file": str(source_file),
                    "entry_text": part,
                    "lemma_raw": lemma_raw,
                    "refs": refs,
                }
            )

    return entries


def compress_helper_entry(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates = []
    for candidate in helper_entry.get("candidates", [])[:3]:
        candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in candidate.get("evidence", []) if ev.get("kind")],
            }
        )
    return {
        "status": helper_entry.get("status"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper_entry.get("candidates", []) or []),
        "candidates": candidates,
    }


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        refs = entry.get("refs", [])
        if not refs:
            continue
        for idx, ref in enumerate(refs, 1):
            helper_entries.append(
                {
                    "entry_id": f"{VOLUME_ID.lower()}_{entry['order']:04d}_{idx:02d}",
                    "lemma_raw": entry["lemma_raw"],
                    "query_names": [entry["lemma_raw"]],
                    "page_hints": [str(ref)],
                    "page_hint_ints": [ref],
                    "context_raw": entry["entry_text"],
                }
            )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
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
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    entries = extract_entries(source_root)
    write_json(intermediate_dir / "segments.json", entries)

    helper_request = build_helper_request(source_root, entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_map = helper_index(helper_output)
    write_json(intermediate_dir / "helper_output.json", helper_output)

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": 1159,
        "page_end": 1168,
        "file_start": str(source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{FILE_START_SEQ}.txt"),
        "file_end": str(source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{FILE_END_SEQ}.txt"),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Closing ORDO RERUM contents table at the end of the tome.",
            "evidence_files": [
                str(source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{seq}.txt")
                for seq in range(FILE_START_SEQ, FILE_END_SEQ + 1)
            ],
            "heading_variants": [
                "ORDO RERUM QUE IN HOC TOMO CONTINENTUR.",
                "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            ],
        },
    }

    final_entries: list[dict[str, Any]] = []
    final_refs: list[dict[str, Any]] = []

    for entry in entries:
        entry_key = f"{VOLUME_ID}:entry:{entry['order']:04d}"
        ref_numbers = entry.get("refs", [])
        helper_refs: list[dict[str, Any]] = []
        target_file_best = entry["source_file"] if not ref_numbers else None

        for ref_order, ref_number in enumerate(ref_numbers, 1):
            helper_id = f"{VOLUME_ID.lower()}_{entry['order']:04d}_{ref_order:02d}"
            helper_entry = helper_map.get(helper_id)
            best = helper_entry.get("best_candidate") if helper_entry else None
            best_file = best.get("file") if best else None
            best_prob = best.get("probability") if best else None
            if target_file_best is None and best_file:
                target_file_best = best_file
            final_refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(ref_number),
                    "page_ref_raw": str(ref_number),
                    "page_ref_int": ref_number,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": best_file,
                    "target_file_probability": best_prob,
                    "section_start_file": entry["source_file"],
                    "editorial_anchor_file": entry["source_file"],
                    "confidence": 0.88 if best_file else 0.58,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "helper": compress_helper_entry(helper_entry),
                    },
                }
            )
            helper_refs.append(
                {
                    "page_ref": ref_number,
                    "helper_entry_id": helper_id,
                    "helper": compress_helper_entry(helper_entry),
                }
            )

        final_entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry["order"],
                "entry_kind": "heading_group",
                "lemma_raw": entry["lemma_raw"],
                "lemma_display": entry["lemma_raw"],
                "lemma_norm": sort_norm(entry["lemma_raw"]),
                "lemma_sort": sort_norm(entry["lemma_raw"]),
                "entry_raw": entry["entry_text"],
                "context_raw": entry["entry_text"],
                "heading_letter": None,
                "inferred_printed_page": ref_numbers[0] if ref_numbers else None,
                "section_start_file": str(source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{FILE_START_SEQ}.txt"),
                "editorial_anchor_file": entry["source_file"],
                "target_file_best": target_file_best,
                "confidence": 0.9 if ref_numbers else 0.72,
                "raw_json": {
                    "source_file": entry["source_file"],
                    "section_kind": "ordo_rerum",
                    "page_hints": ref_numbers,
                    "helper_refs": helper_refs[:3],
                    "note": "Line-level ORDO RERUM contents fragment preserved conservatively.",
                },
            }
        )

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from files 583-588 and preserved printed page anchors literally.",
        "evidence_files": [
            str(source_root / f"5afc31b5-6ccd-4f33-9af9-78cbc6841afa-{seq}.txt")
            for seq in range(FILE_START_SEQ, FILE_END_SEQ + 1)
        ],
    }

    notes = [
        "The volume contains a single closing ORDO RERUM section rather than an alphabetical subject index.",
        "Helper lookup was run for every page-linked fragment in the contents table.",
        "OCR file suffixes were kept distinct from printed page numbers in all refs.",
    ]

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": [],
        "entries": final_entries,
        "refs": final_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL121 ORDO RERUM alphabetical payload.")
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
        "current_focus": "Resolve the closing ORDO RERUM table and preserve OCR page anchors literally.",
        "completed": [
            "identified the closing ORDO RERUM section",
            "parsed OCR tail files 583-588 into fragments",
        ],
        "pending": [
            "run index_target_locator on every page-linked fragment",
            "assemble and validate the final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep the OCR file suffix, printed page, and cited page separate.",
            "Section evidence is limited to the final six OCR files in the volume tail.",
        ],
    }
    write_json(todo_path, todo)

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

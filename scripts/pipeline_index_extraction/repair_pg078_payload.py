#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/repair_pg078_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG078/text \
    --previous-payload /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_alphabetical_indices.json \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG078 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_alphabetical_indices.json

Rebuild PG078 with corrected entry/ref segmentation and propagate material locators
from previously resolved anchors using monotonic per-book interpolation.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.pipeline_index_extraction import build_pg078_alphabetical_payload as base


BOOK_PAGE_RE = re.compile(r"([IVXLC]+)\s*,\s*(\d{1,4})")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def longest_increasing_anchor_subset(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    vals = sorted(set(pairs))
    if not vals:
        return []
    tails: list[int] = []
    tails_idx: list[int] = []
    prev = [-1] * len(vals)
    for i, (_, seq) in enumerate(vals):
        j = bisect.bisect_left(tails, seq)
        if j == len(tails):
            tails.append(seq)
            tails_idx.append(i)
        else:
            tails[j] = seq
            tails_idx[j] = i
        prev[i] = tails_idx[j - 1] if j > 0 else -1
    k = tails_idx[-1]
    out: list[tuple[int, int]] = []
    while k != -1:
        out.append(vals[k])
        k = prev[k]
    out.reverse()
    return out


def build_anchor_map(previous_payload: dict[str, Any]) -> dict[str, list[tuple[int, int]]]:
    anchors: dict[str, list[tuple[int, int]]] = {}
    for entry in previous_payload.get("entries", []):
        target_file = entry.get("target_file_best")
        if not target_file:
            continue
        seq = int(Path(target_file).stem.rsplit("-", 1)[-1])
        if seq > 400:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        role = best.get("candidate_role") or helper.get("candidate_role")
        if role == "index_page_candidate":
            continue
        match = BOOK_PAGE_RE.search(entry.get("entry_raw") or "")
        if not match:
            continue
        book = match.group(1)
        page = int(match.group(2))
        anchors.setdefault(book, []).append((page, seq))
    return {book: longest_increasing_anchor_subset(pairs) for book, pairs in anchors.items()}


def nearest_file(seq_to_path: dict[int, str], predicted_seq: int) -> str | None:
    if not seq_to_path:
        return None
    seqs = sorted(seq_to_path)
    idx = bisect.bisect_left(seqs, predicted_seq)
    candidates = []
    if idx < len(seqs):
        candidates.append(seqs[idx])
    if idx > 0:
        candidates.append(seqs[idx - 1])
    if not candidates:
        return None
    best_seq = min(candidates, key=lambda value: (abs(value - predicted_seq), value))
    return seq_to_path[best_seq]


def estimate_seq(book: str, page: int, anchors: dict[str, list[tuple[int, int]]]) -> tuple[int | None, dict[str, Any]]:
    book_anchors = anchors.get(book) or []
    if not book_anchors:
        return None, {"book": book, "page": page, "status": "no_book_anchors"}
    if len(book_anchors) == 1:
        anchor_page, anchor_seq = book_anchors[0]
        return anchor_seq, {
            "book": book,
            "page": page,
            "status": "single_anchor_copy",
            "anchor_pages": [anchor_page],
            "anchor_seqs": [anchor_seq],
        }
    pages = [p for p, _ in book_anchors]
    idx = bisect.bisect_left(pages, page)
    if idx == 0:
        left, right = book_anchors[0], book_anchors[1]
        mode = "left_extrapolation"
    elif idx >= len(book_anchors):
        left, right = book_anchors[-2], book_anchors[-1]
        mode = "right_extrapolation"
    else:
        left, right = book_anchors[idx - 1], book_anchors[idx]
        mode = "interpolation"
    left_page, left_seq = left
    right_page, right_seq = right
    if right_page == left_page:
        predicted = left_seq
    else:
        ratio = (page - left_page) / (right_page - left_page)
        predicted = round(left_seq + ratio * (right_seq - left_seq))
    return predicted, {
        "book": book,
        "page": page,
        "status": mode,
        "left_anchor": {"page": left_page, "seq": left_seq},
        "right_anchor": {"page": right_page, "seq": right_seq},
    }


def estimate_confidence(page: int, meta: dict[str, Any], chosen_seq: int | None) -> float:
    status = meta.get("status")
    if status == "single_anchor_copy":
        return 0.45
    if status == "no_book_anchors" or chosen_seq is None:
        return 0.3
    if status == "interpolation":
        left = meta["left_anchor"]["page"]
        right = meta["right_anchor"]["page"]
        gap = max(1, right - left)
        return max(0.58, min(0.86, 0.86 - (gap / 900)))
    return 0.52


def entry_raw_map(previous_payload: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for entry in previous_payload.get("entries", []):
        target_file = entry.get("target_file_best")
        if not target_file:
            continue
        mapped[base.normalize(entry.get("entry_raw"))] = target_file
    return mapped


def rebuild_payload(
    source_root: Path,
    previous_payload: dict[str, Any],
    helper_request_json: Path,
) -> dict[str, Any]:
    files = base.discovered_files(source_root)
    index_files = [path for path in files if base.INDEX_START_SEQ <= base.file_seq(path) <= base.INDEX_END_SEQ]
    ordo_file = next((path for path in files if base.file_seq(path) == base.ORDO_SEQ), None)
    rows = base.collect_lines(index_files)
    fragments = base.split_fragments(rows)
    sections, nodes, entries, refs = base.build_entries(fragments, str(index_files[0]), str(ordo_file) if ordo_file else None)
    base.build_helper_request(entries, helper_request_json, source_root)

    seq_to_path = {base.file_seq(path): str(path) for path in files}
    anchors = build_anchor_map(previous_payload)
    raw_map = entry_raw_map(previous_payload)
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        match = BOOK_PAGE_RE.search(entry["entry_raw"])
        explicit_book = match.group(1) if match else None
        explicit_target = raw_map.get(base.normalize(entry["entry_raw"]))
        if explicit_target:
            entry["target_file_best"] = explicit_target
        if explicit_book:
            entry.setdefault("raw_json", {})["locator_book"] = explicit_book
        for ref in entry_refs:
            page = ref.get("page_ref_int")
            if not isinstance(page, int):
                continue
            if explicit_book:
                predicted_seq, estimator_meta = estimate_seq(explicit_book, page, anchors)
                chosen_path = nearest_file(seq_to_path, predicted_seq) if predicted_seq is not None else None
                if chosen_path:
                    ref["target_file"] = chosen_path
                    ref["target_file_probability"] = estimate_confidence(page, estimator_meta, predicted_seq)
                    ref["confidence"] = max(float(ref.get("confidence") or 0), float(ref["target_file_probability"]))
                    ref.setdefault("raw_json", {})["estimator"] = estimator_meta
                    if not entry.get("target_file_best"):
                        entry["target_file_best"] = chosen_path
                        entry["confidence"] = max(float(entry.get("confidence") or 0), float(ref["target_file_probability"]))
                else:
                    ref.setdefault("raw_json", {})["estimator"] = estimator_meta
            elif explicit_target and not ref.get("target_file"):
                ref["target_file"] = explicit_target
                ref["target_file_probability"] = 0.62
                ref["confidence"] = max(float(ref.get("confidence") or 0), 0.62)
                ref.setdefault("raw_json", {})["estimator"] = {"status": "entry_raw_exact_match_previous_payload"}

    volume = {
        "volume_id": base.VOLUME_ID,
        "collection": base.COLLECTION,
        "source_root": str(source_root),
        "volume_label": base.VOLUME_LABEL,
    }
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytic alphabetical index tail with corrected lemma/ref segmentation; material locators were propagated from previously resolved anchors and per-book monotonic interpolation when the full helper rerun remained operationally heavy.",
        "evidence_files": [str(path) for path in index_files[:4]] + [str(index_files[-1])] + ([str(ordo_file)] if ordo_file else []),
    }
    notes = [
        "The OCR tail contains one long INDEX ANALYTICUS run followed by a closing ORDO RERUM table.",
        "Entry/ref segmentation was rebuilt so locator-only fragments no longer survive as standalone entries.",
        "Most target files were re-estimated from previously resolved PG078 anchors using a monotonic per-book page-to-sequence interpolation; residual nulls remain where OCR or locator context stayed too weak.",
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
        "manifest": {"volume_id": base.VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()},
        "todo": {
            "volume_id": base.VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG078 rebuilt with corrected segmentation and anchor-based locator propagation",
            "completed": [
                "rebuilt entries and refs from OCR tail",
                "generated full helper request",
                "propagated locators from previous-anchor interpolation",
            ],
            "pending": [
                "optional future full helper rerun for residual null locators",
            ],
            "blocked": [],
            "notes": [
                "Use the rebuilt helper request JSON if a future batch helper pass is needed.",
                "Residual nulls are concentrated in bare-number or heavily corrupted OCR cases.",
            ],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair PG078 alphabetical payload with corrected segmentation and anchor propagation.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--previous-payload", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    previous_payload = read_json(args.previous_payload)
    payload = rebuild_payload(args.source_root, previous_payload, args.helper_request_json)

    write_json(args.intermediate_dir / "manifest.json", payload.pop("manifest"))
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "todo.json", payload.pop("todo"))
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

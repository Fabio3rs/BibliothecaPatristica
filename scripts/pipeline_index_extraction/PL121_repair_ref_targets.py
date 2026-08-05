#!/usr/bin/env python3
"""Usage: repair PL121 ref target files from editorial page estimates.

Run from the repository root:
  python scripts/pipeline_index_extraction/PL121_repair_ref_targets.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(ROOT := Path("/homessddata/Projects/pdfocr")))

from patristica_pipeline.editorial_page_estimator import estimate_editorial_pages


VOLUME_ID = "PL121"
SOURCE_ROOT = ROOT / "teste/PL121/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL121_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL121"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_seq(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).stem.rsplit("-", 1)[-1])
    except Exception:
        return None


def page_values(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, int)]
    return []


def build_page_indexes(source_root: Path) -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    """Return exact-best and exact-candidate indexes keyed by page number."""

    est = estimate_editorial_pages(volume_id=VOLUME_ID, source_root=source_root, collection="PL")
    best_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    candidate_map: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for item in est.get("files", []):
        file_path = str(item.get("file") or "")
        if not file_path:
            continue

        best_pages = set()
        for key in ("best_guess", "best_single_page", "best_left_page", "best_right_page"):
            best_pages.update(page_values(item.get(key)))

        for page in best_pages:
            best_map[page].append(
                {
                    "file": file_path,
                    "confidence": float(item.get("confidence") or 0.0),
                    "file_seq": file_seq(file_path),
                    "pages": sorted(best_pages),
                }
            )

        for cand in item.get("candidate_editorial_pages") or []:
            if not isinstance(cand, dict):
                continue
            pages = cand.get("pages") or []
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, int):
                    continue
                candidate_map[page].append(
                    {
                        "file": file_path,
                        "score": float(cand.get("score") or 0.0),
                        "confidence": float(item.get("confidence") or 0.0),
                        "file_seq": file_seq(file_path),
                        "pages": [p for p in pages if isinstance(p, int)],
                    }
                )

    return best_map, candidate_map


def choose_target(
    page_ref_int: int,
    best_map: dict[int, list[dict[str, Any]]],
    candidate_map: dict[int, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str]:
    best_candidates = best_map.get(page_ref_int) or []
    if best_candidates:
        chosen = sorted(
            best_candidates,
            key=lambda item: (-item["confidence"], item["file_seq"] if item["file_seq"] is not None else 10**9),
        )[0]
        return chosen, "best_guess_exact"

    fallback_candidates = candidate_map.get(page_ref_int) or []
    if fallback_candidates:
        chosen = sorted(
            fallback_candidates,
            key=lambda item: (
                -item["score"],
                -item["confidence"],
                item["file_seq"] if item["file_seq"] is not None else 10**9,
            ),
        )[0]
        return chosen, "candidate_exact_fallback"

    return None, "unresolved"


def probability_for(choice: dict[str, Any] | None, method: str) -> float | None:
    if not choice:
        return None
    if method == "best_guess_exact":
        conf = float(choice.get("confidence") or 0.0)
        return round(min(0.99, max(0.55, conf)), 6)
    score = float(choice.get("score") or 0.0)
    return round(min(0.9, max(0.55, 0.5 + score / 10.0)), 6)


def compact_candidates(candidates: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in candidates[:limit]:
        compacted.append(
            {
                "file": item.get("file"),
                "file_seq": item.get("file_seq"),
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "pages": item.get("pages"),
            }
        )
    return compacted


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    best_map, candidate_map = build_page_indexes(SOURCE_ROOT)

    resolved_refs = 0
    unresolved_refs = 0

    for ref in payload.get("refs", []):
        page_ref_int = ref.get("page_ref_int")
        if not isinstance(page_ref_int, int):
            unresolved_refs += 1
            continue

        choice, method = choose_target(page_ref_int, best_map, candidate_map)
        raw_json = ref.setdefault("raw_json", {})
        raw_json["target_resolution"] = {
            "source": "editorial_page_estimator",
            "method": method,
            "page_ref_int": page_ref_int,
        }

        if choice:
            ref["target_file"] = choice["file"]
            ref["target_file_probability"] = probability_for(choice, method)
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), float(ref["target_file_probability"] or 0.0))
            raw_json["target_resolution"]["chosen_file"] = choice["file"]
            raw_json["target_resolution"]["chosen_file_seq"] = choice.get("file_seq")
            raw_json["target_resolution"]["chosen_probability"] = ref.get("target_file_probability")
            if method == "best_guess_exact":
                raw_json["target_resolution"]["top_candidates"] = compact_candidates(best_map.get(page_ref_int) or [])
            else:
                raw_json["target_resolution"]["top_candidates"] = compact_candidates(candidate_map.get(page_ref_int) or [])
            resolved_refs += 1
        else:
            raw_json["target_resolution"]["chosen_file"] = None
            raw_json["target_resolution"]["top_candidates"] = []
            unresolved_refs += 1

    if payload.get("sections"):
        section = payload["sections"][0]
        section_start_file = section.get("file_start")
        section_end_file = section.get("file_end")
        if section_start_file and section_end_file:
            start_seq = file_seq(section_start_file)
            end_seq = file_seq(section_end_file)
            if start_seq == 583 and end_seq == 588:
                section["page_start"] = 1157
                section["page_end"] = 1168
                section.setdefault("raw_json", {})["page_span_repaired_from_editorial_estimator"] = {
                    "reason": "The closing ORDO RERUM section begins on the uncited facing page before file 584 and ends on the 1167/1168 spread.",
                    "repaired_page_start": 1157,
                    "repaired_page_end": 1168,
                }

    payload["generated_at"] = now_iso()
    notes = payload.setdefault("notes", [])
    note_text = f"Repaired ref target_file values with editorial page estimates: resolved={resolved_refs}, unresolved={unresolved_refs}."
    if note_text not in notes:
        notes.append(note_text)

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "refs.json", payload.get("refs", []))
    write_json(INTERMEDIATE_DIR / "sections.json", payload.get("sections", []))
    write_json(INTERMEDIATE_DIR / "notes.json", payload.get("notes", []))

    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PL121 ref target_file repair completed; remaining ambiguity is limited to a few early-page citations.",
            "completed": [
                "built editorial page maps from the OCR volume",
                "repaired ref target_file values",
                "corrected the closing ORDO RERUM page span",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes distinct from printed page numbers.",
                "Fallback candidate pages were used only when no best_guess exact match existed.",
            ],
        },
    )


if __name__ == "__main__":
    main()

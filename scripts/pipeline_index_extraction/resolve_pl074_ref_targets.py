#!/usr/bin/env python3
"""Usage: resolve PL074 ref target files and clean scripture-only pseudo-refs.

Run from the repository root:
  python scripts/pipeline_index_extraction/resolve_pl074_ref_targets.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import estimate_editorial_pages
from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL074/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL074_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL074"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PL074_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL074_helper_output.json"

HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
SCRIPTURE_LOCATOR_ONLY_RE = re.compile(
    r"^(?:[IVXLCDM]+|Ibid\.?(?:\s+et\s+[IVXLCDM]+)?)\s*,\s*\d+(?:\s*,\s*\d+)*\.?$",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def file_seq(path_str: str | None) -> int | None:
    if not path_str:
        return None
    try:
        return int(Path(path_str).stem.rsplit("-", 1)[-1])
    except Exception:
        return None


def best_guess_pages(best_guess: Any) -> list[int]:
    if isinstance(best_guess, int):
        return [best_guess]
    if isinstance(best_guess, list):
        return [item for item in best_guess if isinstance(item, int)]
    if isinstance(best_guess, dict):
        pages = best_guess.get("pages")
        if isinstance(pages, list):
            return [item for item in pages if isinstance(item, int)]
        if isinstance(pages, int):
            return [pages]
    return []


def build_page_map(source_root: Path) -> dict[int, list[dict[str, str]]]:
    page_map: dict[int, list[dict[str, str]]] = {}
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            if not 1 <= page <= 2000:
                continue
            bucket = page_map.setdefault(page, [])
            item = {"file": str(path), "source": "header"}
            if item not in bucket:
                bucket.append(item)
    try:
        estimate = estimate_editorial_pages(volume_id="PL074", source_root=source_root, collection="PL")
    except Exception:
        estimate = {"files": []}
    for item in estimate.get("files") or []:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        candidate_pages: list[int] = []
        candidate_pages.extend(best_guess_pages(item.get("best_guess")))
        candidate_pages.extend(best_guess_pages(item.get("best_single_page")))
        candidate_pages.extend(best_guess_pages(item.get("best_left_page")))
        candidate_pages.extend(best_guess_pages(item.get("best_right_page")))
        for candidate in item.get("candidate_editorial_pages") or []:
            if isinstance(candidate, dict):
                candidate_pages.extend(best_guess_pages(candidate.get("pages")))
        for page in candidate_pages:
            bucket = page_map.setdefault(page, [])
            est_item = {"file": file_path, "source": "estimator"}
            if est_item not in bucket:
                bucket.append(est_item)
    return page_map


def load_helper_by_context() -> dict[str, dict[str, Any]]:
    if not HELPER_REQUEST_PATH.exists() or not HELPER_OUTPUT_PATH.exists():
        return {}
    req = read_json(HELPER_REQUEST_PATH)
    out = read_json(HELPER_OUTPUT_PATH)
    output_by_id = {item.get("entry_id"): item for item in out.get("entries", []) if isinstance(item, dict)}
    by_context: dict[str, dict[str, Any]] = {}
    for item in req.get("entries", []):
        if not isinstance(item, dict):
            continue
        context = normalize(item.get("context_raw"))
        entry_id = item.get("entry_id")
        if context and entry_id in output_by_id:
            by_context[context] = output_by_id[entry_id]
    return by_context


def is_scripture_locator_only(entry: dict[str, Any]) -> bool:
    if entry.get("section_key") != "PL074:alpha:scripture_index:001":
        return False
    raw = normalize(entry.get("entry_raw")) or ""
    lemma = normalize(entry.get("lemma_raw")) or ""
    if SCRIPTURE_LOCATOR_ONLY_RE.fullmatch(raw):
        return True
    if ROMAN_RE.fullmatch(lemma) and raw.startswith(lemma):
        return True
    if lemma.lower() == "ibid" and raw.lower().startswith("ibid"):
        return True
    return False


def choose_candidate(
    page_int: int | None,
    candidates: list[dict[str, str]],
    *,
    helper_file: str | None,
    anchor_file: str | None,
) -> tuple[str | None, float | None, dict[str, Any]]:
    if page_int is None:
        return None, None, {"resolution": "no_page_int"}
    if not candidates:
        if helper_file:
            return helper_file, 0.58, {"resolution": "helper_only_fallback", "candidate_count": 0}
        return None, None, {"resolution": "unmapped_page", "candidate_count": 0}
    if len(candidates) == 1:
        source = candidates[0]["source"]
        prob = 0.98 if source == "header" else 0.86
        return candidates[0]["file"], prob, {"resolution": f"{source}_unique", "candidate_count": 1}
    files = [item["file"] for item in candidates]
    if helper_file and helper_file in files:
        chosen = next(item for item in candidates if item["file"] == helper_file)
        return chosen["file"], 0.94, {"resolution": "helper_exact_candidate", "candidate_count": len(candidates)}
    anchor_seq = file_seq(anchor_file)
    if anchor_seq is not None:
        chosen = min(candidates, key=lambda item: abs((file_seq(item["file"]) or anchor_seq) - anchor_seq))
        prob = 0.83 if chosen["source"] == "header" else 0.74
        return chosen["file"], prob, {"resolution": "nearest_to_anchor", "candidate_count": len(candidates)}
    chosen = candidates[0]
    prob = 0.78 if chosen["source"] == "header" else 0.7
    return chosen["file"], prob, {"resolution": "first_candidate_fallback", "candidate_count": len(candidates)}


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    page_map = build_page_map(SOURCE_ROOT)
    helper_by_context = load_helper_by_context()

    entries = payload["entries"]
    refs = payload["refs"]
    entry_by_key = {entry["entry_key"]: entry for entry in entries}
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    cleaned_refs: list[dict[str, Any]] = []
    removed_ref_count = 0

    for entry in entries:
        raw_json = entry.setdefault("raw_json", {})
        helper = helper_by_context.get(normalize(entry.get("entry_raw")))
        helper_file = None
        if helper:
            best = helper.get("best_candidate") or {}
            helper_file = best.get("file")
            raw_json["helper"] = {
                "status": helper.get("status"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                },
                "top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                    }
                    for cand in (helper.get("candidates") or [])[:5]
                ],
            }

        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if is_scripture_locator_only(entry):
            if entry_refs:
                raw_json["removed_spurious_material_refs"] = [ref.get("ref_raw") for ref in entry_refs]
                removed_ref_count += len(entry_refs)
            continue

        resolved_entry_targets: list[tuple[str, float]] = []
        new_entry_refs: list[dict[str, Any]] = []
        for ref in entry_refs:
            page_int = ref.get("page_ref_int")
            candidates = page_map.get(page_int, [])
            target_file, prob, resolution = choose_candidate(
                page_int,
                candidates,
                helper_file=helper_file,
                anchor_file=entry.get("editorial_anchor_file"),
            )
            ref["target_file"] = target_file
            ref["target_file_probability"] = prob
            ref_raw_json = ref.setdefault("raw_json", {})
            ref_raw_json["target_resolution"] = resolution
            if candidates:
                ref_raw_json["target_candidates"] = [
                    {"file": item["file"], "source": item["source"]} for item in candidates[:5]
                ]
            if helper_file:
                ref_raw_json["helper_file"] = helper_file
            if target_file and prob is not None:
                ref["confidence"] = max(ref.get("confidence") or 0.0, min(0.96, prob))
                resolved_entry_targets.append((target_file, prob))
            new_entry_refs.append(ref)
        cleaned_refs.extend(new_entry_refs)

        if resolved_entry_targets:
            best_file, best_prob = max(resolved_entry_targets, key=lambda item: item[1])
            entry["target_file_best"] = best_file
            entry["inferred_printed_page"] = entry["inferred_printed_page"] or next(
                (ref.get("page_ref_int") for ref in new_entry_refs if ref.get("page_ref_int") is not None),
                None,
            )
            raw_json["target_file_best_probability"] = best_prob
            raw_json["target_resolution"] = {
                "resolution": "best_resolved_ref",
                "resolved_ref_count": len(resolved_entry_targets),
            }

    for entry_key, group in refs_by_entry.items():
        if entry_key not in entry_by_key:
            continue
        if is_scripture_locator_only(entry_by_key[entry_key]):
            continue
        ref_order = 1
        for ref in cleaned_refs:
            if ref["entry_key"] != entry_key:
                continue
            ref["ref_order"] = ref_order
            ref_order += 1

    payload["refs"] = cleaned_refs
    payload["generated_at"] = now_iso()
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the OCR tail structure and resolved most material locators via global page-map evidence plus estimator fallback. A minority of refs remain unresolved where the cited page still lacks a dependable local mapping, and scripture-only chapter/verse lines were stripped of spurious material refs.",
        "evidence_files": [
            str(SOURCE_ROOT / "0382032d-4d97-499f-b9b2-54e1e6d908d7-640.txt"),
            str(SOURCE_ROOT / "0382032d-4d97-499f-b9b2-54e1e6d908d7-646.txt"),
            str(SOURCE_ROOT / "dc168a43-7322-4439-b828-9776c13dbc96-684.txt"),
            str(SOURCE_ROOT / "dc168a43-7322-4439-b828-9776c13dbc96-692.txt"),
            str(SOURCE_ROOT / "dc168a43-7322-4439-b828-9776c13dbc96-710.txt"),
            str(SOURCE_ROOT / "dc168a43-7322-4439-b828-9776c13dbc96-713.txt"),
        ],
    }
    payload["notes"] = [
        "Recovered the scripture, subject, onomastic, chorographic, homiletic, author, and closing contents indexes from the OCR tail.",
        "Resolved most ref target files from a volume-wide header page-map reinforced by the editorial page estimator.",
        "When multiple OCR files shared the same printed number, the resolver chose the nearest candidate to the entry anchor unless helper evidence selected an exact match.",
        "Scripture-only chapter/verse lines in the opening biblical index were kept as entries and scripture_refs, but their spurious material refs were removed.",
        "Some refs still remain unresolved where neither header evidence nor the estimator gave a dependable page candidate.",
    ]

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": "PL074",
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "source_root": str(SOURCE_ROOT),
            "helper_request_json": str(HELPER_REQUEST_PATH),
            "helper_output_json": str(HELPER_OUTPUT_PATH),
            "output_file": str(PAYLOAD_PATH),
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": "PL074",
            "updated_at": payload["generated_at"],
            "current_focus": "Resolved PL074 material locators and removed scripture-only pseudo-refs.",
            "completed": [
                "section boundaries mapped from OCR headings",
                "helper request written",
                "payload fragments assembled",
                "scripture_refs rebuilt to remove editorial-section-title inheritance",
                "resolved most refs via page-map and estimator evidence",
                "removed spurious material refs from scripture-only chapter/verse lines",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not conflate OCR file suffixes with printed page references.",
                f"Removed {removed_ref_count} scripture-only pseudo-refs during this pass.",
            ],
        },
    )


if __name__ == "__main__":
    main()

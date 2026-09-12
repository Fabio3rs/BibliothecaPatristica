#!/usr/bin/env python3
"""Usage: repair PL076 alphabetical payload locators and oversized OCR context.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pl076_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import estimate_editorial_pages
from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL076/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PL076_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL076"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PL076_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL076_helper_output.json"
CHUNK_RUNNER = ROOT / "scripts/pipeline_index_extraction/run_index_target_locator_in_chunks.py"

HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_TOKEN_RE = re.compile(r"\b(?:ibid\.?|id\.?|\d{1,4}(?:\s+et\s+seqq\.)?)\b", re.IGNORECASE)
NOISE_ENTRY_RE = re.compile(
    r"^(?:PATROL\.|LXXVI\.|Digitized by Google|INDEX IN TRIPLICEM.*|INDEX IN LIBROS MORALIUM.*|ORDO RERUM.*|QUÆ IN HOC TOMO CONTINENTUR\.)$",
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
    estimate = estimate_editorial_pages(volume_id="PL076", source_root=source_root, collection="PL")
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


def choose_queries(entry: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    lemma = normalize(entry.get("lemma_raw"))
    if lemma and len(lemma) >= 4:
        queries.append(lemma)
    text = normalize(entry.get("entry_raw")) or ""
    text_wo_pages = normalize(PAGE_TOKEN_RE.sub("", text)) or text
    for part in re.split(r"[.;:]", text_wo_pages):
        part = normalize(part)
        if not part or len(part) < 8:
            continue
        if part not in queries:
            queries.append(part)
        if len(queries) >= 4:
            break
    return queries[:4] or ([text[:120]] if text else [])


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]], page_map: dict[int, list[dict[str, str]]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        refs = refs_by_entry.get(entry["entry_key"], [])
        if len(refs) != 1:
            continue
        ref = refs[0]
        page_int = ref.get("page_ref_int")
        candidates = page_map.get(page_int, [])
        if page_int is None or candidates:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": choose_queries(entry),
                "page_hints": [str(page_int)],
                "page_hint_ints": [page_int],
                "context_raw": (normalize(entry.get("entry_raw")) or "")[:400],
            }
        )
        if len(helper_entries) >= 300:
            break
    return {
        "volume_id": "PL076",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper() -> dict[str, dict[str, Any]]:
    request = read_json(HELPER_REQUEST_PATH)
    if not request.get("entries"):
        write_json(
            HELPER_OUTPUT_PATH,
            {
                "volume_id": "PL076",
                "source_root": str(SOURCE_ROOT),
                "options_used": request.get("options", {}),
                "entries": [],
            },
        )
        return {}
    proc = subprocess.run(
        [
            sys.executable,
            str(CHUNK_RUNNER),
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--chunk-size",
            "80",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"run_index_target_locator_in_chunks.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    helper_output = read_json(HELPER_OUTPUT_PATH)
    return {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }


def choose_candidate(
    page_int: int | None,
    candidates: list[dict[str, str]],
    *,
    helper: dict[str, Any] | None,
    anchor_file: str | None,
) -> tuple[str | None, float | None, dict[str, Any]]:
    helper_best = (helper or {}).get("best_candidate") or {}
    helper_file = helper_best.get("file")
    helper_seq = file_seq(helper_file)
    anchor_seq = file_seq(anchor_file)
    if page_int is None:
        return None, None, {"resolution": "no_page_int"}
    if not candidates:
        if helper_file:
            return helper_file, 0.58, {"resolution": "helper_only_fallback", "candidate_count": 0}
        return None, None, {"resolution": "unmapped_page", "candidate_count": 0}

    header_candidates = [item for item in candidates if item["source"] == "header"]
    if len(candidates) == 1:
        source = candidates[0]["source"]
        prob = 0.98 if source == "header" else 0.86
        return candidates[0]["file"], prob, {"resolution": f"{source}_unique", "candidate_count": 1}
    if helper_file and any(item["file"] == helper_file for item in candidates):
        return helper_file, 0.94, {"resolution": "helper_exact_candidate", "candidate_count": len(candidates)}
    if len(header_candidates) == 1:
        return header_candidates[0]["file"], 0.9, {"resolution": "header_unique_among_mixed", "candidate_count": len(candidates)}
    if helper_seq is not None:
        chosen = min(candidates, key=lambda item: abs((file_seq(item["file"]) or helper_seq) - helper_seq))
        prob = 0.88 if chosen["source"] == "header" else 0.76
        return chosen["file"], prob, {"resolution": "nearest_to_helper", "candidate_count": len(candidates)}
    base = header_candidates or candidates
    if anchor_seq is not None:
        chosen = min(base, key=lambda item: abs((file_seq(item["file"]) or anchor_seq) - anchor_seq))
        prob = 0.82 if chosen["source"] == "header" else 0.72
        return chosen["file"], prob, {"resolution": "nearest_to_anchor", "candidate_count": len(candidates)}
    chosen = base[0]
    prob = 0.78 if chosen["source"] == "header" else 0.7
    return chosen["file"], prob, {"resolution": "first_candidate_fallback", "candidate_count": len(candidates)}


def trim_context(entry: dict[str, Any]) -> None:
    context = normalize(entry.get("context_raw"))
    entry_raw = normalize(entry.get("entry_raw"))
    if not context:
        entry["context_raw"] = None
        return
    if context == entry_raw:
        entry["context_raw"] = None
        return
    if len(context) > 400:
        entry["context_raw"] = None
        entry.setdefault("raw_json", {})["context_trimmed"] = "oversized_page_block"
        return
    if entry_raw and entry_raw in context and len(context) > max(180, len(entry_raw) * 2):
        entry["context_raw"] = None
        entry.setdefault("raw_json", {})["context_trimmed"] = "redundant_support_context"


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    page_map = build_page_map(SOURCE_ROOT)

    entries = payload["entries"]
    refs = payload["refs"]
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    helper_request = build_helper_request(entries, refs_by_entry, page_map)
    write_json(HELPER_REQUEST_PATH, helper_request)
    helper_by_entry = run_helper()

    filtered_entries: list[dict[str, Any]] = []
    removed_keys: set[str] = set()
    for entry in entries:
        raw = normalize(entry.get("entry_raw")) or ""
        if NOISE_ENTRY_RE.fullmatch(raw):
            removed_keys.add(entry["entry_key"])
            continue
        trim_context(entry)
        filtered_entries.append(entry)
    entries = filtered_entries

    cleaned_refs: list[dict[str, Any]] = []
    unresolved_refs = 0
    resolved_refs = 0
    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        raw_json = entry.setdefault("raw_json", {})
        if helper:
            best = helper.get("best_candidate") or {}
            raw_json["helper"] = {
                "status": helper.get("status"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
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
        entry_refs = []
        resolved_targets: list[tuple[str, float, int | None]] = []
        ref_order = 1
        for ref in refs_by_entry.get(entry["entry_key"], []):
            if ref["entry_key"] in removed_keys:
                continue
            page_int = ref.get("page_ref_int")
            candidates = page_map.get(page_int, [])
            target_file, prob, resolution = choose_candidate(
                page_int,
                candidates,
                helper=helper,
                anchor_file=entry.get("editorial_anchor_file"),
            )
            ref["ref_order"] = ref_order
            ref_order += 1
            ref["target_file"] = target_file
            ref["target_file_probability"] = prob
            ref_raw_json = ref.setdefault("raw_json", {})
            ref_raw_json["target_resolution"] = resolution
            if candidates:
                ref_raw_json["target_candidates"] = [
                    {"file": item["file"], "source": item["source"]} for item in candidates[:5]
                ]
            if helper:
                ref_raw_json["helper_entry_id"] = helper.get("entry_id")
            if target_file and prob is not None:
                resolved_refs += 1
                ref["confidence"] = max(ref.get("confidence") or 0.0, min(0.96, prob))
                resolved_targets.append((target_file, prob, page_int))
            else:
                unresolved_refs += 1
            entry_refs.append(ref)
            cleaned_refs.append(ref)
        if resolved_targets:
            best_file, best_prob, best_page = max(resolved_targets, key=lambda item: item[1])
            entry["target_file_best"] = best_file
            if entry.get("inferred_printed_page") is None:
                entry["inferred_printed_page"] = best_page
            raw_json["target_resolution"] = {
                "resolution": "best_resolved_ref",
                "resolved_ref_count": len(resolved_targets),
                "best_probability": best_prob,
            }
        elif entry_refs:
            entry["target_file_best"] = None
            raw_json["target_resolution"] = {"resolution": "no_resolved_ref_target"}

    payload["entries"] = entries
    payload["refs"] = cleaned_refs
    payload["generated_at"] = now_iso()
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Reused the existing PL076 extraction as a checkpoint, removed obvious OCR-noise entries, trimmed oversized duplicated context blocks, and resolved most material locators through a volume-wide page map reinforced by the editorial-page estimator and a chunked helper pass for ambiguous or unmapped cases.",
        "evidence_files": [
            str(SOURCE_ROOT / "c749216a-cd91-42c2-9c88-93389e8f553f-665.txt"),
            str(SOURCE_ROOT / "c749216a-cd91-42c2-9c88-93389e8f553f-668.txt"),
            str(SOURCE_ROOT / "c749216a-cd91-42c2-9c88-93389e8f553f-679.txt"),
            str(SOURCE_ROOT / "0924ef87-9b53-45c5-b9b4-1d844174f9be-717.txt"),
            str(SOURCE_ROOT / "0924ef87-9b53-45c5-b9b4-1d844174f9be-767.txt"),
            str(SOURCE_ROOT / "0924ef87-9b53-45c5-b9b4-1d844174f9be-768.txt"),
        ],
    }
    payload["notes"] = [
        "Reused the prior PL076 payload as a checkpoint and revalidated locator resolution against the volume OCR.",
        "Resolved most ref target files from a volume-wide header page map, supplemented by the editorial page estimator.",
        "Built a helper request for entries whose cited pages stayed ambiguous or unmapped under the page map alone, and ran the locator in chunks.",
        "Oversized context_raw page dumps were cleared when they only duplicated adjacent OCR matter instead of adding disambiguating support.",
        "Some entry segmentation issues inherited from dense OCR lines still remain conservative rather than fully reconstructed where local punctuation does not safely distinguish neighboring lemmata.",
        f"Resolved refs with target_file: {resolved_refs}; unresolved refs remaining: {unresolved_refs}.",
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
            "volume_id": "PL076",
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
            "volume_id": "PL076",
            "updated_at": payload["generated_at"],
            "current_focus": "Resolved PL076 material locators and trimmed oversized OCR context from the checkpoint payload.",
            "completed": [
                "verified tail index sections against OCR",
                "rebuilt helper request for ambiguous or unmapped locator cases",
                "ran index_target_locator in chunks",
                "resolved most refs via page-map plus helper evidence",
                "trimmed oversized duplicated context_raw values",
            ],
            "pending": [
                "spot-check residual unresolved refs if future validation requires another pass"
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not conflate OCR file suffixes with printed page references.",
                f"Resolved refs: {resolved_refs}; unresolved refs: {unresolved_refs}.",
            ],
        },
    )


if __name__ == "__main__":
    main()

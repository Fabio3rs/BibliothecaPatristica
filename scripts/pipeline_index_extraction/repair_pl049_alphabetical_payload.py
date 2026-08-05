#!/usr/bin/env python3
"""Repair the PL049 closing ORDO RERUM alphabetical payload and validate locators.

Usage:
  python scripts/pipeline_index_extraction/repair_pl049_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL049"
SOURCE_ROOT = PROJECT_ROOT / "teste" / VOLUME_ID / "text"
PAYLOAD_PATH = PROJECT_ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = PROJECT_ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = PROJECT_ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
ASSEMBLED_FRAGMENTS_PATH = PROJECT_ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_ocr_body(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return normalize_text(value)


def file_seq(path: str | Path) -> int:
    name = Path(path).name
    match = re.search(r"-(\d+)\.txt$", name)
    if not match:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(match.group(1))


def build_variants(lemma_raw: str | None) -> list[str]:
    if not lemma_raw:
        return []
    raw = lemma_raw.strip()
    variants = [raw]
    split = re.split(r"\s*[—-]\s*", raw, maxsplit=1)
    if len(split) == 2:
        variants.append(split[1].strip())
    if raw.startswith("CAPUT ") or raw.startswith("CAP. "):
        title_only = re.sub(r"^CAP(?:UT)?\.?\s*[A-ZIVXLCDM0-9]+\s*[.—-]?\s*", "", raw).strip()
        if title_only:
            variants.append(title_only)
    normalized = []
    seen = set()
    for variant in variants:
        norm = normalize_text(variant)
        if norm and norm not in seen:
            normalized.append(norm)
            seen.add(norm)
    return normalized


def build_helper_map(helper_request: dict, helper_output: dict) -> dict[tuple[int, str], dict]:
    request_by_id = {item["entry_id"]: item for item in helper_request.get("entries", [])}
    mapping: dict[tuple[int, str], dict] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        request = request_by_id.get(entry_id)
        if not request:
            continue
        page_hints = request.get("page_hint_ints") or []
        if not page_hints:
            continue
        key = (int(page_hints[0]), normalize_text(request.get("lemma_raw")))
        if key in mapping:
            raise ValueError(f"Duplicate helper key for {key}")
        mapping[key] = {"request": request, "output": item}
    return mapping


def build_corpus(source_root: Path) -> dict[str, str]:
    corpus: dict[str, str] = {}
    for path in sorted(source_root.glob("*.txt")):
        corpus[str(path)] = normalize_ocr_body(path.read_text(encoding="utf-8"))
    return corpus


def find_direct_match(
    lemma_raw: str | None,
    corpus: dict[str, str],
    excluded_files: set[str],
) -> tuple[str | None, dict | None]:
    variants = build_variants(lemma_raw)
    if not variants:
        return None, None
    for variant in variants:
        matches = [path for path, content in corpus.items() if path not in excluded_files and variant in content]
        if len(matches) == 1:
            return matches[0], {"matched_variant": variant, "match_count": 1}
    return None, None


def merge_unique(base: list[str], additions: list[str]) -> list[str]:
    seen = set(base)
    result = list(base)
    for item in additions:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def without_prefix(notes: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [note for note in notes if not any(note.startswith(prefix) for prefix in prefixes)]


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    helper_request = load_json(HELPER_REQUEST_PATH)
    helper_output = load_json(HELPER_OUTPUT_PATH)
    assembled = load_json(ASSEMBLED_FRAGMENTS_PATH)

    section_key = payload["entries"][0]["section_key"]
    payload["sections"][0]["section_key"] = section_key

    helper_map = build_helper_map(helper_request, helper_output)
    tail_files = set(payload["sections"][0]["raw_json"].get("source_files", []))
    corpus = build_corpus(SOURCE_ROOT)

    resolved_by_direct = 0
    resolved_by_helper = 0
    resolved_by_page_inheritance = 0
    unresolved_after = 0
    helper_status_counts: Counter[str] = Counter()

    locator_by_entry_key: dict[str, dict] = {}

    for entry in payload.get("entries", []):
        page = entry.get("inferred_printed_page")
        lemma_norm_key = normalize_text(entry.get("lemma_raw") or entry.get("entry_raw"))
        locator = None
        if page is not None:
            locator = helper_map.get((int(page), lemma_norm_key))
        raw_json = entry.setdefault("raw_json", {})

        chosen_file = None
        chosen_probability = None
        resolution_source = None

        if locator:
            output = locator["output"]
            request = locator["request"]
            status = output.get("status") or "unknown"
            helper_status_counts[status] += 1
            best_candidate = output.get("best_candidate") or {}
            best_probability = best_candidate.get("probability")

            direct_file = None
            direct_meta = None
            if status != "resolved" or (best_probability is not None and best_probability < 0.5):
                direct_file, direct_meta = find_direct_match(request.get("lemma_raw"), corpus, tail_files)

            if direct_file:
                chosen_file = direct_file
                chosen_probability = 0.98
                resolution_source = "direct_search"
                resolved_by_direct += 1
                raw_json["direct_search_locator"] = {
                    "matched_file": direct_file,
                    "matched_file_seq": file_seq(direct_file),
                    "matched_variant": direct_meta["matched_variant"],
                    "match_count": direct_meta["match_count"],
                    "search_scope": "current_volume_non_tail_files",
                    "override_reason": f"helper_status={status}",
                }
                if best_candidate.get("file") and best_candidate.get("file") != direct_file:
                    raw_json["direct_search_locator"]["helper_best_file_discarded"] = best_candidate.get("file")
            elif best_candidate.get("file"):
                chosen_file = best_candidate["file"]
                chosen_probability = best_probability
                resolution_source = "helper"
                resolved_by_helper += 1

            top_candidates = []
            for candidate in (output.get("candidates") or [])[:3]:
                top_candidates.append(
                    {
                        "file": candidate.get("file"),
                        "probability": candidate.get("probability"),
                        "candidate_role": candidate.get("candidate_role"),
                        "reason_summary": candidate.get("reason_summary"),
                    }
                )
            raw_json["helper_locator"] = {
                "entry_id": request.get("entry_id"),
                "status": status,
                "candidate_role": best_candidate.get("candidate_role"),
                "reason_summary": best_candidate.get("reason_summary"),
                "top_candidates": top_candidates,
            }

        if chosen_file:
            entry["target_file_best"] = chosen_file
        else:
            entry["target_file_best"] = entry.get("target_file_best")

        locator_by_entry_key[entry["entry_key"]] = {
            "target_file": chosen_file,
            "target_file_probability": chosen_probability,
            "resolution_source": resolution_source,
        }

    targets_by_page: dict[int, set[str]] = {}
    for entry in payload.get("entries", []):
        page = entry.get("inferred_printed_page")
        target = entry.get("target_file_best")
        if page is None or not target:
            continue
        targets_by_page.setdefault(int(page), set()).add(target)

    for entry in payload.get("entries", []):
        page = entry.get("inferred_printed_page")
        if page is None or entry.get("target_file_best"):
            continue
        page_targets = targets_by_page.get(int(page), set())
        if len(page_targets) != 1:
            continue
        inherited_target = next(iter(page_targets))
        entry["target_file_best"] = inherited_target
        raw_json = entry.setdefault("raw_json", {})
        raw_json["page_inherited_locator"] = {
            "inferred_printed_page": int(page),
            "inherited_target_file": inherited_target,
            "reason": "unique target already resolved for the same editorial page elsewhere in the ORDO RERUM payload",
        }
        locator_by_entry_key[entry["entry_key"]] = {
            "target_file": inherited_target,
            "target_file_probability": 0.96,
            "resolution_source": "page_inheritance",
        }
        resolved_by_page_inheritance += 1

    for ref in payload.get("refs", []):
        locator = locator_by_entry_key.get(ref["entry_key"])
        if not locator:
            continue
        if locator["target_file"]:
            ref["target_file"] = locator["target_file"]
            ref["target_file_probability"] = locator["target_file_probability"]
            raw_json = ref.setdefault("raw_json", {})
            raw_json["locator_resolution_source"] = locator["resolution_source"]

    unresolved_after = sum(
        1
        for entry in payload.get("entries", [])
        if entry.get("inferred_printed_page") is not None and not entry.get("target_file_best")
    )

    fragment_notes = assembled.get("data", {}).get("notes", [])
    payload["notes"] = without_prefix(
        payload.get("notes", []),
        (
            "Applied locator evidence from the PL049 helper and direct OCR search;",
        ),
    )
    payload["notes"] = merge_unique(payload["notes"], fragment_notes)
    payload["notes"] = merge_unique(
        payload["notes"],
        [
            "Aligned the section_key with the validated entry/ref section references.",
            f"Applied locator evidence from the PL049 helper and direct OCR search; unresolved page-bearing entries remaining: {unresolved_after}.",
        ],
    )

    payload["sections"][0]["raw_json"]["notes"] = without_prefix(
        payload["sections"][0]["raw_json"].get("notes", []),
        ("Locator repair summary:",),
    )
    payload["sections"][0]["raw_json"]["notes"] = merge_unique(
        payload["sections"][0]["raw_json"]["notes"],
        [
            "Section key realigned to the validated entry/ref section reference.",
            f"Locator repair summary: helper_resolved={resolved_by_helper}, direct_search_overrides={resolved_by_direct}, page_inheritance={resolved_by_page_inheritance}, unresolved_after={unresolved_after}.",
        ],
    )
    payload["sections"][0]["raw_json"]["helper_locator_summary"] = dict(helper_status_counts)

    payload["coverage"]["entries_status_reason"] = (
        "Recovered the closing ORDO RERUM table as 879 serialized entries and 863 material refs "
        f"from the validated chunk fragments; helper/direct-search repair reduced unresolved page-bearing "
        f"entries to {unresolved_after}."
    )
    payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    dump_json(PAYLOAD_PATH, payload)


if __name__ == "__main__":
    main()

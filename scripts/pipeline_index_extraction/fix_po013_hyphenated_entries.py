#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_po013_hyphenated_entries.py --payload data/alphabetical_index_payloads/PO013_alphabetical_indices.json --helper-request data/alphabetical_index_payloads/PO013_helper_request.json
"""Repair PO013 entries split by OCR line-break hyphenation and rebuild helper input."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path


MERGES = [
    {
        "primary_key": "PO013:entry:172",
        "continuation_key": "PO013:entry:173",
        "entry_raw": "ܐܚܕܝܢܘܬܐ 92₁₀ 99₁ 104₁₄ 108₁ 113₂ 116₂ 134₁₀ 168₃ 171₂₋₄ 172₁₂₋₁₃ 174₆₋ — Liste des évêques monophysites d'Alexandrie 56",
        "lemma_raw": "ܐܚܕܝܢܘܬܐ",
    },
    {
        "primary_key": "PO013:entry:192",
        "continuation_key": "PO013:entry:193",
        "entry_raw": "ܐܦܣܩܦܐ ܒܣܘܡܣܛ ܐܦܣܩܦܐ 85₁₅ ܐܦܩܦܘܬܗܘܢ Hérésie des astronomes 156₋7",
        "lemma_raw": "ܐܦܣܩܦܐ ܒܣܘܡܣܛ ܐܦܣܩܦܐ",
    },
    {
        "primary_key": "PO013:entry:397",
        "continuation_key": "PO013:entry:398",
        "entry_raw": "ܣܘܩܦܐ d'Antioche 56 57 84₆₋₇ ܣܥܘܪܝܐ évêque du pays d'Arzoun 177 n. 4",
        "lemma_raw": "ܣܘܩܦܐ d'Antioche",
    },
    {
        "primary_key": "PO013:entry:416",
        "continuation_key": "PO013:entry:417",
        "entry_raw": "ܦܐܦܐܣܘܣ 100₂ ܦܐܦܐ évêque monophysite d'Éphèse 56",
        "lemma_raw": "ܦܐܦܐܣܘܣ",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--helper-request", required=True, type=Path)
    return parser.parse_args()


def lemma_norm(value: str | None) -> str | None:
    return value.lower() if isinstance(value, str) else value


def sort_page_hints(values: list[str]) -> tuple[list[str], list[int]]:
    page_hint_ints: list[int] = []
    seen_ints: set[int] = set()
    for value in values:
        for match in re.findall(r"\d+", value):
            number = int(match)
            if number not in seen_ints:
                seen_ints.add(number)
                page_hint_ints.append(number)
    page_hints: list[str] = []
    seen_raw: set[str] = set()
    for value in values:
        if value not in seen_raw:
            seen_raw.add(value)
            page_hints.append(value)
    return page_hints, sorted(page_hint_ints)


def main() -> None:
    args = parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    entries = payload["entries"]
    refs = payload["refs"]
    merge_by_primary = {item["primary_key"]: item for item in MERGES}
    continuation_to_primary = {
        item["continuation_key"]: item["primary_key"] for item in MERGES
    }
    original_by_key = {entry["entry_key"]: entry for entry in entries}

    repaired_entries = []
    for entry in entries:
        entry_key = entry["entry_key"]
        if entry_key in continuation_to_primary:
            continue
        if entry_key in merge_by_primary:
            spec = merge_by_primary[entry_key]
            continuation = deepcopy(original_by_key[spec["continuation_key"]])
            merged = deepcopy(entry)
            merged["lemma_raw"] = spec["lemma_raw"]
            merged["lemma_display"] = spec["lemma_raw"]
            merged["lemma_norm"] = lemma_norm(spec["lemma_raw"])
            merged["lemma_sort"] = lemma_norm(spec["lemma_raw"])
            merged["entry_raw"] = spec["entry_raw"]
            merged["context_raw"] = spec["entry_raw"]
            raw_json = deepcopy(merged.get("raw_json", {}))
            raw_json["source_context"] = spec["entry_raw"]
            page_tokens = list(raw_json.get("page_tokens") or [])
            continuation_tokens = continuation.get("raw_json", {}).get("page_tokens") or []
            for token in continuation_tokens:
                if token not in page_tokens:
                    page_tokens.append(token)
            raw_json["page_tokens"] = page_tokens
            raw_json["repair_note"] = (
                f"Merged OCR continuation from {spec['continuation_key']} into "
                f"{spec['primary_key']} and removed terminal line-break hyphen artifact."
            )
            merged["raw_json"] = raw_json
            repaired_entries.append(merged)
            continue
        repaired_entries.append(deepcopy(entry))

    old_to_new_entry_key: dict[str, str] = {}
    for index, entry in enumerate(repaired_entries, start=1):
        new_key = f"PO013:entry:{index:03d}"
        old_to_new_entry_key[entry["entry_key"]] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = index
    for old_key, primary_key in continuation_to_primary.items():
        old_to_new_entry_key[old_key] = old_to_new_entry_key[primary_key]

    ref_groups: dict[str, list[dict]] = defaultdict(list)
    for ref in refs:
        new_entry_key = old_to_new_entry_key[ref["entry_key"]]
        repaired_ref = deepcopy(ref)
        repaired_ref["entry_key"] = new_entry_key
        ref_groups[new_entry_key].append(repaired_ref)

    repaired_refs = []
    for entry in repaired_entries:
        group = ref_groups.get(entry["entry_key"], [])
        for ref_order, ref in enumerate(group, start=1):
            ref["ref_order"] = ref_order
            repaired_refs.append(ref)

    payload["entries"] = repaired_entries
    payload["refs"] = repaired_refs
    payload["generated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload["notes"] = list(payload.get("notes") or [])
    payload["notes"].append(
        "Repaired four PO013 entries that had been split by OCR terminal hyphenation and rebuilt helper input."
    )

    helper_request = {
        "volume_id": payload["volume"]["volume_id"],
        "source_root": payload["volume"]["source_root"],
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [],
    }

    for entry in repaired_entries:
        raw_json = entry.get("raw_json", {})
        page_tokens = raw_json.get("page_tokens") or []
        page_hints, page_hint_ints = sort_page_hints(page_tokens)
        query_names = []
        for candidate in [entry.get("lemma_raw"), entry.get("entry_raw")]:
            if candidate and candidate not in query_names:
                query_names.append(candidate)
        helper_request["entries"].append(
            {
                "entry_id": raw_json.get("source_entry_id") or entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or "",
                "query_names": query_names,
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry.get("context_raw"),
            }
        )

    args.payload.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.helper_request.write_text(
        json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Repair PG091 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg091_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG091"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG091_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG091"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
REF_TAIL_RE = re.compile(
    r"(?:,\s*)?(?:(?:ibid|etc)\.?\s*,?\s*)?(?:\d{1,4})(?:\s*(?:,|;|et)\s*\d{1,4})*\.?$",
    re.IGNORECASE,
)


TERMINAL_REPAIRS = {
    "PG091:entry:00239": {
        "append": "fensio, 507, 508, 309. Ad eum responsa, 313. Viri laudatio, 334.",
        "remove": ["PG091:entry:00240", "PG091:entry:00241"],
        "page_refs": [507, 508, 309, 313, 334],
        "reason": "Merged page-boundary continuation from OCR file 816 and removed intervening page-number header entries.",
    },
    "PG091:entry:00552": {
        "merge_next": "PG091:entry:00553",
        "page_refs": [],
        "reason": "Merged adjacent OCR line-break continuation po-/riculo in the INDEX ANALYTICUS.",
    },
    "PG091:entry:00855": {
        "append": "pia, 367.",
        "remove": ["PG091:entry:00856", "PG091:entry:00857"],
        "reassign_refs_from": ["PG091:entry:00857"],
        "page_refs": [],
        "reason": "Merged page-boundary continuation om-/pia and removed intervening Google footer entry.",
    },
    "PG091:entry:01018": {
        "append": (
            "tur laboribus: prona vitii via. Perinde arduum bonum comparare, et cum metus fueris, "
            "incolume servare. Multo labore partæ virtutes, momento uno amitti possunt, 687, 688. "
            "Vitii immensa præclivitas; facile vitio imbuī; ad illud lenocinia. Facilius ab initio "
            "illi non cedere quam ejus progressum inhibere, 688. Vitii exstirpanda initia, 528."
        ),
        "remove": ["PG091:entry:01019", "PG091:entry:01020"],
        "page_refs": [687, 688, 528],
        "reason": "Merged page-boundary continuation compsa-/tur from OCR file 821 and removed stray column labels.",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def has_validator_hyphen(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value).strip()
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def clean_join(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if left.endswith("-"):
        return re.sub(r"\s+", " ", left[:-1] + right).strip()
    return re.sub(r"\s+", " ", f"{left} {right}").strip()


def lemma_from_entry_raw(entry_raw: str) -> str:
    return REF_TAIL_RE.sub("", entry_raw).strip(" ,;.") or entry_raw.strip()


def normalize_lemma(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", value.casefold())).strip()


def repair_payload_text(payload: dict[str, Any]) -> dict[str, int]:
    counts = {
        "entries_changed": 0,
        "entry_fields_changed": 0,
        "refs_changed": 0,
        "ref_fields_changed": 0,
        "scripture_refs_changed": 0,
        "scripture_ref_fields_changed": 0,
    }
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }
    note = (
        "PG091 rerun merged validation-blocking OCR line-break hyphenation in payload "
        "text fields after checking representative index OCR reader output."
    )

    for collection, fields in field_map.items():
        for obj in payload.get(collection, []):
            touched = False
            for field in fields:
                before = obj.get(field)
                after = dehyphenate(before)
                if before != after:
                    obj[field] = after
                    touched = True
                    counts[f"{collection[:-1]}_fields_changed" if collection != "entries" else "entry_fields_changed"] += 1
            if not touched:
                continue
            if collection == "entries":
                counts["entries_changed"] += 1
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(note)
            elif collection == "refs":
                counts["refs_changed"] += 1
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    "PG091 rerun merged OCR line-break hyphenation in material reference text fields."
                )
            else:
                counts["scripture_refs_changed"] += 1
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    "PG091 rerun merged OCR line-break hyphenation in scripture reference text fields."
                )
    return counts


def fill_safe_ref_targets(payload: dict[str, Any]) -> int:
    """Fill null ref target_file only when the entry has exactly one material ref."""

    entries_by_key = {entry["entry_key"]: entry for entry in payload.get("entries", [])}
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in payload.get("refs", []):
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    changed = 0
    for entry_key, refs in refs_by_entry.items():
        if len(refs) != 1:
            continue
        ref = refs[0]
        if ref.get("target_file"):
            continue
        entry = entries_by_key.get(entry_key)
        target = entry.get("target_file_best") if entry else None
        if not target:
            continue
        ref["target_file"] = target
        ref["target_file_probability"] = ref.get("target_file_probability") or 0.55
        ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            "PG091 rerun filled a single-ref target_file from the entry target_file_best; "
            "multi-ref unresolved targets were left null to avoid collapsing distinct cited pages."
        )
        changed += 1
    return changed


def material_ref_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for ref in payload.get("refs", []):
        page = ref.get("page_ref_int")
        target = ref.get("target_file")
        if isinstance(page, int) and target and page not in mapping:
            mapping[page] = ref
    return mapping


def next_ref_order(refs: list[dict[str, Any]], entry_key: str) -> int:
    orders = [int(ref["ref_order"]) for ref in refs if ref.get("entry_key") == entry_key]
    return max(orders, default=0) + 1


def append_page_refs(
    payload: dict[str, Any],
    entry_key: str,
    pages: list[int],
    page_target_map: dict[int, dict[str, Any]],
    reason: str,
) -> int:
    refs = payload["refs"]
    existing = {
        (ref.get("entry_key"), ref.get("page_ref_int"), ref.get("ref_raw"))
        for ref in refs
    }
    added = 0
    for page in pages:
        ref_raw = str(page)
        if (entry_key, page, ref_raw) in existing:
            continue
        source_ref = page_target_map.get(page, {})
        target_file = source_ref.get("target_file")
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": next_ref_order(refs, entry_key),
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": source_ref.get("target_file_probability") if target_file else None,
                "section_start_file": source_ref.get("section_start_file"),
                "editorial_anchor_file": source_ref.get("editorial_anchor_file"),
                "confidence": 0.74 if target_file else 0.62,
                "raw_json": {
                    "repair_notes": [
                        f"PG091 rerun recovered this ref while repairing a terminal OCR line-break split: {reason}"
                    ],
                    "target_source": "page_ref_map_from_existing_payload" if target_file else "unresolved_after_repair",
                },
            }
        )
        existing.add((entry_key, page, ref_raw))
        added += 1
    return added


def repair_terminal_entries(payload: dict[str, Any]) -> dict[str, int]:
    entries = payload["entries"]
    refs = payload["refs"]
    by_key = {entry["entry_key"]: entry for entry in entries}
    remove_keys: set[str] = set()
    counts = {"terminal_entries_repaired": 0, "entries_removed": 0, "refs_removed": 0, "refs_reassigned": 0, "refs_added": 0}
    page_target_map = material_ref_map(payload)

    for entry_key, spec in TERMINAL_REPAIRS.items():
        entry = by_key.get(entry_key)
        if not entry or not has_validator_hyphen(entry.get("entry_raw")):
            continue
        if spec.get("merge_next"):
            next_entry = by_key.get(str(spec["merge_next"]))
            if not next_entry:
                continue
            combined = clean_join(str(entry["entry_raw"]), str(next_entry["entry_raw"]))
            remove_keys.add(str(spec["merge_next"]))
        else:
            combined = clean_join(str(entry["entry_raw"]), str(spec["append"]))

        entry["entry_raw"] = combined
        entry["context_raw"] = combined
        lemma = lemma_from_entry_raw(combined)
        entry["lemma_raw"] = lemma
        entry["lemma_display"] = lemma
        entry["lemma_norm"] = normalize_lemma(lemma)
        entry["lemma_sort"] = entry["lemma_norm"]
        entry["confidence"] = min(float(entry.get("confidence") or 0.74), 0.78)
        entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(str(spec["reason"]))
        counts["terminal_entries_repaired"] += 1
        remove_keys.update(str(key) for key in spec.get("remove", []))

        for ref in refs:
            if ref.get("entry_key") in spec.get("reassign_refs_from", []):
                ref["entry_key"] = entry_key
                ref["ref_order"] = next_ref_order(refs, entry_key)
                ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    f"PG091 rerun reassigned this ref from continuation entry during repair: {spec['reason']}"
                )
                counts["refs_reassigned"] += 1
        counts["refs_added"] += append_page_refs(payload, entry_key, list(spec.get("page_refs", [])), page_target_map, str(spec["reason"]))

    if remove_keys:
        kept_refs = []
        for ref in refs:
            if ref.get("entry_key") in remove_keys:
                counts["refs_removed"] += 1
                continue
            kept_refs.append(ref)
        payload["refs"] = kept_refs
        payload["entries"] = [entry for entry in entries if entry["entry_key"] not in remove_keys]
        counts["entries_removed"] = len(remove_keys)
        for order, entry in enumerate(payload["entries"], start=1):
            entry["entry_order"] = order
    return counts


def drop_invalid_context_only(payload: dict[str, Any]) -> int:
    changed = 0
    for entry in payload.get("entries", []):
        if has_validator_hyphen(entry.get("context_raw")) and not has_validator_hyphen(entry.get("entry_raw")):
            entry["context_raw"] = None
            entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                "PG091 rerun dropped truncated auxiliary context_raw after retaining complete entry_raw."
            )
            changed += 1
    return changed


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload.get("entries", [])}
    missing = [
        {"ref_index": idx, "entry_key": ref.get("entry_key")}
        for idx, ref in enumerate(payload.get("refs", []), start=1)
        if ref.get("entry_key") not in entry_keys
    ]
    if missing:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing[:50]}, ensure_ascii=False, indent=2))


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw",),
        "scripture_refs": ("ref_raw",),
    }
    for collection, fields in field_map.items():
        for idx, obj in enumerate(payload.get(collection, []), start=1):
            for field in fields:
                if has_validator_hyphen(obj.get(field)):
                    residual.append(
                        {
                            "collection": collection,
                            "index": idx,
                            "entry_key": obj.get("entry_key"),
                            "field": field,
                            "value": obj.get(field),
                        }
                    )
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2))


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_payload_text(payload)
    terminal_counts = repair_terminal_entries(payload)
    dropped_contexts = drop_invalid_context_only(payload)
    single_ref_targets_filled = fill_safe_ref_targets(payload)
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG091 rerun repaired validation-blocking OCR line-break hyphen artifacts in "
        "entry and reference text fields. Representative rejected forms such as "
        "armatura and conjugia were checked against the cleaned OCR reader output "
        "for the INDEX ANALYTICUS pages before applying the deterministic merge."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            **counts,
            **terminal_counts,
            "dropped_invalid_contexts": dropped_contexts,
            "single_ref_targets_filled": single_ref_targets_filled,
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "PG091 payload repaired after line-break hyphen validation failure; import validation completed next.",
            "completed": [
                "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
                "Verified representative rejected entries against OCR reader output for file 814",
                "Merged validation-blocking OCR line-break hyphen artifacts in entry/ref text fields",
                "Merged terminal page-boundary split entries and removed header/footer noise entries",
                "Filled safe single-ref target_file gaps from entry target_file_best",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "The repair preserves OCR literals except for proven line-break hyphenation.",
                "Refs with multiple cited pages and unresolved individual target_file values were left null instead of collapsing them to one entry-level target.",
            ],
        },
    )
    print(
        json.dumps(
            {**counts, **terminal_counts, "dropped_invalid_contexts": dropped_contexts, "single_ref_targets_filled": single_ref_targets_filled},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

"""Deterministically assemble and reconcile semantic index chunk fragments.

The artifact produced here is not allowed to invent or reinterpret editorial structure. It merges
objects with stable keys, concatenates chunk-owned entries, and records exactly which complete
chunks were consumed. The final agent may resolve remaining semantic ambiguity, while the
post-check ensures that stable fragment objects were not silently dropped.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .index_pipeline_ownership import general_section_ownership


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _stable_key(item: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    if fields in {("entry_key", "ref_order"), ("entry_key", "scripture_ref_order")}:
        values = [item.get(field) for field in fields]
        if all(value is not None and str(value).strip() for value in values):
            return "|".join(f"{field}:{value}" for field, value in zip(fields, values, strict=True))
    for field in fields:
        value = item.get(field)
        if value is not None and str(value).strip():
            return f"{field}:{value}"
    return None


def _merge_unique_objects(
    target: list[dict[str, Any]],
    incoming: list[Any],
    *,
    key_fields: tuple[str, ...],
    duplicate_policy: str = "error",
) -> None:
    by_key = {
        key: item
        for item in target
        if (key := _stable_key(item, key_fields)) is not None
    }
    raw_seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in target}
    for raw_item in incoming:
        if not isinstance(raw_item, dict):
            raise ValueError("Fragment arrays may contain JSON objects only")
        item = deepcopy(raw_item)
        key = _stable_key(item, key_fields)
        if key is not None and key in by_key:
            if duplicate_policy == "keep_first":
                continue
            if duplicate_policy == "merge_entries":
                existing = by_key[key]
                existing_entries = existing.setdefault("entries", [])
                incoming_entries = item.get("entries") or []
                if not isinstance(existing_entries, list) or not isinstance(incoming_entries, list):
                    raise ValueError(f"Cannot merge non-list entries for {key}")
                _merge_unique_objects(
                    existing_entries,
                    incoming_entries,
                    key_fields=("entry_key",),
                )
                continue
            raise ValueError(f"Duplicate stable object across chunk fragments: {key}")
        raw_signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key is None and raw_signature in raw_seen:
            continue
        target.append(item)
        raw_seen.add(raw_signature)
        if key is not None:
            by_key[key] = item


def _merge_unique_notes(target: list[Any], incoming: list[Any]) -> None:
    """Merge canonical string notes and structured notes without losing either format."""
    by_key = {
        key: item
        for item in target
        if isinstance(item, dict)
        if (key := _stable_key(item, ("note_key",))) is not None
    }
    raw_seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in target}
    for raw_item in incoming:
        if not isinstance(raw_item, (str, dict)):
            raise ValueError(
                "Fragment notes may contain strings or JSON objects only; "
                f"got {type(raw_item).__name__}"
            )
        item = deepcopy(raw_item)
        key = _stable_key(item, ("note_key",)) if isinstance(item, dict) else None
        if key is not None and key in by_key:
            continue
        raw_signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if raw_signature in raw_seen:
            continue
        target.append(item)
        raw_seen.add(raw_signature)
        if key is not None:
            by_key[key] = item


def assemble_index_fragments(
    workplan: dict[str, Any],
    output_file: Path,
) -> dict[str, Any]:
    pipeline_kind = str(workplan.get("pipeline_kind") or "")
    if pipeline_kind not in {"general", "alphabetical"}:
        raise ValueError(f"Unsupported pipeline kind: {pipeline_kind}")
    chunks = [item for item in workplan.get("chunks") or [] if isinstance(item, dict)]
    incomplete = [str(item.get("chunk_id")) for item in chunks if item.get("status") != "complete"]
    if incomplete:
        raise ValueError(f"Cannot assemble incomplete chunks: {incomplete}")

    if pipeline_kind == "alphabetical":
        data: dict[str, list[Any]] = {
            "sections": [],
            "nodes": [],
            "entries": [],
            "refs": [],
            "scripture_refs": [],
            "notes": [],
        }
    else:
        data = {"works": [], "sections": [], "notes": []}

    consumed: list[dict[str, Any]] = []
    for chunk in chunks:
        path = Path(str(chunk.get("output_file") or ""))
        if not path.is_file():
            raise FileNotFoundError(f"Complete chunk output is missing: {path}")
        fragment = _read_object(path)
        if fragment.get("chunk_id") != chunk.get("chunk_id") or fragment.get("status") != "complete":
            raise ValueError(f"Chunk identity/status mismatch in {path}")
        if pipeline_kind == "alphabetical":
            _merge_unique_objects(
                data["sections"],
                fragment.get("sections") or [],
                key_fields=("section_key", "section_id"),
                duplicate_policy="keep_first",
            )
            _merge_unique_objects(
                data["nodes"],
                fragment.get("nodes") or [],
                key_fields=("node_key",),
                duplicate_policy="keep_first",
            )
            _merge_unique_objects(
                data["entries"],
                fragment.get("entries") or [],
                key_fields=("entry_key",),
            )
            _merge_unique_objects(
                data["refs"],
                fragment.get("refs") or [],
                key_fields=("entry_key", "ref_order"),
            )
            _merge_unique_objects(
                data["scripture_refs"],
                fragment.get("scripture_refs") or [],
                key_fields=("entry_key", "ref_order"),
            )
            _merge_unique_notes(data["notes"], fragment.get("notes") or [])
        else:
            _merge_unique_objects(
                data["works"],
                fragment.get("works") or [],
                key_fields=("work_key",),
                duplicate_policy="keep_first",
            )
            _merge_unique_objects(
                data["sections"],
                fragment.get("sections") or [],
                key_fields=("section_key",),
                duplicate_policy="merge_entries",
            )
            _merge_unique_notes(data["notes"], fragment.get("notes") or [])
        consumed.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "input_fingerprint": chunk.get("input_fingerprint"),
                "output_file": str(path),
            }
        )

    counts = {field: len(items) for field, items in data.items()}
    if pipeline_kind == "general":
        counts["entries"] = sum(
            len(section.get("entries") or []) for section in data["sections"]
        )
    artifact = {
        "schema_version": 1,
        "volume_id": workplan.get("volume_id"),
        "pipeline_kind": pipeline_kind,
        "status": "complete",
        "expected_chunk_count": len(chunks),
        "consumed_chunk_count": len(consumed),
        "consumed_chunks": consumed,
        "counts": counts,
        "data": data,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_file.with_suffix(output_file.suffix + ".tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_file)
    return artifact


def _key_set(items: list[Any], fields: tuple[str, ...]) -> set[str]:
    return {
        key
        for item in items
        if isinstance(item, dict)
        if (key := _stable_key(item, fields)) is not None
    }


def verify_payload_consumes_fragments(
    payload: dict[str, Any],
    assembled: dict[str, Any],
) -> dict[str, Any]:
    pipeline_kind = str(assembled.get("pipeline_kind") or "")
    data = assembled.get("data") or {}
    checks: dict[str, dict[str, Any]] = {}
    if pipeline_kind == "alphabetical":
        specifications = {
            "sections": ("section_key", "section_id"),
            "nodes": ("node_key",),
            "entries": ("entry_key",),
            "refs": ("entry_key", "ref_order"),
            "scripture_refs": ("entry_key", "ref_order"),
        }
        for field, key_fields in specifications.items():
            expected = _key_set(data.get(field) or [], key_fields)
            actual = _key_set(payload.get(field) or [], key_fields)
            missing = sorted(expected - actual)
            checks[field] = {
                "expected_stable_keys": len(expected),
                "actual_stable_keys": len(actual),
                "missing_stable_keys": missing,
            }
    elif pipeline_kind == "general":
        expected_works = _key_set(data.get("works") or [], ("work_key",))
        actual_works = _key_set(payload.get("works") or [], ("work_key",))
        assembled_sections = [
            section
            for section in data.get("sections") or []
            if isinstance(section, dict)
        ]
        owned_sections: list[dict[str, Any]] = []
        excluded_sections: list[dict[str, str]] = []
        for section in assembled_sections:
            owned, reason = general_section_ownership(section)
            if owned:
                owned_sections.append(section)
                continue
            excluded_sections.append(
                {
                    "section_key": str(section.get("section_key") or ""),
                    "reason": str(reason or "non-owned closing-index section"),
                }
            )
        expected_sections = _key_set(owned_sections, ("section_key",))
        actual_sections = _key_set(payload.get("sections") or [], ("section_key",))
        expected_entries = _key_set(
            [
                entry
                for section in owned_sections
                for entry in section.get("entries") or []
            ],
            ("entry_key",),
        )
        actual_entries = _key_set(
            [
                entry
                for section in payload.get("sections") or []
                if isinstance(section, dict)
                for entry in section.get("entries") or []
            ],
            ("entry_key",),
        )
        for field, expected, actual in (
            ("works", expected_works, actual_works),
            ("sections", expected_sections, actual_sections),
            ("entries", expected_entries, actual_entries),
        ):
            checks[field] = {
                "expected_stable_keys": len(expected),
                "actual_stable_keys": len(actual),
                "missing_stable_keys": sorted(expected - actual),
            }
        checks["ownership"] = {
            "excluded_non_owned_section_count": len(excluded_sections),
            "excluded_non_owned_sections": excluded_sections,
            "missing_stable_keys": [],
        }
    else:
        raise ValueError(f"Unsupported pipeline kind: {pipeline_kind}")

    missing_total = sum(len(item["missing_stable_keys"]) for item in checks.values())
    return {
        "schema_version": 1,
        "volume_id": assembled.get("volume_id"),
        "pipeline_kind": pipeline_kind,
        "status": "ok" if missing_total == 0 else "missing_fragment_objects",
        "missing_stable_key_count": missing_total,
        "checks": checks,
    }

"""Pure helpers for the compact, divide-and-conquer alphabetical-index flow.

The module deliberately has no Codex or filesystem orchestration.  It defines the
small JSON contracts exchanged by the semantic extractor, locator workers,
deterministic assembler, and exceptional repair pass.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SEMANTIC_LIST_FIELDS = ("sections", "nodes", "entries", "refs", "scripture_refs")
LOCATOR_STATUSES = {"resolved", "ambiguous", "unrecoverable_ocr"}
PAGE_SPECIFIC_EVIDENCE_KINDS = {
    "cited_page_match",
    "direct_editorial_page",
    "editorial_header_match",
    "editorial_page_match",
    "header_pair",
    "neighbor_fit",
    "neighbor_sequence",
    "page_number_match",
    "pagination_sequence",
}
SCRIPTURE_CONTENT_EVIDENCE_KINDS = {
    "scripture_regex",
    "scripture_ocr_fuzzy",
    "persistent_scripture_occurrence",
}
MATERIAL_CONTENT_EVIDENCE_KINDS = {
    "body_context_exact",
    "body_context_partial",
    "body_name_fuzzy",
    "body_name_match",
    "body_token_cover",
    "footer_name_fuzzy",
    "footer_name_match",
    "header_name_fuzzy",
    "header_name_match",
    "header_token_cover",
    "page_context_exact",
}
EXACT_EDITORIAL_PAGE_EVIDENCE_KINDS = {
    *PAGE_SPECIFIC_EVIDENCE_KINDS,
    "estimator_page_match",
    "inferred_page_match",
    "raw_number_match",
    "editorial_page_cer_neighbor_validated",
}
EXACT_INTERNAL_LOCATOR_EVIDENCE_KINDS = {
    "body_locator_name_unique",
    "internal_locator_vs_editorial_sequence",
}
INTERNAL_LOCATOR_NUMBER_EVIDENCE_KINDS = {
    "body_locator_match",
    "body_locator_cer_match",
}
WORK_LOCATOR_TEXT_EVIDENCE_KINDS = {
    "body_work_locator_match",
    "header_work_locator_match",
}
WORK_LOCATOR_CORROBORATION_EVIDENCE_KINDS = {
    "work_locator_number_cooccurrence",
    "section_heading_family_match",
}
_ORDO_RE = re.compile(r"\bordo\s+rerum\b", re.IGNORECASE)
_IBID_RE = re.compile(r"^\s*(?:ibid(?:em)?|ibidem)\.?\s*$", re.IGNORECASE)


class CompactPipelineError(ValueError):
    """Raised when a compact artifact violates an invariant."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompactPipelineError(f"{label} must be a JSON object")
    return deepcopy(dict(value))


def _as_object_list(value: Any, *, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CompactPipelineError(f"{label} must be a JSON array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CompactPipelineError(f"{label}[{index}] must be a JSON object")
        result.append(deepcopy(dict(item)))
    return result


def _stable_pair(item: Mapping[str, Any], *, label: str) -> tuple[str, int]:
    entry_key = str(item.get("entry_key") or "").strip()
    if not entry_key:
        raise CompactPipelineError(f"{label} is missing entry_key")
    raw_order = item.get("ref_order")
    if isinstance(raw_order, bool):
        raise CompactPipelineError(f"{label} has invalid ref_order {raw_order!r}")
    try:
        ref_order = int(raw_order)
    except (TypeError, ValueError) as exc:
        raise CompactPipelineError(f"{label} has invalid ref_order {raw_order!r}") from exc
    if ref_order < 1:
        raise CompactPipelineError(f"{label} has invalid ref_order {ref_order!r}")
    return entry_key, ref_order


def locator_key(entry_key: str, ref_order: int) -> str:
    return f"{entry_key}::ref:{ref_order:06d}"


def standardize_locator_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the canonical coordinate view used at pipeline handoffs.

    Flat fields remain readable during the migration because the deterministic
    helpers and historical checkpoints still use them.  New agent prompts should
    prefer ``locator_contract``: it makes source OCR, cited editorial coordinates,
    and the still-unresolved target impossible to confuse by field name alone.
    """

    normalized = deepcopy(dict(item))
    entry_key, ref_order = _stable_pair(normalized, label="locator item")
    normalized["locator_format_version"] = 2
    locator_contract = {
        "ownership": {
            "locator_key": normalized.get("locator_key")
            or locator_key(entry_key, ref_order),
            "entry_key": entry_key,
            "ref_order": ref_order,
            "scripture_ref_order": normalized.get("scripture_ref_order"),
        },
        "index_source": {
            "index_section_start_ocr_file": normalized.get(
                "section_file_start"
            )
            or normalized.get("section_start_file"),
            "index_section_end_ocr_file": normalized.get("section_file_end"),
            "index_entry_source_ocr_file": normalized.get(
                "editorial_anchor_file"
            ),
            "section_key": normalized.get("section_key"),
            "section_kind": normalized.get("section_kind"),
            "section_heading_raw": normalized.get("section_heading"),
        },
        "cited_location": {
            "ref_kind": normalized.get("ref_kind"),
            "ref_raw": normalized.get("ref_raw"),
            "cited_editorial_page_start_raw": normalized.get(
                "page_ref_raw"
            )
            or normalized.get("range_start_raw"),
            "cited_editorial_page_start_number": normalized.get(
                "page_ref_int"
            )
            or normalized.get("range_start_int"),
            "cited_editorial_page_end_raw": normalized.get("range_end_raw"),
            "cited_editorial_page_end_number": normalized.get(
                "range_end_int"
            ),
            "cited_editorial_column_raw": normalized.get("page_ref_col"),
            "cited_editorial_line_raw": normalized.get("line_ref_raw"),
            "candidate_editorial_page_numbers": list(
                normalized.get("cited_pages") or []
            ),
        },
        "search_hints": {
            "lemma_raw": normalized.get("lemma_raw"),
            "scripture_ref": deepcopy(normalized.get("scripture_ref")),
            "entry_excerpt_field": "entry_excerpt",
            "context_excerpt_field": "context_excerpt",
        },
        "target_resolution": {
            "status": "pending",
            "resolved_target_ocr_file": None,
            "target_ocr_file_candidate_score": None,
            "candidate_source_field": "candidates",
        },
    }
    normalized["locator_contract"] = {
        group: {
            key: value
            for key, value in fields.items()
            if value is not None
        }
        for group, fields in locator_contract.items()
    }
    return normalized


def standardize_locator_items(
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [standardize_locator_item(item) for item in items]


def _contains_ordo(section: Mapping[str, Any]) -> bool:
    if str(section.get("section_kind") or "").strip().casefold() == "ordo_rerum":
        return True
    heading = " ".join(
        str(section.get(field) or "") for field in ("heading_raw", "heading_norm")
    )
    return bool(_ORDO_RE.search(heading))


def _validate_semantic_payload(payload: dict[str, Any]) -> None:
    sections = payload["sections"]
    ordo_sections = [
        str(section.get("section_key") or f"sections[{index}]")
        for index, section in enumerate(sections)
        if _contains_ordo(section)
    ]
    if ordo_sections:
        raise CompactPipelineError(
            "ORDO RERUM is boundary-only in the alphabetical pipeline; "
            f"remove sections: {ordo_sections}"
        )
    editorial_closures = [
        str(section.get("section_key") or f"sections[{index}]")
        for index, section in enumerate(sections)
        if str(section.get("section_kind") or "").strip().casefold()
        == "editorial_closure"
    ]
    if editorial_closures:
        raise CompactPipelineError(
            "editorial_closure belongs to the general index pipeline, not the "
            "alphabetical pipeline; remove sections: "
            f"{editorial_closures}"
        )

    section_keys: set[str] = set()
    for index, section in enumerate(sections):
        key = str(section.get("section_key") or "").strip()
        if not key:
            raise CompactPipelineError(f"sections[{index}] is missing section_key")
        if key in section_keys:
            raise CompactPipelineError(f"duplicate section_key {key!r}")
        section_keys.add(key)

    entry_keys: set[str] = set()
    for index, entry in enumerate(payload["entries"]):
        key = str(entry.get("entry_key") or "").strip()
        if not key:
            raise CompactPipelineError(f"entries[{index}] is missing entry_key")
        if key in entry_keys:
            raise CompactPipelineError(f"duplicate entry_key {key!r}")
        section_key = str(entry.get("section_key") or "").strip()
        if section_keys and section_key not in section_keys:
            raise CompactPipelineError(
                f"entries[{index}] references missing section_key {section_key!r}"
            )
        entry_keys.add(key)

    for field in ("refs", "scripture_refs"):
        seen: set[tuple[str, int]] = set()
        for index, item in enumerate(payload[field]):
            pair = _stable_pair(item, label=f"{field}[{index}]")
            if pair[0] not in entry_keys:
                raise CompactPipelineError(
                    f"{field}[{index}] references missing entry_key {pair[0]!r}"
                )
            if pair in seen:
                raise CompactPipelineError(
                    f"duplicate {field} locator entry_key={pair[0]!r}, ref_order={pair[1]}"
                )
            seen.add(pair)

    entry_kind_by_key = {
        str(entry.get("entry_key") or "").strip(): str(entry.get("entry_kind") or "").strip()
        for entry in payload["entries"]
    }
    entry_by_key = {
        str(entry.get("entry_key") or "").strip(): entry
        for entry in payload["entries"]
    }
    section_by_key = {
        str(section.get("section_key") or "").strip(): section
        for section in payload["sections"]
    }
    scripture_orders: dict[str, set[int]] = {}
    for scripture_ref in payload["scripture_refs"]:
        entry_key, ref_order = _stable_pair(
            scripture_ref, label="scripture_refs parent"
        )
        scripture_orders.setdefault(entry_key, set()).add(ref_order)

    scripture_counts = {
        entry_key: len(ref_orders)
        for entry_key, ref_orders in scripture_orders.items()
    }
    for entry_key, count in scripture_counts.items():
        if (
            count > 1
            and entry_kind_by_key.get(entry_key)
            in {"scripture_citation", "scripture_pericope"}
        ):
            raise CompactPipelineError(
                f"{entry_kind_by_key[entry_key]} entry {entry_key!r} contains {count} "
                "biblical citations; split distinct passages into separate entries so each "
                "entry can own all of its material refs"
            )

    material_counts: dict[str, int] = {}
    for index, material_ref in enumerate(payload["refs"]):
        entry_key = str(material_ref.get("entry_key") or "").strip()
        material_counts[entry_key] = material_counts.get(entry_key, 0) + 1
        parent_orders = scripture_orders.get(entry_key, set())
        raw_parent = material_ref.get("scripture_ref_order")
        if parent_orders:
            if isinstance(raw_parent, bool):
                raw_parent = None
            try:
                parent_order = int(raw_parent)
            except (TypeError, ValueError):
                parent_order = None
            if parent_order not in parent_orders:
                raise CompactPipelineError(
                    f"refs[{index}] for biblical entry {entry_key!r} must set "
                    "scripture_ref_order to an existing scripture_refs.ref_order"
                )
        elif raw_parent is not None:
            raise CompactPipelineError(
                f"refs[{index}] sets scripture_ref_order but entry {entry_key!r} "
                "has no scripture_refs parent"
            )
    for entry_key, count in scripture_counts.items():
        entry = entry_by_key.get(entry_key, {})
        section = section_by_key.get(str(entry.get("section_key") or "").strip(), {})
        entry_raw = entry.get("raw_json")
        section_raw = section.get("raw_json")
        source_only = (
            (
                isinstance(entry_raw, Mapping)
                and entry_raw.get("material_reference_mode") == "source_only"
            )
            or (
                isinstance(section_raw, Mapping)
                and section_raw.get("material_reference_mode") == "source_only"
            )
        )
        if (
            material_counts.get(entry_key, 0) == 0
            and entry_kind_by_key.get(entry_key)
            in {"scripture_citation", "scripture_pericope"}
            and not source_only
        ):
            raise CompactPipelineError(
                f"biblical entry {entry_key!r} has no material refs; remissive scripture "
                "entries must serialize every cited page, while non-remissive apparatus "
                "sections must declare raw_json.material_reference_mode='source_only'"
            )


def _record_deterministic_repair(obj: dict[str, Any], repair: dict[str, Any]) -> None:
    raw_json = obj.get("raw_json")
    if isinstance(raw_json, Mapping):
        normalized_raw_json = deepcopy(dict(raw_json))
    elif raw_json is None:
        normalized_raw_json = {}
    else:
        normalized_raw_json = {"semantic_raw_json": deepcopy(raw_json)}
    repairs = normalized_raw_json.get("deterministic_repairs")
    if not isinstance(repairs, list):
        repairs = []
    if repair not in repairs:
        repairs.append(repair)
    normalized_raw_json["deterministic_repairs"] = repairs
    obj["raw_json"] = normalized_raw_json


def canonicalize_mechanical_semantics(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply only lossless or structurally forced semantic repairs.

    The semantic agent owns editorial interpretation.  Python owns mechanical
    consequences that have exactly one valid result: semantic targets must be
    blank, a material ref with one biblical parent links to that parent, plain
    decimal page literals become integers, and an immediate ``ibid.`` may
    inherit the preceding page inside the same entry.
    """

    scripture_orders: dict[str, list[int]] = {}
    section_by_key = {
        str(section.get("section_key") or ""): section
        for section in payload.get("sections") or []
        if isinstance(section, Mapping)
    }
    entry_by_key = {
        str(entry.get("entry_key") or ""): entry
        for entry in payload.get("entries") or []
        if isinstance(entry, Mapping)
    }
    for index, scripture_ref in enumerate(payload.get("scripture_refs") or []):
        if not isinstance(scripture_ref, Mapping):
            continue
        entry_key, ref_order = _stable_pair(
            scripture_ref,
            label=f"scripture_refs[{index}]",
        )
        scripture_orders.setdefault(entry_key, []).append(ref_order)

    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("target_file_best") is not None:
            entry["target_file_best"] = None
            _record_deterministic_repair(
                entry,
                {
                    "kind": "clear_semantic_target",
                    "field": "target_file_best",
                },
            )
        source_span = entry.get("source_span")
        source_file = (
            str(source_span.get("file") or "").strip()
            if isinstance(source_span, Mapping)
            else ""
        )
        section = section_by_key.get(str(entry.get("section_key") or ""), {})
        section_file = str(section.get("file_start") or "").strip()
        for field, value, kind in (
            (
                "editorial_anchor_file",
                source_file,
                "derive_index_entry_source_ocr_file",
            ),
            (
                "section_start_file",
                section_file,
                "derive_index_section_start_ocr_file",
            ),
        ):
            if value and not str(entry.get(field) or "").strip():
                entry[field] = value
                _record_deterministic_repair(
                    entry,
                    {"kind": kind, "field": field, "value": value},
                )

    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for index, ref in enumerate(payload.get("refs") or []):
        if not isinstance(ref, dict):
            continue
        entry_key, ref_order = _stable_pair(ref, label=f"refs[{index}]")
        refs_by_entry.setdefault(entry_key, []).append(ref)
        entry = entry_by_key.get(entry_key, {})
        for field in ("section_start_file", "editorial_anchor_file"):
            inherited = str(entry.get(field) or "").strip()
            if inherited and not str(ref.get(field) or "").strip():
                ref[field] = inherited
                _record_deterministic_repair(
                    ref,
                    {
                        "kind": "inherit_index_source_ocr_file",
                        "field": field,
                        "value": inherited,
                    },
                )

        cleared_fields = []
        for field in ("target_file", "target_file_probability"):
            if ref.get(field) is not None:
                ref[field] = None
                cleared_fields.append(field)
        if cleared_fields:
            _record_deterministic_repair(
                ref,
                {
                    "kind": "clear_semantic_target",
                    "fields": cleared_fields,
                },
            )

        page_ref_raw = str(ref.get("page_ref_raw") or "").strip()
        if ref.get("page_ref_int") is None and page_ref_raw.isdigit():
            page = int(page_ref_raw)
            if 0 < page < 10000:
                ref["page_ref_int"] = page
                _record_deterministic_repair(
                    ref,
                    {
                        "kind": "parse_decimal_page",
                        "source": "page_ref_raw",
                        "value": page,
                    },
                )

        for raw_field, int_field in (
            ("range_start_raw", "range_start_int"),
            ("range_end_raw", "range_end_int"),
        ):
            raw_value = str(ref.get(raw_field) or "").strip()
            if ref.get(int_field) is None and raw_value.isdigit():
                value = int(raw_value)
                if 0 < value < 10000:
                    ref[int_field] = value
                    _record_deterministic_repair(
                        ref,
                        {
                            "kind": "parse_decimal_page_endpoint",
                            "source": raw_field,
                            "field": int_field,
                            "value": value,
                        },
                    )

        parent_orders = sorted(set(scripture_orders.get(entry_key, [])))
        if len(parent_orders) == 1 and ref.get("scripture_ref_order") is None:
            ref["scripture_ref_order"] = parent_orders[0]
            _record_deterministic_repair(
                ref,
                {
                    "kind": "link_single_scripture_parent",
                    "scripture_ref_order": parent_orders[0],
                },
            )
        elif not parent_orders and ref.get("scripture_ref_order") is not None:
            ref["scripture_ref_order"] = None
            _record_deterministic_repair(
                ref,
                {"kind": "clear_orphan_scripture_parent"},
            )

    for entry_key, entry_refs in refs_by_entry.items():
        previous_page: int | None = None
        previous_order: int | None = None
        for ref in sorted(entry_refs, key=lambda item: int(item["ref_order"])):
            page = ref.get("page_ref_int")
            if isinstance(page, int) and not isinstance(page, bool) and page > 0:
                previous_page = page
                previous_order = int(ref["ref_order"])
                continue
            literal = str(ref.get("page_ref_raw") or ref.get("ref_raw") or "")
            if previous_page is None or not _IBID_RE.fullmatch(literal):
                continue
            ref["page_ref_int"] = previous_page
            _record_deterministic_repair(
                ref,
                {
                    "kind": "resolve_immediate_ibid",
                    "inherits_from_ref_order": previous_order,
                    "page_ref_int": previous_page,
                },
            )
            previous_order = int(ref["ref_order"])

    return payload


def coerce_semantic_payload(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    volume: Mapping[str, Any] | None = None,
    sections: list[Mapping[str, Any]] | None = None,
    nodes: list[Mapping[str, Any]] | None = None,
    entries: list[Mapping[str, Any]] | None = None,
    refs: list[Mapping[str, Any]] | None = None,
    scripture_refs: list[Mapping[str, Any]] | None = None,
    coverage: Mapping[str, Any] | None = None,
    notes: list[Any] | None = None,
) -> dict[str, Any]:
    """Normalize a canonical payload, fragment sequence, or explicit semantic lists."""

    if payload is not None and any(
        item is not None
        for item in (
            volume,
            sections,
            nodes,
            entries,
            refs,
            scripture_refs,
            coverage,
            notes,
        )
    ):
        raise CompactPipelineError("pass either payload/fragments or explicit semantic fields")

    if payload is None:
        base: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": None,
            "volume": _as_object(volume or {}, label="volume"),
            "sections": _as_object_list(sections or [], label="sections"),
            "nodes": _as_object_list(nodes or [], label="nodes"),
            "entries": _as_object_list(entries or [], label="entries"),
            "refs": _as_object_list(refs or [], label="refs"),
            "scripture_refs": _as_object_list(
                scripture_refs or [], label="scripture_refs"
            ),
            "coverage": _as_object(coverage or {}, label="coverage"),
            "notes": deepcopy(notes or []),
        }
    elif isinstance(payload, Mapping):
        source = dict(payload)
        if isinstance(source.get("data"), Mapping):
            data = dict(source["data"])
            source = {
                **source,
                **data,
                "volume": source.get("volume")
                or data.get("volume")
                or {"volume_id": source.get("volume_id")},
            }
        base = {
            "schema_version": source.get("schema_version", 1),
            "generated_at": source.get("generated_at"),
            "volume": _as_object(source.get("volume") or {}, label="volume"),
            "coverage": _as_object(source.get("coverage") or {}, label="coverage"),
            "notes": deepcopy(source.get("notes") or []),
        }
        for field in SEMANTIC_LIST_FIELDS:
            base[field] = _as_object_list(source.get(field) or [], label=field)
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        fragments = list(payload)
        if not fragments:
            return coerce_semantic_payload()
        merged: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": None,
            "volume": {},
            "coverage": {},
            "notes": [],
            **{field: [] for field in SEMANTIC_LIST_FIELDS},
        }
        for index, raw_fragment in enumerate(fragments):
            fragment = _as_object(raw_fragment, label=f"fragments[{index}]")
            data = fragment.get("data")
            if isinstance(data, Mapping):
                fragment = {**fragment, **dict(data)}
            if fragment.get("volume") and not merged["volume"]:
                merged["volume"] = _as_object(
                    fragment["volume"], label=f"fragments[{index}].volume"
                )
            elif fragment.get("volume_id") and not merged["volume"]:
                merged["volume"] = {"volume_id": fragment["volume_id"]}
            for field in SEMANTIC_LIST_FIELDS:
                merged[field].extend(
                    _as_object_list(
                        fragment.get(field) or [], label=f"fragments[{index}].{field}"
                    )
                )
            fragment_notes = fragment.get("notes") or []
            if not isinstance(fragment_notes, list):
                raise CompactPipelineError(f"fragments[{index}].notes must be a JSON array")
            merged["notes"].extend(deepcopy(fragment_notes))
            if fragment.get("coverage"):
                merged["coverage"].update(
                    _as_object(
                        fragment["coverage"], label=f"fragments[{index}].coverage"
                    )
                )
        base = merged
    else:
        raise CompactPipelineError("payload must be a JSON object or fragment array")

    if not isinstance(base["notes"], list):
        raise CompactPipelineError("notes must be a JSON array")
    canonicalize_mechanical_semantics(base)
    _validate_semantic_payload(base)
    return base


def _candidate_probability(item: Mapping[str, Any]) -> float | None:
    for field in ("probability", "confidence", "score"):
        raw = item.get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 1.0:
            return value
    label = str(item.get("confidence_label") or "").casefold()
    return {"high": 0.9, "medium": 0.65, "low": 0.35}.get(label)


def _page_values(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        result: list[int] = []
        for item in value:
            result.extend(_page_values(item))
        return list(dict.fromkeys(result))
    if isinstance(value, bool):
        return []
    try:
        number = int(value)
    except (TypeError, ValueError):
        return []
    return [number] if 0 < number < 10000 else []


def normalize_editorial_page_map(
    editorial_page_map: Mapping[Any, Any] | None,
) -> dict[int, list[dict[str, Any]]]:
    """Accept a simple page map or the full estimator payload."""

    if not editorial_page_map:
        return {}
    page_map: dict[int, list[dict[str, Any]]] = {}

    def add(page: int, candidate: Mapping[str, Any] | str) -> None:
        if isinstance(candidate, str):
            normalized = {"file": candidate, "probability": None, "evidence": []}
        else:
            file_path = str(
                candidate.get("file") or candidate.get("path") or candidate.get("target_file") or ""
            ).strip()
            if not file_path:
                return
            normalized = {
                "file": file_path,
                "probability": _candidate_probability(candidate),
                "evidence": [
                    {
                        "kind": item.get("kind"),
                        "detail": str(
                            item.get("detail") or item.get("raw") or ""
                        )[:160],
                        "weight": item.get("weight"),
                    }
                    for item in (candidate.get("evidence") or [])[:4]
                    if isinstance(item, Mapping)
                ],
            }
        bucket = page_map.setdefault(page, [])
        existing = next(
            (item for item in bucket if item["file"] == normalized["file"]), None
        )
        if existing is None:
            bucket.append(normalized)
        elif (normalized["probability"] or -1.0) > (existing["probability"] or -1.0):
            existing.update(normalized)

    files = editorial_page_map.get("files")
    if isinstance(files, list):
        for raw_file in files:
            if not isinstance(raw_file, Mapping):
                continue
            pages: list[int] = []
            for field in (
                "best_guess",
                "best_left_page",
                "best_right_page",
                "best_single_page",
                "pages",
            ):
                pages.extend(_page_values(raw_file.get(field)))
            for page in dict.fromkeys(pages):
                add(page, raw_file)
            base_probability = _candidate_probability(raw_file)
            for rank, hypothesis in enumerate(
                raw_file.get("candidate_editorial_pages") or []
            ):
                if rank >= 2 or not isinstance(hypothesis, Mapping):
                    break
                probability = (
                    max(0.15, (base_probability or 0.5) * (0.8 - 0.2 * rank))
                )
                candidate = {
                    "file": raw_file.get("file"),
                    "probability": probability,
                    "evidence": hypothesis.get("evidence") or [],
                }
                for page in _page_values(hypothesis.get("pages")):
                    add(page, candidate)
    else:
        for raw_page, raw_candidates in editorial_page_map.items():
            pages = _page_values(raw_page)
            if not pages:
                continue
            candidates = (
                raw_candidates
                if isinstance(raw_candidates, list)
                else [raw_candidates]
            )
            for page in pages:
                for candidate in candidates:
                    if isinstance(candidate, (str, Mapping)):
                        add(page, candidate)

    for candidates in page_map.values():
        candidates.sort(
            key=lambda item: (
                -(item["probability"] if item["probability"] is not None else -1.0),
                item["file"],
            )
        )
    return page_map


def _ref_cited_pages(ref: Mapping[str, Any]) -> list[int]:
    pages: list[int] = []
    for field in ("page_ref_int", "range_start_int", "range_end_int"):
        pages.extend(_page_values(ref.get(field)))
    for field in ("page_ref_raw", "range_start_raw", "range_end_raw"):
        raw = str(ref.get(field) or "").strip()
        if raw.isdigit():
            pages.extend(_page_values(raw))
    starts = _page_values(ref.get("range_start_int") or ref.get("range_start_raw"))
    ends = _page_values(ref.get("range_end_int") or ref.get("range_end_raw"))
    if starts and ends:
        lower, upper = sorted((starts[0], ends[0]))
        if upper - lower <= 32:
            pages.extend(range(lower, upper + 1))
    return list(dict.fromkeys(pages))


def build_locator_items(
    semantic_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    editorial_page_map: Mapping[Any, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create one compact locator work item for every material reference."""

    semantic = coerce_semantic_payload(semantic_payload)
    normalized_map = normalize_editorial_page_map(editorial_page_map)
    entries = {
        str(entry["entry_key"]): entry for entry in semantic["entries"]
    }
    sections = {
        str(section["section_key"]): section for section in semantic["sections"]
    }
    excluded_index_intervals = [
        {
            "index_ocr_file_start": section.get("file_start"),
            "index_ocr_file_end": section.get("file_end"),
            "section_key": section.get("section_key"),
        }
        for section in semantic["sections"]
        if section.get("file_start") or section.get("file_end")
    ]
    scripture_refs = {
        _stable_pair(scripture_ref, label=f"scripture_refs[{index}]"): scripture_ref
        for index, scripture_ref in enumerate(semantic["scripture_refs"])
    }
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, ref in enumerate(semantic["refs"]):
        entry_key, ref_order = _stable_pair(ref, label=f"refs[{index}]")
        pair = (entry_key, ref_order)
        if pair in seen:
            raise CompactPipelineError(
                f"duplicate locator entry_key={entry_key!r}, ref_order={ref_order}"
            )
        seen.add(pair)
        entry = entries[entry_key]
        section = sections.get(str(entry.get("section_key") or ""), {})
        pages = _ref_cited_pages(ref)
        candidates: list[dict[str, Any]] = []
        candidate_files: set[str] = set()
        for page in pages:
            for candidate in normalized_map.get(page, []):
                file_path = str(candidate["file"])
                if file_path in candidate_files:
                    continue
                candidate_files.add(file_path)
                candidates.append({**deepcopy(candidate), "matched_page": page})
        candidates.sort(
            key=lambda item: (
                -(item["probability"] if item["probability"] is not None else -1.0),
                item["file"],
                item["matched_page"],
            )
        )
        entry_raw = str(entry.get("entry_raw") or "")
        context_raw = str(entry.get("context_raw") or "")
        scripture_ref = None
        raw_scripture_order = ref.get("scripture_ref_order")
        if raw_scripture_order is not None:
            try:
                scripture_order = int(raw_scripture_order)
            except (TypeError, ValueError):
                scripture_order = 0
            source_scripture = scripture_refs.get((entry_key, scripture_order))
            if source_scripture is not None:
                scripture_ref = {
                    field: deepcopy(source_scripture.get(field))
                    for field in (
                        "ref_order",
                        "ref_role",
                        "ref_raw",
                        "ref_norm",
                        "book_raw",
                        "book_norm",
                        "chapter_start",
                        "verse_start",
                        "chapter_end",
                        "verse_end",
                    )
                    if field in source_scripture
                }
        items.append(
            standardize_locator_item({
                "locator_key": locator_key(entry_key, ref_order),
                "entry_key": entry_key,
                "ref_order": ref_order,
                "scripture_ref_order": ref.get("scripture_ref_order"),
                "scripture_ref": scripture_ref,
                "entry_order": entry.get("entry_order"),
                "section_key": entry.get("section_key"),
                "section_kind": section.get("section_kind"),
                "section_heading": section.get("heading_raw"),
                "section_file_start": section.get("file_start"),
                "section_file_end": section.get("file_end"),
                "excluded_index_intervals": deepcopy(
                    excluded_index_intervals
                ),
                "lemma_raw": entry.get("lemma_raw"),
                "entry_excerpt": entry_raw[:300],
                "context_excerpt": context_raw[:300]
                if context_raw and context_raw != entry_raw
                else None,
                "ref_kind": ref.get("ref_kind"),
                "ref_raw": ref.get("ref_raw"),
                "page_ref_raw": ref.get("page_ref_raw"),
                "page_ref_int": ref.get("page_ref_int"),
                "page_ref_col": ref.get("page_ref_col"),
                "line_ref_raw": ref.get("line_ref_raw"),
                "range_start_raw": ref.get("range_start_raw"),
                "range_end_raw": ref.get("range_end_raw"),
                "range_start_int": ref.get("range_start_int"),
                "range_end_int": ref.get("range_end_int"),
                "cited_pages": pages,
                "section_start_file": ref.get("section_start_file")
                or entry.get("section_start_file"),
                "editorial_anchor_file": ref.get("editorial_anchor_file")
                or entry.get("editorial_anchor_file"),
                "candidates": candidates,
            })
        )

    def sort_key(item: Mapping[str, Any]) -> tuple[int, str, int]:
        raw_order = item.get("entry_order")
        try:
            entry_order = int(raw_order)
        except (TypeError, ValueError):
            entry_order = 2**31 - 1
        return entry_order, str(item["entry_key"]), int(item["ref_order"])

    return sorted(items, key=sort_key)


def shard_locator_items(
    locator_items: Sequence[Mapping[str, Any]],
    *,
    shard_size: int = 40,
) -> list[dict[str, Any]]:
    if isinstance(shard_size, bool) or not isinstance(shard_size, int) or shard_size < 1:
        raise CompactPipelineError("shard_size must be a positive integer")
    normalized = [_as_object(item, label=f"locator_items[{i}]") for i, item in enumerate(locator_items)]
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(normalized):
        pair = _stable_pair(item, label=f"locator_items[{index}]")
        if pair in seen:
            raise CompactPipelineError(
                f"overlapping locator ownership for entry_key={pair[0]!r}, ref_order={pair[1]}"
            )
        seen.add(pair)
    normalized.sort(
        key=lambda item: (
            int(item["entry_order"])
            if str(item.get("entry_order") or "").isdigit()
            else 2**31 - 1,
            str(item["entry_key"]),
            int(item["ref_order"]),
        )
    )
    shard_count = (len(normalized) + shard_size - 1) // shard_size
    shards: list[dict[str, Any]] = []
    for index in range(0, len(normalized), shard_size):
        chunk = deepcopy(normalized[index : index + shard_size])
        excluded_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in chunk:
            for interval in item.pop("excluded_index_intervals", []) or []:
                if not isinstance(interval, Mapping):
                    continue
                key = (
                    str(interval.get("section_key") or ""),
                    str(interval.get("index_ocr_file_start") or ""),
                    str(interval.get("index_ocr_file_end") or ""),
                )
                excluded_by_key[key] = dict(interval)
        shards.append({
            "schema_version": 1,
            "shard_id": f"locator-{index // shard_size + 1:04d}",
            "shard_order": index // shard_size + 1,
            "shard_count": shard_count,
            "item_count": len(chunk),
            "excluded_index_intervals": list(excluded_by_key.values()),
            "items": chunk,
        })
    return shards


def build_deterministic_locator_results(
    locator_items: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve unique candidates with independent page and content signals."""

    resolved: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for index, raw_item in enumerate(locator_items):
        item = _as_object(raw_item, label=f"locator_items[{index}]")
        is_scripture = isinstance(item.get("scripture_ref"), Mapping)
        dual_candidates: list[dict[str, Any]] = []
        for raw_candidate in item.get("candidates") or []:
            if not isinstance(raw_candidate, Mapping):
                continue
            candidate = deepcopy(dict(raw_candidate))
            target_file = str(candidate.get("file") or "").strip()
            evidence = [
                deepcopy(dict(evidence_item))
                for evidence_item in candidate.get("evidence") or []
                if isinstance(evidence_item, Mapping)
            ]
            kinds = {
                str(evidence_item.get("kind") or "").strip().casefold()
                for evidence_item in evidence
            }
            content_kinds = (
                SCRIPTURE_CONTENT_EVIDENCE_KINDS
                if is_scripture
                else MATERIAL_CONTENT_EVIDENCE_KINDS
            )
            helper_confirmed = (
                is_scripture
                or (
                    candidate.get("helper_status") == "resolved"
                    and candidate.get("helper_is_best") is True
                    and candidate.get("candidate_role") == "target_candidate"
                )
            )
            editorial_dual_evidence = bool(
                kinds & EXACT_EDITORIAL_PAGE_EVIDENCE_KINDS
                and kinds & content_kinds
            )
            internal_name_evidence = bool(
                not is_scripture
                and EXACT_INTERNAL_LOCATOR_EVIDENCE_KINDS <= kinds
                and kinds & INTERNAL_LOCATOR_NUMBER_EVIDENCE_KINDS
                and kinds & content_kinds
            )
            work_locator_evidence = bool(
                item.get("ref_kind") in {"target_locator", "parallel_locator"}
                and kinds & WORK_LOCATOR_TEXT_EVIDENCE_KINDS
                and WORK_LOCATOR_CORROBORATION_EVIDENCE_KINDS <= kinds
            )
            if (
                target_file
                and (
                    editorial_dual_evidence
                    or internal_name_evidence
                    or work_locator_evidence
                )
                and helper_confirmed
            ):
                candidate["file"] = target_file
                candidate["evidence"] = evidence
                dual_candidates.append(candidate)
        if len(dual_candidates) != 1:
            pending.append(item)
            continue
        candidate = dual_candidates[0]
        try:
            confidence = float(candidate.get("probability"))
        except (TypeError, ValueError):
            pending.append(item)
            continue
        kinds = {
            str(evidence_item.get("kind") or "").strip().casefold()
            for evidence_item in candidate["evidence"]
        }
        strong_editorial = bool(
            kinds
            & {
                "header_pair",
                "neighbor_fit",
                "neighbor_sequence",
                "pagination_sequence",
            }
        )
        apparatus = "critical_apparatus_context" in kinds
        if is_scripture:
            minimum_confidence = 0.75 if apparatus and strong_editorial else 0.85
        else:
            minimum_confidence = (
                0.85
                if (
                    "body_locator_name_unique" in kinds
                    or kinds & WORK_LOCATOR_TEXT_EVIDENCE_KINDS
                )
                else 0.90
            )
        if not 0.0 <= confidence <= 1.0 or confidence < minimum_confidence:
            pending.append(item)
            continue
        resolved.append(
            {
                "entry_key": item["entry_key"],
                "ref_order": item["ref_order"],
                "status": "resolved",
                "target_file": candidate["file"],
                "confidence": confidence,
                "evidence": [
                    *candidate["evidence"],
                    {
                        "kind": "deterministic_dual_evidence",
                        "detail": (
                            "unique candidate with independent locator and "
                            "content evidence"
                        ),
                    },
                ],
            }
        )
    return resolved, pending


def _flatten_results(results: Any) -> list[dict[str, Any]]:
    if results is None:
        return []
    if isinstance(results, Mapping):
        if isinstance(results.get("shards"), list):
            flattened: list[dict[str, Any]] = []
            for shard in results["shards"]:
                flattened.extend(_flatten_results(shard))
            return flattened
        for field in ("results", "items"):
            if isinstance(results.get(field), list):
                return _flatten_results(results[field])
        return [_as_object(results, label="result")]
    if isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        flattened = []
        for index, item in enumerate(results):
            if isinstance(item, Mapping) and (
                isinstance(item.get("results"), list)
                or isinstance(item.get("items"), list)
            ):
                flattened.extend(_flatten_results(item))
            else:
                flattened.append(_as_object(item, label=f"results[{index}]"))
        return flattened
    raise CompactPipelineError("locator results must be an object or array")


def _confidence(result: Mapping[str, Any]) -> float | None:
    for field in ("target_file_probability", "confidence", "probability"):
        raw = result.get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None
    return None


def _has_attempted_evidence(result: Mapping[str, Any]) -> bool:
    for field in ("attempted_evidence", "attempted_files", "attempted_searches"):
        value = result.get(field)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _has_non_empty_reason(result: Mapping[str, Any]) -> bool:
    return bool(str(result.get("reason") or "").strip())


def _contains_intermediate_facsimile_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                str(key).casefold()
                in {"image_path", "image_file", "facsimile_path"}
                and str(item or "").strip()
            ):
                return True
            if _contains_intermediate_facsimile_path(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_intermediate_facsimile_path(item) for item in value)
    return False


def _competing_candidates(result: Mapping[str, Any]) -> list[Any]:
    for field in ("competing_candidates", "candidates"):
        value = result.get(field)
        if isinstance(value, list):
            return deepcopy(value)
    return []


def _normalize_attempted_searches(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, Mapping):
            query = str(item.get("query") or "").strip()
            result = str(item.get("result") or "").strip()
            if query and result:
                normalized.append(deepcopy(dict(item)))
        elif isinstance(item, str) and item.strip():
            normalized.append(item.strip())
    return normalized


def normalize_locator_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize evidence placement without changing a locator decision."""

    normalized = deepcopy(dict(result))
    resolution = normalized.get("target_resolution")
    if isinstance(resolution, Mapping):
        if not str(normalized.get("status") or "").strip():
            normalized["status"] = resolution.get("status")
        if not str(normalized.get("target_file") or "").strip():
            normalized["target_file"] = resolution.get(
                "resolved_target_ocr_file"
            )
        if normalized.get("confidence") is None:
            normalized["confidence"] = resolution.get(
                "target_ocr_file_candidate_score"
            )
        for field in (
            "evidence",
            "reason",
            "competing_candidates",
            "attempted_files",
            "attempted_searches",
            "attempted_evidence",
        ):
            if not normalized.get(field) and resolution.get(field):
                normalized[field] = deepcopy(resolution[field])
    if not str(normalized.get("target_file") or "").strip():
        normalized["target_file"] = normalized.get("resolved_target_ocr_file")
    if normalized.get("confidence") is None:
        normalized["confidence"] = normalized.get(
            "target_ocr_file_candidate_score"
        )
    raw_evidence = normalized.get("evidence")
    if isinstance(raw_evidence, list):
        evidence_items: list[Any] = []
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                evidence_items.append(item)
                continue
            canonical = dict(item)
            if not canonical.get("file") and canonical.get("ocr_file_path"):
                canonical["file"] = canonical["ocr_file_path"]
            if (
                canonical.get("editorial_page") is None
                and canonical.get("observed_editorial_page_number") is not None
            ):
                canonical["editorial_page"] = canonical[
                    "observed_editorial_page_number"
                ]
            evidence_items.append(canonical)
        normalized["evidence"] = evidence_items
    raw_json = normalized.get("raw_json")
    raw = dict(raw_json) if isinstance(raw_json, Mapping) else {}
    helper = raw.get("helper_locator")
    helper = dict(helper) if isinstance(helper, Mapping) else {}

    for field in ("attempted_files", "attempted_evidence"):
        if not normalized.get(field):
            source = raw.get(field)
            if isinstance(source, list) and source:
                normalized[field] = deepcopy(source)
    if not normalized.get("attempted_searches"):
        normalized_searches = _normalize_attempted_searches(
            raw.get("attempted_searches")
        )
        if normalized_searches:
            normalized["attempted_searches"] = normalized_searches
    else:
        normalized["attempted_searches"] = _normalize_attempted_searches(
            normalized.get("attempted_searches")
        )

    status = str(normalized.get("status") or "").strip()
    if status in {"ambiguous", "unrecoverable_ocr"}:
        evidence = normalized.get("evidence")
        if not normalized.get("attempted_evidence") and isinstance(evidence, list) and evidence:
            normalized["attempted_evidence"] = deepcopy(evidence)
        if not str(normalized.get("reason") or "").strip():
            reason = str(helper.get("reason_summary") or "").strip()
            if reason:
                normalized["reason"] = reason
        if status == "ambiguous" and len(_competing_candidates(normalized)) < 2:
            top_candidates = helper.get("top_candidates")
            if isinstance(top_candidates, list) and len(top_candidates) >= 2:
                normalized["competing_candidates"] = deepcopy(top_candidates)
    return normalized


def _has_page_specific_evidence(result: Mapping[str, Any]) -> bool:
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        if kind in EXACT_EDITORIAL_PAGE_EVIDENCE_KINDS:
            return True
    return False


def _has_page_independent_target_evidence(
    result: Mapping[str, Any],
    locator_item: Mapping[str, Any],
) -> bool:
    if locator_item.get("ref_kind") not in {"target_locator", "parallel_locator"}:
        return False
    if locator_item.get("cited_pages"):
        return False
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        return False
    kinds = {
        str(item.get("kind") or "").strip().casefold()
        for item in evidence
        if isinstance(item, Mapping)
    }
    return bool(
        kinds & WORK_LOCATOR_TEXT_EVIDENCE_KINDS
        and WORK_LOCATOR_CORROBORATION_EVIDENCE_KINDS <= kinds
    )


def _physical_file_number(value: Any) -> int | None:
    name = Path(str(value or "")).name
    match = re.search(r"-(\d+)\.txt$", name, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _target_is_inside_index_section(
    target_file: str,
    locator_item: Mapping[str, Any],
) -> bool:
    target = Path(target_file).expanduser().resolve()
    target_number = _physical_file_number(target)
    intervals = [
        {
            "index_ocr_file_start": locator_item.get("section_file_start"),
            "index_ocr_file_end": locator_item.get("section_file_end"),
        },
        *[
            dict(interval)
            for interval in locator_item.get("excluded_index_intervals") or []
            if isinstance(interval, Mapping)
        ],
    ]
    for interval in intervals:
        start_text = str(
            interval.get("index_ocr_file_start") or ""
        ).strip()
        end_text = str(interval.get("index_ocr_file_end") or "").strip()
        boundaries = [
            Path(value).expanduser().resolve()
            for value in (start_text, end_text)
            if value
        ]
        if target in boundaries:
            return True
        if (
            len(boundaries) != 2
            or any(path.parent != target.parent for path in boundaries)
            or target_number is None
        ):
            continue
        start_number = _physical_file_number(boundaries[0])
        end_number = _physical_file_number(boundaries[1])
        if start_number is None or end_number is None:
            continue
        lower, upper = sorted((start_number, end_number))
        if lower <= target_number <= upper:
            return True
    return False


def validate_locator_results(
    locator_items: Sequence[Mapping[str, Any]],
    locator_results: Any,
    *,
    post_repair: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Validate exact ownership and classify results that need the repair pass."""

    expected: dict[tuple[str, int], dict[str, Any]] = {}
    for index, raw_item in enumerate(locator_items):
        item = _as_object(raw_item, label=f"locator_items[{index}]")
        pair = _stable_pair(item, label=f"locator_items[{index}]")
        if pair in expected:
            raise CompactPipelineError(
                f"duplicate expected locator entry_key={pair[0]!r}, ref_order={pair[1]}"
            )
        expected[pair] = item

    submitted: dict[tuple[str, int], dict[str, Any]] = {}
    invalid_records: list[dict[str, Any]] = []
    for index, result in enumerate(_flatten_results(locator_results)):
        try:
            pair = _stable_pair(result, label=f"results[{index}]")
        except CompactPipelineError as exc:
            invalid_records.append(
                {"reason": str(exc), "submitted_result": deepcopy(result)}
            )
            continue
        if pair not in expected:
            invalid_records.append(
                {
                    "entry_key": pair[0],
                    "ref_order": pair[1],
                    "reason": "unknown locator result",
                    "submitted_result": deepcopy(result),
                }
            )
            continue
        if pair in submitted:
            invalid_records.append(
                {
                    "entry_key": pair[0],
                    "ref_order": pair[1],
                    "reason": "duplicate locator result",
                    "submitted_result": deepcopy(result),
                }
            )
            continue
        submitted[pair] = result

    accepted: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for pair, item in expected.items():
        result = submitted.get(pair)
        if result is None:
            pending.append(
                {
                    "entry_key": pair[0],
                    "ref_order": pair[1],
                    "locator_key": item.get("locator_key"),
                    "reason": "missing locator result",
                    "status": "missing",
                }
            )
            continue
        normalized_result = normalize_locator_result(result)
        normalized_result["locator_key"] = locator_key(pair[0], pair[1])
        status = str(normalized_result.get("status") or "").strip()
        reason = None
        if _contains_intermediate_facsimile_path(normalized_result):
            reason = (
                "locator result must not copy intermediate facsimile paths; "
                "record the visual observation against the OCR target instead"
            )
        elif status not in LOCATOR_STATUSES:
            reason = f"invalid status {status!r}"
        elif status == "resolved":
            target_text = str(normalized_result.get("target_file") or "").strip()
            if not target_text:
                reason = "resolved result is missing target_file"
            elif _confidence(normalized_result) is None:
                reason = "resolved result requires confidence between 0 and 1"
            elif not (
                _has_page_specific_evidence(normalized_result)
                or _has_page_independent_target_evidence(
                    normalized_result,
                    item,
                )
            ):
                if (
                    item.get("ref_kind")
                    in {"target_locator", "parallel_locator"}
                    and not item.get("cited_pages")
                ):
                    reason = (
                        "resolved result requires page-specific evidence or a "
                        "page-independent target-locator evidence bundle"
                    )
                else:
                    reason = (
                        "resolved result requires non-empty page-specific evidence"
                    )
            elif _target_is_inside_index_section(target_text, item):
                reason = "resolved target_file points inside the physical index section"
            elif source_root is not None:
                root = source_root.expanduser().resolve()
                target = Path(target_text).expanduser().resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    reason = "resolved target_file points outside source_root"
                else:
                    if not target.is_file():
                        reason = "resolved target_file does not exist"
        elif status == "ambiguous":
            if str(normalized_result.get("target_file") or "").strip():
                reason = "ambiguous locator must keep target_file null"
            elif not _has_non_empty_reason(normalized_result):
                reason = "ambiguous locator requires a non-empty reason"
            elif len(_competing_candidates(normalized_result)) < 2:
                reason = "ambiguous locator requires at least two competing candidates"
            elif not _has_attempted_evidence(normalized_result):
                reason = "ambiguous locator requires attempted evidence"
        elif str(normalized_result.get("target_file") or "").strip():
            reason = "unrecoverable_ocr must keep target_file null"
        elif not _has_non_empty_reason(normalized_result):
            reason = "unrecoverable_ocr requires a non-empty reason"
        elif not _has_attempted_evidence(normalized_result):
            reason = "unrecoverable_ocr requires attempted evidence"
        if reason is None:
            accepted.append(normalized_result)
        else:
            pending.append(
                {
                    "entry_key": pair[0],
                    "ref_order": pair[1],
                    "locator_key": item.get("locator_key"),
                    "status": status or "invalid",
                    "reason": reason,
                    "submitted_result": normalized_result,
                }
            )

    pending.extend(invalid_records)
    return {
        "schema_version": 1,
        "status": "ok" if not pending else "pending_repair",
        "post_repair": post_repair,
        "expected_count": len(expected),
        "submitted_count": len(submitted),
        "accepted_count": len(accepted),
        "pending_count": len(pending),
        "accepted_results": accepted,
        "pending": pending,
    }


def build_repair_request(
    locator_items: Sequence[Mapping[str, Any]],
    validation_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only unresolved locator context, never the complete semantic payload."""

    pending = validation_report.get("pending")
    if not isinstance(pending, list):
        raise CompactPipelineError("validation_report.pending must be a JSON array")
    items_by_pair = {
        _stable_pair(item, label=f"locator_items[{index}]"): _as_object(
            item, label=f"locator_items[{index}]"
        )
        for index, item in enumerate(locator_items)
    }
    repairs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for record in pending:
        if not isinstance(record, Mapping):
            continue
        try:
            pair = _stable_pair(record, label="pending repair")
        except CompactPipelineError:
            continue
        if pair in seen or pair not in items_by_pair:
            continue
        seen.add(pair)
        repairs.append(
            {
                "locator": items_by_pair[pair],
                "failure": {
                    "status": record.get("status"),
                    "reason": record.get("reason"),
                    "submitted_result": deepcopy(record.get("submitted_result")),
                },
            }
        )
    return {
        "schema_version": 1,
        "kind": "alphabetical_locator_repair",
        "generated_at": _now_iso(),
        "item_count": len(repairs),
        "items": repairs,
    }


def shard_repair_request(
    repair_request: Mapping[str, Any],
    *,
    shard_size: int = 40,
) -> list[dict[str, Any]]:
    if isinstance(shard_size, bool) or not isinstance(shard_size, int) or shard_size < 1:
        raise CompactPipelineError("repair shard_size must be a positive integer")
    items = repair_request.get("items")
    if not isinstance(items, list):
        raise CompactPipelineError("repair_request.items must be an array")
    shard_count = (len(items) + shard_size - 1) // shard_size
    return [
        {
            "schema_version": 1,
            "kind": "alphabetical_locator_repair_shard",
            "repair_shard_id": f"repair-{index // shard_size + 1:04d}",
            "repair_shard_order": index // shard_size + 1,
            "repair_shard_count": shard_count,
            "item_count": len(items[index : index + shard_size]),
            "items": deepcopy(items[index : index + shard_size]),
        }
        for index in range(0, len(items), shard_size)
    ]


def merge_repair_results(base_results: Any, repair_results: Any) -> list[dict[str, Any]]:
    """Replace repaired pairs without duplicating ownership."""

    merged: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for label, source in (("base_results", base_results), ("repair_results", repair_results)):
        for index, result in enumerate(_flatten_results(source)):
            pair = _stable_pair(result, label=f"{label}[{index}]")
            if pair not in merged:
                order.append(pair)
            merged[pair] = deepcopy(result)
    return [merged[pair] for pair in order]


def assemble_compact_payload(
    semantic_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    locator_results: Any,
    *,
    post_repair: bool = False,
) -> dict[str, Any]:
    """Apply validated locator results and derive entry targets deterministically."""

    semantic = coerce_semantic_payload(semantic_payload)
    items = build_locator_items(semantic)
    report = validate_locator_results(items, locator_results, post_repair=post_repair)
    if report["status"] != "ok":
        reasons = [str(item.get("reason") or "pending") for item in report["pending"][:5]]
        raise CompactPipelineError(
            f"cannot assemble with {report['pending_count']} pending locator results: {reasons}"
        )

    result_by_pair = {
        _stable_pair(result, label="accepted result"): result
        for result in report["accepted_results"]
    }
    payload = deepcopy(semantic)
    payload["generated_at"] = payload.get("generated_at") or _now_iso()
    resolved_by_entry: dict[str, list[tuple[float, int, str]]] = {}
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unrecoverable: list[dict[str, Any]] = []
    for index, ref in enumerate(payload["refs"]):
        pair = _stable_pair(ref, label=f"refs[{index}]")
        result = result_by_pair[pair]
        status = str(result["status"])
        raw_json = ref.get("raw_json")
        if isinstance(raw_json, Mapping):
            preserved_raw_json = deepcopy(dict(raw_json))
        elif raw_json is None:
            preserved_raw_json = {}
        else:
            preserved_raw_json = {"semantic_raw_json": deepcopy(raw_json)}
        ref["raw_json"] = {
            **preserved_raw_json,
            "compact_locator": {
                "status": status,
                "reason": result.get("reason"),
                "evidence": deepcopy(result.get("evidence") or []),
                "competing_candidates": _competing_candidates(result),
                "attempted_evidence": deepcopy(
                    result.get("attempted_evidence") or []
                ),
                "attempted_files": deepcopy(result.get("attempted_files") or []),
                "attempted_searches": deepcopy(
                    result.get("attempted_searches") or []
                ),
            },
        }
        if status == "resolved":
            target_file = str(result["target_file"]).strip()
            probability = _confidence(result)
            assert probability is not None
            ref["target_file"] = target_file
            ref["target_file_probability"] = probability
            resolved_by_entry.setdefault(pair[0], []).append(
                (probability, pair[1], target_file)
            )
        else:
            ref["target_file"] = None
            ref["target_file_probability"] = None
            unresolved_record = {
                "entry_key": pair[0],
                "ref_order": pair[1],
                "status": status,
                "reason": result.get("reason"),
                "competing_candidates": _competing_candidates(result),
                "attempted_evidence": deepcopy(
                    result.get("attempted_evidence") or []
                ),
                "attempted_files": deepcopy(result.get("attempted_files") or []),
                "attempted_searches": deepcopy(
                    result.get("attempted_searches") or []
                ),
            }
            unresolved.append(unresolved_record)
            if status == "ambiguous":
                ambiguous.append(deepcopy(unresolved_record))
            else:
                unrecoverable.append(deepcopy(unresolved_record))

    for entry in payload["entries"]:
        entry_key = str(entry["entry_key"])
        candidates = resolved_by_entry.get(entry_key, [])
        entry["target_file_best"] = (
            sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[0][2]
            if candidates
            else None
        )

    coverage = payload["coverage"]
    coverage["locator_status"] = "partial" if unresolved else "complete"
    coverage["locator_total_refs"] = len(payload["refs"])
    coverage["locator_resolved_refs"] = len(payload["refs"]) - len(unresolved)
    coverage["locator_unresolved_refs"] = unresolved
    coverage["locator_ambiguous_refs"] = ambiguous
    coverage["locator_unrecoverable_refs"] = unrecoverable
    if unresolved:
        coverage["locator_status_reason"] = (
            f"{len(unresolved)} material citation(s) could not be localized "
            "after the audited repair pass; semantic entry extraction is unchanged."
        )
    return payload


__all__ = [
    "CompactPipelineError",
    "assemble_compact_payload",
    "build_deterministic_locator_results",
    "build_locator_items",
    "build_repair_request",
    "coerce_semantic_payload",
    "locator_key",
    "merge_repair_results",
    "normalize_editorial_page_map",
    "shard_repair_request",
    "shard_locator_items",
    "standardize_locator_item",
    "standardize_locator_items",
    "validate_locator_results",
]

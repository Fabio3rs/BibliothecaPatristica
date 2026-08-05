#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from alphabetical_index_db import (
    DEFAULT_DB,
    backfill_locator_evidence,
    canonical_ref_location,
    clear_volume,
    connect_db,
    init_schema,
    now_iso,
    refresh_volume_quality,
    upsert_volume,
)
from patristica_pipeline.scripture_book_catalog import (
    canonical_book_key,
    contextual_book_tradition,
    historical_noncanonical_book_key,
    normalize_book_alias,
)

SECTION_KINDS = {
    "analytic_subject",
    "alphabetical_general",
    "onomastic_person",
    "onomastic_place",
    "onomastic_mixed",
    "author_index",
    "scripture_index",
    "pericope_index",
    "concordance_index",
    "foreign_terms",
    "ordo_rerum",
    "crosswalk_index",
    "editorial_closure",
}

NODE_KINDS = {
    "letter_group",
    "heading_group",
    "rubric_group",
    "ordinal_group",
}

ENTRY_KINDS = {
    "lemma",
    "sublemma",
    "cross_reference",
    "editorial_note",
    "heading_group",
    "scripture_citation",
    "scripture_pericope",
    "concordance_item",
}

REF_KINDS = {
    "editorial_page",
    "editorial_column",
    "editorial_page_column",
    "editorial_range",
    "editorial_page_line",
    "target_locator",
    "parallel_locator",
    "unresolved",
}

UNANCHORED_STRUCTURAL_REF_KINDS = {
    "target_locator",
    "parallel_locator",
    "unresolved",
}

CROSS_REFERENCE_MARKER_RE = re.compile(
    r"(?<!\w)(?:vid\.|vide\b|voir\b|v\.|cf\.|id\.)(?!\w)",
    re.IGNORECASE,
)

SCRIPTURE_REF_ROLES = {
    "citation",
    "pericope",
    "concordance_component",
}
EDITORIAL_SCRIPTURE_BOOK_RE = re.compile(
    r"^(?:index|indices|table|tables|ordo|elenchus|cap\.?)\b",
    re.IGNORECASE,
)
SCRIPTURE_SECTION_SPECIAL_HEADINGS = (
    "LOCA EX PSALMIS",
    "VARIANTIA IN PSALTERIIS",
)
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


def material_ref_locator_status(ref: dict[str, Any]) -> str:
    if to_text(ref.get("target_file")) is None:
        return "unresolved"
    raw = ref.get("raw_json", ref)
    locator = raw.get("compact_locator") if isinstance(raw, dict) else None
    if not isinstance(locator, dict):
        return "unverified"
    evidence = locator.get("evidence")
    if locator.get("status") != "resolved" or not isinstance(evidence, list):
        return "unverified"
    if any(
        isinstance(item, dict)
        and str(item.get("kind") or "") in PAGE_SPECIFIC_EVIDENCE_KINDS
        for item in evidence
    ):
        return "resolved"
    return "unverified"


class ValidationErrors(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        count = len(errors)
        suffix = "" if count == 1 else "s"
        joined = "\n".join(f"- {error}" for error in errors)
        super().__init__(f"{count} validation error{suffix}:\n{joined}")


def load_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return json.load(sys.stdin)
    return json.loads(path.read_text(encoding="utf-8"))


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_volume(volume: dict[str, Any]) -> dict[str, Any]:
    volume_id = to_text(volume.get("volume_id")) or ""
    return {
        "volume_id": volume_id,
        "collection": to_text(volume.get("collection")) or volume_id[:2],
        "source_root": to_text(volume.get("source_root")) or "",
        "volume_label": to_text(volume.get("volume_label")) or volume_id,
        "notes": to_text(volume.get("notes")),
    }


def require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    return value


def require_keys(obj: dict[str, Any], label: str, keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ValueError(f"{label} is missing required keys: {missing}")


def require_non_empty_text(value: Any, label: str) -> str:
    text = to_text(value)
    if text is None or not text.strip():
        raise ValueError(f"{label} is required and must be a non-empty string.")
    return text


def require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer.")
    return value


def require_unit_interval(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or value < 0.0 or value > 1.0:
        raise ValueError(f"{label} must be a number between 0.0 and 1.0.")


def require_enum(value: Any, label: str, allowed: set[str]) -> str:
    text = require_non_empty_text(value, label)
    if text not in allowed:
        raise ValueError(f"{label} has invalid value {text!r}. Allowed values: {sorted(allowed)}")
    return text


def normalize_schema_version(value: Any) -> int:
    if value == 1:
        return 1
    if isinstance(value, float) and value.is_integer() and int(value) == 1:
        return 1
    if isinstance(value, str):
        text = value.strip()
        if text in {"1", "1.0"}:
            return 1
    raise ValueError(
        f"Unsupported payload schema_version={value!r}. "
        "This importer currently supports only schema_version=1."
    )


def looks_like_cross_reference_without_anchor(ref_raw: str) -> bool:
    text = ref_raw.strip()
    if not text:
        return False
    return CROSS_REFERENCE_MARKER_RE.search(text) is not None


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def looks_like_editorial_scripture_book(value: Any) -> bool:
    text = collapse_ws(to_text(value))
    if not text:
        return False
    return bool(EDITORIAL_SCRIPTURE_BOOK_RE.match(text))


def looks_like_oversized_scripture_ref_raw(value: Any) -> bool:
    text = collapse_ws(to_text(value))
    if not text:
        return False
    if len(text) > 200:
        return True
    if text.count(";") >= 6:
        return True
    if len(re.findall(r"\b\d{1,4}\b", text)) >= 8:
        return True
    return False


def section_uses_special_scripture_apparatus(section: dict[str, Any]) -> bool:
    heading = collapse_ws(to_text(section.get("heading_raw"))).upper()
    return any(marker in heading for marker in SCRIPTURE_SECTION_SPECIAL_HEADINGS)


def material_reference_mode(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("material_reference_mode")
    return collapse_ws(to_text(value)).casefold() or None


def section_taxonomy(section: dict[str, Any]) -> dict[str, str]:
    """Materialize the four orthogonal section dimensions in DB columns."""

    raw = section.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    section_kind = str(section.get("section_kind") or "")
    inferred_material_mode = (
        "parallel" if section_kind == "concordance_index" else "remissive"
    )
    inferred_scripture_mode = {
        "scripture_index": "citation_index",
        "pericope_index": "pericope_index",
        "concordance_index": "concordance_component",
    }.get(section_kind, "none")
    required_fields = (
        "pipeline_owner",
        "alphabetical_role",
        "material_reference_mode",
        "scripture_mode",
    )
    return {
        "pipeline_owner": str(raw.get("pipeline_owner") or "alphabetical"),
        "alphabetical_role": str(
            raw.get("alphabetical_role") or "owned_section"
        ),
        "material_reference_mode": str(
            raw.get("material_reference_mode") or inferred_material_mode
        ),
        "scripture_mode": str(
            raw.get("scripture_mode") or inferred_scripture_mode
        ),
        "taxonomy_source": (
            "explicit" if all(raw.get(field) is not None for field in required_fields)
            else "legacy_inferred"
        ),
    }


def insert_entry_source_span(
    con: Any,
    *,
    volume_id: str,
    entry_key: str,
    source_span: Any,
) -> None:
    if not isinstance(source_span, dict):
        return
    ocr_file_path = to_text(
        source_span.get("ocr_file_path") or source_span.get("file")
    )
    line_start = source_span.get("line_start")
    line_end = source_span.get("line_end")
    if (
        ocr_file_path is None
        or not isinstance(line_start, int)
        or isinstance(line_start, bool)
        or not isinstance(line_end, int)
        or isinstance(line_end, bool)
        or line_start < 1
        or line_end < line_start
    ):
        return
    block_type = to_text(source_span.get("block_type"))
    text_sha256 = to_text(source_span.get("text_sha256"))
    con.execute(
        """INSERT OR IGNORE INTO alphabetical_source_spans (
            volume_id, ocr_file_path, line_start, line_end, block_type,
            text_sha256, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            volume_id,
            ocr_file_path,
            line_start,
            line_end,
            block_type,
            text_sha256,
            now_iso(),
        ),
    )
    span_row = con.execute(
        """SELECT span_id
        FROM alphabetical_source_spans
        WHERE volume_id = ? AND ocr_file_path = ?
          AND line_start = ? AND line_end = ?
          AND block_type IS ? AND text_sha256 IS ?
        ORDER BY span_id
        LIMIT 1""",
        (
            volume_id,
            ocr_file_path,
            line_start,
            line_end,
            block_type,
            text_sha256,
        ),
    ).fetchone()
    if span_row is None:
        return
    con.execute(
        """INSERT OR IGNORE INTO alphabetical_entry_source_spans (
            entry_key, span_id, span_order, span_role
        ) VALUES (?, ?, 1, 'primary')""",
        (entry_key, int(span_row["span_id"])),
    )


def validate_coverage(
    coverage: dict[str, Any],
    *,
    sections: list[Any],
    entries: list[Any],
    refs: list[Any],
) -> None:
    if not entries:
        entries_status = coverage.get("entries_status")
        allowed_statuses = (
            {"no_index_section"} if not sections else {"unrecoverable_ocr", "no_line_items"}
        )
        if entries_status not in allowed_statuses:
            raise ValueError(
                "entries is empty. "
                f"coverage.entries_status must be one of {sorted(allowed_statuses)} "
                f"when sections is {'empty' if not sections else 'non-empty'}."
            )
        reason = coverage.get("entries_status_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "Empty extraction requires a non-empty coverage.entries_status_reason."
            )
        evidence_files = coverage.get("evidence_files")
        if not isinstance(evidence_files, list) or not evidence_files:
            raise ValueError(
                "Empty extraction requires a non-empty coverage.evidence_files list."
            )
        for idx, item in enumerate(evidence_files, start=1):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"coverage.evidence_files[{idx}] must be a non-empty OCR file path string."
                )
    locator_status = coverage.get("locator_status")
    if locator_status is not None and locator_status not in {"complete", "partial"}:
        raise ValueError(
            "coverage.locator_status must be 'complete' or 'partial' when present."
        )
    unresolved_refs = [
        index
        for index, item in enumerate(refs, start=1)
        if isinstance(item, dict) and to_text(item.get("target_file")) is None
    ]
    if locator_status == "complete" and unresolved_refs:
        raise ValueError(
            "coverage.locator_status='complete' is incompatible with refs without target_file."
        )
    if unresolved_refs and locator_status != "partial":
        raise ValueError(
            "Material refs without target_file require coverage.locator_status='partial'; "
            f"unresolved refs include {unresolved_refs[:10]}."
        )


def normalize_material_path(path_text: str) -> Path:
    path = Path(path_text.strip())
    if path.is_absolute():
        return path
    return Path.cwd() / path


def validate_material_path_in_volume(path_text: str, *, source_root: str, label: str) -> None:
    source_root_path = normalize_material_path(source_root)
    material_path = normalize_material_path(path_text)
    try:
        material_path.relative_to(source_root_path)
    except ValueError as exc:
        raise ValueError(
            f"{label} points outside volume.source_root: {path_text!r} is not inside {source_root!r}."
        ) from exc


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    required_top = {
        "schema_version",
        "generated_at",
        "volume",
        "sections",
        "nodes",
        "entries",
        "refs",
        "scripture_refs",
        "coverage",
        "notes",
    }
    actual_top = set(payload.keys())
    if actual_top != required_top:
        missing = sorted(required_top - actual_top)
        extra = sorted(actual_top - required_top)
        raise ValueError(f"Payload top-level keys mismatch. missing={missing} extra={extra}")

    volume = normalize_volume(require_dict(payload["volume"], "volume"))
    volume_id = volume["volume_id"]
    if not volume_id:
        raise ValueError("volume.volume_id is required.")
    normalize_schema_version(payload["schema_version"])
    require_list(payload["sections"], "sections")
    require_list(payload["nodes"], "nodes")
    require_list(payload["entries"], "entries")
    require_list(payload["refs"], "refs")
    require_list(payload["scripture_refs"], "scripture_refs")
    require_list(payload["notes"], "notes")
    require_dict(payload["coverage"], "coverage")
    return volume, volume_id


def append_error(errors: list[str], exc: ValueError) -> None:
    errors.append(str(exc))


def collect_section_keys(sections: list[Any], volume_id: str, source_root: str, errors: list[str]) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(sections, start=1):
        try:
            section = require_dict(item, f"sections[{idx}]")
            require_keys(section, f"sections[{idx}]", ("section_key", "volume_id", "section_kind", "heading_raw"))
            if section.get("volume_id") != volume_id:
                raise ValueError(f"sections[{idx}] volume_id mismatch: expected {volume_id}")
            key = require_non_empty_text(section.get("section_key"), f"sections[{idx}].section_key")
            section_kind = require_enum(
                section.get("section_kind"),
                f"sections[{idx}].section_kind",
                SECTION_KINDS,
            )
            if section_kind in {"ordo_rerum", "editorial_closure"}:
                raise ValueError(
                    f"sections[{idx}].section_kind={section_kind!r} belongs outside "
                    "the alphabetical-index pipeline and is accepted only as legacy "
                    "database evidence."
                )
            require_non_empty_text(section.get("heading_raw"), f"sections[{idx}].heading_raw")
            if section.get("page_start") is None and to_text(section.get("file_start")) is None:
                raise ValueError(
                    f"sections[{idx}] must include at least one start anchor: page_start or file_start."
                )
            if section.get("page_end") is None and to_text(section.get("file_end")) is None:
                raise ValueError(
                    f"sections[{idx}] must include at least one end anchor: page_end or file_end."
                )
            if section.get("section_order") is not None:
                require_positive_int(section.get("section_order"), f"sections[{idx}].section_order")
            require_unit_interval(section.get("confidence"), f"sections[{idx}].confidence")
            for field in ("file_start", "file_end"):
                value = to_text(section.get(field))
                if value is not None:
                    validate_material_path_in_volume(
                        value,
                        source_root=source_root,
                        label=f"sections[{idx}].{field}",
                    )
            keys.add(key)
        except ValueError as exc:
            append_error(errors, exc)
    return keys


def collect_node_keys(nodes: list[Any], section_keys: set[str], errors: list[str]) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(nodes, start=1):
        try:
            node = require_dict(item, f"nodes[{idx}]")
            require_keys(node, f"nodes[{idx}]", ("node_key", "section_key", "node_order", "node_kind", "label_raw", "node_level"))
            section_key = to_text(node.get("section_key"))
            node_key = require_non_empty_text(node.get("node_key"), f"nodes[{idx}].node_key")
            if section_key not in section_keys:
                raise ValueError(f"nodes[{idx}] references missing section_key: {section_key}")
            require_positive_int(node.get("node_order"), f"nodes[{idx}].node_order")
            require_enum(node.get("node_kind"), f"nodes[{idx}].node_kind", NODE_KINDS)
            require_non_empty_text(node.get("label_raw"), f"nodes[{idx}].label_raw")
            require_positive_int(node.get("node_level"), f"nodes[{idx}].node_level")
            require_unit_interval(node.get("confidence"), f"nodes[{idx}].confidence")
            keys.add(node_key)
        except ValueError as exc:
            append_error(errors, exc)
    for idx, item in enumerate(nodes, start=1):
        try:
            parent_key = to_text(require_dict(item, f"nodes[{idx}]").get("parent_node_key"))
            if parent_key and parent_key not in keys:
                raise ValueError(f"nodes[{idx}] references missing parent_node_key: {parent_key}")
        except ValueError as exc:
            append_error(errors, exc)
    return keys


def collect_entry_keys(
    entries: list[Any],
    section_keys: set[str],
    node_keys: set[str],
    source_root: str,
    errors: list[str],
) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(entries, start=1):
        try:
            entry = require_dict(item, f"entries[{idx}]")
            require_keys(entry, f"entries[{idx}]", ("entry_key", "section_key", "entry_order", "entry_kind", "entry_raw"))
            section_key = to_text(entry.get("section_key"))
            entry_key = require_non_empty_text(entry.get("entry_key"), f"entries[{idx}].entry_key")
            if section_key not in section_keys:
                raise ValueError(f"entries[{idx}] references missing section_key: {section_key}")
            parent_node_key = to_text(entry.get("parent_node_key"))
            if parent_node_key and parent_node_key not in node_keys:
                raise ValueError(f"entries[{idx}] references missing parent_node_key: {parent_node_key}")
            if entry_key in keys:
                raise ValueError(
                    f"Duplicate entry_key detected: entries[{idx}].entry_key={entry_key!r}. "
                    "entry_key must be unique across the whole volume payload and must not restart per section."
                )
            require_positive_int(entry.get("entry_order"), f"entries[{idx}].entry_order")
            require_enum(entry.get("entry_kind"), f"entries[{idx}].entry_kind", ENTRY_KINDS)
            require_non_empty_text(entry.get("entry_raw"), f"entries[{idx}].entry_raw")
            require_unit_interval(entry.get("confidence"), f"entries[{idx}].confidence")
            for field in ("section_start_file", "editorial_anchor_file", "target_file_best"):
                value = to_text(entry.get(field))
                if value is not None:
                    validate_material_path_in_volume(
                        value,
                        source_root=source_root,
                        label=f"entries[{idx}].{field}",
                    )
            keys.add(entry_key)
        except ValueError as exc:
            append_error(errors, exc)
    return keys


def validate_refs(
    refs: list[Any],
    entry_keys: set[str],
    scripture_refs: list[Any],
    source_root: str,
    entries_by_key: dict[str, dict[str, Any]],
    sections_by_key: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    scripture_orders_by_entry: dict[str, set[int]] = {}
    for item in scripture_refs:
        if not isinstance(item, dict):
            continue
        entry_key = to_text(item.get("entry_key"))
        ref_order = item.get("ref_order")
        if entry_key in entry_keys and isinstance(ref_order, int) and ref_order >= 1:
            scripture_orders_by_entry.setdefault(entry_key, set()).add(ref_order)

    seen_ref_orders: dict[str, set[int]] = {}
    for idx, item in enumerate(refs, start=1):
        try:
            ref = require_dict(item, f"refs[{idx}]")
            require_keys(ref, f"refs[{idx}]", ("entry_key", "ref_order", "ref_kind", "ref_raw"))
            entry_key = to_text(ref.get("entry_key"))
            if entry_key not in entry_keys:
                raise ValueError(f"refs[{idx}] references missing entry_key: {entry_key}")
            ref_order = require_positive_int(ref.get("ref_order"), f"refs[{idx}].ref_order")
            entry_ref_orders = seen_ref_orders.setdefault(entry_key or "", set())
            if ref_order in entry_ref_orders:
                raise ValueError(
                    f"Duplicate refs ref_order for entry_key={entry_key!r}: "
                    f"refs[{idx}].ref_order={ref_order}. "
                    "ref_order must be unique per entry_key."
                )
            entry_ref_orders.add(ref_order)
            ref_kind = require_enum(
                ref.get("ref_kind"),
                f"refs[{idx}].ref_kind",
                REF_KINDS,
            )
            ref_raw = require_non_empty_text(ref.get("ref_raw"), f"refs[{idx}].ref_raw")
            scripture_ref_order = ref.get("scripture_ref_order")
            scripture_orders = scripture_orders_by_entry.get(entry_key or "", set())
            if scripture_ref_order is not None:
                scripture_ref_order = require_positive_int(
                    scripture_ref_order,
                    f"refs[{idx}].scripture_ref_order",
                )
                if scripture_ref_order not in scripture_orders:
                    raise ValueError(
                        f"refs[{idx}].scripture_ref_order={scripture_ref_order} does not identify "
                        f"a scripture_refs parent for entry_key={entry_key!r}."
                    )
            elif scripture_orders:
                raise ValueError(
                    f"refs[{idx}] belongs to an entry with scripture_refs and must explicitly set "
                    "scripture_ref_order to the cited passage."
                )
            if (
                to_text(ref.get("page_ref_raw")) is None
                and to_text(ref.get("target_file")) is None
                and to_text(ref.get("range_start_raw")) is None
                and to_text(ref.get("range_end_raw")) is None
            ):
                if looks_like_cross_reference_without_anchor(ref_raw):
                    raise ValueError(
                        f"refs[{idx}] looks like a cross-reference without a material anchor: {ref_raw!r}. "
                        "Do not serialize `vid./vide/voir/id.` style remissions as refs unless they also carry a real locator. "
                        "Model them as an entry-level cross_reference or preserve them in entry_raw/raw_json."
                    )
                if ref_kind not in UNANCHORED_STRUCTURAL_REF_KINDS:
                    raise ValueError(
                        f"refs[{idx}] must include at least one material anchor: "
                        "page_ref_raw, target_file, range_start_raw, or range_end_raw. "
                        "Only target_locator, parallel_locator, and unresolved refs may "
                        "preserve a non-page editorial locator without an anchor."
                    )
            require_unit_interval(ref.get("target_file_probability"), f"refs[{idx}].target_file_probability")
            require_unit_interval(ref.get("confidence"), f"refs[{idx}].confidence")
            for field in ("target_file", "section_start_file", "editorial_anchor_file"):
                value = to_text(ref.get(field))
                if value is not None:
                    validate_material_path_in_volume(
                        value,
                        source_root=source_root,
                        label=f"refs[{idx}].{field}",
                    )
        except ValueError as exc:
            append_error(errors, exc)
    seen_scripture_ref_orders: dict[str, set[int]] = {}
    for idx, item in enumerate(scripture_refs, start=1):
        try:
            ref = require_dict(item, f"scripture_refs[{idx}]")
            require_keys(ref, f"scripture_refs[{idx}]", ("entry_key", "ref_order", "ref_role", "ref_raw"))
            entry_key = to_text(ref.get("entry_key"))
            if entry_key not in entry_keys:
                raise ValueError(f"scripture_refs[{idx}] references missing entry_key: {entry_key}")
            ref_order = require_positive_int(ref.get("ref_order"), f"scripture_refs[{idx}].ref_order")
            entry_ref_orders = seen_scripture_ref_orders.setdefault(entry_key or "", set())
            if ref_order in entry_ref_orders:
                raise ValueError(
                    f"Duplicate scripture_refs ref_order for entry_key={entry_key!r}: "
                    f"scripture_refs[{idx}].ref_order={ref_order}. "
                    "ref_order must be unique per entry_key."
                )
            entry_ref_orders.add(ref_order)
            if len(scripture_orders_by_entry.get(entry_key or "", set())) == 1 and ref_order != 1:
                raise ValueError(
                    f"scripture_refs[{idx}].ref_order must be 1 when it is the entry's only "
                    "scripture reference."
                )
            require_enum(ref.get("ref_role"), f"scripture_refs[{idx}].ref_role", SCRIPTURE_REF_ROLES)
            ref_raw = require_non_empty_text(ref.get("ref_raw"), f"scripture_refs[{idx}].ref_raw")
            if looks_like_oversized_scripture_ref_raw(ref_raw):
                raise ValueError(
                    f"scripture_refs[{idx}].ref_raw looks contaminated by a page/column dump and must be rerun: "
                    f"{ref_raw[:160]!r}"
                )
            book_raw = to_text(ref.get("book_raw"))
            book_norm = to_text(ref.get("book_norm"))
            if looks_like_editorial_scripture_book(book_raw):
                raise ValueError(
                    f"scripture_refs[{idx}].book_raw is an editorial section title, not a biblical book: {book_raw!r}"
                )
            if looks_like_editorial_scripture_book(book_norm):
                raise ValueError(
                    f"scripture_refs[{idx}].book_norm is an editorial section title, not a biblical book: {book_norm!r}"
                )
            for field in ("chapter_start", "verse_start", "chapter_end", "verse_end"):
                value = ref.get(field)
                if value is not None:
                    require_positive_int(value, f"scripture_refs[{idx}].{field}")
            is_range = ref.get("is_range", 0)
            if is_range not in (0, 1):
                raise ValueError(f"scripture_refs[{idx}].is_range must be 0 or 1.")
            entry = entries_by_key.get(entry_key or "")
            section = sections_by_key.get(to_text(entry.get("section_key")) if entry else None)
            if section and section_uses_special_scripture_apparatus(section):
                if book_raw is None and book_norm is None:
                    raise ValueError(
                        f"scripture_refs[{idx}] in special scripture apparatus must carry explicit or inherited book context."
                    )
            require_unit_interval(ref.get("confidence"), f"scripture_refs[{idx}].confidence")
        except ValueError as exc:
            append_error(errors, exc)

    for entry_key, scripture_orders in scripture_orders_by_entry.items():
        entry = entries_by_key.get(entry_key, {})
        entry_kind = to_text(entry.get("entry_kind"))
        if (
            entry_kind in {"scripture_citation", "scripture_pericope"}
            and len(scripture_orders) > 1
        ):
            append_error(
                errors,
                ValueError(
                    f"{entry_kind} entry {entry_key!r} has {len(scripture_orders)} distinct "
                    "scripture_refs; split distinct passages into separate entries."
                ),
            )

    for entry_key, entry in entries_by_key.items():
        entry_kind = to_text(entry.get("entry_kind"))
        if (
            entry_kind in {"scripture_citation", "scripture_pericope"}
            and not scripture_orders_by_entry.get(entry_key)
        ):
            append_error(
                errors,
                ValueError(
                    f"{entry_kind} entry {entry_key!r} has no scripture_refs; "
                    "a biblical entry must identify its passage and book context."
                ),
            )
        section = sections_by_key.get(to_text(entry.get("section_key")), {})
        source_only = (
            material_reference_mode(entry) == "source_only"
            or material_reference_mode(section) == "source_only"
        )
        if (
            entry_kind in {"scripture_citation", "scripture_pericope"}
            and not seen_ref_orders.get(entry_key)
            and not source_only
        ):
            append_error(
                errors,
                ValueError(
                    f"biblical entry {entry_key!r} has no material refs; remissive entries "
                    "must serialize every cited page, while textual apparatus must declare "
                    "raw_json.material_reference_mode='source_only'."
                ),
            )


def build_validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    volume, volume_id = validate_payload(payload)
    sections = require_list(payload["sections"], "sections")
    nodes = require_list(payload["nodes"], "nodes")
    entries = require_list(payload["entries"], "entries")
    refs = require_list(payload["refs"], "refs")
    scripture_refs = require_list(payload["scripture_refs"], "scripture_refs")
    coverage = require_dict(payload["coverage"], "coverage")
    errors: list[str] = []
    section_keys = collect_section_keys(sections, volume_id, volume["source_root"], errors)
    node_keys = collect_node_keys(nodes, section_keys, errors)
    entry_keys = collect_entry_keys(entries, section_keys, node_keys, volume["source_root"], errors)
    entries_by_key = {
        require_non_empty_text(require_dict(item, "entry").get("entry_key"), "entry.entry_key"): require_dict(item, "entry")
        for item in entries
    }
    sections_by_key = {
        require_non_empty_text(require_dict(item, "section").get("section_key"), "section.section_key"): require_dict(item, "section")
        for item in sections
    }
    validate_refs(
        refs,
        entry_keys,
        scripture_refs,
        volume["source_root"],
        entries_by_key,
        sections_by_key,
        errors,
    )
    try:
        validate_coverage(
            coverage,
            sections=sections,
            entries=entries,
            refs=refs,
        )
    except ValueError as exc:
        append_error(errors, exc)
    if errors:
        raise ValidationErrors(errors)
    return {
        "status": "valid",
        "schema_version": 1,
        "volume_id": volume["volume_id"],
        "collection": volume["collection"],
        "source_root": volume["source_root"],
        "counts": {
            "sections": len(sections),
            "nodes": len(nodes),
            "entries": len(entries),
            "refs": len(refs),
            "scripture_refs": len(scripture_refs),
        },
    }


def import_payload(con: Any, payload: dict[str, Any], replace: bool) -> str:
    summary = build_validation_summary(payload)
    volume, volume_id = validate_payload(payload)
    sections = require_list(payload["sections"], "sections")
    nodes = require_list(payload["nodes"], "nodes")
    entries = require_list(payload["entries"], "entries")
    refs = require_list(payload["refs"], "refs")
    scripture_refs = require_list(payload["scripture_refs"], "scripture_refs")
    entry_sections = {
        str(require_dict(item, "entry")["entry_key"]): str(
            require_dict(item, "entry")["section_key"]
        )
        for item in entries
    }
    section_profiles = {
        str(require_dict(item, "section")["section_key"]): (
            require_dict(item, "section").get("raw_json", {}) or {}
        )
        for item in sections
    }
    po_old_english_sections = {
        entry_sections.get(str(require_dict(item, "scripture_ref")["entry_key"]))
        for item in scripture_refs
        if re.search(
            r"\b(?:iii|iv|3|4)\s+kings\b",
            " ".join(
                normalize_book_alias(require_dict(item, "scripture_ref").get(field))
                for field in ("book_raw", "book_norm")
            ),
        )
    }
    po_old_english_sections.discard(None)

    if replace:
        clear_volume(con, volume_id)

    upsert_volume(
        con,
        volume_id=volume["volume_id"],
        collection=volume["collection"],
        source_root=volume["source_root"],
        volume_label=volume["volume_label"],
        notes=volume["notes"],
    )

    for item in sections:
        section = require_dict(item, "section")
        taxonomy = section_taxonomy(section)
        con.execute(
            """INSERT INTO alphabetical_sections (
                section_key, volume_id, work_key, section_order, section_kind, heading_raw,
                heading_norm, heading_letter, page_start, page_end, file_start, file_end,
                index_editorial_page_start, index_editorial_page_end,
                index_ocr_file_start, index_ocr_file_end,
                pipeline_owner, alphabetical_role, material_reference_mode,
                scripture_mode, taxonomy_source, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                section["section_key"],
                section["volume_id"],
                to_text(section.get("work_key")),
                section.get("section_order"),
                section["section_kind"],
                section["heading_raw"],
                to_text(section.get("heading_norm")),
                to_text(section.get("heading_letter")),
                section.get("page_start"),
                section.get("page_end"),
                to_text(section.get("file_start")),
                to_text(section.get("file_end")),
                section.get("page_start"),
                section.get("page_end"),
                to_text(section.get("file_start")),
                to_text(section.get("file_end")),
                taxonomy["pipeline_owner"],
                taxonomy["alphabetical_role"],
                taxonomy["material_reference_mode"],
                taxonomy["scripture_mode"],
                taxonomy["taxonomy_source"],
                section.get("confidence"),
                json_text(section.get("raw_json", section)),
            ),
        )

    for item in nodes:
        node = require_dict(item, "node")
        con.execute(
            """INSERT INTO alphabetical_nodes (
                node_key, section_key, parent_node_key, node_order, node_kind,
                label_raw, label_norm, label_sort, node_level, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node["node_key"],
                node["section_key"],
                to_text(node.get("parent_node_key")),
                node["node_order"],
                node["node_kind"],
                node["label_raw"],
                to_text(node.get("label_norm")),
                to_text(node.get("label_sort")),
                node["node_level"],
                node.get("confidence"),
                json_text(node.get("raw_json", node)),
            ),
        )

    for item in entries:
        entry = require_dict(item, "entry")
        con.execute(
            """INSERT INTO alphabetical_entries (
                entry_key, section_key, parent_node_key, entry_order, entry_kind,
                lemma_raw, lemma_display, lemma_norm, lemma_sort, entry_raw, context_raw,
                heading_letter, inferred_printed_page, section_start_file,
                editorial_anchor_file, target_file_best,
                index_editorial_page_estimate, index_section_start_ocr_file,
                index_entry_source_ocr_file, resolved_target_ocr_file,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry["entry_key"],
                entry["section_key"],
                to_text(entry.get("parent_node_key")),
                entry["entry_order"],
                entry["entry_kind"],
                to_text(entry.get("lemma_raw")),
                to_text(entry.get("lemma_display")),
                to_text(entry.get("lemma_norm")),
                to_text(entry.get("lemma_sort")),
                entry["entry_raw"],
                to_text(entry.get("context_raw")),
                to_text(entry.get("heading_letter")),
                entry.get("inferred_printed_page"),
                to_text(entry.get("section_start_file")),
                to_text(entry.get("editorial_anchor_file")),
                to_text(entry.get("target_file_best")),
                entry.get("inferred_printed_page"),
                to_text(entry.get("section_start_file")),
                to_text(entry.get("editorial_anchor_file")),
                to_text(entry.get("target_file_best")),
                entry.get("confidence"),
                json_text(entry.get("raw_json", entry)),
            ),
        )
        insert_entry_source_span(
            con,
            volume_id=volume_id,
            entry_key=str(entry["entry_key"]),
            source_span=entry.get("source_span"),
        )

    for item in scripture_refs:
        ref = require_dict(item, "scripture_ref")
        section_key = entry_sections.get(str(ref["entry_key"]))
        section_raw = section_profiles.get(str(section_key), {})
        local_profile = (
            section_raw.get("scripture_numbering_profile")
            if isinstance(section_raw, dict)
            else None
        )
        book_tradition = contextual_book_tradition(
            volume["collection"],
            ref.get("book_raw"),
            ref.get("book_norm"),
            local_profile=local_profile,
            section_uses_old_english=section_key in po_old_english_sections,
        )
        book_key = canonical_book_key(
            ref.get("book_raw"),
            tradition=book_tradition,
        ) or canonical_book_key(
            ref.get("book_norm"),
            tradition=book_tradition,
        )
        scripture_raw_json = ref.get("raw_json", ref)
        if isinstance(scripture_raw_json, dict):
            scripture_raw_json = dict(scripture_raw_json)
        historical_key = (
            historical_noncanonical_book_key(ref.get("book_raw"))
            or historical_noncanonical_book_key(ref.get("book_norm"))
        )
        if book_key is None and historical_key and isinstance(scripture_raw_json, dict):
            scripture_raw_json.setdefault(
                "canonical_status",
                "historical_noncanonical",
            )
            scripture_raw_json.setdefault("historical_book_key", historical_key)
        con.execute(
            """INSERT INTO alphabetical_scripture_refs (
                entry_key, ref_order, ref_role, ref_raw, ref_norm, book_raw, book_norm, book_key,
                chapter_start, verse_start, chapter_end, verse_end, is_range, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref["entry_key"],
                ref["ref_order"],
                ref["ref_role"],
                ref["ref_raw"],
                to_text(ref.get("ref_norm")),
                to_text(ref.get("book_raw")),
                to_text(ref.get("book_norm")),
                book_key,
                ref.get("chapter_start"),
                ref.get("verse_start"),
                ref.get("chapter_end"),
                ref.get("verse_end"),
                ref.get("is_range", 0),
                ref.get("confidence"),
                json_text(scripture_raw_json),
            ),
        )

    for item in refs:
        ref = require_dict(item, "ref")
        cited = canonical_ref_location(ref)
        con.execute(
            """INSERT INTO alphabetical_refs (
                entry_key, ref_order, scripture_ref_order, ref_kind, ref_raw,
                page_ref_raw, page_ref_int, page_ref_col, line_ref_raw,
                range_start_raw, range_end_raw, target_file, target_file_probability,
                cited_editorial_page_start_raw,
                cited_editorial_page_start_number,
                cited_editorial_page_end_raw,
                cited_editorial_page_end_number,
                cited_editorial_column_raw, cited_editorial_line_raw,
                cited_editorial_line_start_raw,
                cited_editorial_line_start_number,
                cited_editorial_line_end_raw,
                cited_editorial_line_end_number,
                resolved_target_ocr_file, target_ocr_file_candidate_score,
                cited_location_parse_status,
                locator_status, section_start_file, editorial_anchor_file,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref["entry_key"],
                ref["ref_order"],
                ref.get("scripture_ref_order"),
                ref["ref_kind"],
                ref["ref_raw"],
                to_text(ref.get("page_ref_raw")),
                ref.get("page_ref_int"),
                to_text(ref.get("page_ref_col")),
                to_text(ref.get("line_ref_raw")),
                to_text(ref.get("range_start_raw")),
                to_text(ref.get("range_end_raw")),
                to_text(ref.get("target_file")),
                ref.get("target_file_probability"),
                cited["cited_editorial_page_start_raw"],
                cited["cited_editorial_page_start_number"],
                cited["cited_editorial_page_end_raw"],
                cited["cited_editorial_page_end_number"],
                cited["cited_editorial_column_raw"],
                cited["cited_editorial_line_raw"],
                cited["cited_editorial_line_start_raw"],
                cited["cited_editorial_line_start_number"],
                cited["cited_editorial_line_end_raw"],
                cited["cited_editorial_line_end_number"],
                to_text(ref.get("target_file")),
                ref.get("target_file_probability"),
                cited["cited_location_parse_status"],
                material_ref_locator_status(ref),
                to_text(ref.get("section_start_file")),
                to_text(ref.get("editorial_anchor_file")),
                ref.get("confidence"),
                json_text(ref.get("raw_json", ref)),
            ),
        )

    backfill_locator_evidence(con)

    con.execute(
        """INSERT INTO alphabetical_runs (
            volume_id, status, started_at, finished_at, notes, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            volume_id,
            "imported",
            to_text(payload.get("started_at")) or now_iso(),
            to_text(payload.get("finished_at")) or now_iso(),
            json_text(payload.get("notes", [])),
            json_text(payload),
        ),
    )
    refresh_volume_quality(con, volume_id)
    return summary["volume_id"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Import one alphabetical index payload into SQLite.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="Database path")
    ap.add_argument("--input", type=Path, help="JSON payload file (defaults to stdin)")
    ap.add_argument("--replace", action="store_true", help="Replace existing rows for the same volume_id")
    ap.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate one payload JSON without writing anything to SQLite",
    )
    ap.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a JSON validation/import summary after success",
    )
    args = ap.parse_args()

    payload = load_payload(args.input)
    try:
        summary = build_validation_summary(payload)
        if args.validate_only:
            if args.print_summary:
                print(json.dumps(summary, ensure_ascii=False))
            else:
                print(f"[OK] valid payload for {summary['volume_id']}")
            return
        with connect_db(args.db) as con:
            init_schema(con)
            volume_id = import_payload(con, payload, replace=args.replace)
            con.commit()
    except ValueError as exc:
        raise SystemExit(f"Alphabetical import validation error: {exc}") from exc
    except sqlite3.IntegrityError as exc:
        raise SystemExit(
            "Alphabetical import failed due to SQLite integrity constraints. "
            f"This usually means duplicate ordering keys, broken references, or enum drift.\n{exc}"
        ) from exc
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Alphabetical import failed due to SQLite operational error: {exc}") from exc
    if args.print_summary:
        summary["db"] = str(args.db)
        summary["imported"] = True
        print(json.dumps(summary, ensure_ascii=False))
        return
    print(f"[OK] imported {volume_id} into {args.db}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from alphabetical_index_db import (
    DEFAULT_DB,
    clear_volume,
    connect_db,
    init_schema,
    now_iso,
    upsert_volume,
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
    "scripture",
    "parallel_locator",
    "unresolved",
}

SCRIPTURE_REF_ROLES = {
    "citation",
    "pericope",
    "concordance_component",
}


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


def validate_coverage(coverage: dict[str, Any], *, sections: list[Any], entries: list[Any]) -> None:
    if sections and not entries:
        entries_status = coverage.get("entries_status")
        if entries_status not in {"unrecoverable_ocr", "no_line_items"}:
            raise ValueError(
                "sections is non-empty but entries is empty. "
                "coverage.entries_status must be 'unrecoverable_ocr' or 'no_line_items'."
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
    schema_version = payload["schema_version"]
    if schema_version != 1:
        raise ValueError(
            f"Unsupported payload schema_version={schema_version!r}. "
            "This importer currently supports only schema_version=1."
        )
    require_list(payload["sections"], "sections")
    require_list(payload["nodes"], "nodes")
    require_list(payload["entries"], "entries")
    require_list(payload["refs"], "refs")
    require_list(payload["scripture_refs"], "scripture_refs")
    require_list(payload["notes"], "notes")
    require_dict(payload["coverage"], "coverage")
    return volume, volume_id


def collect_section_keys(sections: list[Any], volume_id: str) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(sections, start=1):
        section = require_dict(item, f"sections[{idx}]")
        require_keys(section, f"sections[{idx}]", ("section_key", "volume_id", "section_kind", "heading_raw"))
        if section.get("volume_id") != volume_id:
            raise ValueError(f"sections[{idx}] volume_id mismatch: expected {volume_id}")
        key = require_non_empty_text(section.get("section_key"), f"sections[{idx}].section_key")
        require_enum(section.get("section_kind"), f"sections[{idx}].section_kind", SECTION_KINDS)
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
        keys.add(key)
    return keys


def collect_node_keys(nodes: list[Any], section_keys: set[str]) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(nodes, start=1):
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
    for idx, item in enumerate(nodes, start=1):
        parent_key = to_text(require_dict(item, f"nodes[{idx}]").get("parent_node_key"))
        if parent_key and parent_key not in keys:
            raise ValueError(f"nodes[{idx}] references missing parent_node_key: {parent_key}")
    return keys


def collect_entry_keys(entries: list[Any], section_keys: set[str], node_keys: set[str]) -> set[str]:
    keys: set[str] = set()
    for idx, item in enumerate(entries, start=1):
        entry = require_dict(item, f"entries[{idx}]")
        require_keys(entry, f"entries[{idx}]", ("entry_key", "section_key", "entry_order", "entry_kind", "entry_raw"))
        section_key = to_text(entry.get("section_key"))
        entry_key = require_non_empty_text(entry.get("entry_key"), f"entries[{idx}].entry_key")
        if section_key not in section_keys:
            raise ValueError(f"entries[{idx}] references missing section_key: {section_key}")
        parent_node_key = to_text(entry.get("parent_node_key"))
        if parent_node_key and parent_node_key not in node_keys:
            raise ValueError(f"entries[{idx}] references missing parent_node_key: {parent_node_key}")
        require_positive_int(entry.get("entry_order"), f"entries[{idx}].entry_order")
        require_enum(entry.get("entry_kind"), f"entries[{idx}].entry_kind", ENTRY_KINDS)
        require_non_empty_text(entry.get("entry_raw"), f"entries[{idx}].entry_raw")
        require_unit_interval(entry.get("confidence"), f"entries[{idx}].confidence")
        keys.add(entry_key)
    return keys


def validate_refs(refs: list[Any], entry_keys: set[str], scripture_refs: list[Any]) -> None:
    seen_ref_orders: dict[str, set[int]] = {}
    for idx, item in enumerate(refs, start=1):
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
        require_enum(ref.get("ref_kind"), f"refs[{idx}].ref_kind", REF_KINDS)
        require_non_empty_text(ref.get("ref_raw"), f"refs[{idx}].ref_raw")
        if (
            to_text(ref.get("page_ref_raw")) is None
            and to_text(ref.get("target_file")) is None
            and to_text(ref.get("range_start_raw")) is None
            and to_text(ref.get("range_end_raw")) is None
        ):
            raise ValueError(
                f"refs[{idx}] must include at least one material anchor: "
                "page_ref_raw, target_file, range_start_raw, or range_end_raw."
            )
        require_unit_interval(ref.get("target_file_probability"), f"refs[{idx}].target_file_probability")
        require_unit_interval(ref.get("confidence"), f"refs[{idx}].confidence")
    seen_scripture_ref_orders: dict[str, set[int]] = {}
    for idx, item in enumerate(scripture_refs, start=1):
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
        require_enum(ref.get("ref_role"), f"scripture_refs[{idx}].ref_role", SCRIPTURE_REF_ROLES)
        require_non_empty_text(ref.get("ref_raw"), f"scripture_refs[{idx}].ref_raw")
        for field in ("chapter_start", "verse_start", "chapter_end", "verse_end"):
            value = ref.get(field)
            if value is not None:
                require_positive_int(value, f"scripture_refs[{idx}].{field}")
        is_range = ref.get("is_range", 0)
        if is_range not in (0, 1):
            raise ValueError(f"scripture_refs[{idx}].is_range must be 0 or 1.")
        require_unit_interval(ref.get("confidence"), f"scripture_refs[{idx}].confidence")


def build_validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    volume, volume_id = validate_payload(payload)
    sections = require_list(payload["sections"], "sections")
    nodes = require_list(payload["nodes"], "nodes")
    entries = require_list(payload["entries"], "entries")
    refs = require_list(payload["refs"], "refs")
    scripture_refs = require_list(payload["scripture_refs"], "scripture_refs")
    coverage = require_dict(payload["coverage"], "coverage")
    section_keys = collect_section_keys(sections, volume_id)
    node_keys = collect_node_keys(nodes, section_keys)
    entry_keys = collect_entry_keys(entries, section_keys, node_keys)
    validate_refs(refs, entry_keys, scripture_refs)
    validate_coverage(coverage, sections=sections, entries=entries)
    return {
        "status": "valid",
        "schema_version": payload["schema_version"],
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
        con.execute(
            """INSERT INTO alphabetical_sections (
                section_key, volume_id, work_key, section_order, section_kind, heading_raw,
                heading_norm, heading_letter, page_start, page_end, file_start, file_end,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                editorial_anchor_file, target_file_best, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                entry.get("confidence"),
                json_text(entry.get("raw_json", entry)),
            ),
        )

    for item in refs:
        ref = require_dict(item, "ref")
        con.execute(
            """INSERT INTO alphabetical_refs (
                entry_key, ref_order, ref_kind, ref_raw, page_ref_raw, page_ref_int,
                page_ref_col, line_ref_raw, range_start_raw, range_end_raw, target_file,
                target_file_probability, section_start_file, editorial_anchor_file,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref["entry_key"],
                ref["ref_order"],
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
                to_text(ref.get("section_start_file")),
                to_text(ref.get("editorial_anchor_file")),
                ref.get("confidence"),
                json_text(ref.get("raw_json", ref)),
            ),
        )

    for item in scripture_refs:
        ref = require_dict(item, "scripture_ref")
        con.execute(
            """INSERT INTO alphabetical_scripture_refs (
                entry_key, ref_order, ref_role, ref_raw, ref_norm, book_raw, book_norm,
                chapter_start, verse_start, chapter_end, verse_end, is_range, confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ref["entry_key"],
                ref["ref_order"],
                ref["ref_role"],
                ref["ref_raw"],
                to_text(ref.get("ref_norm")),
                to_text(ref.get("book_raw")),
                to_text(ref.get("book_norm")),
                ref.get("chapter_start"),
                ref.get("verse_start"),
                ref.get("chapter_end"),
                ref.get("verse_end"),
                ref.get("is_range", 0),
                ref.get("confidence"),
                json_text(ref.get("raw_json", ref)),
            ),
        )

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

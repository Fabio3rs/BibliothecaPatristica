#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import re
from pathlib import Path
from typing import Any

from index_db import (
    DEFAULT_DB,
    canonicalize_work_key,
    clear_volume,
    connect_db,
    derive_section_key,
    derive_work_key,
    init_schema,
    slugify,
    upsert_volume,
)


PAGE_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)

# Canonical payload schema:
# - top-level keys: volume, works, sections, notes
# - sections use `scope_kind` / `index_kind`
# - entries are objects, not free-form strings
#
# The only valid schema is the one documented in
# `.codex/skills/patristic-index-extractor/references/output-format.md`.


def load_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return json.load(sys.stdin)
    return json.loads(path.read_text(encoding='utf-8'))


def parse_page_num(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"(\d+)", value)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def build_page_file_index(source_root: str | None) -> dict[int, str]:
    if not source_root:
        return {}
    root = Path(source_root)
    if not root.exists():
        return {}
    page_map: dict[int, str] = {}
    for path in sorted(root.glob("*.txt")):
        m = PAGE_RE.search(path.name)
        if not m:
            continue
        try:
            page_map[int(m.group(1))] = str(path)
        except ValueError:
            continue
    return page_map


def infer_file_from_page(page_map: dict[int, str], page_value: Any) -> str | None:
    page_num = parse_page_num(page_value)
    if page_num is None:
        return None
    return page_map.get(page_num)


def coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def resolve_page_id_path(source_root: str | None, page_id: Any) -> str | None:
    if not page_id:
        return None
    text = str(page_id)
    if text.startswith("/"):
        return text
    if source_root:
        return str(Path(source_root) / text)
    return text


def infer_scope_kind(section: dict[str, Any]) -> str:
    section_type = str(section.get("section_type") or "").lower().replace("-", "_")
    kind = str(section.get("kind") or "").lower().replace("-", "_")
    scope = str(section.get("scope") or "").lower()
    if section_type.startswith("volume_front") or kind.startswith("volume_front"):
        return "volume_front"
    if section_type.startswith("volume_end") or kind.startswith("volume_end"):
        return "volume_end"
    if section_type.startswith("work_front") or section_type.startswith("work_open") or kind.startswith("work_opening") or kind.startswith("work_front"):
        return "work_front"
    if section_type.startswith("work_end") or kind.startswith("work_end"):
        return "work_end"
    if section_type.startswith("work_index") or kind.startswith("work_index"):
        return "work_front"
    if scope.startswith("work:"):
        return "work_front"
    if scope == "work":
        return "work_front"
    if scope == "volume":
        return "volume_front"
    return "volume_front"


def normalize_section(section: dict[str, Any], volume_id: str, section_idx: int, source_root: str | None = None) -> dict[str, Any]:
    normalized = dict(section)
    if "scope_kind" not in normalized or not normalized.get("scope_kind"):
        normalized["scope_kind"] = infer_scope_kind(normalized)
    if "index_kind" not in normalized or not normalized.get("index_kind"):
        normalized["index_kind"] = normalized.get("heading_raw") or normalized.get("heading") or normalized.get("title") or normalized.get("section_type") or normalized.get("kind") or "INDEX"
    if "heading_raw" not in normalized or not normalized.get("heading_raw"):
        normalized["heading_raw"] = normalized.get("heading") or normalized.get("title") or normalized.get("index_kind") or normalized.get("section_type") or normalized.get("kind") or "INDEX"
    if "section_key" not in normalized or not normalized.get("section_key"):
        normalized["section_key"] = normalized.get("section_id")
    if "page_start" not in normalized or normalized.get("page_start") is None:
        normalized["page_start"] = normalized.get("start_page") if normalized.get("start_page") is not None else normalized.get("heading_page")
    if "page_end" not in normalized or normalized.get("page_end") is None:
        normalized["page_end"] = normalized.get("end_page") if normalized.get("end_page") is not None else normalized.get("heading_page")
    if normalized.get("page_start") is None and normalized.get("page") is not None:
        normalized["page_start"] = normalized.get("page")
    if normalized.get("page_end") is None and normalized.get("page") is not None:
        normalized["page_end"] = normalized.get("page")
    if isinstance(normalized.get("pages"), list) and normalized["pages"]:
        if normalized.get("page_start") is None:
            normalized["page_start"] = normalized["pages"][0]
        if normalized.get("page_end") is None:
            normalized["page_end"] = normalized["pages"][-1]
    if isinstance(normalized.get("source_pages"), list) and normalized["source_pages"]:
        if normalized.get("page_start") is None:
            normalized["page_start"] = normalized["source_pages"][0]
        if normalized.get("page_end") is None:
            normalized["page_end"] = normalized["source_pages"][-1]
    if (not normalized.get("file_start")) and isinstance(normalized.get("page_ids"), list) and normalized["page_ids"]:
        normalized["file_start"] = resolve_page_id_path(source_root, normalized["page_ids"][0])
    if (not normalized.get("file_end")) and isinstance(normalized.get("page_ids"), list) and normalized["page_ids"]:
        normalized["file_end"] = resolve_page_id_path(source_root, normalized["page_ids"][-1])
    if (not normalized.get("file_start")) and isinstance(normalized.get("source_files"), list) and normalized["source_files"]:
        normalized["file_start"] = resolve_page_id_path(source_root, normalized["source_files"][0])
    if (not normalized.get("file_end")) and isinstance(normalized.get("source_files"), list) and normalized["source_files"]:
        normalized["file_end"] = resolve_page_id_path(source_root, normalized["source_files"][-1])
    if not normalized.get("file_start") and normalized.get("file"):
        normalized["file_start"] = resolve_page_id_path(source_root, normalized.get("file"))
    if not normalized.get("file_end") and normalized.get("file"):
        normalized["file_end"] = resolve_page_id_path(source_root, normalized.get("file"))
    if (not normalized.get("work_key")) and isinstance(normalized.get("scope"), str) and normalized["scope"].startswith("work:"):
        normalized["work_key"] = normalized["scope"].split(":", 1)[1].strip()
    if "scope_kind" not in normalized:
        raise ValueError(
            "Missing required `scope_kind` in section payload. Use the canonical schema in "
            "`.codex/skills/patristic-index-extractor/references/output-format.md`."
        )
    if "index_kind" not in normalized or not normalized.get("index_kind"):
        normalized["index_kind"] = normalized.get("heading_raw") or "INDEX"
    if "heading_raw" not in normalized or not normalized.get("heading_raw"):
        normalized["heading_raw"] = normalized.get("index_kind") or "INDEX"
    if "heading_norm" not in normalized or not normalized.get("heading_norm"):
        normalized["heading_norm"] = slugify(str(normalized.get("heading_raw") or normalized.get("index_kind") or "INDEX"))
    if "section_key" not in normalized or not normalized.get("section_key"):
        normalized["section_key"] = f"{volume_id}:{normalized['scope_kind']}:{slugify(str(normalized['index_kind']))}:{section_idx:03d}"
    has_page_start = "page_start" in normalized and normalized.get("page_start") is not None
    has_page_end = "page_end" in normalized and normalized.get("page_end") is not None
    has_file_start = bool(normalized.get("file_start"))
    has_file_end = bool(normalized.get("file_end"))
    if not has_page_start and not has_file_start:
        raise ValueError(
            "Section payload must include at least one start anchor: `page_start` or `file_start`. "
            "Use the canonical schema in "
            "`.codex/skills/patristic-index-extractor/references/output-format.md`."
        )
    if not has_page_end and not has_file_end:
        raise ValueError(
            "Section payload must include at least one end anchor: `page_end` or `file_end`. "
            "Use the canonical schema in "
            "`.codex/skills/patristic-index-extractor/references/output-format.md`."
        )
    if "entries" not in normalized or normalized.get("entries") is None:
        normalized["entries"] = []
    return normalized


def normalize_volume(volume: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(volume)
    normalized["volume_id"] = coerce_text(normalized.get("volume_id")) or ""
    normalized["collection"] = coerce_text(normalized.get("collection")) or normalized["volume_id"][:2]
    normalized["source_root"] = coerce_text(normalized.get("source_root")) or ""
    normalized["volume_label"] = coerce_text(normalized.get("volume_label")) or normalized["volume_id"]
    normalized["notes"] = coerce_text(normalized.get("notes"))
    return normalized


def normalize_work_legacy(work_in: dict[str, Any], source_root: str | None) -> dict[str, Any]:
    work = dict(work_in)
    if not work.get("work_key") and work.get("work_id"):
        work["work_key"] = work.get("work_id")
    if not work.get("title_raw") and work.get("title"):
        work["title_raw"] = work.get("title")
    if not work.get("author_raw") and work.get("author"):
        work["author_raw"] = work.get("author")
    page_range = work.get("page_range")
    if isinstance(page_range, list) and page_range:
        if work.get("start_page") is None and len(page_range) >= 1:
            work["start_page"] = page_range[0]
        if work.get("end_page") is None and len(page_range) >= 2:
            work["end_page"] = page_range[1]
    page_ids = work.get("page_ids")
    if isinstance(page_ids, list) and page_ids:
        if not work.get("start_file"):
            work["start_file"] = resolve_page_id_path(source_root, page_ids[0])
        if not work.get("end_file"):
            work["end_file"] = resolve_page_id_path(source_root, page_ids[-1])
    return work


def build_work_key_maps(payload: dict[str, Any], volume_id: str, source_root: str | None) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    normalized_works: list[dict[str, Any]] = []
    explicit_map: dict[str, str] = {}
    generated_map: dict[str, str] = {}
    for work_idx, work_in in enumerate(payload.get("works", []), start=1):
        work = normalize_work_legacy(dict(work_in), source_root)
        raw_work_key = coerce_text(work.get("work_key") or work.get("work_id"))
        generated_work_key = derive_work_key(volume_id, work)
        canonical_work_key = canonicalize_work_key(volume_id, generated_work_key) or generated_work_key
        if raw_work_key:
            explicit_map[raw_work_key] = canonical_work_key
        generated_map[generated_work_key] = canonical_work_key
        work["work_key"] = canonical_work_key
        if raw_work_key and raw_work_key != canonical_work_key:
            raw_json = work.get("raw_json")
            if not isinstance(raw_json, dict):
                raw_json = {"value": raw_json} if raw_json is not None else {}
            raw_json["original_work_key"] = raw_work_key
            work["raw_json"] = raw_json
        work["work_order"] = work.get("work_order", work_idx)
        normalized_works.append(work)
    return normalized_works, explicit_map, generated_map


def build_section_key_maps(
    payload: dict[str, Any],
    volume_id: str,
    source_root: str | None,
    explicit_work_map: dict[str, str],
    generated_work_map: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    normalized_sections: list[dict[str, Any]] = []
    explicit_map: dict[str, str] = {}
    generated_map: dict[str, str] = {}
    for section_idx, section_in in enumerate(payload.get("sections", []), start=1):
        if not isinstance(section_in, dict):
            raise ValueError(
                "Invalid section payload. Each section must be an object with `scope_kind`, "
                "`index_kind`, start/end anchors (`page_*` or `file_*`), and structured `entries`."
            )
        section = normalize_section(dict(section_in), volume_id, section_idx, source_root)
        raw_section_key = coerce_text(section.get("section_key") or section.get("section_id"))
        generated_section_key = derive_section_key(volume_id, section, section_idx)
        canonical_section_key = canonicalize_work_key(volume_id, generated_section_key) or generated_section_key
        if raw_section_key:
            explicit_map[raw_section_key] = canonical_section_key
        generated_map[generated_section_key] = canonical_section_key
        section["section_key"] = canonical_section_key
        raw_work_key = coerce_text(section.get("work_key"))
        if raw_work_key:
            section["work_key"] = (
                explicit_work_map.get(raw_work_key)
                or generated_work_map.get(raw_work_key)
                or canonicalize_work_key(volume_id, raw_work_key)
            )
        if raw_section_key and raw_section_key != canonical_section_key:
            raw_json = section.get("raw_json")
            if not isinstance(raw_json, dict):
                raw_json = {"value": raw_json} if raw_json is not None else {}
            raw_json["original_section_key"] = raw_section_key
            section["raw_json"] = raw_json
        normalized_sections.append(section)
    return normalized_sections, explicit_map, generated_map


def validate_section_work_refs(sections: list[dict[str, Any]], works: list[dict[str, Any]], volume_id: str) -> None:
    valid_work_keys = {
        str(work.get("work_key"))
        for work in works
        if isinstance(work, dict) and work.get("work_key") is not None
    }
    dangling_refs: list[str] = []
    for section in sections:
        work_key = section.get("work_key")
        if work_key is None:
            continue
        work_key = str(work_key)
        if work_key not in valid_work_keys:
            section_key = section.get("section_key") or "<missing section_key>"
            dangling_refs.append(f"{section_key} -> {work_key}")
    if dangling_refs:
        sample = "; ".join(dangling_refs[:10])
        suffix = "" if len(dangling_refs) <= 10 else f" (+{len(dangling_refs) - 10} more)"
        raise ValueError(
            f"Payload has section.work_key references that do not exist in works for {volume_id}: "
            f"{sample}{suffix}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description='Import one extracted index payload into SQLite.')
    ap.add_argument('--db', type=Path, default=DEFAULT_DB, help='Database path')
    ap.add_argument('--input', type=Path, help='JSON payload file (defaults to stdin)')
    ap.add_argument('--replace', action='store_true', help='Replace existing rows for the same volume_id')
    args = ap.parse_args()

    payload = load_payload(args.input)
    volume = normalize_volume(payload['volume'])
    volume_id = volume['volume_id']
    page_map = build_page_file_index(volume.get('source_root'))
    works, explicit_work_map, generated_work_map = build_work_key_maps(payload, volume_id, volume.get('source_root'))
    sections, explicit_section_map, generated_section_map = build_section_key_maps(
        payload,
        volume_id,
        volume.get('source_root'),
        explicit_work_map,
        generated_work_map,
    )
    validate_section_work_refs(sections, works, volume_id)

    with connect_db(args.db) as con:
        init_schema(con)
        if args.replace:
            clear_volume(con, volume_id)
        upsert_volume(con, volume)

        for work_idx, work in enumerate(works, start=1):
            work_key = work.get('work_key') or derive_work_key(volume_id, work)
            work_order = work.get('work_order', work_idx)
            raw_work_json = work.get('raw_json', work)
            start_file = work.get('start_file') or infer_file_from_page(page_map, work.get('start_page'))
            end_file = work.get('end_file') or infer_file_from_page(page_map, work.get('end_page'))
            source_section_key = coerce_text(work.get('source_section_key'))
            if source_section_key:
                source_section_key = (
                    explicit_section_map.get(source_section_key)
                    or generated_section_map.get(source_section_key)
                    or canonicalize_work_key(volume_id, source_section_key)
                )
            con.execute(
                '''INSERT INTO works (
                    work_key, volume_id, work_order, author_raw, title_raw, title_norm,
                    start_page, end_page, start_file, end_file, source_section_key,
                    confidence, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_key) DO UPDATE SET
                    volume_id = excluded.volume_id,
                    work_order = excluded.work_order,
                    author_raw = excluded.author_raw,
                    title_raw = excluded.title_raw,
                    title_norm = excluded.title_norm,
                    start_page = excluded.start_page,
                    end_page = excluded.end_page,
                    start_file = excluded.start_file,
                    end_file = excluded.end_file,
                    source_section_key = excluded.source_section_key,
                    confidence = excluded.confidence,
                    raw_json = excluded.raw_json''',
                (
                    work_key,
                    volume_id,
                    work_order,
                    work.get('author_raw'),
                    work.get('title_raw') or work_key,
                    work.get('title_norm') or slugify(str(work.get('title_raw') or work_key)),
                    work.get('start_page'),
                    work.get('end_page'),
                    start_file,
                    end_file,
                    source_section_key,
                    work.get('confidence'),
                    json.dumps(raw_work_json, ensure_ascii=False),
                ),
            )

        for section_idx, section in enumerate(sections, start=1):
            section_key = section.get('section_key') or derive_section_key(volume_id, section, section_idx)
            raw_section_json = section.get('raw_json', section)
            work_key = section.get('work_key')
            if work_key is not None:
                work_key = str(work_key)
            file_start = section.get('file_start') or infer_file_from_page(page_map, section.get('page_start'))
            file_end = section.get('file_end') or infer_file_from_page(page_map, section.get('page_end'))
            con.execute(
                '''INSERT INTO index_sections (
                    section_key, volume_id, work_key, scope_kind, index_kind, heading_raw,
                    heading_norm, page_start, page_end, file_start, file_end, confidence, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(section_key) DO UPDATE SET
                    volume_id = excluded.volume_id,
                    work_key = excluded.work_key,
                    scope_kind = excluded.scope_kind,
                    index_kind = excluded.index_kind,
                    heading_raw = excluded.heading_raw,
                    heading_norm = excluded.heading_norm,
                    page_start = excluded.page_start,
                    page_end = excluded.page_end,
                    file_start = excluded.file_start,
                    file_end = excluded.file_end,
                    confidence = excluded.confidence,
                    raw_json = excluded.raw_json''',
                (
                    section_key,
                    volume_id,
                    work_key,
                    section.get('scope_kind'),
                    section.get('index_kind'),
                    section.get('heading_raw') or section.get('index_kind') or 'INDEX',
                    section.get('heading_norm') or slugify(str(section.get('heading_raw') or section.get('index_kind') or 'INDEX')),
                    section.get('page_start'),
                    section.get('page_end'),
                    file_start,
                    file_end,
                    section.get('confidence'),
                    json.dumps(raw_section_json, ensure_ascii=False),
                ),
            )
            for entry_idx, entry_in in enumerate(section.get('entries', []), start=1):
                if not isinstance(entry_in, dict):
                    raise ValueError(
                        "Invalid entry payload. Each `entries[]` item must be an object with "
                        "fields like `entry_raw`, `target_raw`, `page_ref_raw`, and `target_file` as "
                        "documented in `.codex/skills/patristic-index-extractor/references/output-format.md`."
                    )
                entry = dict(entry_in)
                target_file = entry.get('target_file') or infer_file_from_page(
                    page_map,
                    entry.get('page_ref_int') if entry.get('page_ref_int') is not None else entry.get('page_ref_col'),
                )
                if not target_file and entry.get('page_id'):
                    target_file = resolve_page_id_path(volume.get('source_root'), entry.get('page_id'))
                con.execute(
                    '''INSERT INTO index_entries (
                        section_key, entry_order, entry_raw, target_raw, target_file, page_ref_raw,
                        page_ref_int, page_ref_col, note_raw, normalized_target, confidence, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (
                        section_key,
                        int(entry.get('entry_order', entry_idx)),
                        entry.get('entry_raw') or entry.get('raw') or entry.get('text') or '',
                        entry.get('target_raw') or entry.get('target'),
                        target_file,
                        entry.get('page_ref_raw') or entry.get('page') or entry.get('reference'),
                        entry.get('page_ref_int') if entry.get('page_ref_int') is not None else entry.get('page'),
                        entry.get('page_ref_col'),
                        entry.get('note_raw') or entry.get('kind'),
                        entry.get('normalized_target'),
                        entry.get('confidence'),
                        json.dumps(entry.get('raw_json', entry), ensure_ascii=False),
                    ),
                )

        con.execute(
            'INSERT INTO runs (volume_id, status, started_at, finished_at, notes, raw_json) VALUES (?, ?, ?, ?, ?, ?)',
            (
                volume_id,
                'imported',
                payload.get('started_at') or '',
                payload.get('finished_at') or '',
                json.dumps(payload.get('notes', []), ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        con.commit()

    print(f'[OK] imported {volume_id} into {args.db}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import re
from pathlib import Path
from typing import Any

from index_db import DEFAULT_DB, clear_volume, connect_db, derive_section_key, derive_work_key, init_schema, slugify, upsert_volume


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


def normalize_section(section: dict[str, Any], volume_id: str, section_idx: int) -> dict[str, Any]:
    normalized = dict(section)
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
    if "page_start" not in normalized or normalized.get("page_start") is None:
        raise ValueError(
            "Missing required `page_start` in section payload. Use the canonical schema in "
            "`.codex/skills/patristic-index-extractor/references/output-format.md`."
        )
    if "page_end" not in normalized or normalized.get("page_end") is None:
        raise ValueError(
            "Missing required `page_end` in section payload. Use the canonical schema in "
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

    with connect_db(args.db) as con:
        init_schema(con)
        if args.replace:
            clear_volume(con, volume_id)
        upsert_volume(con, volume)

        for work_idx, work_in in enumerate(payload.get('works', []), start=1):
            work = dict(work_in)
            work_key = derive_work_key(volume_id, work)
            work_order = work.get('work_order', work_idx)
            raw_work_json = work.get('raw_json', work)
            start_file = work.get('start_file') or infer_file_from_page(page_map, work.get('start_page'))
            end_file = work.get('end_file') or infer_file_from_page(page_map, work.get('end_page'))
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
                    work.get('source_section_key'),
                    work.get('confidence'),
                    json.dumps(raw_work_json, ensure_ascii=False),
                ),
            )

        for section_idx, section_in in enumerate(payload.get('sections', []), start=1):
            if not isinstance(section_in, dict):
                raise ValueError(
                    "Invalid section payload. Each section must be an object with `scope_kind`, "
                    "`index_kind`, `page_start`, `page_end`, and structured `entries`."
                )
            section = normalize_section(dict(section_in), volume_id, section_idx)
            section_key = derive_section_key(volume_id, section, section_idx)
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
                con.execute(
                    '''INSERT INTO index_entries (
                        section_key, entry_order, entry_raw, target_raw, target_file, page_ref_raw,
                        page_ref_int, page_ref_col, note_raw, normalized_target, confidence, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (
                        section_key,
                        int(entry.get('entry_order', entry_idx)),
                        entry.get('entry_raw', ''),
                        entry.get('target_raw'),
                        target_file,
                        entry.get('page_ref_raw'),
                        entry.get('page_ref_int'),
                        entry.get('page_ref_col'),
                        entry.get('note_raw'),
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

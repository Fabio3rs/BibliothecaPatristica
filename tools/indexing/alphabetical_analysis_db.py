"""Operational SQLite checkpoints for the staged alphabetical-index pipeline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .alphabetical_compact_pipeline import (
    build_locator_items,
    coerce_semantic_payload,
    locator_key,
    standardize_locator_item,
)
from tools.corpus_utils import page_number


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANALYSIS_DB = PROJECT_ROOT / "data" / "alphabetical_analysis.db"
ANALYSIS_SCHEMA_VERSION = 3
STAGES = ("discover", "extract", "locate", "verify", "assemble")
TERMINAL_LOCATOR_STATUSES = {"resolved", "ambiguous", "unrecoverable_ocr"}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analysis_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_volumes (
    volume_id TEXT PRIMARY KEY,
    collection TEXT NOT NULL CHECK (collection IN ('PG', 'PL', 'PO')),
    source_root TEXT NOT NULL,
    semantic_payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_stage_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (
        stage IN ('discover', 'extract', 'locate', 'verify', 'assemble')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('running', 'complete', 'failed', 'skipped')
    ),
    input_fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_stage_runs_volume_stage
    ON analysis_stage_runs(volume_id, stage, run_id);

CREATE TABLE IF NOT EXISTS analysis_stage_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES analysis_stage_runs(run_id) ON DELETE CASCADE,
    volume_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    artifact_role TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    artifact_fingerprint TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (run_id, artifact_role, artifact_path)
);
CREATE INDEX IF NOT EXISTS idx_analysis_stage_artifacts_volume_stage
    ON analysis_stage_artifacts(volume_id, stage, run_id);

CREATE TABLE IF NOT EXISTS analysis_volume_stages (
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (
        stage IN ('discover', 'extract', 'locate', 'verify', 'assemble')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('missing', 'running', 'complete', 'failed', 'stale')
    ),
    input_fingerprint TEXT,
    last_run_id INTEGER REFERENCES analysis_stage_runs(run_id),
    summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (volume_id, stage)
);

CREATE TABLE IF NOT EXISTS analysis_discovered_pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_seq INTEGER,
    physical_index INTEGER,
    ownership_role TEXT NOT NULL CHECK (
        ownership_role IN ('candidate', 'tail_context', 'neighbor_context', 'boundary')
    ),
    source_size INTEGER,
    source_mtime_ns INTEGER,
    evidence_json TEXT NOT NULL,
    UNIQUE (volume_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_analysis_pages_volume_physical
    ON analysis_discovered_pages(volume_id, physical_index);

CREATE TABLE IF NOT EXISTS analysis_discovered_segments (
    segment_id TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    line_start INTEGER NOT NULL CHECK (line_start >= 1),
    line_end INTEGER NOT NULL CHECK (line_end >= line_start),
    ownership_role TEXT NOT NULL CHECK (
        ownership_role IN ('owned', 'boundary', 'context', 'uncertain')
    ),
    section_key TEXT,
    heading_raw TEXT,
    reason TEXT NOT NULL,
    segment_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_segments_volume_file
    ON analysis_discovered_segments(volume_id, file_path, line_start);

CREATE TABLE IF NOT EXISTS analysis_sections (
    section_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    section_order INTEGER,
    section_kind TEXT,
    heading_raw TEXT,
    file_start TEXT,
    file_end TEXT,
    semantic_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_sections_volume
    ON analysis_sections(volume_id, section_order);

CREATE TABLE IF NOT EXISTS analysis_entries (
    entry_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    section_key TEXT NOT NULL REFERENCES analysis_sections(section_key) ON DELETE CASCADE,
    entry_order INTEGER,
    entry_kind TEXT,
    lemma_raw TEXT,
    lemma_norm TEXT,
    entry_raw TEXT NOT NULL,
    context_raw TEXT,
    source_file TEXT,
    semantic_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_entries_volume
    ON analysis_entries(volume_id, section_key, entry_order);
CREATE INDEX IF NOT EXISTS idx_analysis_entries_lemma
    ON analysis_entries(lemma_norm);

CREATE TABLE IF NOT EXISTS analysis_scripture_refs (
    scripture_ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    entry_key TEXT NOT NULL REFERENCES analysis_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    ref_raw TEXT NOT NULL,
    ref_norm TEXT,
    book_raw TEXT,
    book_norm TEXT,
    book_key TEXT,
    chapter_start INTEGER,
    verse_start INTEGER,
    chapter_end INTEGER,
    verse_end INTEGER,
    semantic_json TEXT NOT NULL,
    UNIQUE (entry_key, ref_order)
);
CREATE INDEX IF NOT EXISTS idx_analysis_scripture_lookup
    ON analysis_scripture_refs(volume_id, book_key, chapter_start, verse_start);

CREATE TABLE IF NOT EXISTS analysis_occurrences (
    occurrence_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
    entry_key TEXT NOT NULL REFERENCES analysis_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    scripture_ref_order INTEGER,
    ref_kind TEXT,
    ref_raw TEXT NOT NULL,
    page_ref_raw TEXT,
    page_ref_int INTEGER,
    page_ref_col TEXT,
    range_start_raw TEXT,
    range_end_raw TEXT,
    locator_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        locator_status IN (
            'pending', 'resolved', 'ambiguous', 'unrecoverable_ocr', 'stale'
        )
    ),
    target_file TEXT,
    target_probability REAL,
    ref_json TEXT NOT NULL,
    locator_item_json TEXT,
    decision_json TEXT,
    UNIQUE (entry_key, ref_order)
);
CREATE INDEX IF NOT EXISTS idx_analysis_occurrences_volume_status
    ON analysis_occurrences(volume_id, locator_status);
CREATE INDEX IF NOT EXISTS idx_analysis_occurrences_page
    ON analysis_occurrences(volume_id, page_ref_int);

CREATE TABLE IF NOT EXISTS analysis_locator_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_key TEXT NOT NULL
        REFERENCES analysis_occurrences(occurrence_key) ON DELETE CASCADE,
    candidate_order INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    probability REAL,
    provider TEXT,
    evidence_json TEXT NOT NULL,
    UNIQUE (occurrence_key, candidate_order)
);
CREATE INDEX IF NOT EXISTS idx_analysis_candidates_occurrence
    ON analysis_locator_candidates(occurrence_key, candidate_order);

CREATE VIRTUAL TABLE IF NOT EXISTS analysis_entry_fts USING fts5(
    entry_key UNINDEXED,
    volume_id UNINDEXED,
    lemma_raw,
    lemma_norm,
    entry_raw,
    context_raw,
    scripture_text,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIEW IF NOT EXISTS analysis_occurrences_flat AS
SELECT
    o.occurrence_key,
    o.volume_id,
    s.section_kind,
    s.heading_raw,
    e.entry_key,
    e.entry_kind,
    e.lemma_raw,
    e.lemma_norm,
    e.entry_raw,
    sr.ref_raw AS scripture_ref_raw,
    sr.ref_norm AS scripture_ref_norm,
    sr.book_norm,
    sr.book_key,
    sr.chapter_start,
    sr.verse_start,
    o.ref_order,
    o.ref_kind,
    o.ref_raw,
    o.page_ref_raw,
    o.page_ref_int,
    o.page_ref_col,
    o.range_start_raw,
    o.range_end_raw,
    o.locator_status,
    o.target_file,
    o.target_probability,
    e.source_file AS index_source_file
FROM analysis_occurrences o
JOIN analysis_entries e ON e.entry_key = o.entry_key
JOIN analysis_sections s ON s.section_key = e.section_key
LEFT JOIN analysis_scripture_refs sr
  ON sr.entry_key = o.entry_key
 AND sr.ref_order = o.scripture_ref_order;

CREATE VIEW IF NOT EXISTS analysis_name_seeds AS
SELECT DISTINCT
    volume_id,
    lemma_raw,
    lemma_norm,
    entry_kind,
    section_key
FROM analysis_entries
WHERE lemma_raw IS NOT NULL
  AND TRIM(lemma_raw) != ''
  AND entry_kind IN ('lemma', 'sublemma', 'concordance_item');

CREATE VIEW IF NOT EXISTS analysis_scripture_seeds AS
SELECT DISTINCT
    volume_id,
    ref_raw,
    ref_norm,
    book_raw,
    book_norm,
    book_key,
    chapter_start,
    verse_start,
    chapter_end,
    verse_end
FROM analysis_scripture_refs;

CREATE VIEW IF NOT EXISTS analysis_occurrences_canonical AS
SELECT
    o.occurrence_key,
    o.volume_id,
    o.entry_key,
    o.ref_order,
    o.scripture_ref_order,
    s.section_key,
    s.file_start AS index_section_start_ocr_file,
    s.file_end AS index_section_end_ocr_file,
    e.source_file AS index_entry_source_ocr_file,
    o.ref_kind,
    o.ref_raw,
    COALESCE(o.page_ref_raw, o.range_start_raw)
        AS cited_editorial_page_start_raw,
    o.page_ref_int AS cited_editorial_page_start_number,
    o.range_end_raw AS cited_editorial_page_end_raw,
    o.page_ref_col AS cited_editorial_column_raw,
    o.target_file AS resolved_target_ocr_file,
    o.target_probability AS target_ocr_file_candidate_score,
    o.locator_status,
    o.ref_json,
    o.locator_item_json,
    o.decision_json
FROM analysis_occurrences o
JOIN analysis_entries e ON e.entry_key = o.entry_key
JOIN analysis_sections s ON s.section_key = e.section_key;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade_locator_contracts(
    con: sqlite3.Connection,
    *,
    volume_id: str | None = None,
) -> dict[str, int]:
    """Upgrade stored locator work items to the unambiguous v2 coordinate view."""

    parameters: tuple[Any, ...] = ()
    section_where = ""
    occurrence_where = "WHERE locator_item_json IS NOT NULL"
    if volume_id is not None:
        section_where = "WHERE volume_id=?"
        occurrence_where += " AND volume_id=?"
        parameters = (volume_id,)
    intervals_by_volume: dict[str, list[dict[str, Any]]] = {}
    for row in con.execute(
        f"""SELECT volume_id, section_key, file_start, file_end
        FROM analysis_sections {section_where}
        ORDER BY volume_id, section_order, section_key""",
        parameters,
    ):
        if not row["file_start"] and not row["file_end"]:
            continue
        intervals_by_volume.setdefault(str(row["volume_id"]), []).append(
            {
                "section_key": row["section_key"],
                "index_ocr_file_start": row["file_start"],
                "index_ocr_file_end": row["file_end"],
            }
        )
    counts = {"examined": 0, "updated": 0, "unchanged": 0, "invalid_json": 0}
    rows = con.execute(
        f"""SELECT occurrence_key, volume_id, entry_key, ref_order,
                   locator_item_json
        FROM analysis_occurrences
        {occurrence_where}
        ORDER BY occurrence_key""",
        parameters,
    ).fetchall()
    for row in rows:
        counts["examined"] += 1
        try:
            loaded = json.loads(row["locator_item_json"])
        except (TypeError, json.JSONDecodeError):
            counts["invalid_json"] += 1
            continue
        if not isinstance(loaded, Mapping):
            counts["invalid_json"] += 1
            continue
        item = dict(loaded)
        item["entry_key"] = str(row["entry_key"])
        item["ref_order"] = int(row["ref_order"])
        item["locator_key"] = str(row["occurrence_key"])
        item["excluded_index_intervals"] = intervals_by_volume.get(
            str(row["volume_id"]),
            [],
        )
        standardized = standardize_locator_item(item)
        serialized = _json(standardized)
        if serialized == _json(dict(loaded)):
            counts["unchanged"] += 1
            continue
        con.execute(
            """UPDATE analysis_occurrences
            SET locator_item_json=? WHERE occurrence_key=?""",
            (serialized, row["occurrence_key"]),
        )
        counts["updated"] += 1
    return counts


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_summary_artifacts(
    con: sqlite3.Connection,
    *,
    run_id: int,
    volume_id: str,
    stage: str,
    summary: Mapping[str, Any],
) -> None:
    timestamp = _now_iso()
    for field, value in summary.items():
        if not field.endswith("_file") or not isinstance(value, str) or not value:
            continue
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            continue
        con.execute(
            """INSERT OR REPLACE INTO analysis_stage_artifacts(
                run_id, volume_id, stage, artifact_role, artifact_path,
                content_sha256, artifact_fingerprint, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                volume_id,
                stage,
                field,
                str(path),
                _artifact_sha256(path),
                summary.get("input_fingerprint"),
                _json({"summary_field": field}),
                timestamp,
            ),
        )


def connect_analysis_db(
    path: Path | str = DEFAULT_ANALYSIS_DB,
    *,
    read_only: bool = False,
) -> sqlite3.Connection:
    db_path = Path(path).expanduser().resolve()
    if read_only:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    if not read_only:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    return con


def init_analysis_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    con.execute(
        """INSERT INTO analysis_meta(meta_key, meta_value, updated_at)
        VALUES ('schema_version', ?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET
          meta_value=excluded.meta_value,
          updated_at=excluded.updated_at""",
        (str(ANALYSIS_SCHEMA_VERSION), _now_iso()),
    )
    con.commit()


def ensure_volume(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
) -> None:
    timestamp = _now_iso()
    con.execute(
        """INSERT INTO analysis_volumes(
            volume_id, collection, source_root, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
          collection=excluded.collection,
          source_root=excluded.source_root,
          updated_at=excluded.updated_at""",
        (volume_id, collection, str(source_root.resolve()), timestamp, timestamp),
    )
    for stage in STAGES:
        con.execute(
            """INSERT OR IGNORE INTO analysis_volume_stages(
                volume_id, stage, status, updated_at
            ) VALUES (?, ?, 'missing', ?)""",
            (volume_id, stage, timestamp),
        )


def stage_state(
    con: sqlite3.Connection,
    volume_id: str,
    stage: str,
) -> dict[str, Any] | None:
    row = con.execute(
        """SELECT status, input_fingerprint, last_run_id, summary_json, updated_at
        FROM analysis_volume_stages
        WHERE volume_id=? AND stage=?""",
        (volume_id, stage),
    ).fetchone()
    if row is None:
        return None
    return {
        **dict(row),
        "summary": json.loads(row["summary_json"] or "{}"),
    }


def begin_stage(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    stage: str,
    input_fingerprint: str,
) -> int:
    started = _now_iso()
    cursor = con.execute(
        """INSERT INTO analysis_stage_runs(
            volume_id, stage, status, input_fingerprint, started_at
        ) VALUES (?, ?, 'running', ?, ?)""",
        (volume_id, stage, input_fingerprint, started),
    )
    run_id = int(cursor.lastrowid)
    con.execute(
        """UPDATE analysis_volume_stages
        SET status='running', input_fingerprint=?, last_run_id=?, updated_at=?
        WHERE volume_id=? AND stage=?""",
        (input_fingerprint, run_id, started, volume_id, stage),
    )
    con.commit()
    return run_id


def finish_stage(
    con: sqlite3.Connection,
    *,
    run_id: int,
    volume_id: str,
    stage: str,
    status: str,
    summary: Mapping[str, Any] | None = None,
    error_text: str | None = None,
) -> None:
    finished = _now_iso()
    summary_json = _json(dict(summary or {}))
    con.execute(
        """UPDATE analysis_stage_runs
        SET status=?, finished_at=?, summary_json=?, error_text=?
        WHERE run_id=?""",
        (status, finished, summary_json, error_text, run_id),
    )
    state_status = "complete" if status in {"complete", "skipped"} else "failed"
    con.execute(
        """UPDATE analysis_volume_stages
        SET status=?, summary_json=?, updated_at=?
        WHERE volume_id=? AND stage=?""",
        (state_status, summary_json, finished, volume_id, stage),
    )
    if status in {"complete", "skipped"}:
        _record_summary_artifacts(
            con,
            run_id=run_id,
            volume_id=volume_id,
            stage=stage,
            summary=dict(summary or {}),
        )
    con.commit()


def mark_downstream_stale(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    after_stage: str,
) -> None:
    position = STAGES.index(after_stage)
    downstream = STAGES[position + 1 :]
    if not downstream:
        return
    placeholders = ",".join("?" for _ in downstream)
    con.execute(
        f"""UPDATE analysis_volume_stages
        SET status='stale', updated_at=?
        WHERE volume_id=?
          AND stage IN ({placeholders})
          AND status != 'missing'""",
        (_now_iso(), volume_id, *downstream),
    )


def replace_discovery(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages: Mapping[str, Any],
    discovery_manifest: Mapping[str, Any] | None = None,
) -> int:
    ensure_volume(
        con,
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
    )
    files = sorted(
        source_root.glob("*.txt"),
        key=lambda path: (
            page_number(path) if page_number(path) is not None else 2**31 - 1,
            str(path),
        ),
    )
    physical_index = {str(path.resolve()): index for index, path in enumerate(files)}
    evidence_by_file: dict[str, list[dict[str, Any]]] = {}
    roles: dict[str, str] = {}
    section_markers: list[tuple[int, str, dict[str, Any]]] = []
    for record in filtered_pages.get("candidate_sections") or []:
        if not isinstance(record, Mapping) or not record.get("file"):
            continue
        path = str(Path(str(record["file"])).resolve())
        evidence_by_file.setdefault(path, []).append(dict(record))
        if record.get("role") == "alphabetical_stop_boundary":
            roles[path] = "boundary"
        else:
            roles.setdefault(path, "candidate")
        marker_index = physical_index.get(path)
        if marker_index is not None:
            section_markers.append((marker_index, path, dict(record)))
    section_markers.sort(key=lambda item: (item[0], item[1]))
    distinct_marker_indexes = sorted({item[0] for item in section_markers})
    for marker_index, marker_path, record in section_markers:
        if record.get("role") == "alphabetical_stop_boundary":
            continue
        later_indexes = [
            value for value in distinct_marker_indexes if value > marker_index
        ]
        end_index = (later_indexes[0] - 1) if later_indexes else (len(files) - 1)
        for file_index in range(marker_index, end_index + 1):
            interval_path = str(files[file_index].resolve())
            roles.setdefault(interval_path, "candidate")
            evidence_by_file.setdefault(interval_path, []).append(
                {
                    "reason": "inferred_section_interval",
                    "section_start_file": marker_path,
                    "section_heading": record.get("heading")
                    or record.get("text"),
                    "start_physical_index": marker_index,
                    "end_physical_index": end_index,
                }
            )
    for value in filtered_pages.get("candidate_files") or []:
        path = str(Path(str(value)).resolve())
        roles.setdefault(path, "neighbor_context")
    for value in filtered_pages.get("tail_files") or []:
        path = str(Path(str(value)).resolve())
        roles.setdefault(path, "tail_context")
    con.execute(
        "DELETE FROM analysis_discovered_pages WHERE volume_id=?",
        (volume_id,),
    )
    con.execute(
        "DELETE FROM analysis_discovered_segments WHERE volume_id=?",
        (volume_id,),
    )
    for path_text, role in sorted(
        roles.items(),
        key=lambda item: physical_index.get(item[0], 2**31 - 1),
    ):
        path = Path(path_text)
        stat = path.stat() if path.is_file() else None
        file_seq = page_number(path)
        con.execute(
            """INSERT INTO analysis_discovered_pages(
                volume_id, file_path, file_seq, physical_index, ownership_role,
                source_size, source_mtime_ns, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                volume_id,
                path_text,
                file_seq,
                physical_index.get(path_text),
                role,
                stat.st_size if stat else None,
                stat.st_mtime_ns if stat else None,
                _json(evidence_by_file.get(path_text, [])),
            ),
        )
    for raw_segment in (discovery_manifest or {}).get("segments") or []:
        if not isinstance(raw_segment, Mapping):
            continue
        con.execute(
            """INSERT INTO analysis_discovered_segments(
                segment_id, volume_id, file_path, line_start, line_end,
                ownership_role, section_key, heading_raw, reason, segment_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(raw_segment["segment_id"]),
                volume_id,
                str(raw_segment["file"]),
                int(raw_segment["line_start"]),
                int(raw_segment["line_end"]),
                str(raw_segment["role"]),
                raw_segment.get("section_key"),
                raw_segment.get("heading_raw"),
                str(raw_segment["reason"]),
                _json(raw_segment),
            ),
        )
    mark_downstream_stale(con, volume_id=volume_id, after_stage="discover")
    con.commit()
    return len(roles)


def replace_semantic_payload(
    con: sqlite3.Connection,
    *,
    payload: Mapping[str, Any],
) -> dict[str, int]:
    semantic = coerce_semantic_payload(payload)
    volume = semantic["volume"]
    volume_id = str(volume["volume_id"])
    collection = str(volume["collection"])
    source_root = Path(str(volume["source_root"]))
    ensure_volume(
        con,
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
    )
    con.execute(
        """UPDATE analysis_volumes
        SET semantic_payload_json=?, updated_at=?
        WHERE volume_id=?""",
        (_json(semantic), _now_iso(), volume_id),
    )
    con.execute("DELETE FROM analysis_sections WHERE volume_id=?", (volume_id,))
    con.execute("DELETE FROM analysis_entry_fts WHERE volume_id=?", (volume_id,))
    entries_by_key = {
        str(entry["entry_key"]): entry for entry in semantic["entries"]
    }
    scripture_by_pair = {
        (str(ref["entry_key"]), int(ref["ref_order"])): ref
        for ref in semantic["scripture_refs"]
    }
    for section in semantic["sections"]:
        con.execute(
            """INSERT INTO analysis_sections(
                section_key, volume_id, section_order, section_kind,
                heading_raw, file_start, file_end, semantic_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                section["section_key"],
                volume_id,
                section.get("section_order"),
                section.get("section_kind"),
                section.get("heading_raw"),
                section.get("file_start"),
                section.get("file_end"),
                _json(section),
            ),
        )
    for entry in semantic["entries"]:
        entry_raw_json = entry.get("raw_json")
        source_span = entry.get("source_span")
        source_span_file = (
            source_span.get("file")
            if isinstance(source_span, Mapping)
            else None
        )
        owner_file = (
            entry_raw_json.get("analysis_owner_file")
            if isinstance(entry_raw_json, Mapping)
            else None
        )
        source_file = (
            source_span_file
            or owner_file
            or entry.get("source_file")
            or entry.get("section_start_file")
            or entry.get("editorial_anchor_file")
        )
        con.execute(
            """INSERT INTO analysis_entries(
                entry_key, volume_id, section_key, entry_order, entry_kind,
                lemma_raw, lemma_norm, entry_raw, context_raw, source_file,
                semantic_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry["entry_key"],
                volume_id,
                entry["section_key"],
                entry.get("entry_order"),
                entry.get("entry_kind"),
                entry.get("lemma_raw"),
                entry.get("lemma_norm"),
                entry.get("entry_raw") or "",
                entry.get("context_raw"),
                source_file,
                _json(entry),
            ),
        )
    for ref in semantic["scripture_refs"]:
        con.execute(
            """INSERT INTO analysis_scripture_refs(
                volume_id, entry_key, ref_order, ref_raw, ref_norm,
                book_raw, book_norm, book_key, chapter_start, verse_start,
                chapter_end, verse_end, semantic_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                volume_id,
                ref["entry_key"],
                ref["ref_order"],
                ref.get("ref_raw") or "",
                ref.get("ref_norm"),
                ref.get("book_raw"),
                ref.get("book_norm"),
                ref.get("book_key"),
                ref.get("chapter_start"),
                ref.get("verse_start"),
                ref.get("chapter_end"),
                ref.get("verse_end"),
                _json(ref),
            ),
        )
    for ref in semantic["refs"]:
        entry_key = str(ref["entry_key"])
        ref_order = int(ref["ref_order"])
        key = locator_key(entry_key, ref_order)
        con.execute(
            """INSERT INTO analysis_occurrences(
                occurrence_key, volume_id, entry_key, ref_order,
                scripture_ref_order, ref_kind, ref_raw, page_ref_raw,
                page_ref_int, page_ref_col, range_start_raw, range_end_raw,
                locator_status, ref_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                key,
                volume_id,
                entry_key,
                ref_order,
                ref.get("scripture_ref_order"),
                ref.get("ref_kind"),
                ref.get("ref_raw") or "",
                ref.get("page_ref_raw"),
                ref.get("page_ref_int"),
                ref.get("page_ref_col"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
                _json(ref),
            ),
        )
    for entry_key, entry in entries_by_key.items():
        scripture_text = " ".join(
            str(ref.get("ref_norm") or ref.get("ref_raw") or "")
            for pair, ref in scripture_by_pair.items()
            if pair[0] == entry_key
        )
        con.execute(
            """INSERT INTO analysis_entry_fts(
                entry_key, volume_id, lemma_raw, lemma_norm, entry_raw,
                context_raw, scripture_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_key,
                volume_id,
                entry.get("lemma_raw"),
                entry.get("lemma_norm"),
                entry.get("entry_raw"),
                entry.get("context_raw"),
                scripture_text,
            ),
        )
    mark_downstream_stale(con, volume_id=volume_id, after_stage="extract")
    con.commit()
    return {
        "sections": len(semantic["sections"]),
        "entries": len(semantic["entries"]),
        "occurrences": len(semantic["refs"]),
        "scripture_refs": len(semantic["scripture_refs"]),
    }


def load_semantic_payload(
    con: sqlite3.Connection,
    volume_id: str,
) -> dict[str, Any]:
    row = con.execute(
        """SELECT semantic_payload_json
        FROM analysis_volumes WHERE volume_id=?""",
        (volume_id,),
    ).fetchone()
    if row is None or not row["semantic_payload_json"]:
        raise ValueError(f"no extracted semantic checkpoint for {volume_id}")
    return json.loads(row["semantic_payload_json"])


def replace_locator_items(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    locator_items: Sequence[Mapping[str, Any]],
    deterministic_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, int]:
    result_by_key = {
        locator_key(str(result["entry_key"]), int(result["ref_order"])): result
        for result in deterministic_results
    }
    con.execute(
        """DELETE FROM analysis_locator_candidates
        WHERE occurrence_key IN (
          SELECT occurrence_key FROM analysis_occurrences WHERE volume_id=?
        )""",
        (volume_id,),
    )
    for item in locator_items:
        occurrence_key = str(item["locator_key"])
        result = result_by_key.get(occurrence_key)
        if result is None:
            status = "pending"
            target_file = None
            probability = None
            decision_json = None
        else:
            status = str(result["status"])
            target_file = result.get("target_file")
            probability = result.get("confidence")
            decision_json = _json(result)
        con.execute(
            """UPDATE analysis_occurrences
            SET locator_item_json=?, locator_status=?, target_file=?,
                target_probability=?, decision_json=?
            WHERE occurrence_key=? AND volume_id=?""",
            (
                _json(item),
                status,
                target_file,
                probability,
                decision_json,
                occurrence_key,
                volume_id,
            ),
        )
        for candidate_order, candidate in enumerate(
            item.get("candidates") or [],
            start=1,
        ):
            if not isinstance(candidate, Mapping) or not candidate.get("file"):
                continue
            con.execute(
                """INSERT INTO analysis_locator_candidates(
                    occurrence_key, candidate_order, file_path, probability,
                    provider, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    occurrence_key,
                    candidate_order,
                    str(candidate["file"]),
                    candidate.get("probability"),
                    candidate.get("candidate_role"),
                    _json(candidate.get("evidence") or []),
                ),
            )
    mark_downstream_stale(con, volume_id=volume_id, after_stage="locate")
    con.commit()
    return {
        "items": len(locator_items),
        "resolved": len(deterministic_results),
        "pending": len(locator_items) - len(deterministic_results),
    }


def load_locator_items(
    con: sqlite3.Connection,
    volume_id: str,
    *,
    statuses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [volume_id]
    where = "volume_id=? AND locator_item_json IS NOT NULL"
    if statuses:
        status_list = sorted(set(statuses))
        where += " AND locator_status IN (" + ",".join("?" for _ in status_list) + ")"
        params.extend(status_list)
    rows = con.execute(
        f"""SELECT locator_item_json
        FROM analysis_occurrences
        WHERE {where}
        ORDER BY entry_key, ref_order""",
        params,
    ).fetchall()
    return [json.loads(row["locator_item_json"]) for row in rows]


def store_locator_results(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    results: Sequence[Mapping[str, Any]],
) -> int:
    stored = 0
    for result in results:
        key = locator_key(str(result["entry_key"]), int(result["ref_order"]))
        status = str(result.get("status") or "")
        if status not in TERMINAL_LOCATOR_STATUSES:
            raise ValueError(f"invalid terminal locator status: {status!r}")
        con.execute(
            """UPDATE analysis_occurrences
            SET locator_status=?, target_file=?, target_probability=?,
                decision_json=?
            WHERE volume_id=? AND occurrence_key=?""",
            (
                status,
                result.get("target_file"),
                result.get("confidence"),
                _json(result),
                volume_id,
                key,
            ),
        )
        if con.execute("SELECT changes()").fetchone()[0]:
            stored += 1
    con.commit()
    return stored


def load_locator_results(
    con: sqlite3.Connection,
    volume_id: str,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT decision_json
        FROM analysis_occurrences
        WHERE volume_id=?
          AND locator_status IN ('resolved', 'ambiguous', 'unrecoverable_ocr')
          AND decision_json IS NOT NULL
        ORDER BY entry_key, ref_order""",
        (volume_id,),
    ).fetchall()
    return [json.loads(row["decision_json"]) for row in rows]


def volume_status(con: sqlite3.Connection, volume_id: str) -> dict[str, Any]:
    volume = con.execute(
        """SELECT volume_id, collection, source_root, updated_at
        FROM analysis_volumes WHERE volume_id=?""",
        (volume_id,),
    ).fetchone()
    if volume is None:
        return {"volume_id": volume_id, "status": "missing"}
    stages = con.execute(
        """SELECT stage, status, input_fingerprint, summary_json, updated_at
        FROM analysis_volume_stages WHERE volume_id=?""",
        (volume_id,),
    ).fetchall()
    locator_counts = con.execute(
        """SELECT locator_status, COUNT(*) AS count
        FROM analysis_occurrences
        WHERE volume_id=?
        GROUP BY locator_status""",
        (volume_id,),
    ).fetchall()
    return {
        **dict(volume),
        "stages": {
            row["stage"]: {
                "status": row["status"],
                "input_fingerprint": row["input_fingerprint"],
                "summary": json.loads(row["summary_json"] or "{}"),
                "updated_at": row["updated_at"],
            }
            for row in stages
        },
        "locator_counts": {
            row["locator_status"]: row["count"] for row in locator_counts
        },
    }


def review_occurrences(
    con: sqlite3.Connection,
    *,
    volume_id: str | None = None,
    status: str | None = None,
    text_query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if volume_id:
        where.append("flat.volume_id=?")
        params.append(volume_id)
    if status:
        where.append("flat.locator_status=?")
        params.append(status)
    if text_query:
        where.append(
            """flat.entry_key IN (
              SELECT entry_key FROM analysis_entry_fts
              WHERE analysis_entry_fts MATCH ?
            )"""
        )
        params.append(text_query)
    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(max(1, min(limit, 10000)))
    rows = con.execute(
        f"""SELECT flat.*
        FROM analysis_occurrences_flat flat
        {clause}
        ORDER BY flat.volume_id, flat.entry_key, flat.ref_order
        LIMIT ?""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def export_seed_rows(
    con: sqlite3.Connection,
    *,
    kind: str,
    volume_id: str | None = None,
) -> list[dict[str, Any]]:
    if kind not in {"name", "scripture"}:
        raise ValueError("seed kind must be 'name' or 'scripture'")
    view = "analysis_name_seeds" if kind == "name" else "analysis_scripture_seeds"
    params: tuple[Any, ...] = ()
    where = ""
    if volume_id:
        where = " WHERE volume_id=?"
        params = (volume_id,)
    rows = con.execute(
        f"SELECT * FROM {view}{where} ORDER BY volume_id",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def semantic_locator_items(
    con: sqlite3.Connection,
    volume_id: str,
    editorial_page_map: Mapping[Any, Any] | None = None,
) -> list[dict[str, Any]]:
    return build_locator_items(
        load_semantic_payload(con, volume_id),
        editorial_page_map,
    )


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "DEFAULT_ANALYSIS_DB",
    "STAGES",
    "begin_stage",
    "connect_analysis_db",
    "ensure_volume",
    "export_seed_rows",
    "finish_stage",
    "init_analysis_schema",
    "load_locator_items",
    "load_locator_results",
    "load_semantic_payload",
    "mark_downstream_stale",
    "replace_discovery",
    "replace_locator_items",
    "replace_semantic_payload",
    "review_occurrences",
    "semantic_locator_items",
    "stable_fingerprint",
    "stage_state",
    "store_locator_results",
    "upgrade_locator_contracts",
    "volume_status",
]

"""SQLite storage, language detection, and CLTK caching for index translations."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DETECTOR_NAME = "patristica-script-collection"
DETECTOR_VERSION = "1"
TERMINAL_ANALYSIS_STATUSES = {"ok", "unsupported", "mixed", "unrecognized"}
GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-žÆæŒœ]")
SYRIAC_RE = re.compile(r"[\u0700-\u074f]")
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
ARMENIAN_RE = re.compile(r"[\u0530-\u058f]")
ETHIOPIC_RE = re.compile(r"[\u1200-\u137f]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-žÆæŒœ]+")
ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.I)


TRANSLATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS index_strings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_strings_source_text
    ON index_strings(source_text);

CREATE TABLE IF NOT EXISTS index_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id INTEGER NOT NULL REFERENCES index_strings(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    model_name TEXT NOT NULL,
    sample_context TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_translations_string_language
    ON index_translations(string_id, language);
CREATE INDEX IF NOT EXISTS idx_index_translations_language
    ON index_translations(language);
CREATE INDEX IF NOT EXISTS idx_index_translations_model_name
    ON index_translations(model_name);

CREATE TABLE IF NOT EXISTS index_string_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id INTEGER NOT NULL REFERENCES index_strings(id) ON DELETE CASCADE,
    detected_language TEXT NOT NULL,
    detector_name TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    analyzer_name TEXT,
    analyzer_version TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('ok', 'unsupported', 'mixed', 'unrecognized', 'error')
    ),
    error_message TEXT,
    is_truncated INTEGER NOT NULL DEFAULT 0 CHECK (is_truncated IN (0, 1)),
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_string_analyses_string
    ON index_string_analyses(string_id);
CREATE INDEX IF NOT EXISTS idx_index_string_analyses_language_status
    ON index_string_analyses(detected_language, status);

CREATE TABLE IF NOT EXISTS index_string_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES index_string_analyses(id) ON DELETE CASCADE,
    token_order INTEGER NOT NULL,
    sentence_index INTEGER,
    surface TEXT NOT NULL,
    lemma TEXT,
    upos TEXT,
    xpos TEXT,
    features_json TEXT,
    dependency_relation TEXT,
    governor_token_order INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    is_stop INTEGER CHECK (is_stop IN (0, 1)),
    confidence_json TEXT,
    annotation_sources_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_string_tokens_analysis_order
    ON index_string_tokens(analysis_id, token_order);
CREATE INDEX IF NOT EXISTS idx_index_string_tokens_lemma
    ON index_string_tokens(lemma);
CREATE INDEX IF NOT EXISTS idx_index_string_tokens_upos
    ON index_string_tokens(upos);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonicalize_source_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def connect_translation_db(path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(Path(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_translation_schema(con: sqlite3.Connection) -> None:
    con.executescript(TRANSLATION_SCHEMA_SQL)
    con.commit()


def _append_context(bucket: list[str], value: Any, *, limit: int = 3) -> None:
    text = canonicalize_source_text(value)
    if not text or text in bucket or len(bucket) >= limit:
        return
    bucket.append(text)


def collect_volume_candidates(
    con: sqlite3.Connection, volume_id: str
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT 'work_author' AS source_kind, w.author_raw AS source_text,
               w.title_raw AS context_a, v.volume_label AS context_b,
               NULL AS context_c, v.collection
        FROM works w JOIN volumes v ON v.volume_id = w.volume_id
        WHERE w.volume_id = ?

        UNION ALL
        SELECT 'work_title', w.title_raw, w.author_raw, v.volume_label, NULL, v.collection
        FROM works w JOIN volumes v ON v.volume_id = w.volume_id
        WHERE w.volume_id = ?

        UNION ALL
        SELECT 'section_heading', s.heading_raw, w.title_raw, s.scope_kind,
               s.index_kind, v.collection
        FROM index_sections s
        JOIN volumes v ON v.volume_id = s.volume_id
        LEFT JOIN works w ON w.work_key = s.work_key
        WHERE s.volume_id = ?

        UNION ALL
        SELECT 'entry_label',
               CASE WHEN TRIM(COALESCE(e.target_raw, '')) != ''
                    THEN e.target_raw ELSE e.entry_raw END,
               s.heading_raw,
               CASE WHEN TRIM(COALESCE(e.target_raw, '')) != ''
                          AND TRIM(e.entry_raw) != TRIM(e.target_raw)
                    THEN e.entry_raw ELSE NULL END,
               e.note_raw,
               v.collection
        FROM index_entries e
        JOIN index_sections s ON s.section_key = e.section_key
        JOIN volumes v ON v.volume_id = s.volume_id
        WHERE s.volume_id = ?

        UNION ALL
        SELECT 'entry_note', e.note_raw,
               CASE WHEN TRIM(COALESCE(e.target_raw, '')) != ''
                    THEN e.target_raw ELSE e.entry_raw END,
               s.heading_raw, NULL, v.collection
        FROM index_entries e
        JOIN index_sections s ON s.section_key = e.section_key
        JOIN volumes v ON v.volume_id = s.volume_id
        WHERE s.volume_id = ?
        """,
        (volume_id, volume_id, volume_id, volume_id, volume_id),
    ).fetchall()

    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        source_text = canonicalize_source_text(row["source_text"])
        if not source_text:
            continue
        candidate = grouped.setdefault(
            source_text,
            {
                "source_text": source_text,
                "contexts": [],
                "source_kinds": [],
                "collections": [],
            },
        )
        if row["source_kind"] not in candidate["source_kinds"]:
            candidate["source_kinds"].append(row["source_kind"])
        if row["collection"] and row["collection"] not in candidate["collections"]:
            candidate["collections"].append(row["collection"])
        for key in ("context_a", "context_b", "context_c"):
            _append_context(candidate["contexts"], row[key])
    return list(grouped.values())


def ensure_string_rows(
    con: sqlite3.Connection, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    now = now_iso()
    con.executemany(
        """
        INSERT INTO index_strings(source_text, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source_text) DO NOTHING
        """,
        [(item["source_text"], now, now) for item in candidates],
    )
    ids_by_text: dict[str, int] = {}
    texts = [str(item["source_text"]) for item in candidates]
    for start in range(0, len(texts), 500):
        chunk = texts[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in con.execute(
            f"SELECT id, source_text FROM index_strings WHERE source_text IN ({placeholders})",
            chunk,
        ):
            ids_by_text[row["source_text"]] = int(row["id"])
    con.commit()
    return [
        {**item, "string_id": ids_by_text[str(item["source_text"])]}
        for item in candidates
    ]


def collect_pending_translations(
    con: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    languages: list[str],
) -> list[dict[str, Any]]:
    normalized_languages = [
        canonicalize_source_text(language).lower() for language in languages
        if canonicalize_source_text(language)
    ]
    if not candidates or not normalized_languages:
        return []
    string_ids = [int(item["string_id"]) for item in candidates]
    existing: dict[int, set[str]] = {}
    for start in range(0, len(string_ids), 500):
        chunk = string_ids[start : start + 500]
        id_placeholders = ",".join("?" for _ in chunk)
        language_placeholders = ",".join("?" for _ in normalized_languages)
        for row in con.execute(
            f"""
            SELECT string_id, language FROM index_translations
            WHERE string_id IN ({id_placeholders})
              AND language IN ({language_placeholders})
            """,
            (*chunk, *normalized_languages),
        ):
            existing.setdefault(int(row["string_id"]), set()).add(row["language"])
    pending: list[dict[str, Any]] = []
    for item in candidates:
        string_id = int(item["string_id"])
        missing = [
            language for language in normalized_languages
            if language not in existing.get(string_id, set())
        ]
        if missing:
            pending.append({**item, "missing_languages": missing})
    return pending


def upsert_translation_rows(
    con: sqlite3.Connection,
    *,
    string_id: int,
    translations: dict[str, str],
    model_name: str,
    sample_context: str | None,
    commit: bool = True,
) -> int:
    now = now_iso()
    written = 0
    for language, translated_text in translations.items():
        language_key = canonicalize_source_text(language).lower()
        translated_value = canonicalize_source_text(translated_text)
        if not language_key or not translated_value:
            continue
        con.execute(
            """
            INSERT INTO index_translations(
                string_id, language, translated_text, model_name, sample_context,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(string_id, language) DO UPDATE SET
                translated_text = excluded.translated_text,
                model_name = excluded.model_name,
                sample_context = COALESCE(index_translations.sample_context, excluded.sample_context),
                updated_at = excluded.updated_at
            """,
            (
                string_id,
                language_key,
                translated_value,
                model_name,
                canonicalize_source_text(sample_context) or None,
                now,
                now,
            ),
        )
        written += 1
    if commit:
        con.commit()
    return written


def _script_counts(text: str) -> dict[str, int]:
    return {
        "grc": len(GREEK_RE.findall(text)),
        "lat": len(LATIN_RE.findall(text)),
        "syr": len(SYRIAC_RE.findall(text)),
        "ara": len(ARABIC_RE.findall(text)),
        "heb": len(HEBREW_RE.findall(text)),
        "hye": len(ARMENIAN_RE.findall(text)),
        "gez": len(ETHIOPIC_RE.findall(text)),
    }


def _latin_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in LATIN_TOKEN_RE.findall(text):
        if len(raw) <= 1 or ROMAN_NUMERAL_RE.fullmatch(raw):
            continue
        normalized = unicodedata.normalize("NFKD", raw)
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        ).casefold()
        normalized = normalized.replace("æ", "ae").replace("œ", "oe")
        if normalized:
            tokens.append(normalized)
    return tokens


def detect_source_language(
    source_text: str,
    collections: Iterable[str],
    *,
    latin_resolver: Callable[[list[str]], set[str]] | None = None,
) -> dict[str, Any]:
    text = canonicalize_source_text(source_text)
    counts = _script_counts(text)
    active_scripts = [name for name, count in counts.items() if count]
    classical_scripts = {"lat", "grc"}
    unsupported_scripts = {"syr", "ara", "heb", "hye", "gez"}
    if len(active_scripts) > 1:
        return {
            "language": "mixed",
            "status": "mixed",
            "reason": "multiple_scripts",
            "script_counts": counts,
        }
    if active_scripts and active_scripts[0] in unsupported_scripts:
        language = active_scripts[0]
        return {
            "language": language,
            "status": "unsupported",
            "reason": "recognized_unsupported_script",
            "script_counts": counts,
        }
    if active_scripts == ["grc"]:
        return {
            "language": "grc",
            "status": "pending",
            "reason": "greek_script",
            "script_counts": counts,
        }
    if not active_scripts or not classical_scripts.intersection(active_scripts):
        return {
            "language": "und",
            "status": "unrecognized",
            "reason": "no_supported_classical_script",
            "script_counts": counts,
        }

    collection_set = {str(item).upper() for item in collections}
    if collection_set.intersection({"PG", "PL"}):
        return {
            "language": "lat",
            "status": "pending",
            "reason": "pg_pl_latin_script_prior",
            "script_counts": counts,
        }

    tokens = _latin_tokens(text)
    if not tokens or latin_resolver is None:
        return {
            "language": "und",
            "status": "unrecognized",
            "reason": "po_latin_script_without_lexicon_evidence",
            "script_counts": counts,
        }
    resolved = latin_resolver(tokens)
    coverage = len({token for token in tokens if token in resolved}) / len(set(tokens))
    if coverage >= 0.6:
        return {
            "language": "lat",
            "status": "pending",
            "reason": "po_latin_lexicon_coverage",
            "latin_coverage": round(coverage, 4),
            "script_counts": counts,
        }
    return {
        "language": "und",
        "status": "unrecognized",
        "reason": "po_latin_lexicon_coverage_below_threshold",
        "latin_coverage": round(coverage, 4),
        "script_counts": counts,
    }


class LatinLexiconResolver:
    def __init__(self, dictionary_dir: Path | str):
        self.path = Path(dictionary_dir) / "superdb.sqlite"

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def __call__(self, tokens: list[str]) -> set[str]:
        unique = sorted(set(tokens))
        if not unique or not self.available:
            return set()
        con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            found: set[str] = set()
            for start in range(0, len(unique), 400):
                chunk = unique[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = con.execute(
                    f"""
                    SELECT lemma_norm AS value FROM entry
                    WHERE lemma_norm IN ({placeholders})
                    UNION
                    SELECT form_norm AS value FROM entry_form
                    WHERE form_norm IN ({placeholders})
                    """,
                    (*chunk, *chunk),
                ).fetchall()
                found.update(str(row[0]) for row in rows if row[0])
            return found
        finally:
            con.close()


def analysis_is_cached(
    row: sqlite3.Row | None,
    *,
    detection: dict[str, Any],
    analyzer_name: str,
    analyzer_version: str,
) -> bool:
    if row is None or row["detector_name"] != DETECTOR_NAME:
        return False
    if row["detector_version"] != DETECTOR_VERSION:
        return False
    if row["status"] == "error":
        return False
    if detection["status"] != "pending":
        return row["status"] == detection["status"]
    return (
        row["status"] == "ok"
        and row["detected_language"] == detection["language"]
        and row["analyzer_name"] == analyzer_name
        and row["analyzer_version"] == analyzer_version
    )


def collect_analysis_rows(
    con: sqlite3.Connection, string_ids: list[int]
) -> dict[int, sqlite3.Row]:
    rows: dict[int, sqlite3.Row] = {}
    for start in range(0, len(string_ids), 500):
        chunk = string_ids[start : start + 500]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        for row in con.execute(
            f"SELECT * FROM index_string_analyses WHERE string_id IN ({placeholders})",
            chunk,
        ):
            rows[int(row["string_id"])] = row
    return rows


def upsert_analysis(
    con: sqlite3.Connection,
    *,
    string_id: int,
    detection: dict[str, Any],
    analyzer_name: str | None,
    analyzer_version: str | None,
    status: str,
    tokens: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
    is_truncated: bool = False,
    raw_json: dict[str, Any] | None = None,
    commit: bool = True,
) -> int:
    now = now_iso()
    con.execute(
        """
        INSERT INTO index_string_analyses(
            string_id, detected_language, detector_name, detector_version,
            analyzer_name, analyzer_version, status, error_message, is_truncated,
            raw_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(string_id) DO UPDATE SET
            detected_language = excluded.detected_language,
            detector_name = excluded.detector_name,
            detector_version = excluded.detector_version,
            analyzer_name = excluded.analyzer_name,
            analyzer_version = excluded.analyzer_version,
            status = excluded.status,
            error_message = excluded.error_message,
            is_truncated = excluded.is_truncated,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            string_id,
            detection["language"],
            DETECTOR_NAME,
            DETECTOR_VERSION,
            analyzer_name,
            analyzer_version,
            status,
            canonicalize_source_text(error_message)[:2000] or None,
            int(is_truncated),
            json.dumps(raw_json or {"detection": detection}, ensure_ascii=False),
            now,
            now,
        ),
    )
    analysis_id = int(
        con.execute(
            "SELECT id FROM index_string_analyses WHERE string_id = ?", (string_id,)
        ).fetchone()[0]
    )
    con.execute("DELETE FROM index_string_tokens WHERE analysis_id = ?", (analysis_id,))
    if status == "ok" and tokens:
        con.executemany(
            """
            INSERT INTO index_string_tokens(
                analysis_id, token_order, sentence_index, surface, lemma, upos, xpos,
                features_json, dependency_relation, governor_token_order, char_start,
                char_end, is_stop, confidence_json, annotation_sources_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    analysis_id,
                    index,
                    token.get("sentence_index"),
                    canonicalize_source_text(token.get("surface")),
                    canonicalize_source_text(token.get("lemma")) or None,
                    canonicalize_source_text(token.get("upos")) or None,
                    canonicalize_source_text(token.get("xpos")) or None,
                    json.dumps(token.get("features"), ensure_ascii=False)
                    if token.get("features") is not None else None,
                    canonicalize_source_text(token.get("dependency_relation")) or None,
                    token.get("governor_token_order"),
                    token.get("char_start"),
                    token.get("char_end"),
                    int(bool(token["is_stop"])) if token.get("is_stop") is not None else None,
                    json.dumps(token.get("confidence"), ensure_ascii=False)
                    if token.get("confidence") is not None else None,
                    json.dumps(token.get("annotation_sources"), ensure_ascii=False)
                    if token.get("annotation_sources") is not None else None,
                )
                for index, token in enumerate(tokens)
                if canonicalize_source_text(token.get("surface"))
            ],
        )
    if commit:
        con.commit()
    return analysis_id


def load_analysis_summaries(
    con: sqlite3.Connection,
    string_ids: list[int],
    *,
    max_tokens: int = 80,
) -> dict[int, dict[str, Any]]:
    analyses = collect_analysis_rows(con, string_ids)
    summaries: dict[int, dict[str, Any]] = {}
    analysis_to_string: dict[int, int] = {}
    for string_id, row in analyses.items():
        if row["status"] != "ok":
            continue
        analysis_to_string[int(row["id"])] = string_id
        summaries[string_id] = {
            "status": "ok",
            "language": row["detected_language"],
            "analyzer": row["analyzer_name"],
            "analyzer_version": row["analyzer_version"],
            "tokens": [],
        }
    analysis_ids = list(analysis_to_string)
    for start in range(0, len(analysis_ids), 400):
        chunk = analysis_ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        token_rows = con.execute(
            f"""
            SELECT analysis_id, surface, lemma, upos, features_json
            FROM index_string_tokens
            WHERE analysis_id IN ({placeholders}) AND token_order < ?
            ORDER BY analysis_id, token_order
            """,
            (*chunk, max_tokens),
        ).fetchall()
        for token in token_rows:
            string_id = analysis_to_string[int(token["analysis_id"])]
            summaries[string_id]["tokens"].append(
                {
                    "surface": token["surface"],
                    "lemma": token["lemma"],
                    "upos": token["upos"],
                    "features": json.loads(token["features_json"])
                    if token["features_json"] else None,
                }
            )
    return summaries


class CltkWorkerClient:
    MAX_IGNORED_OUTPUT_LINES = 1000

    def __init__(self, python_executable: Path | str, worker_script: Path | str):
        self.process = subprocess.Popen(
            [str(python_executable), str(worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._request_id = 0

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("CLTK worker has no stdin/stdout pipes")
        self._request_id += 1
        request_id = self._request_id
        self.process.stdin.write(
            json.dumps({**payload, "request_id": request_id}, ensure_ascii=False) + "\n"
        )
        self.process.stdin.flush()
        ignored: list[str] = []
        for _ in range(self.MAX_IGNORED_OUTPUT_LINES):
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"CLTK worker exited unexpectedly with code {self.process.poll()}"
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                ignored.append(line.strip()[:200])
                continue
            if not isinstance(response, dict) or response.get("request_id") != request_id:
                ignored.append(line.strip()[:200])
                continue
            return response
        sample = [item for item in ignored if item][-5:]
        raise RuntimeError(
            "CLTK worker emitted too many non-protocol lines while waiting for "
            f"request {request_id}: {sample}"
        )

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)

    def __enter__(self) -> "CltkWorkerClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def cltk_healthcheck(
    python_executable: Path | str, worker_script: Path | str
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(python_executable), str(worker_script), "--healthcheck"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=60,
        )
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": f"invalid CLTK healthcheck output: {result.stdout or result.stderr}",
        }
    if result.returncode != 0 or payload.get("status") != "ok":
        return {
            **payload,
            "status": "error",
            "error": payload.get("error") or result.stderr.strip() or "healthcheck failed",
        }
    return payload


def _run_worker_bucket(
    *,
    python_executable: Path | str,
    worker_script: Path | str,
    items: list[tuple[dict[str, Any], dict[str, Any]]],
    max_tokens: int,
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    results: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    with CltkWorkerClient(python_executable, worker_script) as worker:
        for candidate, detection in items:
            try:
                response = worker.request(
                    {
                        "action": "analyze",
                        "language": detection["language"],
                        "text": candidate["source_text"],
                        "max_tokens": max_tokens,
                    }
                )
            except Exception as exc:
                response = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append((candidate, detection, response))
    return results


def prepare_cltk_analyses(
    con: sqlite3.Connection,
    *,
    candidates: list[dict[str, Any]],
    python_executable: Path | str,
    worker_script: Path | str,
    dictionary_dir: Path | str,
    workers: int = 1,
    max_tokens: int = 512,
) -> dict[str, Any]:
    init_translation_schema(con)
    resolver = LatinLexiconResolver(dictionary_dir)
    latin_resolver = resolver if resolver.available else None
    existing = collect_analysis_rows(
        con, [int(candidate["string_id"]) for candidate in candidates]
    )
    detections: list[tuple[dict[str, Any], dict[str, Any]]] = []
    terminal_written = 0
    cached = 0
    for candidate in candidates:
        detection = detect_source_language(
            str(candidate["source_text"]),
            candidate.get("collections", []),
            latin_resolver=latin_resolver,
        )
        row = existing.get(int(candidate["string_id"]))
        if detection["status"] != "pending":
            if analysis_is_cached(
                row,
                detection=detection,
                analyzer_name="cltk-stanza",
                analyzer_version="",
            ):
                cached += 1
                continue
            upsert_analysis(
                con,
                string_id=int(candidate["string_id"]),
                detection=detection,
                analyzer_name=None,
                analyzer_version=None,
                status=str(detection["status"]),
                raw_json={"detection": detection},
                commit=False,
            )
            terminal_written += 1
            continue
        detections.append((candidate, detection))
    con.commit()

    health = cltk_healthcheck(python_executable, worker_script)
    if health.get("status") != "ok":
        return {
            "status": "unavailable",
            "health": health,
            "candidate_count": len(candidates),
            "classical_pending": len(detections),
            "terminal_written": terminal_written,
            "cached": cached,
            "analyzed": 0,
            "errors": 0,
        }

    analyzer_name = str(health.get("analyzer_name") or "cltk-stanza")
    analyzer_version = str(health.get("analyzer_version") or "unknown")
    languages = health.get("languages") if isinstance(health.get("languages"), dict) else {}
    runnable: list[tuple[dict[str, Any], dict[str, Any]]] = []
    model_missing = 0
    for candidate, detection in detections:
        row = existing.get(int(candidate["string_id"]))
        if analysis_is_cached(
            row,
            detection=detection,
            analyzer_name=analyzer_name,
            analyzer_version=analyzer_version,
        ):
            cached += 1
            continue
        language_health = languages.get(detection["language"], {})
        if not language_health.get("model_present"):
            model_missing += 1
            continue
        runnable.append((candidate, detection))

    if not runnable:
        return {
            "status": "ok",
            "health": health,
            "candidate_count": len(candidates),
            "classical_pending": len(detections),
            "terminal_written": terminal_written,
            "cached": cached,
            "model_missing": model_missing,
            "analyzed": 0,
            "errors": 0,
        }

    worker_count = min(max(1, int(workers)), len(runnable))
    buckets = [runnable[index::worker_count] for index in range(worker_count)]
    all_results: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    if worker_count == 1:
        all_results = _run_worker_bucket(
            python_executable=python_executable,
            worker_script=worker_script,
            items=buckets[0],
            max_tokens=max_tokens,
        )
    else:
        from concurrent.futures import ThreadPoolExecutor

        lock = threading.Lock()

        def collect(bucket: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
            result = _run_worker_bucket(
                python_executable=python_executable,
                worker_script=worker_script,
                items=bucket,
                max_tokens=max_tokens,
            )
            with lock:
                all_results.extend(result)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(collect, bucket) for bucket in buckets]
            for future in futures:
                future.result()

    analyzed = 0
    errors = 0
    for candidate, detection, response in all_results:
        status = str(response.get("status") or "error")
        if status == "ok":
            analyzed += 1
            upsert_analysis(
                con,
                string_id=int(candidate["string_id"]),
                detection=detection,
                analyzer_name=analyzer_name,
                analyzer_version=analyzer_version,
                status="ok",
                tokens=list(response.get("tokens") or []),
                is_truncated=bool(response.get("is_truncated")),
                raw_json={"detection": detection, "worker": response.get("raw") or {}},
                commit=False,
            )
        else:
            errors += 1
            upsert_analysis(
                con,
                string_id=int(candidate["string_id"]),
                detection=detection,
                analyzer_name=analyzer_name,
                analyzer_version=analyzer_version,
                status="error",
                error_message=str(response.get("error") or "CLTK analysis failed"),
                raw_json={"detection": detection},
                commit=False,
            )
    con.commit()
    return {
        "status": "ok",
        "health": health,
        "candidate_count": len(candidates),
        "classical_pending": len(detections),
        "terminal_written": terminal_written,
        "cached": cached,
        "model_missing": model_missing,
        "analyzed": analyzed,
        "errors": errors,
    }

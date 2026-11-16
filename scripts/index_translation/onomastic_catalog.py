"""Incremental, occurrence-scoped translations of the unified search catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from scripts.index_translation.database import canonicalize_source_text
from scripts.index_translation.provider import (
    translate_candidate_worker,
    validate_translation_payload,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_STORE = ROOT / "data/index_catalog/onomastic_translations.sqlite"
DEFAULT_ALPHABETICAL = ROOT / "data/alphabetical_indices.db"
DEFAULT_GENERAL = ROOT / "data/patristic_indices.db"
DEFAULT_LANGUAGES = "en,fr,it,pt-br"
CONTRACT_VERSION = 1
PROMPT_VERSION = 1
CATALOG_VERSION = "unified-onomastic-catalog-v1"

TYPE_COMPATIBILITY = {
    "AUCTORES": {"author_index"},
    "LEXICA": {"foreign_terms"},
    "RERUM_ET_SENTENTIARUM": {"alphabetical_general", "analytic_subject"},
    "CAPITULA_ET_CANONES": {"analytic_subject", "ordo_rerum"},
}

STORE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS translation_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    language TEXT NOT NULL,
    revision INTEGER NOT NULL,
    original_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    context_json TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    contract_version INTEGER NOT NULL,
    prompt_version INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('api', 'alphabetical_legacy')),
    legacy_id INTEGER,
    legacy_source TEXT,
    state TEXT NOT NULL CHECK (state = 'completed'),
    api_call_id TEXT,
    api_usage_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(item_id, language, revision)
);
CREATE INDEX IF NOT EXISTS idx_onomastic_translation_item_language
    ON translation_revisions(item_id, language, revision DESC);
CREATE TABLE IF NOT EXISTS translation_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL REFERENCES translation_revisions(id),
    review_state TEXT NOT NULL CHECK (review_state IN ('approved', 'rejected')),
    reviewer TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_onomastic_review_revision
    ON translation_reviews(revision_id, id DESC);
CREATE TABLE IF NOT EXISTS translation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    languages_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state = 'failed'),
    error TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def section_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", canonicalize_source_text(value))
    text = text.replace("Æ", "Ae").replace("æ", "ae").replace("Œ", "Oe").replace("œ", "oe")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def relative_corpus_path(value: Any, root: Path = ROOT) -> str | None:
    raw = str(value or "").replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("teste/"):
        relative = raw
    elif "/teste/" in raw:
        relative = "teste/" + raw.split("/teste/", 1)[1]
    else:
        try:
            relative = Path(raw).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return relative


def connect_catalog(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    required = {"items", "candidate_storage"}
    found = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= found:
        con.close()
        raise ValueError(f"catalog missing tables: {sorted(required - found)}")
    return con


def connect_legacy(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def connect_store(path: Path, *, create: bool) -> sqlite3.Connection | None:
    if not create and not path.is_file():
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
    else:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    if create:
        con.executescript(STORE_SCHEMA)
        columns = {row[1] for row in con.execute("PRAGMA table_info(translation_revisions)")}
        if "legacy_source" not in columns:
            con.execute("ALTER TABLE translation_revisions ADD COLUMN legacy_source TEXT")
        if "api_call_id" not in columns:
            con.execute("ALTER TABLE translation_revisions ADD COLUMN api_call_id TEXT")
        con.execute("PRAGMA user_version = 1")
        con.commit()
    return con


def iter_items(
    con: sqlite3.Connection, *, volume: str | None = None, item_id: str | None = None
) -> Iterator[sqlite3.Row]:
    sql = "SELECT id, group_id, volume, section, label, ref_raw, source, state, metadata_json FROM items"
    clauses: list[str] = []
    params: list[str] = []
    if volume:
        clauses.append("volume = ?")
        params.append(volume)
    if item_id:
        clauses.append("id = ?")
        params.append(item_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY volume, source, id"
    yield from con.execute(sql, params)


@lru_cache(maxsize=128)
def _read_source_page(path: Path, mtime_ns: int, size: int) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def source_excerpt(row: sqlite3.Row, meta: dict[str, Any], root: Path = ROOT) -> str | None:
    span = meta.get("source_span") or meta.get("entry_span")
    relative = relative_corpus_path(row["source"], root)
    if not relative or not isinstance(span, list) or len(span) != 2:
        return None
    if not all(isinstance(position, int) for position in span):
        return None
    path = (root / relative).resolve()
    if not path.is_file():
        return None
    stat = path.stat()
    if stat.st_size > 2_000_000:
        return None
    content = _read_source_page(path, stat.st_mtime_ns, stat.st_size)
    start, end = span
    if start < 0 or end < start or start > len(content):
        return None
    excerpt = canonicalize_source_text(content[max(0, start - 110) : min(len(content), end + 110)])
    return excerpt[:360] or None


def occurrence(row: sqlite3.Row, *, root: Path = ROOT) -> dict[str, Any]:
    try:
        meta = json.loads(row["metadata_json"] or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except json.JSONDecodeError:
        meta = {}
    source = relative_corpus_path(row["source"], root)
    original = str(row["label"] or "")
    context = {
        "volume": row["volume"],
        "section": canonicalize_source_text(row["section"]),
        "index_category": meta.get("index_category") or "INDEX_GENERALIS",
        "entity_type": meta.get("entity_type") or "ONOMASTICA_HISTORICA",
        "reference_raw": canonicalize_source_text(row["ref_raw"]),
        "source_page": source,
        "source_excerpt": source_excerpt(row, meta, root),
    }
    return {
        "id": str(row["id"]),
        "group_id": row["group_id"],
        "volume": row["volume"],
        "section": row["section"],
        "original": original,
        "reference_raw": row["ref_raw"],
        "source": source,
        "state": row["state"],
        "context": context,
        "source_hash": digest(original),
        "context_hash": digest(canonical_json(context)),
    }


def latest_revision(
    store: sqlite3.Connection | None, item_id: str, language: str
) -> sqlite3.Row | None:
    if store is None:
        return None
    return store.execute(
        "SELECT * FROM translation_revisions WHERE item_id=? AND language=? ORDER BY revision DESC LIMIT 1",
        (item_id, language),
    ).fetchone()


def review_state(store: sqlite3.Connection | None, revision_id: int) -> str:
    if store is None:
        return "unreviewed"
    row = store.execute(
        "SELECT review_state FROM translation_reviews WHERE revision_id=? ORDER BY id DESC LIMIT 1",
        (revision_id,),
    ).fetchone()
    return str(row[0]) if row else "unreviewed"


def revision_status(
    store: sqlite3.Connection | None, item: dict[str, Any], language: str
) -> tuple[str, sqlite3.Row | None, str]:
    revision = latest_revision(store, item["id"], language)
    if revision is None:
        return "missing", None, "unreviewed"
    review = review_state(store, revision["id"])
    if (revision["source_hash"] != item["source_hash"]
            or revision["context_hash"] != item["context_hash"]
            or revision["contract_version"] != CONTRACT_VERSION):
        return "stale", revision, review
    if review == "rejected":
        return "rejected", revision, review
    return "valid", revision, review


def append_revision(
    store: sqlite3.Connection,
    item: dict[str, Any],
    *,
    language: str,
    translated_text: str,
    provider: str,
    model: str,
    origin: str,
    legacy_id: int | None = None,
    legacy_source: str | None = None,
    api_call_id: str | None = None,
    usage: list[dict[str, Any]] | None = None,
) -> int:
    translated = validate_translation_payload({language: translated_text}, [language])[language]
    previous = latest_revision(store, item["id"], language)
    number = int(previous["revision"]) + 1 if previous else 1
    cur = store.execute(
        """INSERT INTO translation_revisions (
            item_id,language,revision,original_text,translated_text,source_hash,context_hash,
            context_json,catalog_version,contract_version,prompt_version,provider,model,
            origin,legacy_id,legacy_source,state,api_call_id,api_usage_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item["id"], language, number, item["original"], translated,
            item["source_hash"], item["context_hash"], canonical_json(item["context"]),
            CATALOG_VERSION, CONTRACT_VERSION, PROMPT_VERSION, provider, model,
            origin, legacy_id, legacy_source,
            "completed", api_call_id, canonical_json(usage) if usage is not None else None,
            now_iso(),
        ),
    )
    return int(cur.lastrowid)


def _valid_hint(value: Any) -> str | None:
    try:
        return validate_translation_payload({"en": value}, ["en"])["en"]
    except (TypeError, ValueError):
        return None


def load_alpha_proofs(
    alpha: sqlite3.Connection, volume: str, root: Path = ROOT
) -> set[tuple[str, str, str, str]]:
    proofs: set[tuple[str, str, str, str]] = set()
    for row in alpha.execute(
        """SELECT s.section_kind,s.heading_raw,e.lemma_display,e.lemma_raw,
                  e.index_entry_source_ocr_file
           FROM alphabetical_entries e
           JOIN alphabetical_sections s ON s.section_key=e.section_key
           WHERE s.volume_id=?""",
        (volume,),
    ):
        source = relative_corpus_path(row["index_entry_source_ocr_file"], root)
        if not source:
            continue
        for field in ("lemma_display", "lemma_raw"):
            label = canonicalize_source_text(row[field])
            if label:
                proofs.add((source, label, section_key(row["heading_raw"]), row["section_kind"]))
    return proofs


def legacy_candidates(
    item: dict[str, Any], languages: list[str],
    alpha: sqlite3.Connection | None, general: sqlite3.Connection | None,
    root: Path = ROOT,
    alpha_proofs: set[tuple[str, str, str, str]] | None = None,
) -> tuple[dict[str, tuple[str, str, int]], dict[str, str]]:
    label = canonicalize_source_text(item["original"])
    if len(label) < 4 or re.fullmatch(r"[IVXLCDM\d\W]+", label, re.I):
        return {}, {}
    accepted: dict[str, tuple[str, str, int]] = {}
    hints_by_language: dict[str, set[str]] = {language: set() for language in languages}
    alpha_rows: list[sqlite3.Row] = []
    if alpha is not None:
        alpha_rows = [
            row for row in alpha.execute(
                """SELECT id, language, translated_text, model_name, sample_context,
                          section_kind, source_kind
                   FROM alphabetical_translations WHERE source_text=?""",
                (label,),
            )
            if row["source_kind"] == "entry_title"
        ]
        for row in alpha_rows:
            if row["language"] in hints_by_language:
                hint = _valid_hint(row["translated_text"])
                if hint:
                    hints_by_language[row["language"]].add(hint)
    if general is not None:
        for row in general.execute(
            """SELECT t.language,t.translated_text FROM index_strings s
               JOIN index_translations t ON t.string_id=s.id WHERE s.source_text=?""",
            (label,),
        ):
            if row["language"] in hints_by_language:
                hint = _valid_hint(row["translated_text"])
                if hint:
                    hints_by_language[row["language"]].add(hint)
    if alpha_rows and item["source"]:
        if alpha_proofs is None:
            alpha_proofs = load_alpha_proofs(alpha, item["volume"], root)
        compatible_kinds = TYPE_COMPATIBILITY.get(item["context"]["index_category"], set())
        proven_kinds = {
            kind for kind in compatible_kinds
            if (item["source"], label, section_key(item["section"]), kind) in alpha_proofs
        }
        for language in languages:
            matches = [
                row for row in alpha_rows
                if row["language"] == language
                and row["section_kind"] in proven_kinds
                and section_key(row["sample_context"]) == section_key(item["section"])
                and _valid_hint(row["translated_text"])
            ]
            values = {row["translated_text"] for row in matches}
            if len(values) == 1:
                row = matches[0]
                accepted[language] = (str(row["translated_text"]), str(row["model_name"]), int(row["id"]))
    hints = {
        language: next(iter(values)) for language, values in hints_by_language.items()
        if len(values) == 1 and language not in accepted
    }
    return accepted, hints


def inventory(
    catalog_path: Path, store_path: Path, *, languages: list[str],
    volume: str | None = None, item_id: str | None = None,
    root: Path = ROOT, out: Path | None = None,
) -> dict[str, Any]:
    catalog = connect_catalog(catalog_path)
    store = connect_store(store_path, create=False)
    counts: Counter[str] = Counter()
    per_language: dict[str, Counter[str]] = {language: Counter() for language in languages}
    pending_sample: list[dict[str, Any]] = []
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
    report_file = out.open("w", encoding="utf-8") if out is not None else None
    try:
        for row in iter_items(catalog, volume=volume, item_id=item_id):
            item = occurrence(row, root=root)
            counts["occurrences"] += 1
            valid = 0
            statuses: dict[str, str] = {}
            for language in languages:
                status, _, _ = revision_status(store, item, language)
                statuses[language] = status
                per_language[language][status] += 1
                if status == "valid":
                    valid += 1
            if valid == len(languages):
                counts["complete_occurrences"] += 1
            else:
                counts["pending_occurrences"] += 1
                pending = {
                    "id": item["id"], "volume": item["volume"],
                    "original": item["original"],
                    "missing_languages": [language for language in languages if statuses[language] != "valid"],
                    "status": statuses,
                    "source_hash": item["source_hash"],
                    "context_hash": item["context_hash"],
                }
                if len(pending_sample) < 5:
                    pending_sample.append(pending)
                if report_file is not None:
                    report_file.write(canonical_json(pending) + "\n")
        return {
            "catalog_version": CATALOG_VERSION,
            "contract_version": CONTRACT_VERSION,
            "volume": volume, "item_id": item_id,
            "languages": languages,
            "occurrences": counts["occurrences"],
            "complete_occurrences": counts["complete_occurrences"],
            "pending_occurrences": counts["pending_occurrences"],
            "by_language": {lang: dict(per_language[lang]) for lang in languages},
            "pending_sample": pending_sample,
            "pending_report": str(out) if out is not None else None,
        }
    finally:
        if report_file is not None:
            report_file.close()
        catalog.close()
        if store is not None:
            store.close()


def run(
    catalog_path: Path, store_path: Path, *, languages: list[str], volume: str | None,
    item_id: str | None, max_api_calls: int, model: str | None,
    provider: str = "openai-compatible", base_url: str = "https://api.openai.com/v1",
    api_key_env: str = "OPENAI_API_KEY", timeout: int = 180,
    alphabetical_path: Path = DEFAULT_ALPHABETICAL, general_path: Path = DEFAULT_GENERAL,
    root: Path = ROOT,
) -> dict[str, Any]:
    if not volume and not item_id:
        raise ValueError("run requires --volume or --item-id")
    if max_api_calls < 0:
        raise ValueError("--max-api-calls must be >= 0")
    catalog = connect_catalog(catalog_path)
    store = connect_store(store_path, create=True)
    assert store is not None
    alpha = connect_legacy(alphabetical_path)
    general = connect_legacy(general_path)
    alpha_proofs_by_volume: dict[str, set[tuple[str, str, str, str]]] = {}
    try:
        legacy_source = alphabetical_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        legacy_source = str(alphabetical_path)
    counts: Counter[str] = Counter()
    try:
        for row in iter_items(catalog, volume=volume, item_id=item_id):
            item = occurrence(row, root=root)
            counts["scanned_occurrences"] += 1
            statuses = {language: revision_status(store, item, language)[0] for language in languages}
            missing = [language for language in languages if statuses[language] != "valid"]
            if not missing:
                continue
            reusable = [language for language in missing if statuses[language] != "rejected"]
            if alpha is not None and reusable and item["volume"] not in alpha_proofs_by_volume:
                alpha_proofs_by_volume[item["volume"]] = load_alpha_proofs(alpha, item["volume"], root)
            reused, hints = legacy_candidates(
                item, reusable, alpha, general, root,
                alpha_proofs_by_volume.get(item["volume"]),
            )
            for language, (translated, legacy_model, legacy_id) in reused.items():
                append_revision(
                    store, item, language=language, translated_text=translated,
                    provider="alphabetical-legacy", model=legacy_model,
                    origin="alphabetical_legacy", legacy_id=legacy_id,
                    legacy_source=legacy_source,
                )
                counts["reused_languages"] += 1
            if reused:
                store.commit()
            missing = [language for language in missing if language not in reused]
            if not missing or counts["api_calls"] >= max_api_calls:
                continue
            if not model:
                raise ValueError("--model is required when API calls are allowed")
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise ValueError(f"missing API key in environment variable {api_key_env}")
            task = {
                "string_id": 0,
                "source_text": canonicalize_source_text(item["original"]),
                "contexts": [],
                "catalog_context": item["context"],
                "legacy_hints": {language: hints[language] for language in missing if language in hints},
                "languages": missing,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "timeout": timeout,
                "retries": 1,
                "tools_enabled": False,
            }
            counts["api_calls"] += 1
            try:
                result = translate_candidate_worker(task)
                translations = validate_translation_payload(dict(result["translations"]), missing)
                api_call_id = uuid.uuid4().hex
                for language, translated in translations.items():
                    append_revision(
                        store, item, language=language, translated_text=translated,
                        provider=provider, model=model, origin="api",
                        api_call_id=api_call_id,
                        usage=result.get("usage") or None,
                    )
                store.commit()
                counts["api_languages"] += len(translations)
            except Exception as exc:
                store.rollback()
                store.execute(
                    """INSERT INTO translation_attempts (
                        item_id,source_hash,context_hash,languages_json,provider,model,state,error,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (item["id"], item["source_hash"], item["context_hash"], canonical_json(missing),
                     provider, model, "failed", str(exc)[:2000], now_iso()),
                )
                store.commit()
                raise
        return {"volume": volume, "item_id": item_id, **dict(counts)}
    finally:
        catalog.close()
        store.close()
        if alpha is not None:
            alpha.close()
        if general is not None:
            general.close()


def export(
    catalog_path: Path, store_path: Path, *, languages: list[str], out: Path,
    volume: str | None = None, item_id: str | None = None,
    limit: int = 20, translated_only: bool = False, root: Path = ROOT,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("--limit must be >= 1")
    catalog = connect_catalog(catalog_path)
    store = connect_store(store_path, create=False)
    entries: list[dict[str, Any]] = []
    coverage: dict[str, Counter[str]] = {lang: Counter() for lang in languages}
    snapshot = hashlib.sha256()
    try:
        for row in iter_items(catalog, volume=volume, item_id=item_id):
            item = occurrence(row, root=root)
            translations: dict[str, dict[str, Any]] = {}
            statuses: dict[str, str] = {}
            for language in languages:
                status, revision, review = revision_status(store, item, language)
                statuses[language] = status
                if status == "valid" and revision is not None:
                    translations[language] = {
                        "text": revision["translated_text"],
                        "revision_id": revision["id"],
                        "revision": revision["revision"],
                        "review_state": review,
                        "provider": revision["provider"],
                        "model": revision["model"],
                        "origin": revision["origin"],
                        "legacy_id": revision["legacy_id"],
                        "legacy_source": revision["legacy_source"],
                        "api_call_id": revision["api_call_id"],
                    }
            if translated_only and not translations:
                continue
            destinations = [
                {
                    "path": relative_corpus_path(link["file"], root),
                    "rank": link["rank"], "score": link["score"],
                    "navigable": bool(link["navigable"]),
                }
                for link in catalog.execute(
                    """SELECT file,rank,score,navigable FROM candidate_storage
                       WHERE item_id=? ORDER BY rank,file""", (item["id"],)
                )
            ]
            entry = {
                "id": item["id"], "group_id": item["group_id"],
                "volume": item["volume"], "section": item["section"],
                "original": item["original"], "context": item["context"],
                "source": item["source"], "reference_raw": item["reference_raw"],
                "destinations": destinations, "translations": translations,
                "translation_status": statuses,
            }
            entries.append(entry)
            snapshot.update(canonical_json({
                "id": item["id"], "group_id": item["group_id"],
                "volume": item["volume"], "section": item["section"],
                "original": item["original"], "context": item["context"],
                "source": item["source"], "reference_raw": item["reference_raw"],
                "destinations": destinations,
            }).encode("utf-8"))
            for language, status in statuses.items():
                coverage[language][status] += 1
            if len(entries) >= limit:
                break
        payload = {
            "schema_version": 1,
            "catalog_version": CATALOG_VERSION,
            "catalog_source": catalog_path.parent.name,
            "catalog_snapshot_sha256": snapshot.hexdigest(),
            "generated_at": now_iso(),
            "requested_languages": languages,
            "coverage": {
                "occurrences": len(entries),
                "by_language": {lang: dict(coverage[lang]) for lang in languages},
                "complete_occurrences": sum(
                    all(lang in entry["translations"] for lang in languages) for entry in entries
                ),
            },
            "entries": entries,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"out": str(out), "occurrences": len(entries), "coverage": payload["coverage"]}
    finally:
        catalog.close()
        if store is not None:
            store.close()


def review(
    store_path: Path, *, item_id: str, language: str, state: str,
    reviewer: str, note: str | None = None,
) -> dict[str, Any]:
    if state not in {"approved", "rejected"}:
        raise ValueError("review state must be approved or rejected")
    store = connect_store(store_path, create=False)
    if store is None:
        raise ValueError("translation store does not exist")
    try:
        revision = latest_revision(store, item_id, language)
        if revision is None:
            raise ValueError("translation revision does not exist")
        store.close()
        store = connect_store(store_path, create=True)
        assert store is not None
        store.execute(
            """INSERT INTO translation_reviews(revision_id,review_state,reviewer,note,created_at)
               VALUES(?,?,?,?,?)""",
            (revision["id"], state, reviewer, note, now_iso()),
        )
        store.commit()
        return {"item_id": item_id, "language": language, "revision_id": revision["id"], "review_state": state}
    finally:
        store.close()

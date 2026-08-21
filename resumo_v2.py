#!/usr/bin/env python3
"""Infraestrutura do pipeline serial v2 de resumos.

O módulo mantém as novas gerações em shadow e nunca substitui diretamente a
tabela legada ``resumos``. A publicação escolhe uma geração v2 promovida ou,
na ausência dela, conserva o conteúdo legado.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence


SUMMARY_SCHEMA_VERSION = "summary-v2"
SUMMARY_PROMPT_VERSION = "summary-v2-gemma4-2026-08-25"
TRANSLATION_PROMPT_VERSION = "translation-v2-multilingual-2026-08-24"
CUMULATIVE_SUMMARY_SOFT_TARGET = 1800
CUMULATIVE_SUMMARY_REVIEW_LIMIT = 6000
VALID_PAGE_KINDS = {
    "body",
    "work_start",
    "transition",
    "preface",
    "dedication",
    "title_page",
    "index",
    "bibliography",
    "critical_apparatus",
    "administrative",
    "illegible",
    "other",
}
VALID_CONTRIBUTOR_ROLES = {
    "editor",
    "translator",
    "dedicant",
    "dedicatee",
    "commentator",
    "printer",
}
PROMOTABLE_STATUSES = {"valid", "metadata_pending"}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resumo_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    documento TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    next_page_num INTEGER,
    blocking_page_num INTEGER,
    blocking_reason TEXT NOT NULL DEFAULT '',
    force_from_page INTEGER,
    force_through_page INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resumo_runs_doc_status
    ON resumo_runs(documento, status, id DESC);

CREATE TABLE IF NOT EXISTS resumo_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES resumo_runs(id) ON DELETE CASCADE,
    legacy_resumo_id INTEGER,
    ocr_result_id INTEGER,
    documento TEXT NOT NULL,
    pagina_num INTEGER NOT NULL,
    pagina_file TEXT NOT NULL,
    previous_generation_id INTEGER REFERENCES resumo_generations(id),
    previous_chain_hash TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL DEFAULT '',
    chain_hash TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0, 1)),
    promoted_at TEXT,
    context_reset INTEGER NOT NULL DEFAULT 0 CHECK(context_reset IN (0, 1)),
    context_reset_confidence REAL NOT NULL DEFAULT 0,
    tainted_by_page INTEGER,
    primary_page_kind TEXT NOT NULL DEFAULT 'other',
    page_kinds_json TEXT NOT NULL DEFAULT '[]',
    segments_json TEXT NOT NULL DEFAULT '[]',
    contributors_json TEXT NOT NULL DEFAULT '[]',
    summary_display_pt TEXT NOT NULL DEFAULT '',
    cumulative_summary TEXT NOT NULL DEFAULT '',
    search_text_pt TEXT NOT NULL DEFAULT '',
    embedding_text TEXT NOT NULL DEFAULT '',
    administrative_reason TEXT NOT NULL DEFAULT '',
    source_conflicts_json TEXT NOT NULL DEFAULT '[]',
    static_analysis_json TEXT NOT NULL DEFAULT '{}',
    validation_issues_json TEXT NOT NULL DEFAULT '[]',
    raw_response TEXT NOT NULL DEFAULT '',
    facsimile_used INTEGER NOT NULL DEFAULT 0 CHECK(facsimile_used IN (0, 1)),
    embedding BLOB,
    embedding_dim INTEGER,
    embedding_model TEXT,
    embedding_source_hash TEXT,
    embedding_updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, documento, pagina_num)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_resumo_generations_current
    ON resumo_generations(documento, pagina_num) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_resumo_generations_run_page
    ON resumo_generations(run_id, pagina_num);
CREATE INDEX IF NOT EXISTS idx_resumo_generations_status
    ON resumo_generations(documento, status, pagina_num);
CREATE INDEX IF NOT EXISTS idx_resumo_generations_chain
    ON resumo_generations(chain_hash);
CREATE INDEX IF NOT EXISTS idx_resumo_generations_embedding_pending
    ON resumo_generations(documento, pagina_num)
    WHERE is_current = 1 AND embedding IS NULL;

CREATE TABLE IF NOT EXISTS resumo_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES resumo_generations(id) ON DELETE CASCADE,
    locale TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    glossary_hash TEXT NOT NULL DEFAULT '',
    summary_display TEXT NOT NULL DEFAULT '',
    cumulative_summary TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '',
    raw_response TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(generation_id, locale, source_hash, prompt_version, model)
);

CREATE INDEX IF NOT EXISTS idx_resumo_translations_current
    ON resumo_translations(generation_id, locale, status);

CREATE TABLE IF NOT EXISTS resumo_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE,
    documento TEXT NOT NULL,
    pagina_num INTEGER NOT NULL,
    issue_kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    resolution_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(generation_id, issue_kind)
);

CREATE INDEX IF NOT EXISTS idx_resumo_review_pending
    ON resumo_review_queue(status, documento, pagina_num);

CREATE TABLE IF NOT EXISTS resumo_context_anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento TEXT NOT NULL,
    pagina_num INTEGER NOT NULL,
    pagina_file TEXT NOT NULL,
    work_key TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    source_hash TEXT NOT NULL,
    is_trusted INTEGER NOT NULL DEFAULT 0 CHECK(is_trusted IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(documento, pagina_num, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_resumo_anchors_next
    ON resumo_context_anchors(documento, pagina_num, is_trusted);

CREATE TABLE IF NOT EXISTS resumo_cluster_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'v2-page',
    embedding_model TEXT NOT NULL,
    embedding_set_hash TEXT NOT NULL,
    params_json TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, embedding_model, embedding_set_hash, params_hash)
);

CREATE TABLE IF NOT EXISTS resumo_generation_clusters (
    cluster_run_id INTEGER NOT NULL REFERENCES resumo_cluster_runs(id) ON DELETE CASCADE,
    generation_id INTEGER NOT NULL REFERENCES resumo_generations(id) ON DELETE CASCADE,
    documento TEXT NOT NULL,
    pagina_num INTEGER NOT NULL,
    cluster_id INTEGER,
    membership_probability REAL,
    outlier_score REAL,
    reduced_dim INTEGER NOT NULL,
    reduced_embedding BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(cluster_run_id, generation_id)
);

CREATE INDEX IF NOT EXISTS idx_resumo_generation_clusters_page
    ON resumo_generation_clusters(cluster_run_id, documento, pagina_num);

CREATE TRIGGER IF NOT EXISTS trg_resumo_runs_updated_at
AFTER UPDATE OF status, next_page_num, blocking_page_num, blocking_reason,
                force_from_page, force_through_page ON resumo_runs
BEGIN
    UPDATE resumo_runs SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_generations_updated_at
AFTER UPDATE OF status, is_current, promoted_at, context_reset,
                context_reset_confidence, tainted_by_page, primary_page_kind,
                page_kinds_json, segments_json, contributors_json,
                summary_display_pt, cumulative_summary, search_text_pt,
                embedding_text, administrative_reason, source_conflicts_json,
                static_analysis_json, validation_issues_json, raw_response,
                facsimile_used, embedding, embedding_dim, embedding_model,
                embedding_source_hash, embedding_updated_at
ON resumo_generations
BEGIN
    UPDATE resumo_generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_generations_clear_embedding
AFTER UPDATE OF embedding_text ON resumo_generations
WHEN NEW.embedding_text IS NOT OLD.embedding_text
BEGIN
    UPDATE resumo_generations
       SET embedding = NULL,
           embedding_dim = NULL,
           embedding_model = NULL,
           embedding_source_hash = NULL,
           embedding_updated_at = NULL
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_translations_updated_at
AFTER UPDATE OF summary_display, cumulative_summary, search_text, raw_response, status, error_message
ON resumo_translations
BEGIN
    UPDATE resumo_translations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_review_updated_at
AFTER UPDATE OF status, resolution_json, evidence_json, severity
ON resumo_review_queue
BEGIN
    UPDATE resumo_review_queue SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_anchors_updated_at
AFTER UPDATE OF work_key, confidence, evidence_json, is_trusted
ON resumo_context_anchors
BEGIN
    UPDATE resumo_context_anchors SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_cluster_runs_updated_at
AFTER UPDATE OF status, params_json ON resumo_cluster_runs
BEGIN
    UPDATE resumo_cluster_runs SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resumo_generation_clusters_updated_at
AFTER UPDATE OF cluster_id, membership_probability, outlier_score,
                reduced_dim, reduced_embedding ON resumo_generation_clusters
BEGIN
    UPDATE resumo_generation_clusters SET updated_at=CURRENT_TIMESTAMP
     WHERE cluster_run_id=NEW.cluster_run_id AND generation_id=NEW.generation_id;
END;
"""


@dataclass(frozen=True)
class StaticPageAnalysis:
    header_original: str
    ocr_clean: str
    ocr_chars: int
    alpha_ratio: float
    single_token_ratio: float
    is_xml: bool
    is_empty: bool
    page_kind_hints: tuple[str, ...]
    scripture_candidates: tuple[dict[str, Any], ...]
    scripture_candidate_count: int
    scripture_ambiguous_count: int
    mixed_scripts: bool
    column_count: int
    has_date_or_roman: bool
    exact_start_candidates: tuple[dict[str, Any], ...]
    containing_work_candidates: tuple[dict[str, Any], ...]
    index_ambiguous: bool
    facsimile_score: int
    facsimile_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_original": self.header_original,
            # O texto integral já existe no corpus e não deve ser duplicado em
            # cada geração de uma base que hoje tem centenas de milhares de
            # páginas. O preview basta para auditoria humana da heurística.
            # Evidência operacional curta; o OCR integral já vive na fonte e não
            # deve ser duplicado em cada geração do banco.
            "ocr_clean_preview": self.ocr_clean[:400],
            "ocr_chars": self.ocr_chars,
            "alpha_ratio": self.alpha_ratio,
            "single_token_ratio": self.single_token_ratio,
            "is_xml": self.is_xml,
            "is_empty": self.is_empty,
            "page_kind_hints": list(self.page_kind_hints),
            "scripture_candidates": list(self.scripture_candidates),
            "scripture_candidate_count": self.scripture_candidate_count,
            "scripture_ambiguous_count": self.scripture_ambiguous_count,
            "mixed_scripts": self.mixed_scripts,
            "column_count": self.column_count,
            "has_date_or_roman": self.has_date_or_roman,
            "exact_start_candidates": list(self.exact_start_candidates),
            "containing_work_candidates": list(self.containing_work_candidates),
            "index_ambiguous": self.index_ambiguous,
            "facsimile_score": self.facsimile_score,
            "facsimile_reasons": list(self.facsimile_reasons),
        }


@dataclass(frozen=True)
class SummaryCandidate:
    page_kinds: tuple[str, ...]
    segments: tuple[dict[str, Any], ...]
    contributors: tuple[dict[str, str], ...]
    summary_display_pt: str
    cumulative_summary: str
    source_conflicts: tuple[dict[str, Any], ...]
    administrative_reason: str
    primary_page_kind: str
    status: str
    validation_issues: tuple[str, ...]
    context_reset: bool
    context_reset_confidence: float


def sha256_text(*values: object) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value if value is not None else "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def normalize_match_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^a-z0-9α-ωа-я]+", " ", text)
    return " ".join(text.split())


def normalize_whitespace(value: object) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split()).strip()


def normalize_confidence(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return {"high": 0.95, "medium": 0.70, "low": 0.40}.get(
            str(value or "").strip().lower(), 0.0
        )


def init_v2_schema(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    tables = {
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "resumo_generations" in tables:
        columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(resumo_generations)")
        }
        if "ocr_result_id" not in columns:
            con.execute("ALTER TABLE resumo_generations ADD COLUMN ocr_result_id INTEGER")
    if "resumo_translations" in tables:
        columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(resumo_translations)")
        }
        if "cumulative_summary" not in columns:
            con.execute(
                "ALTER TABLE resumo_translations "
                "ADD COLUMN cumulative_summary TEXT NOT NULL DEFAULT ''"
            )
    con.executescript(SCHEMA_SQL)
    con.commit()


def manifest_payload(pages: Sequence[Path]) -> tuple[str, str]:
    items = [{"page": page_number(path), "file": path.name} for path in pages]
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return payload, sha256_text(payload)


def page_number(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    fallback = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    return int(fallback.group(1)) if fallback else 0


def open_indices_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _collapse_equivalent_work_candidates(
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Colapsa aliases evidentes sem confundir obras apenas co-localizadas."""
    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        title_tokens = set(
            normalize_match_text(
                str(candidate.get("title_original") or candidate.get("title_pt") or "")
            ).split()
        )
        target: list[dict[str, Any]] | None = None
        for group in groups:
            reference = group[0]
            reference_tokens = set(
                normalize_match_text(
                    str(
                        reference.get("title_original")
                        or reference.get("title_pt")
                        or ""
                    )
                ).split()
            )
            same_anchor = (
                candidate.get("work_order") == reference.get("work_order")
                and candidate.get("physical_start_page")
                == reference.get("physical_start_page")
                and candidate.get("editorial_start_page")
                == reference.get("editorial_start_page")
            )
            overlap = (
                len(title_tokens & reference_tokens)
                / max(1, min(len(title_tokens), len(reference_tokens)))
            )
            if same_anchor and title_tokens and reference_tokens and overlap >= 0.80:
                target = group
                break
        if target is None:
            groups.append([candidate])
        else:
            target.append(candidate)

    collapsed: list[dict[str, Any]] = []
    for group in groups:
        preferred = max(
            group,
            key=lambda item: (
                normalize_confidence(item.get("confidence")),
                len(str(item.get("title_original") or item.get("title_pt") or "")),
                len(str(item.get("work_key") or "")),
            ),
        )
        value = dict(preferred)
        aliases = sorted(
            {
                normalize_whitespace(item.get("work_key"))
                for item in group
                if normalize_whitespace(item.get("work_key"))
                and normalize_whitespace(item.get("work_key"))
                != normalize_whitespace(preferred.get("work_key"))
            }
        )
        if aliases:
            value["alias_work_keys"] = aliases
        collapsed.append(value)
    return collapsed


def load_volume_index_hints(
    indices: sqlite3.Connection | None,
    documento: str,
    pages: Sequence[Path],
) -> dict[int, dict[str, Any]]:
    """Materializa uma vez os hints estruturais do índice para um volume."""
    by_page = {
        page_number(path): {
            "documento": documento,
            "pagina_fisica": page_number(path),
            "exact_start_candidates": [],
            "containing_work_candidates": [],
            "exact_start_ambiguous": False,
            "range_ambiguous": False,
        }
        for path in pages
    }
    if indices is None:
        return by_page
    file_to_page = {path.name: page_number(path) for path in pages}
    rows = indices.execute(
        """
        SELECT w.*,
               (SELECT t.translated_text
                  FROM index_strings s
                  JOIN index_translations t ON t.string_id=s.id
                 WHERE s.source_text=w.author_raw AND t.language='pt-br'
                 ORDER BY t.id LIMIT 1) AS author_pt,
               (SELECT t.translated_text
                  FROM index_strings s
                  JOIN index_translations t ON t.string_id=s.id
                 WHERE s.source_text=w.title_raw AND t.language='pt-br'
                 ORDER BY t.id LIMIT 1) AS title_pt
          FROM works w
         WHERE w.volume_id=?
         ORDER BY w.work_order, w.work_key
        """,
        (documento,),
    ).fetchall()
    resolved: list[dict[str, Any]] = []
    for raw in rows:
        work = dict(raw)
        start_name = Path(str(work.get("start_file") or "")).name
        end_name = Path(str(work.get("end_file") or "")).name
        start_phys = file_to_page.get(start_name)
        end_phys = file_to_page.get(end_name)
        if start_phys is None:
            continue
        resolved.append(
            {
                "work": work,
                "start_phys": start_phys,
                "end_phys": end_phys,
            }
        )

    starts = sorted({int(item["start_phys"]) for item in resolved})
    last_page = max(by_page, default=0)
    for item in resolved:
        work = item["work"]
        start_phys = int(item["start_phys"])
        end_phys = item["end_phys"]
        if end_phys is not None:
            end_phys = max(start_phys, int(end_phys))
            range_source = "index_explicit"
            range_confidence = normalize_confidence(work.get("confidence"))
        else:
            next_start = next((value for value in starts if value > start_phys), None)
            if next_start is not None:
                # A página de fronteira pode terminar a obra anterior e iniciar a
                # seguinte. Mantemos ambas como candidatas somente nessa página.
                end_phys = next_start
                range_source = "inferred_next_start"
                range_confidence = min(
                    normalize_confidence(work.get("confidence")), 0.65
                )
            else:
                end_phys = max(start_phys, last_page)
                range_source = "inferred_volume_end"
                range_confidence = min(
                    normalize_confidence(work.get("confidence")), 0.50
                )
        compact = {
            "work_order": work.get("work_order"),
            "work_key": str(work.get("work_key") or ""),
            "author_original": str(work.get("author_raw") or ""),
            "author_pt": str(work.get("author_pt") or ""),
            "title_original": str(work.get("title_raw") or ""),
            "title_pt": str(work.get("title_pt") or ""),
            "editorial_start_page": work.get("start_page"),
            "editorial_end_page": work.get("end_page"),
            "physical_start_page": start_phys,
            "physical_end_page": end_phys,
            "confidence": normalize_confidence(work.get("confidence")),
            "range_source": range_source,
            "range_confidence": range_confidence,
        }
        if start_phys in by_page:
            by_page[start_phys]["exact_start_candidates"].append(
                {**compact, "exact_physical_start": True}
            )
        for pagina_num in range(start_phys, end_phys + 1):
            if pagina_num in by_page:
                by_page[pagina_num]["containing_work_candidates"].append(
                    {**compact, "exact_physical_start": pagina_num == start_phys}
                )
    for hints in by_page.values():
        hints["exact_start_candidates"] = _collapse_equivalent_work_candidates(
            hints["exact_start_candidates"]
        )
        hints["containing_work_candidates"] = _collapse_equivalent_work_candidates(
            hints["containing_work_candidates"]
        )
        hints["exact_start_ambiguous"] = len(hints["exact_start_candidates"]) > 1
        hints["range_ambiguous"] = len(hints["containing_work_candidates"]) > 1
    return by_page


def canonical_labels_from_hints(hints: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in [
        *(hints.get("exact_start_candidates") or []),
        *(hints.get("containing_work_candidates") or []),
    ]:
        for field in ("author_pt", "author_original", "title_pt", "title_original"):
            value = normalize_whitespace(item.get(field))
            if value and normalize_match_text(value) not in {
                normalize_match_text(existing) for existing in labels
            }:
                labels.append(value)
    return labels


def next_trusted_work_start(
    hints_by_page: dict[int, dict[str, Any]], start_page: int
) -> int | None:
    for pagina_num in sorted(value for value in hints_by_page if value > start_page):
        starts = hints_by_page[pagina_num].get("exact_start_candidates") or []
        if len(starts) == 1 and float(starts[0].get("confidence") or 0.0) >= 0.70:
            return pagina_num
    return None


def make_run_key(
    documento: str,
    manifest_hash: str,
    schema_version: str,
    prompt_version: str,
    provider: str,
    model: str,
) -> str:
    return sha256_text(
        documento,
        manifest_hash,
        schema_version,
        prompt_version,
        provider,
        model,
    )


def get_or_create_run(
    con: sqlite3.Connection,
    *,
    documento: str,
    pages: Sequence[Path],
    provider: str,
    model: str,
    force_from_page: int | None = None,
    force_through_page: int | None = None,
) -> sqlite3.Row:
    manifest_json, manifest_hash = manifest_payload(pages)
    run_key = make_run_key(
        documento,
        manifest_hash,
        SUMMARY_SCHEMA_VERSION,
        SUMMARY_PROMPT_VERSION,
        provider,
        model,
    )
    con.execute(
        """
        INSERT INTO resumo_runs
            (run_key, documento, schema_version, prompt_version, provider, model,
             manifest_hash, manifest_json, force_from_page, force_through_page)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_key) DO UPDATE SET
            force_from_page=COALESCE(excluded.force_from_page, resumo_runs.force_from_page),
            force_through_page=COALESCE(excluded.force_through_page, resumo_runs.force_through_page),
            status=CASE WHEN excluded.force_from_page IS NOT NULL THEN 'running' ELSE resumo_runs.status END,
            blocking_page_num=CASE WHEN excluded.force_from_page IS NOT NULL THEN NULL ELSE resumo_runs.blocking_page_num END,
            blocking_reason=CASE WHEN excluded.force_from_page IS NOT NULL THEN '' ELSE resumo_runs.blocking_reason END
        """,
        (
            run_key,
            documento,
            SUMMARY_SCHEMA_VERSION,
            SUMMARY_PROMPT_VERSION,
            provider,
            model,
            manifest_hash,
            manifest_json,
            force_from_page,
            force_through_page,
        ),
    )
    con.commit()
    row = con.execute("SELECT * FROM resumo_runs WHERE run_key = ?", (run_key,)).fetchone()
    if row is None:
        raise RuntimeError("Não foi possível criar ou retomar resumo_run")
    return row


def generation_for_page(
    con: sqlite3.Connection, run_id: int, documento: str, pagina_num: int
) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM resumo_generations WHERE run_id=? AND documento=? AND pagina_num=?",
        (run_id, documento, pagina_num),
    ).fetchone()


def resume_page_number(
    con: sqlite3.Connection,
    run: sqlite3.Row,
    pages: Sequence[Path],
    force_from_page: int | None = None,
) -> int | None:
    ordered = [page_number(path) for path in pages]
    if force_from_page is not None:
        return next((value for value in ordered if value >= force_from_page), None)
    blocked = run["blocking_page_num"]
    if blocked is not None:
        return next((value for value in ordered if value >= int(blocked)), None)
    rows = con.execute(
        "SELECT pagina_num, status FROM resumo_generations WHERE run_id=? AND documento=?",
        (int(run["id"]), str(run["documento"])),
    ).fetchall()
    done = {
        int(row["pagina_num"])
        for row in rows
        if row["status"] in {"valid", "metadata_pending", "context_provisional"}
    }
    return next((value for value in ordered if value not in done), None)


def mark_run_blocked(
    con: sqlite3.Connection, run_id: int, pagina_num: int, reason: str
) -> None:
    con.execute(
        """
        UPDATE resumo_runs
           SET status='blocked', blocking_page_num=?, blocking_reason=?, next_page_num=?
         WHERE id=?
        """,
        (pagina_num, reason[:2000], pagina_num, run_id),
    )
    con.commit()


def mark_run_complete(con: sqlite3.Connection, run_id: int) -> None:
    con.execute(
        "UPDATE resumo_runs SET status='completed', next_page_num=NULL, blocking_page_num=NULL, blocking_reason='' WHERE id=?",
        (run_id,),
    )
    con.commit()


def _extract_xml_blocks(text: str, block_type: str) -> list[str]:
    pattern = re.compile(
        rf"<bloco\b[^>]*\btipo\s*=\s*(['\"]){re.escape(block_type)}\1[^>]*>(.*?)</bloco>",
        re.IGNORECASE | re.DOTALL,
    )
    return [
        normalize_whitespace(html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))))
        for match in pattern.finditer(text or "")
        if normalize_whitespace(html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))))
    ]


def extract_original_header(text: str) -> str:
    headers = _extract_xml_blocks(text, "cabecalho")
    if headers:
        candidates = [re.sub(r"^\d+\s+|\s+\d+$", "", value).strip() for value in headers]
        candidates = [value for value in candidates if len(value) >= 4]
        if candidates:
            return max(candidates, key=lambda value: (sum(ch.isalpha() for ch in value), -len(value)))[:500]
    lines = [normalize_whitespace(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line and not re.fullmatch(r"[\d\W_]+", line)]
    candidates = []
    for line in lines[:12]:
        letters = [ch for ch in line if ch.isalpha()]
        if len(letters) < 4 or len(line) > 180:
            continue
        upper_ratio = sum(ch.isupper() for ch in letters) / max(1, len(letters))
        if upper_ratio >= 0.55 or len(line) <= 80:
            candidates.append(line)
        if len(candidates) >= 3:
            break
    return normalize_whitespace(" ".join(candidates))[:500]


def _script_counts(text: str) -> tuple[bool, int]:
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]", text or ""))
    greek = len(re.findall(r"[Α-Ωα-ω]", text or ""))
    cyrillic = len(re.findall(r"[А-Яа-я]", text or ""))
    syriac = len(re.findall(r"[\u0700-\u074F]", text or ""))
    active = sum(count >= 12 for count in (latin, greek, cyrillic, syriac))
    return active >= 2, active


def _estimate_column_count(page_text: str) -> int:
    """Estima colunas reais sem confundir cada bbox com uma coluna.

    O OCR estruturado possui um bbox por bloco. Contar bbox distintos fazia
    quase toda página parecer multicolunada. Aqui só contamos faixas estreitas
    que se sobrepõem verticalmente, um sinal bem mais conservador.
    """
    boxes: list[tuple[int, int, int, int]] = []
    for match in re.finditer(
        r"<bloco\b[^>]*\bbbox\s*=\s*['\"](\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)['\"]",
        page_text or "",
        re.IGNORECASE,
    ):
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))
    if len(boxes) < 2:
        return 1
    page_width = max(box[2] for box in boxes)
    narrow = [box for box in boxes if box[2] - box[0] <= page_width * 0.62 and box[3] - box[1] >= 80]
    centers: list[float] = []
    for index, first in enumerate(narrow):
        for second in narrow[index + 1 :]:
            overlap = min(first[3], second[3]) - max(first[1], second[1])
            min_height = min(first[3] - first[1], second[3] - second[1])
            if overlap < min_height * 0.35:
                continue
            first_center = (first[0] + first[2]) / 2
            second_center = (second[0] + second[2]) / 2
            if abs(first_center - second_center) >= page_width * 0.24:
                centers.extend((first_center, second_center))
    if not centers:
        return 1
    centers.sort()
    groups = 1
    for previous, current in zip(centers, centers[1:]):
        if current - previous >= page_width * 0.18:
            groups += 1
    return min(groups, 4)


def _page_kind_hints(text: str, header: str) -> tuple[str, ...]:
    probe = normalize_match_text(f"{header} {text[:4000]}")
    hints: list[str] = []
    patterns = [
        ("title_page", r"\b(?:titulus|title page|page de titre|tomus|patrologia)\b"),
        ("preface", r"\b(?:praefatio|prael[oó]quium|preface|prefacio)\b"),
        ("dedication", r"\b(?:dedicatio|dedication|dedicatoria)\b"),
        ("index", r"\b(?:index|elenchus|ordo rerum|tabula|contents)\b"),
        ("bibliography", r"\b(?:bibliographia|bibliographie|bibliography)\b"),
        ("critical_apparatus", r"\b(?:apparatus|variantia|codices|codd)\b"),
    ]
    for kind, pattern in patterns:
        if re.search(pattern, probe, re.IGNORECASE):
            hints.append(kind)
    return tuple(hints)


def analyze_page(
    *,
    documento: str,
    pagina_num: int,
    pagina_file: str,
    page_text: str,
    ocr_clean: str,
    index_hints: dict[str, Any] | None = None,
    scripture_evidence: dict[str, Any] | None = None,
) -> StaticPageAnalysis:
    header = extract_original_header(page_text)
    tokens = re.findall(r"\b\w+\b", ocr_clean or "", re.UNICODE)
    alpha_chars = sum(ch.isalpha() for ch in ocr_clean or "")
    alpha_ratio = alpha_chars / max(1, len(ocr_clean or ""))
    single_ratio = sum(len(token) == 1 for token in tokens) / max(1, len(tokens))
    stripped = (page_text or "").lstrip()
    is_xml = bool(re.match(r"<(?:\?xml|pagina)\b", stripped, re.IGNORECASE))
    is_empty = not normalize_whitespace(ocr_clean)
    mixed_scripts, _script_count = _script_counts(page_text)
    column_count = _estimate_column_count(page_text)
    has_date_or_roman = bool(
        re.search(r"\b(?:1[4-9]\d{2}|20\d{2}|M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{1,3}))\b", header)
    )
    hints = index_hints or {}
    starts = tuple(hints.get("exact_start_candidates") or ())
    containing = tuple(hints.get("containing_work_candidates") or ())
    evidence = scripture_evidence or {}
    scriptures = tuple(evidence.get("candidates") or ())
    candidate_count = int(evidence.get("candidate_count") or len(scriptures))
    ambiguous_count = sum(
        1
        for candidate in scriptures
        if float(candidate.get("confidence") or 0) < 0.75
        or any((item.get("status") not in {None, "ok", "normalized"}) for item in candidate.get("occurrences") or [])
    )
    kind_hints = _page_kind_hints(page_text, header)

    score = 0
    reasons: list[str] = []
    if is_empty or alpha_ratio < 0.25 or len(tokens) < 12:
        score += 3
        reasons.append("ocr_low_signal")
    if single_ratio > 0.35 or (is_xml and "</pagina>" not in page_text.lower()):
        score += 3
        reasons.append("ocr_structure_suspect")
    if starts or any(kind in kind_hints for kind in {"title_page", "preface", "dedication"}):
        score += 2
        reasons.append("editorial_boundary")
    if any(kind in kind_hints for kind in {"index", "bibliography", "critical_apparatus"}):
        score += 2
        reasons.append("dense_editorial_material")
    if mixed_scripts or column_count >= 3:
        score += 2
        reasons.append("mixed_scripts_or_columns")
    if candidate_count >= 8:
        score += 2
        reasons.append("many_scripture_candidates")
    if ambiguous_count >= 3:
        score += 2
        reasons.append("ambiguous_scripture_candidates")
    if has_date_or_roman:
        score += 1
        reasons.append("date_or_roman")
    if len(starts) > 1 or bool(hints.get("range_ambiguous")):
        score += 2
        reasons.append("catalog_ambiguity")

    return StaticPageAnalysis(
        header_original=header,
        ocr_clean=ocr_clean,
        ocr_chars=len(ocr_clean or ""),
        alpha_ratio=round(alpha_ratio, 4),
        single_token_ratio=round(single_ratio, 4),
        is_xml=is_xml,
        is_empty=is_empty,
        page_kind_hints=kind_hints,
        scripture_candidates=scriptures,
        scripture_candidate_count=candidate_count,
        scripture_ambiguous_count=ambiguous_count,
        mixed_scripts=mixed_scripts,
        column_count=column_count,
        has_date_or_roman=has_date_or_roman,
        exact_start_candidates=starts,
        containing_work_candidates=containing,
        index_ambiguous=len(starts) > 1 or bool(hints.get("range_ambiguous")),
        facsimile_score=score,
        facsimile_reasons=tuple(reasons),
    )


def build_summary_search_text(
    summary_display: str,
    segments: Iterable[dict[str, Any]],
    analysis: StaticPageAnalysis,
    canonical_labels: Iterable[str],
) -> str:
    values = [summary_display]
    values.extend(normalize_whitespace(item.get("summary")) for item in segments)
    values.extend(normalize_whitespace(value) for value in canonical_labels)
    for candidate in analysis.scripture_candidates:
        for occurrence in candidate.get("occurrences") or []:
            if occurrence.get("ref_norm"):
                values.append(normalize_whitespace(occurrence["ref_norm"]))
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = normalize_whitespace(value)
        key = normalize_match_text(normalized)
        if not normalized or not key or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return normalize_whitespace(" ".join(output))[:5000]


def build_embedding_text(summary_display: str, canonical_labels: Iterable[str]) -> str:
    prefix = ". ".join(value for value in map(normalize_whitespace, canonical_labels) if value)
    return normalize_whitespace(f"{prefix}. {summary_display}" if prefix else summary_display)[:1800]


MARKDOWN_JSON_FENCE_RE = re.compile(
    r"(?:```|~~~)(?:[ \t]*[\w.+-]+)?[ \t]*\n?(.*?)(?:```|~~~)",
    flags=re.DOTALL | re.IGNORECASE,
)


def extract_json_object_response(raw_response: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON completo de uma resposta de modelo.

    Segue a tolerância a cercas Markdown usada pelo OCR em ``main2.py``, mas
    usa ``JSONDecoder.raw_decode`` para não confundir texto residual após o
    fechamento do objeto com parte do JSON. A validação semântica do payload
    continua a cargo de ``parse_summary_candidate``.
    """
    text = re.sub(
        r"<think>.*?</think>",
        "",
        raw_response or "",
        flags=re.DOTALL | re.IGNORECASE,
    ).lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("resposta vazia")

    fenced_candidates = [
        match.group(1).strip()
        for match in MARKDOWN_JSON_FENCE_RE.finditer(text)
        if match.group(1).strip()
    ]
    candidates = [*fenced_candidates, text]
    decoder = json.JSONDecoder()
    last_error: Exception | None = None

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
        else:
            if isinstance(payload, dict):
                return payload
            last_error = ValueError("o valor JSON não é um objeto")

        for start, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(payload, dict):
                return payload
            last_error = ValueError("o valor JSON não é um objeto")

    detail = str(last_error) if last_error is not None else "objeto JSON não encontrado"
    raise ValueError(detail)


def parse_summary_candidate(
    raw_response: str,
    analysis: StaticPageAnalysis,
    allowed_work_keys: set[str],
) -> SummaryCandidate:
    try:
        payload = extract_json_object_response(raw_response)
    except Exception as exc:
        raise ValueError(f"JSON inválido: {exc}") from exc

    issues: list[str] = []
    raw_kinds = payload.get("page_kinds")
    if not isinstance(raw_kinds, list):
        raise ValueError("page_kinds deve ser uma lista")
    page_kinds = tuple(dict.fromkeys(normalize_match_text(str(item)).replace(" ", "_") for item in raw_kinds))
    if not page_kinds or any(kind not in VALID_PAGE_KINDS for kind in page_kinds):
        raise ValueError(f"page_kinds inválido: {page_kinds}")

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raise ValueError("segments deve ser uma lista")
    segments: list[dict[str, Any]] = []
    for expected_order, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, dict):
            raise ValueError("Cada segmento deve ser um objeto")
        kind = normalize_match_text(str(raw.get("kind") or "other")).replace(" ", "_")
        if kind not in VALID_PAGE_KINDS:
            raise ValueError(f"Tipo de segmento inválido: {kind}")
        summary = normalize_whitespace(raw.get("summary"))
        work_key = normalize_whitespace(raw.get("work_key"))
        if work_key and work_key not in allowed_work_keys:
            issues.append(f"work_key_not_candidate:{work_key}")
            work_key = ""
        if not summary and kind not in {"administrative", "illegible"}:
            issues.append(f"empty_segment_summary:{expected_order}")
        segments.append(
            {
                "order": expected_order,
                "kind": kind,
                "work_key": work_key,
                "summary": summary,
            }
        )

    contributors: list[dict[str, str]] = []
    raw_contributors = payload.get("contributors") or []
    if not isinstance(raw_contributors, list):
        raise ValueError("contributors deve ser uma lista")
    for raw in raw_contributors:
        if not isinstance(raw, dict):
            continue
        name = normalize_whitespace(raw.get("name"))
        role = normalize_match_text(str(raw.get("role") or "")).replace(" ", "_")
        if not name or role not in VALID_CONTRIBUTOR_ROLES:
            issues.append("invalid_contributor")
            continue
        contributors.append({"name": name, "role": role})

    cumulative = normalize_whitespace(payload.get("cumulative_summary"))
    if len(cumulative) > CUMULATIVE_SUMMARY_REVIEW_LIMIT:
        issues.append("cumulative_summary_excessive")
    admin_reason = normalize_whitespace(payload.get("administrative_reason"))
    conflicts = payload.get("source_conflicts") or []
    if not isinstance(conflicts, list):
        conflicts = []
        issues.append("invalid_source_conflicts")

    display_parts = [segment["summary"] for segment in segments if segment["summary"]]
    summary_display = normalize_whitespace(" ".join(display_parts))
    is_administrative = "administrative" in page_kinds
    if is_administrative and (analysis.exact_start_candidates or analysis.ocr_chars >= 500):
        issues.append("false_administrative_risk")
    if is_administrative and not admin_reason:
        issues.append("administrative_without_reason")
    if not is_administrative and len(summary_display) < 80:
        issues.append("summary_too_short")
    if not cumulative and not is_administrative:
        issues.append("missing_cumulative_summary")

    chosen_keys = [segment["work_key"] for segment in segments if segment["work_key"]]
    exact_keys = {
        str(item.get("work_key") or "")
        for item in analysis.exact_start_candidates
        if item.get("work_key")
    }
    context_reset = bool(exact_keys and chosen_keys and chosen_keys[-1] in exact_keys and "work_start" in page_kinds)
    reset_confidence = 0.0
    if context_reset:
        exact = next(item for item in analysis.exact_start_candidates if item.get("work_key") == chosen_keys[-1])
        reset_confidence = float(exact.get("confidence") or 0.0)
        if analysis.header_original:
            title = str(exact.get("title_original") or exact.get("title_pt") or "")
            header_tokens = set(normalize_match_text(analysis.header_original).split())
            title_tokens = set(normalize_match_text(title).split())
            if title_tokens and len(header_tokens & title_tokens) / max(1, min(len(header_tokens), len(title_tokens))) >= 0.4:
                reset_confidence = max(reset_confidence, 0.95)

    contextual_issues = {
        "summary_too_short",
        "missing_cumulative_summary",
        "false_administrative_risk",
    }
    if contextual_issues & set(issues):
        status = "context_provisional"
    elif issues:
        status = "metadata_pending"
    else:
        status = "valid"
    primary = segments[0]["kind"] if segments else page_kinds[0]
    return SummaryCandidate(
        page_kinds=page_kinds,
        segments=tuple(segments),
        contributors=tuple(contributors),
        summary_display_pt=summary_display,
        cumulative_summary=cumulative,
        source_conflicts=tuple(item for item in conflicts if isinstance(item, dict)),
        administrative_reason=admin_reason,
        primary_page_kind=primary,
        status=status,
        validation_issues=tuple(issues),
        context_reset=context_reset,
        context_reset_confidence=reset_confidence,
    )


def generation_last_work_key(generation: Any | None) -> str:
    """Obtém a última obra ativa gravada sem depender do texto cumulativo."""
    if generation is None:
        return ""
    try:
        raw_segments = generation["segments_json"]
    except (KeyError, IndexError, TypeError):
        return ""
    try:
        segments = json.loads(str(raw_segments or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(segments, list):
        return ""
    for segment in reversed(segments):
        if not isinstance(segment, dict):
            continue
        work_key = normalize_whitespace(segment.get("work_key"))
        if work_key:
            return work_key
    return ""


def initial_active_work_key(
    previous_generation: Any | None,
    analysis: StaticPageAnalysis,
) -> str:
    """Reconstrói a obra corrente ao retomar ou iniciar no meio do volume."""
    previous_key = generation_last_work_key(previous_generation)
    if previous_key:
        return previous_key
    containing_keys = {
        normalize_whitespace(item.get("work_key"))
        for item in analysis.containing_work_candidates
        if normalize_whitespace(item.get("work_key"))
    }
    if len(containing_keys) == 1:
        return next(iter(containing_keys))
    exact_keys = {
        normalize_whitespace(item.get("work_key"))
        for item in analysis.exact_start_candidates
        if normalize_whitespace(item.get("work_key"))
    }
    return next(iter(exact_keys)) if len(exact_keys) == 1 else ""


def reconcile_candidate_work_keys(
    candidate: SummaryCandidate,
    analysis: StaticPageAnalysis,
    active_work_key: str,
) -> tuple[SummaryCandidate, str, dict[str, Any]]:
    """Preenche omissões de ``work_key`` sem delegar continuidade ao modelo.

    Uma chave explícita e permitida continua sendo respeitada. Em sua ausência,
    a obra anterior permanece ativa. Um início exato e não ambíguo troca a obra
    na página de fronteira; a chave anterior ainda pode aparecer em segmentos
    anteriores ao marcador de transição.
    """
    active = normalize_whitespace(active_work_key)
    incoming_active = active
    exact_candidates = {
        normalize_whitespace(item.get("work_key")): item
        for item in analysis.exact_start_candidates
        if normalize_whitespace(item.get("work_key"))
    }
    containing_candidates = {
        normalize_whitespace(item.get("work_key")): item
        for item in analysis.containing_work_candidates
        if normalize_whitespace(item.get("work_key"))
    }
    sole_containing = (
        next(iter(containing_candidates)) if len(containing_candidates) == 1 else ""
    )
    if not active and sole_containing:
        active = sole_containing

    trusted_exact = ""
    if len(exact_candidates) == 1:
        key, exact = next(iter(exact_candidates.items()))
        if normalize_confidence(exact.get("confidence")) >= 0.70:
            trusted_exact = key

    segments = [dict(segment) for segment in candidate.segments]
    eligible = [
        index
        for index, segment in enumerate(segments)
        if segment.get("kind") not in {"administrative", "illegible"}
    ]
    switch_at: int | None = None
    if trusted_exact and eligible:
        marked = [
            index
            for index in eligible
            if segments[index].get("kind") in {"work_start", "transition"}
        ]
        if marked:
            switch_at = marked[0]
        elif "work_start" in candidate.page_kinds or "transition" in candidate.page_kinds:
            switch_at = eligible[-1]
        elif not active:
            switch_at = eligible[0]
        else:
            # O índice afirma que a obra começa nesta página. Sem segmentação
            # explícita, atribuímos pelo menos o último trecho à nova obra.
            switch_at = eligible[-1]

    current = active
    active_candidate = containing_candidates.get(current, {})
    final_source = (
        str(active_candidate.get("range_source") or "inherited_previous")
        if current
        else ""
    )
    for index, segment in enumerate(segments):
        if index not in eligible:
            continue
        explicit = normalize_whitespace(segment.get("work_key"))
        if explicit:
            current = explicit
            final_source = "model_selected"
        elif switch_at is not None and index >= switch_at:
            current = trusted_exact
            segment["work_key"] = current
            final_source = "index_exact_start"
        elif current:
            segment["work_key"] = current
        elif sole_containing:
            current = sole_containing
            segment["work_key"] = current
            final_source = str(
                containing_candidates[current].get("range_source") or "index_range"
            )

    if trusted_exact and switch_at is not None:
        current = trusted_exact
        final_source = "index_exact_start"

    issues = list(candidate.validation_issues)
    possible_change = bool(
        ("transition" in candidate.page_kinds or "work_start" in candidate.page_kinds)
        and not trusted_exact
        and not any(
            normalize_whitespace(segment.get("work_key"))
            and normalize_whitespace(segment.get("work_key")) != active
            for segment in segments
        )
    )
    if possible_change and "possible_work_transition_unresolved" not in issues:
        issues.append("possible_work_transition_unresolved")

    status = candidate.status
    if possible_change and status == "valid":
        status = "metadata_pending"

    context_reset = candidate.context_reset
    reset_confidence = candidate.context_reset_confidence
    if (
        trusted_exact
        and switch_at is not None
        and trusted_exact != incoming_active
    ):
        context_reset = True
        reset_confidence = max(
            reset_confidence,
            normalize_confidence(exact_candidates[trusted_exact].get("confidence")),
        )

    resolution_candidate = (
        exact_candidates.get(current) or containing_candidates.get(current) or {}
    )
    confidence = normalize_confidence(resolution_candidate.get("confidence"))
    if final_source.startswith("inferred_"):
        confidence = normalize_confidence(
            resolution_candidate.get("range_confidence")
        )
    elif final_source == "inherited_previous" and current:
        confidence = 0.70
    resolution = {
        "work_key": current,
        "source": final_source,
        "confidence": confidence,
        "possible_change": possible_change,
    }
    return (
        replace(
            candidate,
            segments=tuple(segments),
            status=status,
            validation_issues=tuple(issues),
            context_reset=context_reset,
            context_reset_confidence=reset_confidence,
        ),
        current,
        resolution,
    )


def store_generation(
    con: sqlite3.Connection,
    *,
    run_id: int,
    legacy_resumo_id: int | None,
    ocr_result_id: int | None,
    documento: str,
    pagina_num: int,
    pagina_file: str,
    previous_generation: sqlite3.Row | None,
    source_hash: str,
    provider: str,
    model: str,
    candidate: SummaryCandidate,
    search_text_pt: str,
    embedding_text: str,
    analysis: StaticPageAnalysis,
    raw_response: str,
    facsimile_used: bool,
    tainted_by_page: int | None,
    previous_chain_hash_override: str = "",
) -> sqlite3.Row:
    previous_chain = (
        str(previous_generation["chain_hash"] or "")
        if previous_generation
        else previous_chain_hash_override
    )
    previous_id = int(previous_generation["id"]) if previous_generation else None
    output_hash = sha256_text(
        candidate.summary_display_pt,
        candidate.cumulative_summary,
        json.dumps(candidate.segments, ensure_ascii=False, sort_keys=True),
    )
    chain_hash = sha256_text(previous_chain, source_hash, output_hash)
    values = (
        run_id,
        legacy_resumo_id,
        ocr_result_id,
        documento,
        pagina_num,
        pagina_file,
        previous_id,
        previous_chain,
        source_hash,
        output_hash,
        chain_hash,
        SUMMARY_SCHEMA_VERSION,
        SUMMARY_PROMPT_VERSION,
        provider,
        model,
        candidate.status,
        int(candidate.context_reset),
        candidate.context_reset_confidence,
        tainted_by_page,
        candidate.primary_page_kind,
        json.dumps(candidate.page_kinds, ensure_ascii=False),
        json.dumps(candidate.segments, ensure_ascii=False),
        json.dumps(candidate.contributors, ensure_ascii=False),
        candidate.summary_display_pt,
        candidate.cumulative_summary,
        search_text_pt,
        embedding_text,
        candidate.administrative_reason,
        json.dumps(candidate.source_conflicts, ensure_ascii=False),
        json.dumps(analysis.to_dict(), ensure_ascii=False),
        json.dumps(candidate.validation_issues, ensure_ascii=False),
        raw_response,
        int(facsimile_used),
    )
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        """
        INSERT INTO resumo_generations (
            run_id, legacy_resumo_id, ocr_result_id, documento, pagina_num, pagina_file,
            previous_generation_id, previous_chain_hash, source_hash,
            output_hash, chain_hash, schema_version, prompt_version, provider,
            model, status, context_reset, context_reset_confidence,
            tainted_by_page, primary_page_kind, page_kinds_json, segments_json,
            contributors_json, summary_display_pt, cumulative_summary,
            search_text_pt, embedding_text, administrative_reason,
            source_conflicts_json, static_analysis_json,
            validation_issues_json, raw_response, facsimile_used
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id, documento, pagina_num) DO UPDATE SET
            legacy_resumo_id=excluded.legacy_resumo_id,
            ocr_result_id=excluded.ocr_result_id,
            pagina_file=excluded.pagina_file,
            previous_generation_id=excluded.previous_generation_id,
            previous_chain_hash=excluded.previous_chain_hash,
            source_hash=excluded.source_hash,
            output_hash=excluded.output_hash,
            chain_hash=excluded.chain_hash,
            provider=excluded.provider,
            model=excluded.model,
            status=excluded.status,
            context_reset=excluded.context_reset,
            context_reset_confidence=excluded.context_reset_confidence,
            tainted_by_page=excluded.tainted_by_page,
            primary_page_kind=excluded.primary_page_kind,
            page_kinds_json=excluded.page_kinds_json,
            segments_json=excluded.segments_json,
            contributors_json=excluded.contributors_json,
            summary_display_pt=excluded.summary_display_pt,
            cumulative_summary=excluded.cumulative_summary,
            search_text_pt=excluded.search_text_pt,
            embedding_text=excluded.embedding_text,
            administrative_reason=excluded.administrative_reason,
            source_conflicts_json=excluded.source_conflicts_json,
            static_analysis_json=excluded.static_analysis_json,
            validation_issues_json=excluded.validation_issues_json,
            raw_response=excluded.raw_response,
            facsimile_used=excluded.facsimile_used
        """,
        values,
    )
    con.execute(
        "UPDATE resumo_runs SET status='running', next_page_num=?, blocking_page_num=NULL, blocking_reason='' WHERE id=?",
        (pagina_num + 1, run_id),
    )
    row = con.execute(
        "SELECT * FROM resumo_generations WHERE run_id=? AND documento=? AND pagina_num=?",
        (run_id, documento, pagina_num),
    ).fetchone()
    if row is None:
        con.rollback()
        raise RuntimeError("Geração v2 não foi persistida")
    for issue in candidate.validation_issues:
        con.execute(
            """
            INSERT INTO resumo_review_queue
                (generation_id, documento, pagina_num, issue_kind, severity, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(generation_id, issue_kind) DO UPDATE SET
                severity=excluded.severity, evidence_json=excluded.evidence_json, status='pending'
            """,
            (
                int(row["id"]),
                documento,
                pagina_num,
                issue,
                "context" if candidate.status == "context_provisional" else "metadata",
                json.dumps({"analysis": analysis.to_dict()}, ensure_ascii=False),
            ),
        )
    con.commit()
    return row


def store_context_anchor(
    con: sqlite3.Connection,
    *,
    documento: str,
    pagina_num: int,
    pagina_file: str,
    source_hash: str,
    resolution: dict[str, Any],
) -> None:
    """Registra separadamente a proveniência da obra ativa inferida."""
    work_key = normalize_whitespace(resolution.get("work_key"))
    if not work_key:
        return
    source = normalize_whitespace(resolution.get("source"))
    confidence = normalize_confidence(resolution.get("confidence"))
    is_trusted = int(source == "index_exact_start" and confidence >= 0.70)
    anchor_source_hash = sha256_text(
        source_hash,
        work_key,
        source,
        confidence,
        bool(resolution.get("possible_change")),
    )
    con.execute(
        """
        INSERT INTO resumo_context_anchors
            (documento, pagina_num, pagina_file, work_key, confidence,
             evidence_json, source_hash, is_trusted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(documento, pagina_num, source_hash) DO UPDATE SET
            pagina_file=excluded.pagina_file,
            work_key=excluded.work_key,
            confidence=excluded.confidence,
            evidence_json=excluded.evidence_json,
            is_trusted=excluded.is_trusted
        """,
        (
            documento,
            pagina_num,
            pagina_file,
            work_key,
            confidence,
            json.dumps(resolution, ensure_ascii=False, sort_keys=True),
            anchor_source_hash,
            is_trusted,
        ),
    )
    con.commit()


def invalidate_generation_range(
    con: sqlite3.Connection,
    documento: str,
    start_page: int,
    through_page: int | None,
) -> int:
    where = "documento=? AND pagina_num>=? AND is_current=1"
    params: list[Any] = [documento, start_page]
    if through_page is not None:
        where += " AND pagina_num<=?"
        params.append(through_page)
    rows = con.execute(f"SELECT id FROM resumo_generations WHERE {where}", params).fetchall()
    ids = [int(row["id"]) for row in rows]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        f"UPDATE resumo_generations SET is_current=0, status='stale' WHERE id IN ({placeholders})",
        ids,
    )
    con.execute(
        f"UPDATE resumo_translations SET status='stale' WHERE generation_id IN ({placeholders})",
        ids,
    )
    con.commit()
    return len(ids)


def promote_segment(
    con: sqlite3.Connection,
    run_id: int,
    documento: str,
    start_page: int,
    end_page: int,
) -> int:
    rows = con.execute(
        """
        SELECT * FROM resumo_generations
         WHERE run_id=? AND documento=? AND pagina_num BETWEEN ? AND ?
         ORDER BY pagina_num
        """,
        (run_id, documento, start_page, end_page),
    ).fetchall()
    if not rows:
        return 0
    run = con.execute("SELECT manifest_json FROM resumo_runs WHERE id=?", (run_id,)).fetchone()
    manifest = json.loads(run["manifest_json"] or "[]") if run is not None else []
    expected_pages = [
        int(item["page"])
        for item in manifest
        if start_page <= int(item.get("page") or 0) <= end_page
    ]
    if [int(row["pagina_num"]) for row in rows] != expected_pages:
        return 0
    if any(row["status"] not in PROMOTABLE_STATUSES or row["tainted_by_page"] is not None for row in rows):
        return 0
    ids = [int(row["id"]) for row in rows]
    pages = [int(row["pagina_num"]) for row in rows]
    placeholders = ",".join("?" for _ in pages)
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        f"UPDATE resumo_generations SET is_current=0 WHERE documento=? AND pagina_num IN ({placeholders}) AND is_current=1",
        [documento, *pages],
    )
    placeholders_ids = ",".join("?" for _ in ids)
    con.execute(
        f"UPDATE resumo_generations SET is_current=1, promoted_at=CURRENT_TIMESTAMP WHERE id IN ({placeholders_ids})",
        ids,
    )
    con.commit()
    return len(ids)


def translation_source_hash(generation: sqlite3.Row, locale: str, glossary_hash: str) -> str:
    return sha256_text(
        generation["id"],
        generation["output_hash"],
        generation["summary_display_pt"],
        generation["cumulative_summary"],
        generation["search_text_pt"],
        locale,
        glossary_hash,
    )


def current_summary_select_sql() -> str:
    """Projeção híbrida usada por exportadores durante a migração."""
    return """
        SELECT
            r.*,
            g.id AS v2_generation_id,
            COALESCE(NULLIF(g.summary_display_pt, ''), r.resumo_pagina) AS effective_summary_page,
            COALESCE(NULLIF(g.cumulative_summary, ''), r.resumo_global) AS effective_cumulative_summary,
            COALESCE(NULLIF(g.search_text_pt, ''), NULLIF(r.summary_page_clean, ''), r.resumo_pagina) AS effective_search_text,
            COALESCE(NULLIF(g.primary_page_kind, ''), 'unknown') AS effective_page_kind,
            g.page_kinds_json AS effective_page_kinds_json,
            g.segments_json AS effective_segments_json,
            g.embedding_text AS effective_embedding_text,
            g.static_analysis_json AS effective_static_analysis_json,
            g.output_hash AS effective_summary_hash,
            g.model AS v2_model,
            g.status AS v2_status,
            g.prompt_version AS v2_prompt_version,
            g.created_at AS v2_created_at
        FROM resumos r
        LEFT JOIN resumo_generations g
          ON g.documento = r.documento
         AND g.pagina_num = r.pagina_num
         AND g.is_current = 1
    """


__all__ = [
    "CUMULATIVE_SUMMARY_REVIEW_LIMIT",
    "CUMULATIVE_SUMMARY_SOFT_TARGET",
    "SUMMARY_SCHEMA_VERSION",
    "SUMMARY_PROMPT_VERSION",
    "TRANSLATION_PROMPT_VERSION",
    "StaticPageAnalysis",
    "SummaryCandidate",
    "analyze_page",
    "build_embedding_text",
    "build_summary_search_text",
    "canonical_labels_from_hints",
    "current_summary_select_sql",
    "extract_original_header",
    "generation_for_page",
    "generation_last_work_key",
    "get_or_create_run",
    "initial_active_work_key",
    "init_v2_schema",
    "invalidate_generation_range",
    "load_volume_index_hints",
    "make_run_key",
    "manifest_payload",
    "mark_run_blocked",
    "mark_run_complete",
    "normalize_match_text",
    "normalize_whitespace",
    "next_trusted_work_start",
    "open_indices_readonly",
    "page_number",
    "parse_summary_candidate",
    "promote_segment",
    "reconcile_candidate_work_keys",
    "resume_page_number",
    "sha256_text",
    "store_context_anchor",
    "store_generation",
    "translation_source_hash",
]

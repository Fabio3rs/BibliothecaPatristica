#!/usr/bin/env python3
"""Prepara IR V0/V1/V2 e gera embeddings OCR em banco experimental separado.

O texto da IR e dos chunks existe em memoria e, opcionalmente, em JSONL de
auditoria. O SQLite guarda somente metadados, hashes, origens e vetores.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ocr_embedding_ir import (
    IR_VERSION,
    SUPPORTED_VARIANTS,
    LogicalSegment,
    PreparedChunk,
    build_variant_chunks,
    extract_page_segments,
    repeated_running_fingerprints,
    stable_hash,
    write_ir_jsonl,
)
from tools.ocr_xml_utils import OcrPage, parse_ocr_xml_page
from scripts.playgrounds.embedding_playground import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    embed_documents,
)
from tools.generate_ocr_embeddings import SourceChoice, discover_source_pages, sha256_text
from tools.resumo_embedding_utils import floats_to_blob


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_ocr_embedding_experiment.db"
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "teste"
SCHEMA_VERSION = 2

VARIANT_DESCRIPTIONS = {
    "v0": "Todos os blocos preservados pelo filtro, concatenados por pagina.",
    "v1": "Somente corpo inferido, ainda concatenado por pagina.",
    "v2": "Corpo separado por script dentro de cada pagina.",
}

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    volumes_json TEXT NOT NULL,
    variants_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    model TEXT,
    processed_embeddings INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS variants (
    variant_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    ir_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volumes (
    volume_id TEXT PRIMARY KEY,
    source_dir TEXT NOT NULL,
    prepared_page_count INTEGER NOT NULL,
    segment_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
    page_num INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_candidates_json TEXT NOT NULL,
    work_label TEXT NOT NULL,
    is_xml INTEGER NOT NULL,
    parse_ok INTEGER NOT NULL,
    repairs_json TEXT NOT NULL DEFAULT '{}',
    page_state TEXT NOT NULL,
    page_type TEXT NOT NULL,
    segment_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(volume_id, page_num)
);

CREATE TABLE IF NOT EXISTS segments (
    segment_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    block_index INTEGER NOT NULL,
    part_index INTEGER NOT NULL,
    tag_name TEXT NOT NULL,
    xml_type TEXT NOT NULL,
    declared_script TEXT NOT NULL,
    inferred_script TEXT NOT NULL,
    script_confidence REAL NOT NULL,
    inferred_role TEXT NOT NULL,
    role_confidence REAL NOT NULL,
    bbox TEXT NOT NULL,
    section_label TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    normalized_hash TEXT NOT NULL,
    normalized_char_count INTEGER NOT NULL,
    included_v0 INTEGER NOT NULL,
    drop_reason TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    FOREIGN KEY(volume_id, page_num) REFERENCES pages(volume_id, page_num)
        ON DELETE CASCADE,
    UNIQUE(volume_id, page_num, block_index, part_index)
);
CREATE INDEX IF NOT EXISTS idx_ocr_ir_segments_page
    ON segments(volume_id, page_num, block_index, part_index);
CREATE INDEX IF NOT EXISTS idx_ocr_ir_segments_role_script
    ON segments(inferred_role, inferred_script);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_key TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
    volume_id TEXT NOT NULL,
    anchor_page_num INTEGER NOT NULL,
    unit_key TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    role TEXT NOT NULL,
    script TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    approx_token_count INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(volume_id, anchor_page_num) REFERENCES pages(volume_id, page_num)
        ON DELETE CASCADE,
    UNIQUE(variant_id, volume_id, unit_key, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_ocr_experiment_chunks_scope
    ON chunks(variant_id, volume_id, anchor_page_num);

CREATE TABLE IF NOT EXISTS chunk_sources (
    chunk_key TEXT NOT NULL REFERENCES chunks(chunk_key) ON DELETE CASCADE,
    segment_key TEXT NOT NULL REFERENCES segments(segment_key) ON DELETE CASCADE,
    source_order INTEGER NOT NULL,
    page_num INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    segment_char_start INTEGER NOT NULL,
    segment_char_end INTEGER NOT NULL,
    PRIMARY KEY(chunk_key, source_order)
);
CREATE INDEX IF NOT EXISTS idx_ocr_experiment_chunk_sources_segment
    ON chunk_sources(segment_key);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_key TEXT NOT NULL REFERENCES chunks(chunk_key) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(chunk_key, model)
);

CREATE TABLE IF NOT EXISTS page_embeddings (
    volume_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    component_unit_count INTEGER NOT NULL,
    component_chunk_count INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(volume_id, page_num, variant_id, model),
    FOREIGN KEY(volume_id, page_num) REFERENCES pages(volume_id, page_num)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ocr_page_embeddings_lookup
    ON page_embeddings(variant_id, model, volume_id, page_num);
"""


@dataclass(frozen=True)
class ParsedPage:
    choice: SourceChoice
    raw: str
    source_hash: str
    page: OcrPage


@dataclass(frozen=True)
class PreparedVolume:
    volume_id: str
    work_label: str
    pages: tuple[ParsedPage, ...]
    segments: tuple[LogicalSegment, ...]
    chunks: tuple[PreparedChunk, ...]
    repeated_fingerprints: frozenset[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.executescript(SCHEMA_SQL)
    page_columns = {row[1] for row in con.execute("PRAGMA table_info(pages)")}
    if "repairs_json" not in page_columns:
        con.execute(
            "ALTER TABLE pages ADD COLUMN repairs_json TEXT NOT NULL DEFAULT '{}'"
        )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('ir_version',?)",
        (IR_VERSION,),
    )
    con.commit()
    return con


def _selected_choices(
    source_root: Path,
    volume_id: str,
    *,
    page_start: int,
    page_end: int,
    limit_pages: int,
) -> list[SourceChoice]:
    choices = discover_source_pages(source_root, volume_id)
    if page_start > 0:
        choices = [choice for choice in choices if choice.page_num >= page_start]
    if page_end > 0:
        choices = [choice for choice in choices if choice.page_num <= page_end]
    if limit_pages > 0:
        choices = choices[:limit_pages]
    return choices


def prepare_volume(
    source_root: Path,
    volume_id: str,
    *,
    work_label: str,
    variants: Sequence[str],
    page_start: int,
    page_end: int,
    limit_pages: int,
    max_chunk_chars: int,
    overlap_chars: int,
    repeated_header_min_pages: int,
    repeated_header_min_ratio: float,
) -> PreparedVolume:
    choices = _selected_choices(
        source_root,
        volume_id,
        page_start=page_start,
        page_end=page_end,
        limit_pages=limit_pages,
    )
    parsed: list[ParsedPage] = []
    for choice in choices:
        raw = choice.path.read_text(encoding="utf-8", errors="replace")
        parsed.append(
            ParsedPage(
                choice=choice,
                raw=raw,
                source_hash=sha256_text(raw),
                page=parse_ocr_xml_page(raw, repair_non_xml_ampersands=True),
            )
        )
    repeated = repeated_running_fingerprints(
        [item.page for item in parsed],
        repeated_header_min_pages,
        repeated_header_min_ratio,
    )
    segments: list[LogicalSegment] = []
    for item in parsed:
        segments.extend(
            extract_page_segments(
                volume_id=volume_id,
                work_label=work_label,
                page_num=item.choice.page_num,
                source_file=item.choice.path.name,
                source_hash=item.source_hash,
                page=item.page,
                repeated_fingerprints=repeated,
            )
        )
    chunks = build_variant_chunks(
        segments,
        variants=variants,
        max_chars=max_chunk_chars,
        overlap_chars=overlap_chars,
    )
    return PreparedVolume(
        volume_id=volume_id,
        work_label=work_label,
        pages=tuple(parsed),
        segments=tuple(segments),
        chunks=tuple(chunks),
        repeated_fingerprints=frozenset(repeated),
    )


def volume_report(prepared: PreparedVolume) -> dict[str, object]:
    roles = Counter(segment.inferred_role for segment in prepared.segments)
    scripts = Counter(segment.inferred_script for segment in prepared.segments)
    chunks = Counter(chunk.variant_id for chunk in prepared.chunks)
    repair_pages = Counter(
        repair
        for item in prepared.pages
        for repair in item.page.repairs
    )
    repair_counts: Counter[str] = Counter()
    for item in prepared.pages:
        repair_counts.update(item.page.repairs)
    return {
        "volume_id": prepared.volume_id,
        "pages": len(prepared.pages),
        "xml_pages": sum(item.page.is_xml for item in prepared.pages),
        "malformed_xml_pages": sum(
            item.page.is_xml and not item.page.parse_ok for item in prepared.pages
        ),
        "repaired_xml_pages": sum(bool(item.page.repairs) for item in prepared.pages),
        "xml_repair_pages_by_kind": dict(sorted(repair_pages.items())),
        "xml_repair_counts": dict(sorted(repair_counts.items())),
        "segments": len(prepared.segments),
        "roles": dict(sorted(roles.items())),
        "scripts": dict(sorted(scripts.items())),
        "chunks": dict(sorted(chunks.items())),
        "shadowed_source_files": sum(
            len(item.choice.candidates) - 1 for item in prepared.pages
        ),
        "repeated_running_fingerprints": len(prepared.repeated_fingerprints),
    }


def _variant_config(args: argparse.Namespace, variant_id: str) -> dict[str, object]:
    return {
        "ir_version": IR_VERSION,
        "variant_id": variant_id,
        "max_chunk_chars": args.max_chunk_chars,
        "overlap_chars": args.overlap_chars,
        "repeated_header_min_pages": args.repeated_header_min_pages,
        "repeated_header_min_ratio": args.repeated_header_min_ratio,
        "document_instruct": False,
    }


def write_prepared(
    con: sqlite3.Connection,
    source_root: Path,
    prepared: PreparedVolume,
    *,
    variants: Sequence[str],
    args: argparse.Namespace,
) -> dict[str, int]:
    now = utc_now()
    variant_set = set(variants)
    prepared_chunks = [chunk for chunk in prepared.chunks if chunk.variant_id in variant_set]
    for variant_id in variants:
        config = _variant_config(args, variant_id)
        config_json = stable_json(config)
        config_hash = stable_hash(config_json)
        existing_variant = con.execute(
            "SELECT config_hash FROM variants WHERE variant_id=?", (variant_id,)
        ).fetchone()
        if (
            existing_variant is not None
            and str(existing_variant["config_hash"]) != config_hash
            and con.execute(
                "SELECT 1 FROM chunks WHERE variant_id=? LIMIT 1", (variant_id,)
            ).fetchone()
            is not None
        ):
            raise RuntimeError(
                f"A configuracao de {variant_id} difere da existente no DB; "
                "use outro --db para manter experimentos comparaveis."
            )
        con.execute(
            """INSERT INTO variants(
                   variant_id,description,ir_version,config_hash,config_json,updated_at
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(variant_id) DO UPDATE SET
                   description=excluded.description,
                   ir_version=excluded.ir_version,
                   config_hash=excluded.config_hash,
                   config_json=excluded.config_json,
                   updated_at=excluded.updated_at""",
            (
                variant_id,
                VARIANT_DESCRIPTIONS[variant_id],
                IR_VERSION,
                config_hash,
                config_json,
                now,
            ),
        )

    con.execute(
        """INSERT INTO volumes(
               volume_id,source_dir,prepared_page_count,segment_count,updated_at
           ) VALUES(?,?,?,?,?)
           ON CONFLICT(volume_id) DO UPDATE SET
               source_dir=excluded.source_dir,
               prepared_page_count=excluded.prepared_page_count,
               segment_count=excluded.segment_count,
               updated_at=excluded.updated_at""",
        (
            prepared.volume_id,
            str((source_root / prepared.volume_id / "text").resolve()),
            len(prepared.pages),
            len(prepared.segments),
            now,
        ),
    )

    segments_by_page: dict[int, list[LogicalSegment]] = defaultdict(list)
    for segment in prepared.segments:
        segments_by_page[segment.page_num].append(segment)
    changed_pages = 0
    invalidated_centroids: set[tuple[str, int, str | None]] = set()
    for item in prepared.pages:
        existing = con.execute(
            "SELECT source_hash FROM pages WHERE volume_id=? AND page_num=?",
            (prepared.volume_id, item.choice.page_num),
        ).fetchone()
        if existing is not None and str(existing["source_hash"]) != item.source_hash:
            con.execute(
                "DELETE FROM chunks WHERE volume_id=? AND anchor_page_num=?",
                (prepared.volume_id, item.choice.page_num),
            )
            con.execute(
                "DELETE FROM segments WHERE volume_id=? AND page_num=?",
                (prepared.volume_id, item.choice.page_num),
            )
            invalidated_centroids.add(
                (prepared.volume_id, item.choice.page_num, None)
            )
            changed_pages += 1
        con.execute(
            """INSERT INTO pages(
                   volume_id,page_num,source_file,source_hash,source_candidates_json,
                   work_label,is_xml,parse_ok,repairs_json,page_state,page_type,
                   segment_count,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(volume_id,page_num) DO UPDATE SET
                   source_file=excluded.source_file,
                   source_hash=excluded.source_hash,
                   source_candidates_json=excluded.source_candidates_json,
                   work_label=excluded.work_label,
                   is_xml=excluded.is_xml,
                   parse_ok=excluded.parse_ok,
                   repairs_json=excluded.repairs_json,
                   page_state=excluded.page_state,
                   page_type=excluded.page_type,
                   segment_count=excluded.segment_count,
                   updated_at=excluded.updated_at""",
            (
                prepared.volume_id,
                item.choice.page_num,
                item.choice.path.name,
                item.source_hash,
                stable_json([candidate.name for candidate in item.choice.candidates]),
                prepared.work_label,
                int(item.page.is_xml),
                int(item.page.parse_ok),
                stable_json(item.page.repairs),
                item.page.estado,
                item.page.tipo,
                len(segments_by_page[item.choice.page_num]),
                now,
            ),
        )

    for segment in prepared.segments:
        con.execute(
            """INSERT INTO segments(
                   segment_key,volume_id,page_num,block_index,part_index,tag_name,
                   xml_type,declared_script,inferred_script,script_confidence,
                   inferred_role,role_confidence,bbox,section_label,raw_hash,
                   normalized_hash,normalized_char_count,included_v0,drop_reason,
                   flags_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(segment_key) DO UPDATE SET
                   inferred_script=excluded.inferred_script,
                   script_confidence=excluded.script_confidence,
                   inferred_role=excluded.inferred_role,
                   role_confidence=excluded.role_confidence,
                   section_label=excluded.section_label,
                   normalized_hash=excluded.normalized_hash,
                   normalized_char_count=excluded.normalized_char_count,
                   included_v0=excluded.included_v0,
                   drop_reason=excluded.drop_reason,
                   flags_json=excluded.flags_json""",
            (
                segment.segment_key,
                segment.volume_id,
                segment.page_num,
                segment.block_index,
                segment.part_index,
                segment.tag_name,
                segment.xml_type,
                segment.declared_script,
                segment.inferred_script,
                segment.script_confidence,
                segment.inferred_role,
                segment.role_confidence,
                segment.bbox,
                segment.section,
                stable_hash(segment.raw_text),
                stable_hash(segment.normalized_text),
                len(segment.normalized_text),
                int(segment.included_v0),
                segment.drop_reason,
                stable_json(list(segment.flags)),
            ),
        )

    selected_pages = [item.choice.page_num for item in prepared.pages]
    expected_keys = {chunk.chunk_key for chunk in prepared_chunks}
    stale_chunks = 0
    if selected_pages:
        page_placeholders = ",".join("?" for _ in selected_pages)
        variant_placeholders = ",".join("?" for _ in variants)
        existing_chunks = con.execute(
            f"""SELECT chunk_key,variant_id,anchor_page_num FROM chunks
                WHERE volume_id=?
                  AND anchor_page_num IN ({page_placeholders})
                  AND variant_id IN ({variant_placeholders})""",
            [prepared.volume_id, *selected_pages, *variants],
        ).fetchall()
        for row in existing_chunks:
            key = str(row["chunk_key"])
            if key not in expected_keys:
                con.execute("DELETE FROM chunks WHERE chunk_key=?", (key,))
                invalidated_centroids.add(
                    (
                        prepared.volume_id,
                        int(row["anchor_page_num"]),
                        str(row["variant_id"]),
                    )
                )
                stale_chunks += 1

    new_chunks = 0
    for chunk in prepared_chunks:
        exists = con.execute(
            "SELECT 1 FROM chunks WHERE chunk_key=?", (chunk.chunk_key,)
        ).fetchone()
        con.execute(
            """INSERT INTO chunks(
                   chunk_key,variant_id,volume_id,anchor_page_num,unit_key,
                   stream_id,role,script,chunk_index,char_count,
                   approx_token_count,text_hash,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chunk_key) DO UPDATE SET
                   stream_id=excluded.stream_id,
                   role=excluded.role,
                   script=excluded.script,
                   char_count=excluded.char_count,
                   approx_token_count=excluded.approx_token_count,
                   text_hash=excluded.text_hash""",
            (
                chunk.chunk_key,
                chunk.variant_id,
                chunk.volume_id,
                chunk.anchor_page_num,
                chunk.unit_key,
                chunk.stream_id,
                chunk.role,
                chunk.script,
                chunk.chunk_index,
                len(chunk.text),
                max(1, math.ceil(len(chunk.text) / 4)),
                chunk.text_hash,
                now,
            ),
        )
        if exists is None:
            new_chunks += 1
            invalidated_centroids.add(
                (chunk.volume_id, chunk.anchor_page_num, chunk.variant_id)
            )
        con.execute("DELETE FROM chunk_sources WHERE chunk_key=?", (chunk.chunk_key,))
        con.executemany(
            """INSERT INTO chunk_sources(
                   chunk_key,segment_key,source_order,page_num,source_file,
                   segment_char_start,segment_char_end
               ) VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    chunk.chunk_key,
                    source.segment_key,
                    source.source_order,
                    source.page_num,
                    source.source_file,
                    source.segment_char_start,
                    source.segment_char_end,
                )
                for source in chunk.sources
            ],
        )
    for volume_id, page_num, variant_id in invalidated_centroids:
        if variant_id is None:
            con.execute(
                "DELETE FROM page_embeddings WHERE volume_id=? AND page_num=?",
                (volume_id, page_num),
            )
        else:
            con.execute(
                """DELETE FROM page_embeddings
                   WHERE volume_id=? AND page_num=? AND variant_id=?""",
                (volume_id, page_num, variant_id),
            )
    return {
        "changed_source_pages": changed_pages,
        "new_chunks": new_chunks,
        "stale_chunks_removed": stale_chunks,
        "centroid_scopes_invalidated": len(invalidated_centroids),
    }


def embedding_input_hash(model: str, chunk: PreparedChunk) -> str:
    return stable_hash(model, chunk.variant_id, chunk.text)


def embed_chunks(
    con: sqlite3.Connection,
    chunks: Sequence[PreparedChunk],
    *,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit_chunks: int,
) -> tuple[int, int]:
    pending: list[tuple[PreparedChunk, str]] = []
    for chunk in chunks:
        expected = embedding_input_hash(model, chunk)
        row = con.execute(
            "SELECT input_hash FROM embeddings WHERE chunk_key=? AND model=?",
            (chunk.chunk_key, model),
        ).fetchone()
        if row is None or str(row["input_hash"]) != expected:
            pending.append((chunk, expected))
    if limit_chunks > 0:
        pending = pending[:limit_chunks]

    processed = 0
    batches = 0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        vectors = embed_documents(
            [chunk.text for chunk, _expected in batch],
            model=model,
            ollama_url=ollama_url,
        )
        if len(vectors) != len(batch):
            raise RuntimeError(f"Embedding count mismatch: {len(vectors)} != {len(batch)}")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or not dimensions or next(iter(dimensions)) <= 0:
            raise RuntimeError(f"Dimensoes inconsistentes: {sorted(dimensions)}")
        now = utc_now()
        for (chunk, expected), vector in zip(batch, vectors):
            array = np.asarray(vector, dtype=np.float32)
            if not np.isfinite(array).all() or float(np.linalg.norm(array)) == 0.0:
                raise RuntimeError(f"Embedding invalido para {chunk.chunk_key}")
            con.execute(
                """INSERT INTO embeddings(
                       chunk_key,model,embedding_dim,embedding,input_hash,
                       created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(chunk_key,model) DO UPDATE SET
                       embedding_dim=excluded.embedding_dim,
                       embedding=excluded.embedding,
                       input_hash=excluded.input_hash,
                       updated_at=excluded.updated_at""",
                (
                    chunk.chunk_key,
                    model,
                    len(array),
                    floats_to_blob(array),
                    expected,
                    now,
                    now,
                ),
            )
        con.commit()
        processed += len(batch)
        batches += 1
        print(f"[embed] batch={batches} chunks={processed}")
    return processed, batches


def _unit_centroid(vectors: Sequence[np.ndarray]) -> np.ndarray:
    normalized = []
    for vector in vectors:
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            normalized.append(vector / norm)
    if not normalized:
        raise ValueError("Nao ha vetores validos para centroide")
    centroid = np.mean(np.stack(normalized), axis=0, dtype=np.float32)
    norm = float(np.linalg.norm(centroid))
    if norm == 0.0:
        raise ValueError("Centroide nulo")
    return np.asarray(centroid / norm, dtype=np.float32)


def rebuild_page_embeddings(
    con: sqlite3.Connection,
    chunks: Sequence[PreparedChunk],
    *,
    model: str,
) -> tuple[int, int]:
    grouped: dict[tuple[str, int, str], list[PreparedChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[(chunk.volume_id, chunk.anchor_page_num, chunk.variant_id)].append(chunk)
    written = 0
    incomplete = 0
    for (volume_id, page_num, variant_id), page_chunks in grouped.items():
        vectors_by_unit: dict[str, list[np.ndarray]] = defaultdict(list)
        input_hashes: list[str] = []
        dimension = 0
        complete = True
        for chunk in page_chunks:
            row = con.execute(
                """SELECT embedding_dim,embedding,input_hash FROM embeddings
                   WHERE chunk_key=? AND model=?""",
                (chunk.chunk_key, model),
            ).fetchone()
            if row is None:
                complete = False
                break
            dimension = int(row["embedding_dim"])
            vector = np.frombuffer(row["embedding"], dtype=np.float32, count=dimension).copy()
            vectors_by_unit[chunk.unit_key].append(vector)
            input_hashes.append(str(row["input_hash"]))
        if not complete:
            con.execute(
                """DELETE FROM page_embeddings
                   WHERE volume_id=? AND page_num=? AND variant_id=? AND model=?""",
                (volume_id, page_num, variant_id, model),
            )
            incomplete += 1
            continue
        unit_vectors = [_unit_centroid(unit) for unit in vectors_by_unit.values()]
        page_vector = _unit_centroid(unit_vectors)
        input_hash = stable_hash(
            model,
            variant_id,
            sorted(input_hashes),
            sorted(vectors_by_unit),
            "equal-unit-weight-v1",
        )
        con.execute(
            """INSERT INTO page_embeddings(
                   volume_id,page_num,variant_id,model,embedding_dim,embedding,
                   component_unit_count,component_chunk_count,input_hash,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(volume_id,page_num,variant_id,model) DO UPDATE SET
                   embedding_dim=excluded.embedding_dim,
                   embedding=excluded.embedding,
                   component_unit_count=excluded.component_unit_count,
                   component_chunk_count=excluded.component_chunk_count,
                   input_hash=excluded.input_hash,
                   updated_at=excluded.updated_at""",
            (
                volume_id,
                page_num,
                variant_id,
                model,
                dimension,
                floats_to_blob(page_vector),
                len(unit_vectors),
                len(page_chunks),
                input_hash,
                utc_now(),
            ),
        )
        written += 1
    con.commit()
    return written, incomplete


def print_report(report: dict[str, object], *, verbose: bool) -> None:
    print(
        f"{report['volume_id']} pages={report['pages']} xml={report['xml_pages']} "
        f"malformed={report['malformed_xml_pages']} "
        f"repaired={report['repaired_xml_pages']} segments={report['segments']} "
        f"chunks={stable_json(report['chunks'])} shadowed={report['shadowed_source_files']}"
    )
    if verbose:
        print(
            "  xml_repair_pages_by_kind="
            f"{stable_json(report['xml_repair_pages_by_kind'])}"
        )
        print(f"  xml_repair_counts={stable_json(report['xml_repair_counts'])}")
        print(f"  roles={stable_json(report['roles'])}")
        print(f"  scripts={stable_json(report['scripts'])}")
        print(
            "  repeated_running_fingerprints="
            f"{report['repeated_running_fingerprints']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volumes", nargs="+", required=True)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=SUPPORTED_VARIANTS,
        default=list(SUPPORTED_VARIANTS),
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stage", choices=("audit", "prepare", "embed"), default="audit")
    parser.add_argument("--ir-dir", type=Path, help="Exporta uma IR JSONL auditavel por volume.")
    parser.add_argument("--work-label", default="")
    parser.add_argument("--page-start", type=int, default=0)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--limit-pages", type=int, default=0)
    parser.add_argument("--max-chunk-chars", type=int, default=3200)
    parser.add_argument("--overlap-chars", type=int, default=320)
    parser.add_argument("--repeated-header-min-pages", type=int, default=5)
    parser.add_argument("--repeated-header-min-ratio", type=float, default=0.01)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit-chunks", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    args.volumes = list(dict.fromkeys(item.strip() for item in args.volumes if item.strip()))
    args.variants = list(dict.fromkeys(args.variants))
    if not args.volumes:
        raise SystemExit("Informe ao menos um volume")
    if args.page_start < 0 or args.page_end < 0:
        raise SystemExit("Limites de pagina nao podem ser negativos")
    if args.page_end and args.page_start and args.page_end < args.page_start:
        raise SystemExit("--page-end deve ser >= --page-start")
    if args.limit_pages < 0 or args.limit_chunks < 0:
        raise SystemExit("Limites nao podem ser negativos")
    if args.max_chunk_chars < 200:
        raise SystemExit("--max-chunk-chars deve ser >= 200")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_chunk_chars:
        raise SystemExit("--overlap-chars deve ser menor que --max-chunk-chars")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size deve ser positivo")


def main() -> None:
    args = build_parser().parse_args()
    validate_args(args)
    prepared_volumes: list[PreparedVolume] = []
    for volume_id in args.volumes:
        prepared = prepare_volume(
            args.source_root,
            volume_id,
            work_label=args.work_label,
            variants=args.variants,
            page_start=args.page_start,
            page_end=args.page_end,
            limit_pages=args.limit_pages,
            max_chunk_chars=args.max_chunk_chars,
            overlap_chars=args.overlap_chars,
            repeated_header_min_pages=args.repeated_header_min_pages,
            repeated_header_min_ratio=args.repeated_header_min_ratio,
        )
        prepared_volumes.append(prepared)
        print_report(volume_report(prepared), verbose=args.verbose)
        if args.ir_dir:
            path = args.ir_dir / f"{volume_id}.logical_segments.jsonl"
            count = write_ir_jsonl(path, prepared.segments)
            print(f"[ir] {path}: segments={count}")
    if args.stage == "audit":
        print("audit: nenhum DB foi aberto e nenhum embedding foi solicitado")
        return

    config = {
        "ir_version": IR_VERSION,
        "volumes": args.volumes,
        "variants": args.variants,
        "page_start": args.page_start,
        "page_end": args.page_end,
        "limit_pages": args.limit_pages,
        "max_chunk_chars": args.max_chunk_chars,
        "overlap_chars": args.overlap_chars,
        "work_label": args.work_label,
    }
    with connect_db(args.db) as con:
        started = utc_now()
        run_id = con.execute(
            """INSERT INTO runs(
                   stage,status,volumes_json,variants_json,config_json,model,started_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                args.stage,
                "running",
                stable_json(args.volumes),
                stable_json(args.variants),
                stable_json(config),
                args.model if args.stage == "embed" else None,
                started,
            ),
        ).lastrowid
        try:
            for prepared in prepared_volumes:
                result = write_prepared(
                    con,
                    args.source_root,
                    prepared,
                    variants=args.variants,
                    args=args,
                )
                print(f"[prepare] {prepared.volume_id} {stable_json(result)}")
            con.commit()
            processed = 0
            if args.stage == "embed":
                all_chunks = [
                    chunk for prepared in prepared_volumes for chunk in prepared.chunks
                ]
                processed, batches = embed_chunks(
                    con,
                    all_chunks,
                    model=args.model,
                    ollama_url=args.ollama_url,
                    batch_size=args.batch_size,
                    limit_chunks=args.limit_chunks,
                )
                pages_written, pages_incomplete = rebuild_page_embeddings(
                    con, all_chunks, model=args.model
                )
                print(
                    f"[centroids] written={pages_written} incomplete={pages_incomplete} "
                    f"batches={batches}"
                )
            con.execute(
                """UPDATE runs SET status='completed',processed_embeddings=?,finished_at=?
                   WHERE run_id=?""",
                (processed, utc_now(), run_id),
            )
            con.commit()
        except Exception as exc:
            con.execute(
                """UPDATE runs SET status='failed',finished_at=?,error_text=?
                   WHERE run_id=?""",
                (utc_now(), str(exc), run_id),
            )
            con.commit()
            raise
    print(f"concluido: db={args.db}")


if __name__ == "__main__":
    main()

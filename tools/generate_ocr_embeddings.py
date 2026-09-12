#!/usr/bin/env python3
"""Prepara OCR filtrado e gera embeddings em um SQLite independente.

O fluxo tem duas operacoes explicitas:

1. ``audit`` le somente os volumes informados, escolhe um arquivo por pagina,
   filtra ruido em memoria e apenas imprime contagens.
2. ``embed`` repete essa preparacao em memoria, envia chunks pendentes ao
   endpoint ``/api/embed`` do Ollama e grava somente vetores, locators e hashes.

O banco de resumos nunca e alterado. O banco padrao deste piloto e
``data/patristica_ocr_embeddings.db``.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.corpus_utils import page_number
from tools.ocr_xml_utils import (
    OcrBlock,
    OcrPage,
    join_linebreak_hyphenation,
    parse_ocr_xml_page,
)
from scripts.limpeza_ocr import strip_google_boilerplate
from scripts.playgrounds.embedding_playground import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    embed_documents,
)
from tools.resumo_embedding_utils import floats_to_blob


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_ocr_embeddings.db"
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "teste"
FILTER_VERSION = "ocr-embedding-clean-v1"

SPACE_RE = re.compile(r"\s+")
DIGITS_RE = re.compile(r"\d+")
NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)
ONLY_LOCATOR_RE = re.compile(
    r"^(?:p(?:ag(?:ina)?)?\.?\s*)?(?:\d+|[ivxlcdm]+)(?:\s*[-–—]\s*(?:\d+|[ivxlcdm]+))?$",
    re.IGNORECASE,
)
MARGINAL_MARKER_RE = re.compile(r"^(?:[A-ZΑ-Ω]\s*){1,4}$", re.UNICODE)
SCAN_NOISE_RE = re.compile(
    r"(?:digitized\s+by\s+google|google(?:tm)?\s+books?|books\.google\.com|"
    r"распознавание\s+текста|правила\s+использования|"
    r"tipo\s+de\s+p[aá]gina\s*:)",
    re.IGNORECASE,
)
STAMP_RE = re.compile(r"^\[(?:selo|sello|stamp)\b", re.IGNORECASE)
ARGUMENTATIVE_HEADER_RE = re.compile(
    r"\b(?:responsio|obiectio|objectio|quaestio|qu[aæ]stio|argumentum|"
    r"solutio|conclusio|distinctio|demonstratio|ἀπόκρισις|ἀποκρισις|"
    r"ἐρώτησις|ἐρωτησις)\b",
    re.IGNORECASE,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volumes (
    volume_id TEXT PRIMARY KEY,
    source_dir TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    ready_page_count INTEGER NOT NULL,
    dropped_page_count INTEGER NOT NULL,
    shadowed_source_files INTEGER NOT NULL,
    repeated_headers_json TEXT NOT NULL,
    filter_version TEXT NOT NULL,
    chunk_config_json TEXT NOT NULL,
    prepared_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
    page_num INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_candidates_json TEXT NOT NULL,
    is_xml INTEGER NOT NULL,
    parse_ok INTEGER NOT NULL,
    page_state TEXT NOT NULL,
    page_type TEXT NOT NULL,
    raw_char_count INTEGER NOT NULL,
    clean_char_count INTEGER NOT NULL,
    clean_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready', 'dropped')),
    drop_reason TEXT NOT NULL,
    removed_blocks_json TEXT NOT NULL,
    filter_version TEXT NOT NULL,
    chunk_config_hash TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    UNIQUE(volume_id, page_num)
);
CREATE INDEX IF NOT EXISTS idx_ocr_pages_volume_status
    ON pages(volume_id, status, page_num);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    approx_token_count INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(page_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_ocr_chunks_page ON chunks(page_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_ocr_chunks_hash ON chunks(text_hash);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chunk_id, model)
);
CREATE INDEX IF NOT EXISTS idx_ocr_embeddings_model
    ON embeddings(model, chunk_id);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    volumes_json TEXT NOT NULL,
    model TEXT,
    filter_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    processed_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_text TEXT
);
"""


@dataclass(frozen=True)
class SourceChoice:
    page_num: int
    path: Path
    candidates: tuple[Path, ...]


@dataclass(frozen=True)
class ParsedSource:
    choice: SourceChoice
    raw: str
    source_hash: str
    page: OcrPage


@dataclass(frozen=True)
class PreparedPage:
    choice: SourceChoice
    source_hash: str
    page: OcrPage
    raw_char_count: int
    clean_text: str
    clean_hash: str
    status: str
    drop_reason: str
    removed_blocks: dict[str, int]
    chunks: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','1')"
    )
    con.commit()
    return con


def discover_source_pages(source_root: Path, volume_id: str) -> list[SourceChoice]:
    text_dir = source_root / volume_id / "text"
    if not text_dir.is_dir():
        raise FileNotFoundError(f"Diretorio de OCR ausente: {text_dir}")

    grouped: dict[int, list[Path]] = defaultdict(list)
    for path in text_dir.glob("*.txt"):
        if path.name == "texto_extraido.txt":
            continue
        pnum = page_number(path)
        if pnum is None or pnum <= 0:
            continue
        grouped[int(pnum)].append(path)

    choices: list[SourceChoice] = []
    for pnum, candidates in sorted(grouped.items()):
        candidates = sorted(candidates, key=lambda item: item.name)
        canonical_name = f"{volume_id}-{pnum:03d}.txt".casefold()
        canonical = [p for p in candidates if p.name.casefold() == canonical_name]
        if len(canonical) == 1:
            chosen = canonical[0]
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            names = ", ".join(p.name for p in candidates)
            raise RuntimeError(
                f"{volume_id}:{pnum} tem fontes ambiguas e nenhuma canonica: {names}"
            )
        choices.append(SourceChoice(pnum, chosen, tuple(candidates)))
    return choices


def parse_sources(choices: Sequence[SourceChoice]) -> list[ParsedSource]:
    parsed: list[ParsedSource] = []
    for choice in choices:
        raw = choice.path.read_text(encoding="utf-8", errors="replace")
        parsed.append(
            ParsedSource(
                choice=choice,
                raw=raw,
                source_hash=sha256_text(raw),
                page=parse_ocr_xml_page(raw),
            )
        )
    return parsed


def normalize_embedding_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = join_linebreak_hyphenation(text)
    text = strip_google_boilerplate(text)
    text = text.replace("\x00", " ")
    return SPACE_RE.sub(" ", text).strip()


def header_fingerprint(text: str) -> str:
    text = normalize_embedding_text(text).casefold()
    text = DIGITS_RE.sub("#", text)
    return NON_WORD_RE.sub(" ", text).strip()


def is_header(block: OcrBlock) -> bool:
    return block.tag_name == "cabecalho" or block.tipo == "cabecalho"


def is_footer(block: OcrBlock) -> bool:
    return block.tag_name == "rodape" or block.tipo == "rodape"


def repeated_header_fingerprints(
    pages: Sequence[ParsedSource], min_pages: int, min_ratio: float
) -> tuple[set[str], Counter[str]]:
    occurrences: Counter[str] = Counter()
    for source in pages:
        seen: set[str] = set()
        for block in source.page.blocks:
            text = normalize_embedding_text(block.content_clean)
            if not (is_header(block) or is_footer(block)) or len(text) > 240:
                continue
            fp = header_fingerprint(text)
            if fp:
                seen.add(fp)
        occurrences.update(seen)

    threshold = max(min_pages, math.ceil(len(pages) * min_ratio))
    repeated = {fp for fp, count in occurrences.items() if count >= threshold}
    return repeated, occurrences


def split_text_windows(text: str, max_chars: int, overlap_chars: int) -> tuple[str, ...]:
    text = text.strip()
    if not text:
        return ()
    if len(text) <= max_chars:
        return (text,)

    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        end = hard_end
        if hard_end < len(text):
            boundary = text.rfind(" ", start + max_chars // 2, hard_end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(0, end - overlap_chars)
        if next_start > 0:
            boundary = text.find(" ", next_start, end)
            if boundary >= 0:
                next_start = boundary + 1
        if next_start <= start:
            next_start = end
        start = next_start
    return tuple(chunks)


def prepare_page(
    source: ParsedSource,
    repeated_headers: set[str],
    min_page_chars: int,
    max_chunk_chars: int,
    overlap_chars: int,
) -> PreparedPage:
    page = source.page
    removed: Counter[str] = Counter()
    kept: list[str] = []
    root_notes = len(re.findall(r"<notas\b", source.raw, flags=re.IGNORECASE))
    if root_notes:
        removed["root_notes"] += root_notes

    if page.estado == "vazio":
        return PreparedPage(
            choice=source.choice,
            source_hash=source.source_hash,
            page=page,
            raw_char_count=len(source.raw),
            clean_text="",
            clean_hash=sha256_text(""),
            status="dropped",
            drop_reason="xml_pagina_vazia",
            removed_blocks={"empty_page": 1},
            chunks=(),
        )

    for block in page.blocks:
        text = normalize_embedding_text(block.content_clean)
        if not text:
            removed["empty_or_google"] += 1
            continue
        if SCAN_NOISE_RE.search(text) or STAMP_RE.search(text):
            removed["scan_boilerplate"] += 1
            continue
        if (
            (is_header(block) or is_footer(block))
            and len(text) <= 240
            and header_fingerprint(text) in repeated_headers
            and not ARGUMENTATIVE_HEADER_RE.search(text)
        ):
            removed["repeated_running_text"] += 1
            continue
        if ONLY_LOCATOR_RE.fullmatch(text) or MARGINAL_MARKER_RE.fullmatch(text):
            removed["isolated_locator"] += 1
            continue
        kept.append(text)

    clean_text = "\n\n".join(kept).strip()
    alpha_count = sum(ch.isalpha() for ch in clean_text)
    token_count = len(re.findall(r"\b\w+\b", clean_text, flags=re.UNICODE))
    if not clean_text:
        status, reason = "dropped", "empty_after_filter"
    elif len(clean_text) < min_page_chars or alpha_count < 40 or token_count < 15:
        status, reason = "dropped", "too_short_after_filter"
    else:
        status, reason = "ready", ""

    chunks = (
        split_text_windows(clean_text, max_chunk_chars, overlap_chars)
        if status == "ready"
        else ()
    )
    return PreparedPage(
        choice=source.choice,
        source_hash=source.source_hash,
        page=page,
        raw_char_count=len(source.raw),
        clean_text=clean_text,
        clean_hash=sha256_text(clean_text),
        status=status,
        drop_reason=reason,
        removed_blocks=dict(removed),
        chunks=chunks,
    )


def prepare_volume(
    source_root: Path,
    volume_id: str,
    *,
    min_page_chars: int,
    max_chunk_chars: int,
    overlap_chars: int,
    repeated_header_min_pages: int,
    repeated_header_min_ratio: float,
    limit_pages: int,
) -> tuple[list[PreparedPage], dict[str, object]]:
    choices = discover_source_pages(source_root, volume_id)
    if limit_pages > 0:
        choices = choices[:limit_pages]
    parsed = parse_sources(choices)
    repeated, occurrences = repeated_header_fingerprints(
        parsed, repeated_header_min_pages, repeated_header_min_ratio
    )
    prepared = [
        prepare_page(
            source,
            repeated,
            min_page_chars,
            max_chunk_chars,
            overlap_chars,
        )
        for source in parsed
    ]

    removed = Counter()
    for page in prepared:
        removed.update(page.removed_blocks)
    repeated_report = sorted(
        (
            {"fingerprint": fp, "pages": occurrences[fp]}
            for fp in repeated
        ),
        key=lambda item: (-int(item["pages"]), str(item["fingerprint"])),
    )
    report: dict[str, object] = {
        "volume_id": volume_id,
        "pages": len(prepared),
        "xml_pages": sum(page.page.is_xml for page in prepared),
        "plain_pages": sum(not page.page.is_xml for page in prepared),
        "malformed_xml_pages": sum(
            page.page.is_xml and not page.page.parse_ok for page in prepared
        ),
        "ready_pages": sum(page.status == "ready" for page in prepared),
        "dropped_pages": sum(page.status == "dropped" for page in prepared),
        "chunks": sum(len(page.chunks) for page in prepared),
        "raw_chars": sum(page.raw_char_count for page in prepared),
        "clean_chars": sum(len(page.clean_text) for page in prepared),
        "shadowed_source_files": sum(len(page.choice.candidates) - 1 for page in prepared),
        "removed_blocks": dict(removed),
        "repeated_headers": repeated_report,
    }
    return prepared, report


def chunk_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "max_chunk_chars": args.max_chunk_chars,
        "overlap_chars": args.overlap_chars,
        "min_page_chars": args.min_page_chars,
        "repeated_header_min_pages": args.repeated_header_min_pages,
        "repeated_header_min_ratio": args.repeated_header_min_ratio,
    }


def write_prepared_volume(
    con: sqlite3.Connection,
    source_root: Path,
    volume_id: str,
    prepared: Sequence[PreparedPage],
    report: dict[str, object],
    config: dict[str, object],
    *,
    prune_missing: bool = True,
) -> int:
    now = utc_now()
    config_json = stable_json(config)
    config_hash = sha256_text(config_json)
    repeated_json = stable_json(report["repeated_headers"])
    con.execute(
        """
        INSERT INTO volumes(
            volume_id,source_dir,page_count,ready_page_count,dropped_page_count,
            shadowed_source_files,repeated_headers_json,filter_version,
            chunk_config_json,prepared_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(volume_id) DO UPDATE SET
            source_dir=excluded.source_dir,
            page_count=excluded.page_count,
            ready_page_count=excluded.ready_page_count,
            dropped_page_count=excluded.dropped_page_count,
            shadowed_source_files=excluded.shadowed_source_files,
            repeated_headers_json=excluded.repeated_headers_json,
            filter_version=excluded.filter_version,
            chunk_config_json=excluded.chunk_config_json,
            prepared_at=excluded.prepared_at
        """,
        (
            volume_id,
            str((source_root / volume_id / "text").resolve()),
            report["pages"],
            report["ready_pages"],
            report["dropped_pages"],
            report["shadowed_source_files"],
            repeated_json,
            FILTER_VERSION,
            config_json,
            now,
        ),
    )

    changed = 0
    if prune_missing:
        live_pages = {page.choice.page_num for page in prepared}
        for stale in con.execute(
            "SELECT id,page_num FROM pages WHERE volume_id=?", (volume_id,)
        ).fetchall():
            if int(stale["page_num"]) not in live_pages:
                con.execute("DELETE FROM pages WHERE id=?", (stale["id"],))
                changed += 1

    for page in prepared:
        candidate_names = [candidate.name for candidate in page.choice.candidates]
        values = (
            volume_id,
            page.choice.page_num,
            page.choice.path.name,
            page.source_hash,
            stable_json(candidate_names),
            int(page.page.is_xml),
            int(page.page.parse_ok),
            page.page.estado,
            page.page.tipo,
            page.raw_char_count,
            len(page.clean_text),
            page.clean_hash,
            page.status,
            page.drop_reason,
            stable_json(page.removed_blocks),
            FILTER_VERSION,
            config_hash,
            now,
        )
        existing = con.execute(
            "SELECT id FROM pages WHERE volume_id=? AND page_num=?",
            (volume_id, page.choice.page_num),
        ).fetchone()
        con.execute(
            """
            INSERT INTO pages(
                volume_id,page_num,source_file,source_hash,source_candidates_json,
                is_xml,parse_ok,page_state,page_type,raw_char_count,clean_char_count,
                clean_hash,status,drop_reason,removed_blocks_json,
                filter_version,chunk_config_hash,prepared_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(volume_id,page_num) DO UPDATE SET
                source_file=excluded.source_file,
                source_hash=excluded.source_hash,
                source_candidates_json=excluded.source_candidates_json,
                is_xml=excluded.is_xml,
                parse_ok=excluded.parse_ok,
                page_state=excluded.page_state,
                page_type=excluded.page_type,
                raw_char_count=excluded.raw_char_count,
                clean_char_count=excluded.clean_char_count,
                clean_hash=excluded.clean_hash,
                status=excluded.status,
                drop_reason=excluded.drop_reason,
                removed_blocks_json=excluded.removed_blocks_json,
                filter_version=excluded.filter_version,
                chunk_config_hash=excluded.chunk_config_hash,
                prepared_at=excluded.prepared_at
            """,
            values,
        )
        page_id = (
            int(existing["id"])
            if existing is not None
            else int(
                con.execute(
                    "SELECT id FROM pages WHERE volume_id=? AND page_num=?",
                    (volume_id, page.choice.page_num),
                ).fetchone()["id"]
            )
        )
        expected_hashes = [sha256_text(chunk) for chunk in page.chunks]
        stored_hashes = [
            str(row["text_hash"])
            for row in con.execute(
                "SELECT text_hash FROM chunks WHERE page_id=? ORDER BY chunk_index",
                (page_id,),
            )
        ]
        if stored_hashes == expected_hashes:
            continue
        con.execute("DELETE FROM chunks WHERE page_id=?", (page_id,))
        con.executemany(
            """
            INSERT INTO chunks(
                page_id,chunk_index,char_count,approx_token_count,text_hash,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            [
                (
                    page_id,
                    index,
                    len(chunk),
                    max(1, math.ceil(len(chunk) / 4)),
                    expected_hashes[index],
                    now,
                )
                for index, chunk in enumerate(page.chunks)
            ],
        )
        changed += 1
    return changed


def embedding_input_hash(model: str, text: str) -> str:
    return sha256_text(f"{model}\0{text}")


def embed_prepared(
    con: sqlite3.Connection,
    prepared_by_volume: dict[str, list[PreparedPage]],
    *,
    model: str,
    ollama_url: str,
    batch_size: int,
    limit_chunks: int,
) -> tuple[int, int]:
    """Gera vetores a partir dos chunks mantidos apenas em memoria."""
    pending: list[tuple[int, str, str]] = []
    for volume_id, pages in prepared_by_volume.items():
        for page in pages:
            if not page.chunks:
                continue
            page_row = con.execute(
                "SELECT id FROM pages WHERE volume_id=? AND page_num=?",
                (volume_id, page.choice.page_num),
            ).fetchone()
            if page_row is None:
                raise RuntimeError(
                    f"Pagina preparada ausente no DB: {volume_id}:{page.choice.page_num}"
                )
            rows = con.execute(
                """
                SELECT c.id,c.chunk_index,e.input_hash
                FROM chunks c
                LEFT JOIN embeddings e ON e.chunk_id=c.id AND e.model=?
                WHERE c.page_id=?
                ORDER BY c.chunk_index
                """,
                (model, page_row["id"]),
            ).fetchall()
            if len(rows) != len(page.chunks):
                raise RuntimeError(
                    f"Chunks inconsistentes no DB: {volume_id}:{page.choice.page_num}"
                )
            for row, text in zip(rows, page.chunks):
                expected = embedding_input_hash(model, text)
                if row["input_hash"] != expected:
                    pending.append((int(row["id"]), text, expected))

    if limit_chunks > 0:
        pending = pending[:limit_chunks]

    processed = 0
    batches = 0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        texts = [text for _chunk_id, text, _expected in batch]
        vectors = embed_documents(texts, model=model, ollama_url=ollama_url)
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"Embedding count mismatch: {len(vectors)} != {len(batch)}"
            )
        dims = {len(vector) for vector in vectors}
        if len(dims) != 1 or not dims or next(iter(dims)) <= 0:
            raise RuntimeError(f"Dimensoes inconsistentes no batch: {sorted(dims)}")
        now = utc_now()
        for (chunk_id, _text, expected_hash), vector in zip(batch, vectors):
            array = np.asarray(vector, dtype=np.float32)
            if not np.isfinite(array).all():
                raise RuntimeError(f"Embedding nao finito para chunk {chunk_id}")
            con.execute(
                """
                INSERT INTO embeddings(
                    chunk_id,model,embedding_dim,embedding,input_hash,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(chunk_id,model) DO UPDATE SET
                    embedding_dim=excluded.embedding_dim,
                    embedding=excluded.embedding,
                    input_hash=excluded.input_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    chunk_id,
                    model,
                    len(array),
                    floats_to_blob(array),
                    expected_hash,
                    now,
                    now,
                ),
            )
        con.commit()
        processed += len(batch)
        batches += 1
        print(f"[embed] batch={batches} chunks={processed} last_chunk={batch[-1][0]}")
    return processed, batches


def print_reports(reports: Sequence[dict[str, object]], *, verbose: bool = False) -> None:
    print(
        "volume pages xml malformed ready dropped chunks shadowed raw_chars "
        "clean_chars repeated_headers"
    )
    for report in reports:
        print(
            report["volume_id"],
            report["pages"],
            report["xml_pages"],
            report["malformed_xml_pages"],
            report["ready_pages"],
            report["dropped_pages"],
            report["chunks"],
            report["shadowed_source_files"],
            report["raw_chars"],
            report["clean_chars"],
            len(report["repeated_headers"]),
        )
        if verbose:
            print("  removed_blocks", stable_json(report["removed_blocks"]))
            for item in report["repeated_headers"][:10]:
                print(
                    f"  repeated pages={item['pages']} "
                    f"text={item['fingerprint'][:140]}"
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--volumes",
        nargs="+",
        required=True,
        help="Volumes explicitos; nao existe modo corpus inteiro neste piloto.",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--stage",
        choices=["audit", "embed"],
        default="audit",
        help="audit nao abre DB nem chama Ollama; embed prepara em memoria e salva vetores.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nao grava DB nem chama Ollama.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit-pages", type=int, default=0, help="Limite por volume.")
    parser.add_argument("--limit-chunks", type=int, default=0, help="Limite total no stage embed.")
    parser.add_argument("--min-page-chars", type=int, default=100)
    parser.add_argument("--max-chunk-chars", type=int, default=3200)
    parser.add_argument("--overlap-chars", type=int, default=320)
    parser.add_argument("--repeated-header-min-pages", type=int, default=5)
    parser.add_argument("--repeated-header-min-ratio", type=float, default=0.01)
    parser.add_argument("--verbose", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    args.volumes = list(dict.fromkeys(volume.strip() for volume in args.volumes if volume.strip()))
    if not args.volumes:
        raise SystemExit("Informe ao menos um volume em --volumes")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size deve ser positivo")
    if args.max_chunk_chars < 200:
        raise SystemExit("--max-chunk-chars deve ser >= 200")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_chunk_chars:
        raise SystemExit("--overlap-chars deve estar entre 0 e max-chunk-chars-1")
    if args.min_page_chars < 0:
        raise SystemExit("--min-page-chars nao pode ser negativo")
    if args.stage == "embed" and args.dry_run:
        raise SystemExit("--dry-run com --stage embed nao faria trabalho; use --stage audit")


def main() -> None:
    args = build_parser().parse_args()
    validate_args(args)
    config = chunk_config(args)
    reports: list[dict[str, object]] = []
    prepared_by_volume: dict[str, list[PreparedPage]] = {}

    for volume_id in args.volumes:
        prepared, report = prepare_volume(
            args.source_root,
            volume_id,
            min_page_chars=args.min_page_chars,
            max_chunk_chars=args.max_chunk_chars,
            overlap_chars=args.overlap_chars,
            repeated_header_min_pages=args.repeated_header_min_pages,
            repeated_header_min_ratio=args.repeated_header_min_ratio,
            limit_pages=args.limit_pages,
        )
        prepared_by_volume[volume_id] = prepared
        reports.append(report)
    print_reports(reports, verbose=args.verbose)
    if args.stage == "audit" or args.dry_run:
        print("audit: nenhum DB foi aberto e nenhum embedding foi solicitado")
        return

    with connect_db(args.db) as con:
        for volume_id, report in zip(args.volumes, reports):
            changed = write_prepared_volume(
                con,
                args.source_root,
                volume_id,
                prepared_by_volume[volume_id],
                report,
                config,
                prune_missing=args.limit_pages <= 0,
            )
            con.commit()
            print(f"[prepare] {volume_id}: paginas/chunks alterados={changed}")

        processed, batches = embed_prepared(
            con,
            prepared_by_volume,
            model=args.model,
            ollama_url=args.ollama_url,
            batch_size=args.batch_size,
            limit_chunks=args.limit_chunks,
        )
        print(f"[embed] concluido: chunks={processed} batches={batches}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Exporta o banco `data/patristic_indices.db` para JSON por volume, consumível pelo front.

Saídas padrão:
  - web/public/indices/manifest.json
  - web/public/indices/<VOLUME_ID>.json

O export é derivado do SQLite normalizado e preserva o conteúdo bruto
(`entry_raw`, `heading_raw`, `title_raw`, etc.) sem reinterpretar OCR.
Traduções automatizadas ficam em mapas opcionais dentro dos campos `*_display`.
Os shards publicados são um contrato compacto de runtime, não um dump do SQLite.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.indexing.index_pipeline_ownership import general_section_ownership

DEFAULT_DB = Path("data/patristic_indices.db")
DEFAULT_OUT = Path("web/public/indices")
TRANSLATION_LANGUAGES = ("pt-br", "en", "it", "fr")


def available_cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


DEFAULT_WORKERS = min(20, available_cpu_count())


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, obj: dict[str, Any], *, compresslevel: int = 9) -> None:
    """Write one artifact atomically so an interrupted export keeps the old shard."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if path.suffix == ".gz":
        with gzip.open(temporary, "wb", compresslevel=compresslevel) as f:
            f.write(payload)
        temporary.replace(path)
        return
    temporary.write_bytes(payload)
    temporary.replace(path)


def slugify(value: str) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "item"


def coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def canonicalize_source_text(value: Any) -> str:
    import re

    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_notes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except Exception:
            return [text]
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item).strip()]
        if isinstance(decoded, str) and decoded.strip():
            return [decoded.strip()]
        return [text]
    return [str(value).strip()]


def parse_page_num(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        import re

        m = re.search(r"(\d+)", value)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def parse_file_page(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    import os
    import re

    base = os.path.basename(text)
    m = re.search(r"-(\d+)\.txt$", base)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def choose_physical_page(*values: Any) -> int | None:
    for value in values:
        page = parse_file_page(value)
        if page is not None:
            return page
    return None


def has_editorial_reference(entry: dict[str, Any]) -> bool:
    return any(
        value is not None and str(value).strip()
        for value in (
            entry.get("page_ref_raw"),
            entry.get("page_ref_int"),
            entry.get("page_ref_col"),
        )
    )


def repo_relative_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return path.as_posix().lstrip("./")


def file_reference(value: Any) -> dict[str, Any]:
    rel = repo_relative_path(value)
    page = parse_file_page(value)
    return {
        "path": rel,
        "page": page,
    }


def norm_translation_map(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for k, v in value.items():
            text = coerce_text(v)
            if text and text.strip():
                out[str(k)] = text.strip()
        return out
    return {}


def make_display(original: str | None, translation: Any = None, search: Any = None) -> dict[str, Any]:
    display: dict[str, Any] = {"original": original}
    translations = norm_translation_map(translation)
    if translations:
        display["translation"] = translations
    if search:
        display["search"] = search
    return display


def strip_display_search(display: Any) -> None:
    if isinstance(display, dict):
        display.pop("search", None)


def prune_null_values(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if value[key] is None:
                value.pop(key)
            else:
                prune_null_values(value[key])
    elif isinstance(value, list):
        for item in value:
            prune_null_values(item)


def public_general_sections(sections: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [section for section in sections if general_section_ownership(section)[0]]


@dataclass
class VolumeRow:
    volume_id: str
    collection: str
    source_root: str
    volume_label: str | None
    notes: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TranslationAudit:
    languages: tuple[str, ...]
    strings_total: int
    rows_total: int
    fallback_strings: int
    models: tuple[str, ...]


@dataclass(frozen=True)
class VolumeExportResult:
    manifest_item: dict[str, Any]
    elapsed_seconds: float


_WORKER_CONNECTION: sqlite3.Connection | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")
    return con


def fetch_volume_rows(con: sqlite3.Connection) -> list[VolumeRow]:
    rows = con.execute(
        """
        SELECT volume_id, collection, source_root, volume_label, notes, created_at, updated_at
        FROM volumes
        ORDER BY volume_id
        """
    ).fetchall()
    return [
        VolumeRow(
            volume_id=row["volume_id"],
            collection=row["collection"],
            source_root=row["source_root"],
            volume_label=row["volume_label"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def audit_translations(
    con: sqlite3.Connection,
    languages: tuple[str, ...] = TRANSLATION_LANGUAGES,
) -> TranslationAudit:
    strings_total = int(con.execute("SELECT COUNT(*) FROM index_strings").fetchone()[0])
    if strings_total < 1:
        raise RuntimeError("O banco não possui strings de índice para exportar.")

    placeholders = ",".join("?" for _ in languages)
    counts = {
        str(row["language"]): int(row["row_count"])
        for row in con.execute(
            f"""
            SELECT language, COUNT(*) AS row_count
            FROM index_translations
            WHERE language IN ({placeholders})
            GROUP BY language
            """,
            languages,
        )
    }
    partial_rows = con.execute(
        f"""
        SELECT s.id, s.source_text, COUNT(DISTINCT t.language) AS language_count
        FROM index_strings s
        LEFT JOIN index_translations t
          ON t.string_id = s.id
         AND t.language IN ({placeholders})
        GROUP BY s.id, s.source_text
        HAVING language_count > 0 AND language_count < ?
        LIMIT 5
        """,
        (*languages, len(languages)),
    ).fetchall()
    if partial_rows:
        samples = "; ".join(
            f"id={row['id']} ({row['language_count']}/{len(languages)}): "
            f"{row['source_text'][:100]!r}"
            for row in partial_rows
        )
        raise RuntimeError(
            "Há strings com traduções parciais; remova ou refaça o conjunto completo: "
            f"{samples}"
        )

    fallback_strings = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM index_strings s
            WHERE NOT EXISTS (
                SELECT 1
                FROM index_translations t
                WHERE t.string_id = s.id
                  AND t.language IN ({placeholders})
            )
            """,
            languages,
        ).fetchone()[0]
    )

    malformed = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM index_translations
            WHERE language IN ({placeholders})
              AND json_valid(translated_text) = 1
              AND substr(ltrim(translated_text), 1, 1) IN ('{{', '[')
            """,
            languages,
        ).fetchone()[0]
    )
    if malformed:
        raise RuntimeError(
            f"O banco contém {malformed} tradução(ões) com dados estruturados serializados."
        )

    models = tuple(
        str(row["model_name"])
        for row in con.execute(
            f"""
            SELECT DISTINCT model_name
            FROM index_translations
            WHERE language IN ({placeholders})
            ORDER BY model_name
            """,
            languages,
        )
    )
    return TranslationAudit(
        languages=languages,
        strings_total=strings_total,
        rows_total=sum(counts.values()),
        fallback_strings=fallback_strings,
        models=models,
    )


def collect_translatable_texts(
    works: list[dict[str, Any]], sections: list[dict[str, Any]]
) -> list[str]:
    values: list[Any] = []
    for work in works:
        values.extend((work.get("author_raw"), work.get("title_raw")))
    for section in sections:
        values.append(section.get("heading_raw"))
        for entry in section.get("entries", []):
            values.extend(
                (
                    entry.get("target_raw") or entry.get("entry_raw"),
                    entry.get("note_raw"),
                )
            )
    return list(
        dict.fromkeys(
            text
            for value in values
            if (text := canonicalize_source_text(value))
        )
    )


def fetch_translation_map(
    con: sqlite3.Connection,
    source_texts: Iterable[str],
    languages: tuple[str, ...] = TRANSLATION_LANGUAGES,
) -> dict[str, dict[str, str]]:
    texts = list(dict.fromkeys(canonicalize_source_text(value) for value in source_texts))
    texts = [text for text in texts if text]
    translations: dict[str, dict[str, str]] = {}
    for start in range(0, len(texts), 400):
        chunk = texts[start : start + 400]
        text_placeholders = ",".join("?" for _ in chunk)
        language_placeholders = ",".join("?" for _ in languages)
        rows = con.execute(
            f"""
            SELECT s.source_text, t.language, t.translated_text
            FROM index_strings s
            JOIN index_translations t ON t.string_id = s.id
            WHERE s.source_text IN ({text_placeholders})
              AND t.language IN ({language_placeholders})
            """,
            (*chunk, *languages),
        )
        for row in rows:
            translations.setdefault(str(row["source_text"]), {})[
                str(row["language"])
            ] = str(row["translated_text"])

    partial = [
        text for text in texts
        if translations.get(text, {})
        and any(language not in translations[text] for language in languages)
    ]
    if partial:
        samples = "; ".join(repr(text[:120]) for text in partial[:5])
        raise RuntimeError(
            f"{len(partial)} string(s) do volume possuem traduções parciais: {samples}"
        )
    return {
        text: {language: translations[text][language] for language in languages}
        for text in texts
        if translations.get(text)
    }


def apply_translations(
    works: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    translations: dict[str, dict[str, str]],
) -> None:
    def translated(value: Any) -> dict[str, str]:
        return translations.get(canonicalize_source_text(value), {})

    for work in works:
        author = work.get("author_raw")
        if canonicalize_source_text(author):
            work["author_display"] = make_display(
                author,
                translation=translated(author),
            )
        title = work.get("title_raw")
        work["title_display"] = make_display(
            title,
            translation=translated(title),
            search=work.get("title_norm") or title,
        )

    for section in sections:
        heading = section.get("heading_raw")
        section["heading_display"] = make_display(
            heading,
            translation=translated(heading),
            search=section.get("heading_norm") or heading,
        )
        for entry in section.get("entries", []):
            label = entry.get("target_raw") or entry.get("entry_raw")
            entry["target_display"] = make_display(
                label,
                translation=translated(label),
                search=entry.get("normalized_target") or label,
            )
            note = entry.get("note_raw")
            if canonicalize_source_text(note):
                entry["note_display"] = make_display(
                    note,
                    translation=translated(note),
                )


def fetch_works(con: sqlite3.Connection, volume_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT work_key, volume_id, work_order, author_raw, title_raw, title_norm,
               start_page, end_page, start_file, end_file, source_section_key,
               confidence, raw_json
        FROM works
        WHERE volume_id = ?
        ORDER BY COALESCE(work_order, 999999), work_key
        """,
        (volume_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw_json = json.loads(row["raw_json"]) if row["raw_json"] else {}
        start_ref = file_reference(row["start_file"])
        end_ref = file_reference(row["end_file"])
        reference_start_page = choose_physical_page(start_ref["path"])
        reference_end_page = choose_physical_page(end_ref["path"])
        out.append(
            {
                "work_key": row["work_key"],
                "work_order": row["work_order"],
                "author_raw": row["author_raw"],
                "title_raw": row["title_raw"],
                "title_norm": row["title_norm"],
                "title_display": {
                    "original": row["title_raw"],
                    "search": row["title_norm"] or row["title_raw"],
                },
                "start_page": row["start_page"],
                "end_page": row["end_page"],
                "start_file": start_ref["path"],
                "end_file": end_ref["path"],
                "reference_start_page": reference_start_page,
                "reference_end_page": reference_end_page,
                "source_section_key": row["source_section_key"],
                "confidence": row["confidence"],
                "raw_json": raw_json,
            }
        )
    return out


def fetch_sections(con: sqlite3.Connection, volume_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT section_key, volume_id, work_key, scope_kind, index_kind, heading_raw,
               heading_norm, page_start, page_end, file_start, file_end, confidence, raw_json
        FROM index_sections
        WHERE volume_id = ?
        ORDER BY page_start IS NULL, page_start, section_key
        """,
        (volume_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw_json = json.loads(row["raw_json"]) if row["raw_json"] else {}
        start_ref = file_reference(row["file_start"])
        end_ref = file_reference(row["file_end"])
        reference_page_start = choose_physical_page(start_ref["path"])
        reference_page_end = choose_physical_page(end_ref["path"])
        out.append(
            {
                "section_key": row["section_key"],
                "work_key": row["work_key"],
                "scope_kind": row["scope_kind"],
                "index_kind": row["index_kind"],
                "heading_raw": row["heading_raw"],
                "heading_norm": row["heading_norm"],
                "heading_display": {
                    "original": row["heading_raw"],
                    "search": row["heading_norm"] or row["heading_raw"],
                },
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "file_start": start_ref["path"],
                "file_end": end_ref["path"],
                "reference_page_start": reference_page_start,
                "reference_page_end": reference_page_end,
                "confidence": row["confidence"],
                "raw_json": raw_json,
                "entries": [],
            }
        )
    return out


def fetch_entries(con: sqlite3.Connection, volume_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = con.execute(
        """
        SELECT e.id, e.section_key, e.entry_order, e.entry_raw, e.target_raw, e.target_file,
               e.page_ref_raw, e.page_ref_int, e.page_ref_col, e.note_raw,
               e.normalized_target, e.confidence, e.raw_json
        FROM index_entries e
        JOIN index_sections s ON s.section_key = e.section_key
        WHERE s.volume_id = ?
        ORDER BY s.page_start IS NULL, s.page_start, e.entry_order, e.id
        """,
        (volume_id,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw_json = json.loads(row["raw_json"]) if row["raw_json"] else {}
        target_ref = file_reference(row["target_file"])
        reference_page = choose_physical_page(target_ref["path"])
        editorial_reference_page = parse_page_num(row["page_ref_int"])
        if editorial_reference_page is None:
            editorial_reference_page = parse_page_num(row["page_ref_raw"])
        label = row["target_raw"] or row["entry_raw"]
        grouped[row["section_key"]].append(
            {
                "id": row["id"],
                "section_key": row["section_key"],
                "entry_order": row["entry_order"],
                "entry_raw": row["entry_raw"],
                "target_raw": row["target_raw"],
                "target_display": make_display(
                    label,
                    search=row["normalized_target"] or label,
                ),
                "target_file": target_ref["path"],
                "reference_page": reference_page,
                "editorial_reference_page": editorial_reference_page,
                "page_ref_raw": row["page_ref_raw"],
                "page_ref_int": row["page_ref_int"],
                "page_ref_col": row["page_ref_col"],
                "note_raw": row["note_raw"],
                "normalized_target": row["normalized_target"],
                "confidence": row["confidence"],
                "raw_json": raw_json,
            }
        )
    return grouped


def compute_coverage(works: list[dict[str, Any]], sections: list[dict[str, Any]]) -> dict[str, Any]:
    entries_total = sum(len(sec.get("entries", [])) for sec in sections)
    work_count = len(works)
    section_count = len(sections)
    entries_with_editorial_reference = 0
    entries_with_numeric_editorial_reference = 0
    entries_with_target_file = 0
    entries_with_resolved_target_page = 0
    for sec in sections:
        for entry in sec.get("entries", []):
            if has_editorial_reference(entry):
                entries_with_editorial_reference += 1
            if entry.get("editorial_reference_page") is not None:
                entries_with_numeric_editorial_reference += 1
            if entry.get("target_file"):
                entries_with_target_file += 1
            if entry.get("reference_page") is not None:
                entries_with_resolved_target_page += 1
    editorial_reference_coverage = (
        round(entries_with_editorial_reference / entries_total, 4)
        if entries_total
        else 0.0
    )
    target_coverage = (
        round(entries_with_resolved_target_page / entries_total, 4)
        if entries_total
        else 0.0
    )
    return {
        "entries_total": entries_total,
        "works_total": work_count,
        "sections_total": section_count,
        "entries_with_page_ref": entries_with_numeric_editorial_reference,
        "entries_with_editorial_reference": entries_with_editorial_reference,
        "entries_with_numeric_editorial_reference": entries_with_numeric_editorial_reference,
        "entries_with_target_file": entries_with_target_file,
        "entries_with_resolved_target_page": entries_with_resolved_target_page,
        "entries_without_resolved_target_page": entries_total - entries_with_resolved_target_page,
        "editorial_reference_coverage": editorial_reference_coverage,
        "target_coverage": target_coverage,
        "completeness": target_coverage,
    }


def build_volume_payload(volume: VolumeRow, works: list[dict[str, Any]], sections: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = compute_coverage(works, sections)
    source_root = repo_relative_path(volume.source_root)
    return {
        "schema_version": 3,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": volume.volume_id,
            "collection": volume.collection,
            "source_root": source_root,
            "volume_label": volume.volume_label or volume.volume_id,
            "notes": parse_notes(volume.notes),
            "created_at": volume.created_at,
            "updated_at": volume.updated_at,
            "display": {
                "original": volume.volume_label or volume.volume_id,
                "search": slugify(volume.volume_label or volume.volume_id),
            },
        },
        "coverage": coverage,
        "works": works,
        "sections": sections,
        "relations": [],
        "render_hints": {
            "preferred_view": "volume",
            "show_work_tree": True,
            "show_section_tree": True,
            "show_truncated_entries": True,
            "show_translations": True,
        },
    }


def compact_runtime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove material de auditoria sem perder texto pesquisável ou navegação."""

    payload.pop("generated_at", None)
    payload.pop("coverage", None)
    payload.pop("relations", None)
    payload.pop("render_hints", None)

    volume = payload.get("volume")
    if isinstance(volume, dict):
        for key in ("source_root", "created_at", "updated_at"):
            volume.pop(key, None)
        strip_display_search(volume.get("display"))

    for work in payload.get("works", []):
        work.pop("raw_json", None)
        strip_display_search(work.get("author_display"))
        strip_display_search(work.get("title_display"))
        for key in (
            "work_order",
            "author_raw",
            "title_raw",
            "title_norm",
            "start_file",
            "end_file",
            "source_section_key",
        ):
            work.pop(key, None)

    for section in payload.get("sections", []):
        section.pop("raw_json", None)
        strip_display_search(section.get("heading_display"))
        for key in ("heading_raw", "heading_norm", "file_start", "file_end"):
            section.pop(key, None)

        for entry in section.get("entries", []):
            entry.pop("raw_json", None)
            original_entry = canonicalize_source_text(entry.get("entry_raw"))
            target_original = canonicalize_source_text(
                (entry.get("target_display") or {}).get("original")
            )
            strip_display_search(entry.get("target_display"))
            strip_display_search(entry.get("note_display"))
            if not original_entry or original_entry == target_original:
                entry.pop("entry_raw", None)
            for key in (
                "section_key",
                "target_raw",
                "target_file",
                "page_ref_int",
                "page_ref_col",
                "note_raw",
                "normalized_target",
            ):
                entry.pop(key, None)

    prune_null_values(payload)
    return payload


def build_manifest_item(volume_payload: dict[str, Any], file_name: str) -> dict[str, Any]:
    volume = volume_payload["volume"]
    return {
        "volume_id": volume["volume_id"],
        "collection": volume["collection"],
        "path": f"indices/{file_name}",
        "works_total": volume_payload["coverage"]["works_total"],
        "sections_total": volume_payload["coverage"]["sections_total"],
        "entries_total": volume_payload["coverage"]["entries_total"],
        "entries_with_page_ref": volume_payload["coverage"]["entries_with_page_ref"],
        "entries_with_editorial_reference": volume_payload["coverage"]["entries_with_editorial_reference"],
        "entries_with_target_file": volume_payload["coverage"]["entries_with_target_file"],
        "entries_with_resolved_target_page": volume_payload["coverage"]["entries_with_resolved_target_page"],
        "editorial_reference_coverage": volume_payload["coverage"]["editorial_reference_coverage"],
        "target_coverage": volume_payload["coverage"]["target_coverage"],
        "completeness": volume_payload["coverage"]["completeness"],
        "updated_at": volume["updated_at"],
    }


def export_volume(
    con: sqlite3.Connection,
    volume: VolumeRow,
    out_dir: Path,
    languages: tuple[str, ...],
    compresslevel: int,
) -> VolumeExportResult:
    """Build and atomically write one independent volume shard."""

    started_at = time.perf_counter()
    works = fetch_works(con, volume.volume_id)
    sections = public_general_sections(fetch_sections(con, volume.volume_id))
    entries_by_section = fetch_entries(con, volume.volume_id)

    for section in sections:
        section["entries"] = entries_by_section.get(section["section_key"], [])

    source_texts = collect_translatable_texts(works, sections)
    translation_map = fetch_translation_map(con, source_texts, languages)
    apply_translations(works, sections, translation_map)

    payload = build_volume_payload(volume, works, sections)
    file_name = f"{volume.volume_id}.json.gz"
    manifest_item = build_manifest_item(payload, file_name)
    compact_runtime_payload(payload)
    write_json(
        out_dir / file_name,
        payload,
        compresslevel=compresslevel,
    )
    return VolumeExportResult(
        manifest_item=manifest_item,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def initialize_export_worker(db_path: str) -> None:
    """Open one read-only SQLite connection per worker process."""

    global _WORKER_CONNECTION
    _WORKER_CONNECTION = connect(Path(db_path))


def export_volume_worker(
    volume: VolumeRow,
    out_dir: Path,
    languages: tuple[str, ...],
    compresslevel: int,
) -> VolumeExportResult:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("Worker de exportação iniciado sem conexão SQLite.")
    return export_volume(
        _WORKER_CONNECTION,
        volume,
        out_dir,
        languages,
        compresslevel,
    )


def merge_manifest_items(
    manifest_path: Path,
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace selected volume rows while retaining the rest of a full manifest."""

    if not manifest_path.is_file():
        raise RuntimeError(
            f"Não é possível mesclar: manifesto anterior ausente em {manifest_path}."
        )
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_items = previous.get("volumes")
    if not isinstance(old_items, list):
        raise RuntimeError(f"Manifesto anterior inválido em {manifest_path}.")
    merged = {
        str(item["volume_id"]): item
        for item in old_items
        if isinstance(item, dict) and item.get("volume_id")
    }
    for item in new_items:
        merged[str(item["volume_id"])] = item
    return [merged[volume_id] for volume_id in sorted(merged)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Exporta patristic_indices.db para JSON por volume.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite patristic_indices.db")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Diretório de saída (default: web/public/indices)")
    ap.add_argument("--manifest-out", type=Path, default=None, help="Caminho opcional para o manifesto")
    ap.add_argument("--volume", action="append", default=None, help="Exporta somente o(s) volume(s) informado(s); repetir para múltiplos")
    ap.add_argument(
        "--merge-manifest",
        action="store_true",
        help="Com --volume, substitui esses itens no manifesto completo já existente",
    )
    ap.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Emite progresso a cada N volumes; 0 desativa (default: 10)",
    )
    ap.add_argument(
        "--compresslevel",
        type=int,
        choices=range(0, 10),
        default=9,
        help="Nível gzip de 0 a 9 (default: 9)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Processos paralelos, cada um com conexão SQLite própria "
            f"(default nesta máquina: {DEFAULT_WORKERS})"
        ),
    )
    args = ap.parse_args()

    if args.merge_manifest and not args.volume:
        ap.error("--merge-manifest exige ao menos um --volume")
    if args.progress_every < 0:
        ap.error("--progress-every deve ser maior ou igual a zero")
    if args.workers < 1:
        ap.error("--workers deve ser maior ou igual a 1")

    con = connect(args.db)
    try:
        started_at = time.perf_counter()
        print("[export] auditando relações e cobertura das traduções...", flush=True)
        translation_audit = audit_translations(con)
        print(
            "[export] traduções válidas: "
            f"{translation_audit.strings_total} strings, "
            f"{translation_audit.rows_total} relações, "
            f"{translation_audit.fallback_strings} fallback(s) para a origem",
            flush=True,
        )
        volume_rows = fetch_volume_rows(con)
        if args.volume:
            wanted = {str(v).strip() for v in args.volume if str(v).strip()}
            volume_rows = [row for row in volume_rows if row.volume_id in wanted]
            if not volume_rows:
                raise RuntimeError("Nenhum dos volumes solicitados existe no banco.")
    finally:
        con.close()

    total_volumes = len(volume_rows)
    worker_count = min(args.workers, max(total_volumes, 1))
    print(
        f"[export] exportando {total_volumes} volume(s) para {args.out} "
        f"(gzip={args.compresslevel}; workers={worker_count})",
        flush=True,
    )

    def report_progress(
        position: int,
        volume_id: str,
        result: VolumeExportResult,
    ) -> None:
        if args.progress_every and (
            position == 1
            or position == total_volumes
            or position % args.progress_every == 0
        ):
            elapsed = time.perf_counter() - started_at
            print(
                f"[export] {position}/{total_volumes} {volume_id} "
                f"({result.elapsed_seconds:.1f}s no worker; total {elapsed:.1f}s)",
                flush=True,
            )

    manifest_items: list[dict[str, Any]] = []
    output_dir = args.out.resolve()
    if worker_count == 1:
        con = connect(args.db)
        try:
            for position, volume in enumerate(volume_rows, start=1):
                result = export_volume(
                    con,
                    volume,
                    output_dir,
                    translation_audit.languages,
                    args.compresslevel,
                )
                manifest_items.append(result.manifest_item)
                report_progress(position, volume.volume_id, result)
        finally:
            con.close()
    else:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=initialize_export_worker,
            initargs=(str(args.db.resolve()),),
        ) as executor:
            futures = {
                executor.submit(
                    export_volume_worker,
                    volume,
                    output_dir,
                    translation_audit.languages,
                    args.compresslevel,
                ): volume.volume_id
                for volume in volume_rows
            }
            for position, future in enumerate(as_completed(futures), start=1):
                volume_id = futures[future]
                result = future.result()
                manifest_items.append(result.manifest_item)
                report_progress(position, volume_id, result)

    manifest_items.sort(key=lambda item: str(item["volume_id"]))

    manifest_path = args.manifest_out or (args.out / "manifest.json")
    if args.merge_manifest:
        manifest_items = merge_manifest_items(manifest_path, manifest_items)
    manifest = {
        "schema_version": 3,
        "generated_at": now_iso(),
        "translations": {
            "automated": True,
            "languages": list(translation_audit.languages),
            "strings_total": translation_audit.strings_total,
            "rows_total": translation_audit.rows_total,
            "fallback_strings": translation_audit.fallback_strings,
            "models": list(translation_audit.models),
        },
        "volumes": manifest_items,
    }
    write_json(manifest_path, manifest)
    elapsed = time.perf_counter() - started_at
    print(
        f"[OK] exported {total_volumes} volume(s) to {args.out} in {elapsed:.1f}s; "
        f"manifest has {len(manifest_items)} volume(s)",
        flush=True,
    )


if __name__ == "__main__":
    main()

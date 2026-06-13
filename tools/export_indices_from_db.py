#!/usr/bin/env python3
"""
Exporta o banco `data/patristic_indices.db` para JSON por volume, consumível pelo front.

Saídas padrão:
  - web/public/indices/manifest.json
  - web/public/indices/<VOLUME_ID>.json

O export é derivado do SQLite normalizado e preserva o conteúdo bruto
(`entry_raw`, `heading_raw`, `title_raw`, etc.) sem tentar traduzir ou
reinterpretar OCR. Campos de exibição bilíngue ficam prontos para consumo,
mas são opcionais.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path("data/patristic_indices.db")
DEFAULT_OUT = Path("web/public/indices")
REPO_ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix == ".gz":
        with gzip.open(path, "wb", compresslevel=9) as f:
            f.write(payload)
        return
    path.write_bytes(payload)


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


def choose_reference_page(*values: Any) -> int | None:
    for value in values:
        page = parse_file_page(value)
        if page is not None:
            return page
    for value in values:
        page = parse_page_num(value)
        if page is not None:
            return page
    return None


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


def make_display(original: str | None, translation: Any = None, search: str | None = None) -> dict[str, Any]:
    display: dict[str, Any] = {"original": original}
    translations = norm_translation_map(translation)
    if translations:
        display["translation"] = translations
    if search:
        display["search"] = search
    return display


@dataclass
class VolumeRow:
    volume_id: str
    collection: str
    source_root: str
    volume_label: str | None
    notes: str | None
    created_at: str
    updated_at: str


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
        reference_start_page = choose_reference_page(start_ref["path"], row["start_page"])
        reference_end_page = choose_reference_page(end_ref["path"], row["end_page"])
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
        reference_page_start = choose_reference_page(start_ref["path"], row["page_start"])
        reference_page_end = choose_reference_page(end_ref["path"], row["page_end"])
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
        target_translation = raw_json.get("target_translation") if isinstance(raw_json, dict) else None
        target_ref = file_reference(row["target_file"])
        reference_page = choose_reference_page(target_ref["path"], row["page_ref_int"], row["page_ref_raw"])
        grouped[row["section_key"]].append(
            {
                "id": row["id"],
                "section_key": row["section_key"],
                "entry_order": row["entry_order"],
                "entry_raw": row["entry_raw"],
                "target_raw": row["target_raw"],
                "target_display": make_display(
                    row["target_raw"],
                    translation=target_translation,
                    search=row["normalized_target"] or row["target_raw"],
                ),
                "target_file": target_ref["path"],
                "reference_page": reference_page,
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
    entries_with_page_ref = 0
    entries_without_page_ref = 0
    entries_with_target_file = 0
    for sec in sections:
        for entry in sec.get("entries", []):
            if entry.get("reference_page") is not None:
                entries_with_page_ref += 1
            else:
                entries_without_page_ref += 1
            if entry.get("target_file"):
                entries_with_target_file += 1
    completeness = round(entries_with_page_ref / entries_total, 4) if entries_total else 0.0
    return {
        "entries_total": entries_total,
        "works_total": work_count,
        "sections_total": section_count,
        "entries_with_page_ref": entries_with_page_ref,
        "entries_without_page_ref": entries_without_page_ref,
        "entries_with_target_file": entries_with_target_file,
        "completeness": completeness,
    }


def build_volume_payload(volume: VolumeRow, works: list[dict[str, Any]], sections: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = compute_coverage(works, sections)
    source_root = repo_relative_path(volume.source_root)
    return {
        "schema_version": 1,
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
            "show_translations": False,
        },
    }


def derive_section_summary(section: dict[str, Any]) -> dict[str, Any]:
    notes = section.get("raw_json", {}).get("notes") if isinstance(section.get("raw_json"), dict) else None
    summary: dict[str, Any] = {
        "section_key": section["section_key"],
        "scope_kind": section["scope_kind"],
        "index_kind": section["index_kind"],
        "heading_raw": section["heading_raw"],
        "heading_norm": section["heading_norm"],
        "heading_display": section["heading_display"],
        "page_start": section["page_start"],
        "page_end": section["page_end"],
        "confidence": section["confidence"],
        "raw_json": section["raw_json"],
        "entries": section["entries"],
    }
    if notes:
        summary["notes"] = notes
    return summary


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
        "completeness": volume_payload["coverage"]["completeness"],
        "updated_at": volume["updated_at"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Exporta patristic_indices.db para JSON por volume.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite patristic_indices.db")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Diretório de saída (default: web/public/indices)")
    ap.add_argument("--manifest-out", type=Path, default=None, help="Caminho opcional para o manifesto")
    ap.add_argument("--volume", action="append", default=None, help="Exporta somente o(s) volume(s) informado(s); repetir para múltiplos")
    args = ap.parse_args()

    con = connect(args.db)
    try:
        volume_rows = fetch_volume_rows(con)
        if args.volume:
            wanted = {str(v).strip() for v in args.volume if str(v).strip()}
            volume_rows = [row for row in volume_rows if row.volume_id in wanted]

        manifest_items: list[dict[str, Any]] = []
        for volume in volume_rows:
            works = fetch_works(con, volume.volume_id)
            sections = fetch_sections(con, volume.volume_id)
            entries_by_section = fetch_entries(con, volume.volume_id)

            for section in sections:
                section["entries"] = entries_by_section.get(section["section_key"], [])

            payload = build_volume_payload(volume, works, sections)
            file_name = f"{volume.volume_id}.json.gz"
            write_json(args.out / file_name, payload)
            manifest_items.append(build_manifest_item(payload, file_name))

        manifest = {
            "schema_version": 1,
            "generated_at": now_iso(),
            "volumes": manifest_items,
        }
        manifest_path = args.manifest_out or (args.out / "manifest.json")
        write_json(manifest_path, manifest)
        print(f"[OK] exported {len(manifest_items)} volume(s) to {args.out}")
    finally:
        con.close()


if __name__ == "__main__":
    main()

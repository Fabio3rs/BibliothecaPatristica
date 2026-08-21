#!/usr/bin/env python3
"""
Exporta shards de enriquecimento (resumos + keywords) do SQLite primário
`data/patristica_resumos.db` para NDJSON por volume, com catálogo JSON.

Uso típico:
    python tools/export_enrichment_shards.py --all --no-text --split 0
    python tools/export_enrichment_shards.py --docs PG144,PL001 --no-text

Saídas (por padrão):
    data/shards/enrichment/<DOC>.ndjson
    data/shards/enrichment/index.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "patristica_resumos.db"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "shards" / "enrichment"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from resumo_v2 import current_summary_select_sql  # noqa: E402


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def list_docs(con: sqlite3.Connection) -> List[str]:
    rows = con.execute("SELECT DISTINCT documento FROM resumos ORDER BY documento").fetchall()
    return [r["documento"] for r in rows]


def fetch_rows(con: sqlite3.Connection, doc: str) -> List[sqlite3.Row]:
    has_v2 = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resumo_generations'"
    ).fetchone()
    if not has_v2:
        return con.execute(
            "SELECT * FROM resumos WHERE documento = ? ORDER BY pagina_num",
            (doc,),
        ).fetchall()
    translation_columns = {
        str(row[1]) for row in con.execute("PRAGMA table_info(resumo_translations)")
    }
    cumulative_en = "en.cumulative_summary" if "cumulative_summary" in translation_columns else "''"
    cumulative_fr = "fr.cumulative_summary" if "cumulative_summary" in translation_columns else "''"
    cumulative_it = "it.cumulative_summary" if "cumulative_summary" in translation_columns else "''"
    hybrid = current_summary_select_sql()
    return con.execute(
        f"""
        WITH hybrid AS ({hybrid})
        SELECT h.*,
               en.summary_display AS summary_en, {cumulative_en} AS cumulative_en,
               en.search_text AS search_en,
               fr.summary_display AS summary_fr, {cumulative_fr} AS cumulative_fr,
               fr.search_text AS search_fr,
               it.summary_display AS summary_it, {cumulative_it} AS cumulative_it,
               it.search_text AS search_it
          FROM hybrid h
          LEFT JOIN resumo_translations en ON en.id=(
              SELECT MAX(t.id) FROM resumo_translations t
               WHERE t.generation_id=h.v2_generation_id AND t.locale='en' AND t.status='completed'
          )
          LEFT JOIN resumo_translations fr ON fr.id=(
              SELECT MAX(t.id) FROM resumo_translations t
               WHERE t.generation_id=h.v2_generation_id AND t.locale='fr' AND t.status='completed'
          )
          LEFT JOIN resumo_translations it ON it.id=(
              SELECT MAX(t.id) FROM resumo_translations t
               WHERE t.generation_id=h.v2_generation_id AND t.locale='it' AND t.status='completed'
          )
         WHERE h.documento=? ORDER BY h.pagina_num
        """,
        (doc,),
    ).fetchall()


def ensure_out(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def chunked(iterable: List, size: int) -> Iterable[List]:
    if size <= 0:
        yield iterable
        return
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def normalize_keywords(raw: str) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            kws = val.get("keywords") or val.get("keywords_ranking")
            if isinstance(kws, list):
                return kws
    except Exception:
        pass
    return []

def filtra_duplicado_resumos(text : str, author : str , work : str) -> str:
    # Remove informações duplicadas do resumo
    if not text:
        return ""
    text = text.replace(author or "", "").replace(work or "", "").strip()
    text = text.replace("Autor:", "").replace("Livro/obra identificada:", "").strip()
    return text


def export_doc(
    doc: str,
    rows: List[sqlite3.Row],
    out_dir: Path,
    include_text: bool,
    split: int,
) -> dict:
    parts_meta = []
    page_total = len(rows)
    for part_idx, rows_chunk in enumerate(chunked(rows, split or page_total), start=1):
        fname = f"{doc}.ndjson" if split == 0 else f"{doc}-part{part_idx:02d}.ndjson"
        fpath = out_dir / fname
        with fpath.open("w", encoding="utf-8") as f:
            for r in rows_chunk:
                keys = set(r.keys())
                has_v2 = "v2_generation_id" in keys and r["v2_generation_id"] is not None
                summary_page = (
                    r["effective_summary_page"] if has_v2 else r["resumo_pagina"]
                )
                summary_global = (
                    r["effective_cumulative_summary"] if has_v2 else r["resumo_global"]
                )
                rec = {
                    "doc": r["documento"],
                    "page": r["pagina_num"],
                    "file": r["pagina_file"],
                    "summary_page": filtra_duplicado_resumos(summary_page, r["author_detected"], r["work_detected"]),
                    "summary_global": filtra_duplicado_resumos(summary_global, r["author_detected"], r["work_detected"]),
                    "keywords": normalize_keywords(r["keywords_json"]),
                    "keywords_source": r["keywords_source"],
                    "keywords_model": r["keywords_modelo"],
                    "model": r["v2_model"] if has_v2 else r["modelo"],
                    "created_at": r["v2_created_at"] if has_v2 else r["criado_em"],
                    "author": r["author_detected"],
                    "work": r["work_detected"],
                    "summary_generation": "v2" if has_v2 else "legacy",
                }
                if has_v2:
                    static_analysis = json.loads(r["effective_static_analysis_json"] or "{}")
                    rec.update(
                        {
                            "summary_generation_id": r["v2_generation_id"],
                            "summary_status": r["v2_status"],
                            "summary_prompt_version": r["v2_prompt_version"],
                            "search_text_pt": r["effective_search_text"],
                            "embedding_text": r["effective_embedding_text"],
                            "header_original": static_analysis.get("header_original", ""),
                            "page_kinds": json.loads(r["effective_page_kinds_json"] or "[]"),
                            "segments": json.loads(r["effective_segments_json"] or "[]"),
                            "translations": {
                                locale: {
                                    "summary_page": r[f"summary_{locale}"],
                                    "summary_global": r[f"cumulative_{locale}"],
                                    "search_text": r[f"search_{locale}"],
                                }
                                for locale in ("en", "fr", "it")
                                if r[f"summary_{locale}"] or r[f"search_{locale}"]
                            },
                        }
                    )
                if include_text:
                    rec["text"] = r["pagina_texto"]
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        parts_meta.append(
            {
                "file": fname,
                "page_first": rows_chunk[0]["pagina_num"],
                "page_last": rows_chunk[-1]["pagina_num"],
                "pages": len(rows_chunk),
                "hash": sha256_file(fpath),
                "has_text": include_text,
                "has_keywords": any(normalize_keywords(r["keywords_json"]) for r in rows_chunk),
            }
        )
    return {
        "id": doc,
        "pages": page_total,
        "shards": parts_meta,
    }


def build_index(volumes_meta: List[dict]) -> dict:
    return {
        "schema_version": 2,
        "generated_at": now_iso(),
        "volumes": volumes_meta,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Exporta shards NDJSON de resumos/keywords.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="Caminho para patristica_resumos.db")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Diretório de saída (será criado)")
    ap.add_argument("--docs", type=str, help="Lista de docs separada por vírgula (ex.: PG144,PL001)")
    ap.add_argument("--all", action="store_true", help="Exporta todos os documentos do DB")
    ap.add_argument("--no-text", action="store_true", help="Não exportar pagina_texto (recomendado para site)")
    ap.add_argument("--split", type=int, default=0, help="Tamanho do shard em páginas (0 = um por volume)")
    args = ap.parse_args()

    include_text = not args.no_text
    ensure_out(args.out)

    con = connect_readonly(args.db)

    docs: List[str]
    if args.all:
        docs = list_docs(con)
    elif args.docs:
        docs = [d.strip() for d in args.docs.split(",") if d.strip()]
    else:
        raise SystemExit("Informe --all ou --docs DOC1,DOC2")

    volumes_meta = []
    for doc in docs:
        rows = fetch_rows(con, doc)
        if not rows:
            print(f"[WARN] documento {doc} não encontrado no DB; pulando")
            continue
        meta = export_doc(doc, rows, args.out, include_text, args.split)
        volumes_meta.append(meta)
        print(f"[OK] {doc}: {meta['pages']} páginas, {len(meta['shards'])} shard(s)")

    index = build_index(volumes_meta)
    index_path = args.out / "index.json"
    # Catálogo em JSON compacto para economizar espaço
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"[OK] catálogo escrito em {index_path}")


if __name__ == "__main__":
    main()

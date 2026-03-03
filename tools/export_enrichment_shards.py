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
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "patristica_resumos.db"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "shards" / "enrichment"


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
    return con.execute(
        "SELECT * FROM resumos WHERE documento = ? ORDER BY pagina_num",
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
            kws = val.get("keywords")
            if isinstance(kws, list):
                return kws
    except Exception:
        pass
    return []


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
                rec = {
                    "doc": r["documento"],
                    "page": r["pagina_num"],
                    "file": r["pagina_file"],
                    "summary_page": r["resumo_pagina"],
                    "summary_global": r["resumo_global"],
                    "keywords": normalize_keywords(r["keywords_json"]),
                    "keywords_source": r["keywords_source"],
                    "keywords_model": r["keywords_modelo"],
                    "model": r["modelo"],
                    "created_at": r["criado_em"],
                }
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
        "schema_version": 1,
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
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] catálogo escrito em {index_path}")


if __name__ == "__main__":
    main()

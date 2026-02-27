#!/usr/bin/env python3
"""
Gera artefatos do site (volumes.json, manifests, snapshots, dicionário de keywords)
a partir dos shards de enriquecimento em data/shards/enrichment/.

Uso:
    python tools/render_publication_from_shards.py \
        --index data/shards/enrichment/index.json \
        --out web/public \
        --db data/patristica_keywords.db \
        --keywords-json web/public/dict/keywords.json \
        --page-block-size 100 \
        --raw-base-url "https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste"

O dicionário canônico de keywords vem do SQLite (patristica_keywords.db) +
keywords.json gerado por export_keywords_dicts.py.  Cada keyword bruta dos
shards é resolvida para o ID canônico do seu grupo HDBSCAN (não-escritura) ou
para o ID da própria citação (escritura), colapsando variantes no canônico.

O campo `raw` em cada página aponta para o arquivo de texto OCR original no
GitHub raw content, permitindo que o viewer exiba o link "Ver OCR Original"
em produção. Formato: {raw_base_url}/{DOC}/text/{filename}.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


@dataclass
class PageRecord:
    doc: str
    page: int
    file: str
    summary_page: str
    summary_global: str
    keywords: List[str]
    keyword_categories: Dict[str, List[str]]
    created_at: str


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_DB = Path("data/patristica_keywords.db")
DEFAULT_KEYWORDS_JSON = Path("web/public/dict/keywords.json")


def load_shard(shard_path: Path) -> List[PageRecord]:
    recs = []
    with shard_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            kw_cats = r.get("categorias") or r.get("keyword_categories") or {}
            # Normaliza: só listas de strings
            kw_cats_norm: Dict[str, List[str]] = {}
            for cat, items in kw_cats.items():
                if not isinstance(items, list):
                    continue
                clean = [str(it).strip() for it in items if str(it).strip()]
                if clean:
                    kw_cats_norm[str(cat).strip()] = clean

            recs.append(PageRecord(
                doc=r["doc"],
                page=int(r["page"]),
                file=r.get("file", ""),
                summary_page=r.get("summary_page", ""),
                summary_global=r.get("summary_global", ""),
                keywords=r.get("keywords") or [],
                keyword_categories=kw_cats_norm,
                created_at=r.get("created_at", ""),
            ))
    return recs


def ensure_dirs(out: Path) -> None:
    for sub in [out, out / "meta", out / "snapshots", out / "dict"]:
        sub.mkdir(parents=True, exist_ok=True)


def build_keyword_lookup(
    db_path: Path, keywords_json_path: Path
) -> Dict[str, str]:
    """Constrói mapa  keyword_original (bruta do shard) → canonical_id.

    Estratégia:
    - Para não-escritura: usa o canonical_slug_id do grupo HDBSCAN
      (mesmo ID presente no keywords.json gerado por export_keywords_dicts.py).
    - Para escritura: usa o slug_id da própria keyword_norm.
    - Keywords sem grupo (hdbscan_group_id NULL) são ignoradas — não aparecem
      no dicionário canônico e portanto não devem ser emitidas nas páginas.

    O mapeamento é feito via SQLite para resolver corretamente as variantes
    para o nome canônico do grupo.
    """

    def slugify(label: str) -> str:
        norm = (
            unicodedata.normalize("NFKD", label or "")
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        norm = norm.lower().strip()
        out = []
        for ch in norm:
            if ch.isalnum():
                out.append(ch)
            elif ch in [" ", "-", "_", "/", "."]:
                out.append("-")
        slug = "".join(out).strip("-")
        slug = "-".join([p for p in slug.split("-") if p])
        return slug or "kw"

    def canonical_slug_id(nome_canonico: str, label_norm: str, label: str, group_id: int) -> str:
        for base in (nome_canonico, label_norm, label):
            s = slugify(base or "")
            if s and s != "kw":
                return f"k:{s}"
        return f"k:g{group_id}"

    # Carrega o conjunto de IDs válidos do keywords.json para garantir
    # que só emitimos IDs que realmente existem no dicionário publicado.
    valid_ids: set[str] = set()
    if keywords_json_path.exists():
        data = json.loads(keywords_json_path.read_text(encoding="utf-8"))
        for item in data.get("items") or []:
            if item.get("id"):
                valid_ids.add(item["id"])
    else:
        raise FileNotFoundError(
            f"keywords.json não encontrado em {keywords_json_path}. "
            "Execute export_keywords_dicts.py primeiro."
        )

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")

    sql = """
        SELECT
            k.keyword_original,
            k.keyword_norm,
            k.hdbscan_group_id,
            k.is_scripture_citation,
            cc.nome_canonico,
            cc.nome_canonico_original
        FROM keywords k
        LEFT JOIN cluster_canonical_names cc ON cc.group_id = k.hdbscan_group_id
        WHERE k.hdbscan_group_id IS NOT NULL AND k.hdbscan_group_id > 0
          AND k.keyword_original IS NOT NULL
    """

    lookup: Dict[str, str] = {}
    skipped = 0
    for row in con.execute(sql):
        original = row["keyword_original"]
        if not original:
            continue

        is_scripture = bool(row["is_scripture_citation"] == 1)

        if is_scripture:
            kid = f"k:{slugify(row['keyword_norm'] or original)}"
        else:
            kid = canonical_slug_id(
                row["nome_canonico"] or "",
                row["keyword_norm"] or "",
                original,
                row["hdbscan_group_id"],
            )

        if kid not in valid_ids:
            skipped += 1
            continue

        lookup[original] = kid

    con.close()

    if skipped:
        log(f"[WARN] {skipped} keywords do DB não encontradas no keywords.json (ignoradas).")

    return lookup


def page_blocks(
    recs: List[PageRecord],
    block_size: int,
    volume_id: str,
    keyword_ids: Dict[str, str],
    raw_base_url: str = "",
) -> Tuple[List[dict], List[dict]]:
    recs_sorted = sorted(recs, key=lambda r: r.page)
    blocks = []
    page_files = []
    for idx, start in enumerate(range(0, len(recs_sorted), block_size), start=1):
        chunk = recs_sorted[start:start+block_size]
        file_name = f"meta/{volume_id}-pages-{idx:03d}.json"
        block = {
            "schema_version": 1,
            "volume_id": volume_id,
            "block_type": "pages",
            "block_index": idx,
            "page_first": chunk[0].page,
            "page_last": chunk[-1].page,
            "dict_refs": {"keywords": "dict/keywords.json"},
            "pages": []
        }
        for r in chunk:
            kws = [keyword_ids[k] for k in r.keywords if k in keyword_ids]

            kw_cats_ids: Dict[str, List[str]] = {}
            for cat, items in r.keyword_categories.items():
                ids = [keyword_ids[k] for k in items if k in keyword_ids]
                if ids:
                    kw_cats_ids[cat] = ids

            page_entry: dict = {
                "page": r.page,
                "label": str(r.page),
                "summary_page": r.summary_page,
                "summary_global": r.summary_global,
                "created_at": r.created_at,
                "keyword_ids": kws,
                "keyword_categories": kw_cats_ids,
                "snapshot_ids": [f"snap:{volume_id}:global"],
            }

            # Salva o nome do arquivo de texto OCR e, se tivermos uma base URL,
            # monta a URL completa para o GitHub raw content.
            if r.file:
                base = raw_base_url.rstrip("/") if raw_base_url else ""
                raw_url = f"{base}/{volume_id}/text/{r.file}" if base else ""
                page_entry["raw"] = {
                    "file": r.file,
                    "url": raw_url,
                }

            block["pages"].append(page_entry)
        blocks.append(block)
        page_files.append({
            "index": idx,
            "file": file_name,
            "page_first": chunk[0].page,
            "page_last": chunk[-1].page,
            "count": len(chunk)
        })
    return blocks, page_files


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Grava JSON compacto para economizar espaço em disco/banda
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser(description="Renderiza artefatos do site a partir dos shards de enriquecimento.")
    ap.add_argument("--index", type=Path, default=Path("data/shards/enrichment/index.json"))
    ap.add_argument("--out", type=Path, default=Path("web/public"))
    ap.add_argument("--page-block-size", type=int, default=100)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help="SQLite patristica_keywords.db (para resolver keywords → IDs canônicos)")
    ap.add_argument("--keywords-json", type=Path, default=DEFAULT_KEYWORDS_JSON,
                    help="keywords.json gerado por export_keywords_dicts.py")
    ap.add_argument(
        "--raw-base-url",
        type=str,
        default="https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste",
        help=(
            "URL base para os arquivos de texto OCR originais no GitHub raw content. "
            "Formato esperado: https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/teste  "
            "O caminho final montado será: {raw_base_url}/{DOC}/text/{filename}. "
            "Passe string vazia para omitir a URL (raw.file ainda será salvo)."
        ),
    )
    args = ap.parse_args()

    ensure_dirs(args.out)
    idx = load_index(args.index)

    # Carregar todos os records por volume
    volumes_pages: Dict[str, List[PageRecord]] = {}
    base_dir = args.index.parent
    shard_total = sum(len(vol.get("shards", [])) for vol in idx.get("volumes", []))
    shard_done = 0
    for vol in idx.get("volumes", []):
        vid = vol["id"]
        recs: List[PageRecord] = []
        for shard in vol.get("shards", []):
            shard_path = base_dir / shard["file"]
            shard_done += 1
            pct = (shard_done / max(1, shard_total)) * 100
            log(f"[{shard_done}/{shard_total} | {pct:5.1f}%] Lendo shard {shard_path}")
            recs.extend(load_shard(shard_path))
        volumes_pages[vid] = recs
        log(f"Volume {vid}: {len(recs)} páginas carregadas.")

    # Lookup keyword_original → canonical_id via SQLite + keywords.json
    log(f"Construindo lookup de keywords via {args.db} + {args.keywords_json} ...")
    kw_map = build_keyword_lookup(args.db, args.keywords_json)
    log(f"Lookup pronto: {len(kw_map)} entradas.")

    volumes_out = []
    for vol in idx.get("volumes", []):
        vid = vol["id"]
        recs = volumes_pages.get(vid, [])
        if not recs:
            continue
        pages_sorted = sorted([r.page for r in recs])
        page_first, page_last = pages_sorted[0], pages_sorted[-1]
        # snapshot global
        summary_global = next((r.summary_global for r in recs if r.summary_global), "")
        snapshot_path = args.out / "snapshots" / f"{vid}.json"
        snapshot_obj = {
            "schema_version": 1,
            "volume_id": vid,
            "block_type": "snapshots",
            "snapshots": [
                {
                    "id": f"snap:{vid}:global",
                    "kind": "volume_summary",
                    "label": "Resumo global",
                    "summary": summary_global or "",
                    "keyword_ids": [],
                }
            ]
        }
        write_json(snapshot_path, snapshot_obj)

        # blocos de páginas
        blocks, page_files = page_blocks(recs, args.page_block_size, vid, kw_map, args.raw_base_url)
        for b in blocks:
            out_path = args.out / "meta" / f"{vid}-pages-{b['block_index']:03d}.json"
            write_json(out_path, b)

        # manifesto do volume
        meta_obj = {
            "schema_version": 1,
            "id": vid,
            "collection_id": vid[:2],
            "page_first": page_first,
            "page_last": page_last,
            "page_count": len(recs),
            "page_block_size": args.page_block_size,
            "page_blocks": page_files,
            "snapshot_blocks": [
                {"index": 1, "file": f"snapshots/{vid}.json", "snapshot_count": 1}
            ],
            "stats": {
                "pages_with_summary": len(recs),
                "has_keywords": any(r.keywords for r in recs),
            },
        }
        write_json(args.out / "meta" / f"{vid}.json", meta_obj)

        # volumes.json entry
        volumes_out.append({
            "id": vid,
            "collection_id": vid[:2],
            "page_first": page_first,
            "page_last": page_last,
            "page_count": len(recs),
            "meta_url": f"meta/{vid}.json",
            "search_bundle": "pagefind/main",
            "viewer_url_template": f"/pdfocr/viewer?doc={vid}&page={{page}}",
        })
        log(f"Volume {vid}: snapshots/meta/page-blocks escritos.")

    volumes_json = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volumes": volumes_out,
    }
    write_json(args.out / "volumes.json", volumes_json)
    log(f"[OK] Renderização concluída. Volumes: {len(volumes_out)}")


if __name__ == "__main__":
    main()

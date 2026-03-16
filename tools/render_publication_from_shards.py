#!/usr/bin/env python3
"""
Gera artefatos do site (volumes.json, manifests, snapshots, dicionário de keywords)
a partir dos shards de enriquecimento em data/shards/enrichment/.

Uso:
    python tools/render_publication_from_shards.py \
        --index data/shards/enrichment/index.json \
        --out web/public \
        --page-block-size 100

Nada depende do SQLite; tudo vem dos shards NDJSON.
"""
from __future__ import annotations

import argparse
import json
import math
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


def slugify_keyword(label: str) -> str:
    norm = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    norm = norm.lower().strip()
    out = []
    for ch in norm:
        if ch.isalnum():
            out.append(ch)
        elif ch in [' ', '-', '_', '/', '.']:
            out.append('-')
    slug = ''.join(out).strip('-')
    slug = '-'.join([p for p in slug.split('-') if p])
    return slug or "kw"


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


def build_keywords_dict(volumes_pages: Dict[str, List[PageRecord]]) -> Tuple[Dict[str, str], List[dict]]:
    """Cria dicionário global de keywords a partir das páginas.

    Inclui labels que aparecem em keywords ou nas categorias do LLM.
    """
    label_to_id: Dict[str, str] = {}
    items: List[dict] = []
    slug_counts: Dict[str, int] = {}

    def add_label(label: str) -> None:
        label = label.strip()
        if not label:
            return
        if label in label_to_id:
            return
        slug = slugify_keyword(label)
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        suffix = slug_counts[slug]
        kid = f"k:{slug}" if suffix == 1 else f"k:{slug}-{suffix}"
        label_to_id[label] = kid
        items.append({"id": kid, "label": label})

    for recs in volumes_pages.values():
        for r in recs:
            for kw in r.keywords:
                if isinstance(kw, str):
                    add_label(kw)
            for cat_items in r.keyword_categories.values():
                for kw in cat_items:
                    if isinstance(kw, str):
                        add_label(kw)

    items.sort(key=lambda x: x["label"].lower())
    return label_to_id, items


def load_keywords_dict(path: Path) -> Tuple[Dict[str, str], List[dict]]:
    """Lê keywords.json existente e retorna (label->id, items)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    label_to_id = {it.get("label"): it.get("id") for it in items if it.get("label") and it.get("id")}
    return label_to_id, items


def page_blocks(recs: List[PageRecord], block_size: int, volume_id: str, keyword_ids: Dict[str, str]) -> Tuple[List[dict], List[dict]]:
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

            block["pages"].append({
                "page": r.page,
                "label": str(r.page),
                "summary_page": r.summary_page,
                "summary_global": r.summary_global,
                "created_at": r.created_at,
                "keyword_ids": kws,
                "keyword_categories": kw_cats_ids,
                "snapshot_ids": [f"snap:{volume_id}:global"],
            })
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
    ap.add_argument("--reuse-keywords-json", action="store_true", help="Não re-gerar dict/keywords.json se já existir; usa-o para mapear IDs.")
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

    # Dicionário global de keywords
    dict_path = args.out / "dict" / "keywords.json"
    if args.reuse_keywords_json and dict_path.exists():
        log(f"Reutilizando dicionário existente em {dict_path}")
        kw_map, kw_items = load_keywords_dict(dict_path)
    else:
        log("Gerando dicionário global de keywords...")
        kw_map, kw_items = build_keywords_dict(volumes_pages)
        write_json(dict_path, {"items": kw_items})
        log(f"Keywords distintas: {len(kw_items)}")

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
        blocks, page_files = page_blocks(recs, args.page_block_size, vid, kw_map)
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

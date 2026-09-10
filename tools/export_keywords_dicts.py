#!/usr/bin/env python3
"""
Gera dicionários de keywords para o site a partir de `data/patristica_keywords.db`.

Saídas em `web/public/dict/` (configurável via --out):
  - keywords.json          : catálogo canônico com contagem, grupo e flags.
  - keywords_lookup.json   : lookup leve para renderizadores e indexadores.
  - keywords_manifest.json : manifesto dos shards de keywords por consumo.
  - keywords/*.json        : shards alfabéticos do catálogo navegável.
  - keywords_top.json      : top N termos NÃO-escritura por frequência real.
  - keyword_groups.json    : metadados consumidos pelos grupos temáticos.

A frequência real é calculada a partir de `keyword_occurrence` no SQLite.

Uso típico:
  python tools/export_keywords_dicts.py --db data/patristica_keywords.db \
      --out web/public/dict --top 80 --min-count 1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DEFAULT_DB = Path("data/patristica_keywords.db")
DEFAULT_OUT = Path("web/public/dict")


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


@dataclass
class KeywordRow:
    id: int
    label: str
    label_norm: str
    count: int
    group_id: Optional[int]
    is_noise: int
    status: str
    nome_canonico: str
    nome_canonico_original: str
    is_scripture_citation: bool

    @property
    def slug_id(self) -> str:
        # label_norm já é ASCII-friendly (sem diacríticos gregos/árabes/siríacos)
        base = self.label_norm or self.label
        return f"k:{slugify(base)}"

    @property
    def canonical_slug_id(self) -> str:
        """Slug baseado no nome canônico do grupo (para itens não-scripture).
        Tenta nome_canonico (já normalizado), depois label_norm, depois usa
        o group_id como âncora para evitar colisões em textos não-ASCII.
        """
        for base in (self.nome_canonico, self.label_norm, self.label):
            s = slugify(base or "")
            if s and s != "kw":
                return f"k:{s}"
        # Último recurso: group_id garante unicidade mesmo para textos 100% não-ASCII
        return f"k:g{self.group_id}"


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")
    return con


def fetch_keywords(con: sqlite3.Connection) -> List[KeywordRow]:
    sql = """
        SELECT
            k.id,
            k.keyword_norm AS norm,
            k.keyword_original AS label,
            k.hdbscan_group_id AS group_id,
            k.is_noise,
            k.status,
            COALESCE(occ.cnt, 0) AS cnt,
            cc.nome_canonico AS nome_canonico,
            cc.nome_canonico_original AS nome_canonico_original,
            k.is_scripture_citation
        FROM keywords k
        LEFT JOIN (
            SELECT keyword_id, COUNT(*) AS cnt
            FROM keyword_occurrence
            GROUP BY keyword_id
        ) occ ON occ.keyword_id = k.id
        LEFT JOIN cluster_canonical_names cc ON cc.group_id = k.hdbscan_group_id
        ORDER BY k.id
    """
    rows = []
    for r in con.execute(sql):
        rows.append(
            KeywordRow(
                id=int(r["id"]),
                label=r["label"],
                label_norm=r["norm"] or "",
                count=int(r["cnt"] or 0),
                group_id=r["group_id"],
                is_noise=int(r["is_noise"] or 0),
                status=r["status"] or "pending",
                nome_canonico=r["nome_canonico"] or "",
                nome_canonico_original=r["nome_canonico_original"] or "",
                is_scripture_citation=bool(r["is_scripture_citation"] == 1),
            )
        )
    return rows


def fetch_group_page_counts(con: sqlite3.Connection) -> Dict[int, int]:
    """Conta páginas distintas por tema, sem inflar sinônimos na mesma página."""
    sql = """
        SELECT
            k.hdbscan_group_id AS group_id,
            COUNT(DISTINCT o.pagina_id) AS page_count
        FROM keywords k
        JOIN keyword_occurrence o ON o.keyword_id = k.id
        WHERE k.hdbscan_group_id IS NOT NULL
          AND k.hdbscan_group_id >= 0
          AND COALESCE(k.is_scripture_citation, 0) = 0
        GROUP BY k.hdbscan_group_id
    """
    return {
        int(row["group_id"]): int(row["page_count"] or 0)
        for row in con.execute(sql)
    }


def build_groups(
    rows: Iterable[KeywordRow],
    group_count: Dict[int, int],
    top_per_group: int,
    min_count: int,
) -> Dict[int, dict]:
    groups: Dict[int, dict] = {}
    for kw in rows:
        gid = kw.group_id
        if gid is None or gid < 0 or kw.is_scripture_citation:
            continue
        agg = group_count.get(gid, 0)
        if agg < min_count:
            continue
        g = groups.setdefault(
            gid,
            {
                "id": gid,
                "total": agg,  # frequência agregada já calculada
                "is_noise": 0,
                "top_keywords": [],
            },
        )
        # total já está correto, não somar novamente
        _ = g

    # ordenar keywords por grupo e preencher amostras
    by_group: Dict[int, List[KeywordRow]] = {}
    for kw in rows:
        gid = kw.group_id
        if (
            gid is None
            or gid < 0
            or kw.is_scripture_citation
            or group_count.get(gid, 0) < min_count
        ):
            continue
        by_group.setdefault(gid, []).append(kw)

    for gid, kws in by_group.items():
        kws_sorted = sorted(kws, key=lambda k: k.count, reverse=True)
        groups[gid]["top_keywords"] = [
            {"label": k.label, "count": k.count} for k in kws_sorted[:top_per_group]
        ]
    return groups


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def canonical_group_rows(rows: Iterable[KeywordRow]) -> Dict[int, KeywordRow]:
    by_group: Dict[int, List[KeywordRow]] = {}
    for kw in rows:
        if kw.group_id is None or kw.group_id < 0 or kw.is_scripture_citation:
            continue
        by_group.setdefault(kw.group_id, []).append(kw)

    representatives: Dict[int, KeywordRow] = {}
    for gid, members in by_group.items():
        representatives[gid] = max(
            members,
            key=lambda kw: (
                bool(kw.nome_canonico_original),
                kw.count,
                -kw.id,
            ),
        )
    return representatives


def build_catalog_items(
    rows: List[KeywordRow],
    group_count: Dict[int, int],
    min_count: int,
) -> tuple[List[dict], Dict[int, str], Dict[int, str]]:
    items: List[dict] = []
    canonical_ids: Dict[int, str] = {}
    scripture_ids: Dict[int, str] = {}

    for gid, kw in canonical_group_rows(rows).items():
        real_count = group_count.get(gid, kw.count)
        if real_count < min_count:
            continue
        label = kw.nome_canonico_original or kw.label
        item_id = kw.canonical_slug_id
        canonical_ids[gid] = item_id
        items.append(
            {
                "id": item_id,
                "label": label,
                "count": real_count,
                "group_id": gid,
                "iscit": False,
            }
        )

    for kw in rows:
        if not kw.is_scripture_citation or kw.count < min_count:
            continue
        item_id = kw.slug_id
        scripture_ids[kw.id] = item_id
        items.append(
            {
                "id": item_id,
                "label": kw.label,
                "count": kw.count,
                "group_id": kw.group_id,
                "iscit": True,
            }
        )

    deduped = {item["id"]: item for item in items}
    return (
        sorted(deduped.values(), key=lambda item: item["label"].casefold()),
        canonical_ids,
        scripture_ids,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def keyword_shard_key(keyword_id: str) -> str:
    slug = (keyword_id or "").removeprefix("k:").strip().lower()
    if not slug:
        return "other"
    first = slug[0]
    if first.isdigit():
        return "0-9"
    if first.isalpha():
        return first
    if slug.startswith("g"):
        return "g"
    return "other"


def main():
    ap = argparse.ArgumentParser(
        description="Exporta dicionários de keywords com grupos HDBSCAN para o site."
    )
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help="SQLite patristica_keywords.db"
    )
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="Diretório de saída (dict)"
    )
    ap.add_argument(
        "--top", type=int, default=3000, help="Quantidade no keywords_top.json"
    )
    ap.add_argument(
        "--min-count", type=int, default=1, help="Mínimo de ocorrências para considerar"
    )
    ap.add_argument(
        "--top-per-group",
        type=int,
        default=5,
        help="Quantidade de palavras por grupo em keyword_groups.json",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Executar em modo de teste (sem gravação)",
    )
    args = ap.parse_args()
    con = connect(args.db)
    rows = fetch_keywords(con)
    group_count = fetch_group_page_counts(con)
    con.close()

    # ── Pré-computar frequência agregada por group_id ──────────────────────────
    # Um conceito conta no máximo uma vez por página. Somar ocorrências dos
    # sinônimos inflava os temas e ainda misturava citações bíblicas no total.

    items, _, _ = build_catalog_items(
        rows,
        group_count=group_count,
        min_count=args.min_count,
    )

    lookup_items = [
        {
            "id": item["id"],
            "label": item["label"],
            "count": item["count"],
            "iscit": item["iscit"],
            "group_id": item["group_id"],
        }
        for item in items
    ]

    shard_map: Dict[str, List[dict]] = {}
    for item in items:
        shard_key = keyword_shard_key(item["id"])
        shard_map.setdefault(shard_key, []).append(item)

    shard_manifest = []
    for shard_key in sorted(shard_map.keys()):
        shard_items = sorted(shard_map[shard_key], key=lambda x: x["label"].lower())
        shard_manifest.append(
            {
                "id": shard_key,
                "path": f"dict/keywords/{shard_key}.json",
                "count": len(shard_items),
                "first_label": shard_items[0]["label"] if shard_items else "",
                "last_label": shard_items[-1]["label"] if shard_items else "",
            }
        )

    if args.dry_run:
        print(
            f"[DRY RUN] keywords: {len(items)} | lookup: {len(lookup_items)} | "
            f"shards: {len(shard_manifest)} | top: {min(len(items), args.top)}"
        )
        return

    write_json(args.out / "keywords.json", {"items": items})
    write_json(args.out / "keywords_lookup.json", {"items": lookup_items})
    write_json(
        args.out / "keywords_manifest.json",
        {
            "schema_version": 1,
            "strategy": "alpha",
            "generated_at": now_iso(),
            "lookup_path": "dict/keywords_lookup.json",
            "full_path": "dict/keywords.json",
            "top_path": "dict/keywords_top.json",
            "shards": shard_manifest,
        },
    )

    for shard_key, shard_items in shard_map.items():
        shard_path = args.out / "keywords" / f"{shard_key}.json"
        write_json(shard_path, {"items": sorted(shard_items, key=lambda x: x["label"].lower())})

    # keywords_top.json — apenas temas (iscit=false), ordenados por frequência agregada
    # Usado pela nuvem de tags da página inicial; citações bíblicas são excluídas
    # porque dominam o top por volume puro mas têm baixo valor de descoberta.
    top_items = sorted(
        [x for x in items if not x.get("iscit", False)],
        key=lambda x: x.get("count", 0),
        reverse=True,
    )[: args.top]
    write_json(args.out / "keywords_top.json", {"items": top_items})

    # keyword_groups.json
    groups = build_groups(
        rows,
        group_count=group_count,
        top_per_group=args.top_per_group,
        min_count=args.min_count,
    )
    groups_sorted = sorted(groups.values(), key=lambda g: g["total"], reverse=True)
    write_json(args.out / "keyword_groups.json", {"groups": groups_sorted})

    print(
        f"[OK] keywords: {len(items)} | lookup: {len(lookup_items)} | "
        f"shards: {len(shard_manifest)} | top: {len(top_items)} | "
        f"groups: {len(groups_sorted)}"
    )


if __name__ == "__main__":
    main()

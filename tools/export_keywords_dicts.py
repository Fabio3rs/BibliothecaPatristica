#!/usr/bin/env python3
"""
Gera dicionários de keywords para o site a partir de `data/patristica_keywords.db`.

Saídas em `web/public/dict/` (configurável via --out):
  - keywords.json        : todos os termos com contagem, grupo e flags.
  - keywords_top.json    : top N termos por contagem (para UI leve).
  - keyword_groups.json  : metadados por grupo HDBSCAN.

Uso típico:
  python tools/export_keywords_dicts.py --db data/patristica_keywords.db \
      --out web/public/dict --top 3000 --min-count 1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DEFAULT_DB = Path("data/patristica_keywords.db")
DEFAULT_OUT = Path("web/public/dict")


def slugify(label: str) -> str:
    norm = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode("ascii")
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


@dataclass
class KeywordRow:
    id: int
    label: str
    count: int
    group_id: Optional[int]
    is_noise: int
    status: str

    @property
    def slug_id(self) -> str:
        return f"k:{slugify(self.label)}"


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")
    return con


def fetch_keywords(con: sqlite3.Connection, include_noise: bool) -> List[KeywordRow]:
    sql = """
        SELECT
            k.id,
            k.keyword_original AS label,
            COALESCE(k.hdbscan_group_id, kc.cluster_id) AS group_id,
            k.is_noise,
            k.status,
            COUNT(ko.id) AS cnt
        FROM keywords k
        LEFT JOIN keyword_occurrence ko ON ko.keyword_id = k.id
        LEFT JOIN keyword_clusters kc ON kc.keyword_id = k.id
        GROUP BY k.id
    """
    rows = []
    for r in con.execute(sql):
        if (not include_noise) and int(r["is_noise"] or 0) == 1:
            continue
        rows.append(
            KeywordRow(
                id=int(r["id"]),
                label=r["label"],
                count=int(r["cnt"] or 0),
                group_id=r["group_id"],
                is_noise=int(r["is_noise"] or 0),
                status=r["status"] or "pending",
            )
        )
    return rows


def build_groups(rows: Iterable[KeywordRow], top_per_group: int, min_count: int) -> Dict[int, dict]:
    groups: Dict[int, dict] = {}
    for kw in rows:
        gid = kw.group_id
        if gid is None or gid == -1:
            continue
        if kw.count < min_count:
            continue
        g = groups.setdefault(gid, {"id": gid, "total": 0, "is_noise": 0, "top_keywords": [], "sample_ids": []})
        g["total"] += kw.count
    # ordenar keywords por grupo e preencher amostras
    by_group: Dict[int, List[KeywordRow]] = {}
    for kw in rows:
        gid = kw.group_id
        if gid is None or gid == -1 or kw.count < min_count:
            continue
        by_group.setdefault(gid, []).append(kw)

    for gid, kws in by_group.items():
        kws_sorted = sorted(kws, key=lambda k: k.count, reverse=True)
        groups[gid]["top_keywords"] = [
            {"label": k.label, "count": k.count} for k in kws_sorted[:top_per_group]
        ]
        groups[gid]["sample_ids"] = [k.slug_id for k in kws_sorted[:top_per_group]]
    return groups


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Exporta dicionários de keywords com grupos HDBSCAN para o site.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite patristica_keywords.db")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Diretório de saída (dict)")
    ap.add_argument("--top", type=int, default=3000, help="Quantidade no keywords_top.json")
    ap.add_argument("--min-count", type=int, default=1, help="Mínimo de ocorrências para considerar")
    ap.add_argument("--top-per-group", type=int, default=5, help="Quantidade de palavras por grupo em keyword_groups.json")
    ap.add_argument("--include-noise", action="store_true", help="Incluir is_noise=1")
    args = ap.parse_args()

    con = connect(args.db)
    rows = fetch_keywords(con, include_noise=args.include_noise)
    con.close()

    # keywords.json
    items = []
    for kw in rows:
        if kw.count < args.min_count:
            continue
        items.append(
            {
                "id": kw.slug_id,
                "label": kw.label,
                "count": kw.count,
                "group_id": kw.group_id,
                "is_noise": kw.is_noise,
                "status": kw.status,
            }
        )
    items.sort(key=lambda x: x["label"].lower())
    write_json(args.out / "keywords.json", {"items": items})

    # keywords_top.json
    top_items = sorted(items, key=lambda x: x.get("count", 0), reverse=True)[: args.top]
    write_json(args.out / "keywords_top.json", {"items": top_items})

    # keyword_groups.json
    groups = build_groups(rows, top_per_group=args.top_per_group, min_count=args.min_count)
    groups_sorted = sorted(groups.values(), key=lambda g: g["total"], reverse=True)
    write_json(args.out / "keyword_groups.json", {"groups": groups_sorted})

    print(f"[OK] keywords: {len(items)} | top: {len(top_items)} | groups: {len(groups_sorted)}")


if __name__ == "__main__":
    main()


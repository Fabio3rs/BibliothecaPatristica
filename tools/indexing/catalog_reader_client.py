"""
Independent Catalog Reader Client
Sprint 19 & Sprint 174 of Onomastic Catalog Recovery

Strictly read-only client (mode=ro, PRAGMA query_only=ON) providing API-ready
search, item inspection with candidate decompression, configurable corpus-root
relative path resolution, taxonomic filtering, and structured web-ready JSON export.
"""

import argparse
import json
import os
import sqlite3
import zlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

DEFAULT_DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
DEFAULT_CORPUS_ROOT = '/homessddata/Projects/pdfocr'


class CatalogReaderClient:
    def __init__(self, db_path: str = DEFAULT_DB_PATH, corpus_root: Optional[str] = DEFAULT_CORPUS_ROOT):
        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self.corpus_root = os.path.abspath(corpus_root) if corpus_root else None

    def _get_connection(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _relativize_path(self, path: Optional[str]) -> Optional[str]:
        if not path or not self.corpus_root:
            return path
        try:
            abs_p = os.path.abspath(path)
            if abs_p.startswith(self.corpus_root):
                rel = os.path.relpath(abs_p, self.corpus_root)
                return rel
            return path
        except Exception:
            return path

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            total = cur.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            with_dest = cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'").fetchone()[0]
            source_only = cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'").fetchone()[0]
            candidates = cur.execute("SELECT COUNT(*) FROM candidate_storage").fetchone()[0]
            volumes = cur.execute("SELECT COUNT(DISTINCT volume) FROM items").fetchone()[0]

            return {
                "total_items": total,
                "with_destinations": with_dest,
                "source_only": source_only,
                "coverage_pct": round((with_dest / total) * 100, 4) if total else 0.0,
                "total_candidates": candidates,
                "total_volumes": volumes,
                "corpus_root": self.corpus_root,
                "read_only": True
            }

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            row = cur.execute("""
                SELECT id, group_id, volume, section, label, ref_raw, source, state, metadata_json
                FROM items
                WHERE id = ?
            """, (item_id,)).fetchone()

            if not row:
                return None

            item_dict = dict(row)
            item_dict["source_relative"] = self._relativize_path(item_dict.get("source"))

            meta = {}
            if item_dict.get("metadata_json"):
                try:
                    meta = json.loads(item_dict["metadata_json"])
                except Exception:
                    meta = {}
            item_dict["metadata"] = meta
            item_dict["index_category"] = meta.get("index_category", "INDEX_GENERALIS")
            item_dict["entity_type"] = meta.get("entity_type", "ONOMASTICA_HISTORICA")

            # Fetch decompressed candidates
            cand_rows = cur.execute("""
                SELECT file, rank, score, navigable, candidate_json
                FROM candidate_storage
                WHERE item_id = ?
                ORDER BY rank ASC
            """, (item_id,)).fetchall()

            candidates = []
            for c in cand_rows:
                raw_file = c["file"]
                c_dict = {
                    "file": raw_file,
                    "file_relative": self._relativize_path(raw_file),
                    "rank": c["rank"],
                    "score": c["score"],
                    "navigable": bool(c["navigable"])
                }
                if c["candidate_json"]:
                    try:
                        c_dict["details"] = json.loads(zlib.decompress(c["candidate_json"]).decode('utf-8'))
                    except Exception:
                        c_dict["details"] = {}
                candidates.append(c_dict)

            item_dict["candidates"] = candidates
            return item_dict

    def search(
        self,
        query: str = "",
        volume: Optional[str] = None,
        section: Optional[str] = None,
        category: Optional[str] = None,
        entity_type: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            conditions = []
            params: List[Any] = []

            if query:
                conditions.append("(label LIKE ? OR ref_raw LIKE ?)")
                q_param = f"%{query}%"
                params.extend([q_param, q_param])

            if volume:
                conditions.append("volume = ?")
                params.append(volume.upper())

            if section:
                conditions.append("section LIKE ?")
                params.append(f"%{section}%")

            if state:
                conditions.append("state = ?")
                params.append(state)

            if category:
                conditions.append("json_extract(metadata_json, '$.index_category') = ?")
                params.append(category.upper())

            if entity_type:
                conditions.append("json_extract(metadata_json, '$.entity_type') = ?")
                params.append(entity_type.upper())

            where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)

            sql = f"""
                SELECT id, volume, section, label, ref_raw, source, state, metadata_json
                FROM items
                {where_sql}
                ORDER BY volume, id
                LIMIT ?
            """
            rows = cur.execute(sql, params).fetchall()

            results = []
            for r in rows:
                it = dict(r)
                meta_str = it.pop("metadata_json", None)
                meta = {}
                if meta_str:
                    try:
                        meta = json.loads(meta_str)
                    except Exception:
                        meta = {}
                it["index_category"] = meta.get("index_category", "INDEX_GENERALIS")
                it["entity_type"] = meta.get("entity_type", "ONOMASTICA_HISTORICA")
                it["source_relative"] = self._relativize_path(it.get("source"))

                # fetch top candidate file if with_destinations
                if it["state"] == "with_destinations":
                    cand = cur.execute("""
                        SELECT file, score FROM candidate_storage
                        WHERE item_id = ? ORDER BY rank ASC LIMIT 1
                    """, (it["id"],)).fetchone()
                    if cand:
                        it["top_destination"] = cand["file"]
                        it["top_destination_relative"] = self._relativize_path(cand["file"])
                        it["top_score"] = cand["score"]
                results.append(it)

            return results

    def export_web_json(
        self,
        query: str = "",
        volume: Optional[str] = None,
        section: Optional[str] = None,
        category: Optional[str] = None,
        entity_type: Optional[str] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Produces clean, web-ready structured JSON suitable for UI consumers and APIs.
        """
        raw_results = self.search(
            query=query,
            volume=volume,
            section=section,
            category=category,
            entity_type=entity_type,
            limit=limit
        )

        entries = []
        for r in raw_results:
            full_item = self.get_item(r["id"])
            if not full_item:
                continue

            entry = {
                "id": full_item["id"],
                "label": full_item["label"],
                "volume": full_item["volume"],
                "section": full_item["section"],
                "category": full_item["index_category"],
                "entity_type": full_item["entity_type"],
                "ref_raw": full_item["ref_raw"],
                "state": full_item["state"],
                "source": {
                    "path": full_item["source"],
                    "relative_path": full_item["source_relative"]
                },
                "destinations": [
                    {
                        "rank": c["rank"],
                        "score": c["score"],
                        "path": c["file"],
                        "relative_path": c["file_relative"],
                        "reasons": c.get("details", {}).get("reasons", []),
                        "snippet": c.get("details", {}).get("snippet", ""),
                        "witness": c.get("details", {}).get("witness", "")
                    }
                    for c in full_item.get("candidates", [])
                ]
            }
            entries.append(entry)

        return {
            "metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "filters": {
                    "volume": volume,
                    "section": section,
                    "category": category,
                    "entity_type": entity_type
                },
                "count": len(entries),
                "limit": limit
            },
            "entries": entries
        }


def main():
    parser = argparse.ArgumentParser(description="Query Patristica Onomastic Catalog in Read-Only Mode")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--corpus-root", default=DEFAULT_CORPUS_ROOT, help="Root folder of the corpus for relative paths")
    parser.add_argument("--stats", action="store_true", help="Show catalog summary statistics")
    parser.add_argument("--query", default="", help="Text search on label or reference")
    parser.add_argument("--volume", help="Filter by volume (e.g. PL161, PG095)")
    parser.add_argument("--section", help="Filter by section (e.g. INDEX AUCTORUM)")
    parser.add_argument("--category", help="Filter by macro category (e.g. AUCTORES, SCRIPTURA)")
    parser.add_argument("--entity-type", help="Filter by entity type (e.g. AUCTOR_PATRISTICUS)")
    parser.add_argument("--item", help="Inspect single item by ID with decompressed candidates")
    parser.add_argument("--limit", type=int, default=20, help="Maximum search results")
    parser.add_argument("--json-out", help="Export search results to JSON file")
    parser.add_argument("--web-json", action="store_true", help="Format JSON output as web-ready API structure")
    args = parser.parse_args()

    client = CatalogReaderClient(args.db, corpus_root=args.corpus_root)

    if args.stats:
        stats = client.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    if args.item:
        it = client.get_item(args.item)
        if it:
            print(json.dumps(it, indent=2, ensure_ascii=False))
        else:
            print(f"Item not found: {args.item}")
        return

    if args.web_json:
        payload = client.export_web_json(
            query=args.query,
            volume=args.volume,
            section=args.section,
            category=args.category,
            entity_type=args.entity_type,
            limit=args.limit
        )
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            print(f"Exported {payload['metadata']['count']} web-ready entries to {args.json_out}")
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if args.query or args.volume or args.section or args.category or args.entity_type:
        results = client.search(
            query=args.query,
            volume=args.volume,
            section=args.section,
            category=args.category,
            entity_type=args.entity_type,
            limit=args.limit
        )
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump({"query": args.query, "volume": args.volume, "count": len(results), "results": results}, f, indent=2, ensure_ascii=False)
            print(f"Exported {len(results)} results to {args.json_out}")
        else:
            print(f"Found {len(results)} items:")
            for r in results:
                top = r.get("top_destination_relative", r.get("top_destination", "None"))
                print(f"[{r['volume']}] [{r['index_category']}] {r['label'][:40]:40s} | {r['ref_raw'][:12]:12s} -> {top}")
        return

    # Default action if no flags provided
    stats = client.get_stats()
    print("Patristica Onomastic Catalog Reader Client (mode=ro)")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

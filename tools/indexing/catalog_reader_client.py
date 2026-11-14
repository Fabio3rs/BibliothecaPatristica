"""
Independent Catalog Reader Client
Sprint 19 of Onomastic Catalog Recovery

Strictly read-only client (mode=ro, PRAGMA query_only=ON) providing API-ready
search, item inspection with candidate decompression, and structured JSON export.
"""

import argparse
import json
import os
import sqlite3
import zlib
from typing import Dict, List, Optional, Any

DEFAULT_DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class CatalogReaderClient:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        return conn

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
                "coverage_pct": round((with_dest / total) * 100, 2) if total else 0.0,
                "total_candidates": candidates,
                "total_volumes": volumes,
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
            if item_dict.get("metadata_json"):
                try:
                    item_dict["metadata"] = json.loads(item_dict["metadata_json"])
                except Exception:
                    item_dict["metadata"] = {}

            # Fetch decompressed candidates
            cand_rows = cur.execute("""
                SELECT file, rank, score, navigable, candidate_json
                FROM candidate_storage
                WHERE item_id = ?
                ORDER BY rank ASC
            """, (item_id,)).fetchall()

            candidates = []
            for c in cand_rows:
                c_dict = {
                    "file": c["file"],
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
        query: str,
        volume: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            conditions = []
            params = []

            if query:
                conditions.append("(label LIKE ? OR ref_raw LIKE ?)")
                q_param = f"%{query}%"
                params.extend([q_param, q_param])

            if volume:
                conditions.append("volume = ?")
                params.append(volume.upper())

            if state:
                conditions.append("state = ?")
                params.append(state)

            where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)

            sql = f"""
                SELECT id, volume, section, label, ref_raw, source, state
                FROM items
                {where_sql}
                ORDER BY volume, id
                LIMIT ?
            """
            rows = cur.execute(sql, params).fetchall()

            results = []
            for r in rows:
                it = dict(r)
                # fetch top candidate file if with_destinations
                if it["state"] == "with_destinations":
                    cand = cur.execute("""
                        SELECT file, score FROM candidate_storage
                        WHERE item_id = ? ORDER BY rank ASC LIMIT 1
                    """, (it["id"],)).fetchone()
                    if cand:
                        it["top_destination"] = cand["file"]
                        it["top_score"] = cand["score"]
                results.append(it)

            return results


def main():
    parser = argparse.ArgumentParser(description="Query Patristica Onomastic Catalog in Read-Only Mode")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--stats", action="store_true", help="Show catalog summary statistics")
    parser.add_argument("--query", help="Text search on label or reference")
    parser.add_argument("--volume", help="Filter by volume (e.g. PL161, PG095)")
    parser.add_argument("--item", help="Inspect single item by ID with decompressed candidates")
    parser.add_argument("--limit", type=int, default=20, help="Maximum search results")
    parser.add_argument("--json-out", help="Export search results to JSON file")
    args = parser.parse_args()

    client = CatalogReaderClient(args.db)

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

    if args.query or args.volume:
        results = client.search(query=args.query or "", volume=args.volume, limit=args.limit)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump({"query": args.query, "volume": args.volume, "count": len(results), "results": results}, f, indent=2, ensure_ascii=False)
            print(f"Exported {len(results)} results to {args.json_out}")
        else:
            print(f"Found {len(results)} items:")
            for r in results:
                top = r.get("top_destination", "None")
                print(f"[{r['volume']}] {r['label'][:45]:45s} | {r['ref_raw'][:15]:15s} -> {os.path.basename(top)}")
        return

    # Default action if no flags provided
    stats = client.get_stats()
    print("Patristica Onomastic Catalog Reader Client (mode=ro)")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

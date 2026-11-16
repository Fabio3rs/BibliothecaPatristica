"""
PL064 Anicius Manlius Severinus Boethius (Index Generalis in Boethium) Locator
Sprint 160 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL064:
1. Editorial Context:
   - PL063 and PL064 contain the complete works of Boethius (ed. Glareanus / Martianus Rota / Migne).
     PL063 forms Tomus I (Opera Philosophica, Arithmetica, Musica, Geometrica).
     PL064 forms Tomus II (Opera Dialectica, Theologica, Indices).
   - In PL064 physical pp. 793-832 (cols 1585-1664):
     'INDEX GENERALIS IN OMNIA BOETII OPERA.'
   - In col. 1616 (physical p. 808 / 7fcedb40-32e4-450c-ab42-cad043deba4b-808.txt):
     'Geometria ubi et quomodo inventa,
      Geometriæ unde dicta, I, 1359.
      Geometria quid? ibid.
      Geometriæ intentio, ibid.
      Geometriæ utilitas, ibid.
      Geometriæ ordo, ibid.
      Geometrici tituli inscriptio, ibid.
      Geometriæ proprius codex, ibid.
      Geometrica demonstratio, ibid.'
   - The residual item:
     id: 93497edb4c4c511915dcbabe73a851de1ea65518a6b3276a59919ee8f6222317
     'Geometriæ unde dicta' -> 'I, 1359'
   - Textual and editorial analysis reveals:
     - 'I, 1359' refers directly to Tomus I (PL063), col. 1359, which sits on physical p. 682
       (865f3843-e11a-45cb-b593-aa35b4defd46-682.txt, cols 1359-1360, Euclidis Geometrica a Boethio translata lib. II):
       'D. Qui ordo est geometriæ in disciplinis?
        M. Aliquatenus post arithmeticam servus est, aliquatenus tertius.
        D. Tituli inscriptio quomodo intelligatur?
        M. Est enim tituli præscriptio elementorum...
        D. Si proprius codex?
        M. Codex iste secundum dispositionem Euclidis esse dicitur...
        D. Quæ sunt in demonstratione geometrica?
        M. Propositio, dispositio, distributio, descriptio, demonstratio et conclusio.'
     - Preceding col. 1358 sits on physical p. 681 (865f3843-e11a-45cb-b593-aa35b4defd46-681.txt).
     - Primary index source sits on physical p. 808 of PL064 (7fcedb40-32e4-450c-ab42-cad043deba4b-808.txt).
"""

import os
import json
import zlib
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any

SUMMARY_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"
DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "93497edb4c4c511915dcbabe73a851de1ea65518a6b3276a59919ee8f6222317": {
        "label": "Geometriæ unde dicta",
        "ref_raw": "I, 1359",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL063/text/865f3843-e11a-45cb-b593-aa35b4defd46-682.txt",
                "physical": 682,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "pl063_tom_i_col_1359",
                    "boethii_geometria_dialogus",
                    "ordo_geometriae_tituli_inscriptio"
                ],
                "snippet": "1359 AN. MANL. SEV. BOETII 1360 ... D. Qui ordo est geometriæ in disciplinis? M. Aliquatenus post arithmeticam servus est, aliquatenus tertius. D. Tituli inscriptio quomodo intelligatur? ... D. Si proprius codex? M. Codex iste secundum dispositionem Euclidis... D. Quæ sunt in demonstratione geometrica?...",
                "witness": "PL063 col. 1359, Boethii Demonstratio Artis Geometricae (physical p. 682)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL063/text/865f3843-e11a-45cb-b593-aa35b4defd46-681.txt",
                "physical": 681,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl063_col_1357_1358",
                    "euclidis_geometrica_lib_ii",
                    "contextual_precedent"
                ],
                "snippet": "1357 EUCLIDIS GEOMETRICA LIB. II. 1358 ... agrorum intentionem et certamen tollere potest, prudentiam tamen hi mensores habere debent...",
                "witness": "PL063 cols 1357-1358, Euclidis Geometrica lib. II (physical p. 681)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL064/text/7fcedb40-32e4-450c-ab42-cad043deba4b-808.txt",
                "physical": 808,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_generalis_in_boetium_col_1616"
                ],
                "snippet": "Geometriæ unde dicta, I, 1359. Geometria quid? ibid. Geometriæ intentio, ibid. Geometriæ ordo, ibid.",
                "witness": "PL064 col. 1616, Index Generalis in omnia Boetii Opera (physical p. 808)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_160_pl064_boethius"):
    if not os.path.exists(summary_path):
        return

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM items")
    total_items = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    items_src_only = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT volume) FROM items")
    vol_count = c.fetchone()[0]
    conn.close()

    summary["total_items"] = total_items
    summary["items_with_destinations"] = items_with_dest
    summary["items_source_only"] = items_src_only
    summary["candidate_storage_records"] = cand_count
    summary["total_volumes"] = vol_count
    summary["coverage_percentage"] = round((items_with_dest / total_items) * 100, 4) if total_items else 0.0
    summary["last_campaign"] = campaign_name
    summary["last_sync"] = datetime.now(timezone.utc).isoformat()

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def apply_destinations(db_path: str = DB_PATH, dry_run: bool = True):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    fixed_count = 0
    new_candidates_count = 0

    for item_id, item_data in TARGET_ITEMS.items():
        c.execute("SELECT state, metadata_json FROM items WHERE id = ?", (item_id,))
        row = c.fetchone()
        if not row:
            continue

        current_state, meta_str = row
        meta = json.loads(meta_str) if meta_str else {}

        meta["verified"] = True
        meta["status"] = "resolved_single_target"
        meta["destinations"] = item_data["destinations"]
        meta["resolution_campaign"] = "sprint_160_pl064_boethius"

        if not dry_run:
            c.execute(
                "UPDATE items SET state = 'with_destinations', metadata_json = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), item_id)
            )

            # Insert candidates into candidate_storage
            for dest in item_data["destinations"]:
                cand_blob = zlib.compress(json.dumps(dest, ensure_ascii=False).encode("utf-8"))
                c.execute(
                    """
                    INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, dest["file"], dest["rank"], dest["score"], 1, cand_blob)
                )
                new_candidates_count += 1

        fixed_count += 1

    if not dry_run:
        conn.commit()
    conn.close()

    if not dry_run:
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_160_pl064_boethius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL064.")

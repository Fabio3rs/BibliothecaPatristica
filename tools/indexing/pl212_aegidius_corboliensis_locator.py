"""
PL212 Aegidius Corboliensis / Parisiensis (Aurora) Locator
Sprint 172 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL212:
1. Editorial Context:
   - PL212 contains Petri de Riga et Aegidii Parisiensis (Corboliensis) Fragmenta ex Aurora (cols 17-42).
   - In physical p. 13 (f6676cdc-a8c9-4cea-9bd9-51a7d8e5b019-013.txt, cols 17-18):
     '17 FRAGMENTA EX AURORA. 18'
     '[Hæc inter alia Auroræ adjecit Ægidius :
       1° Præfationem metricam, ad Odonem Parisiensem episcopum, quam ex codice Helmstad. exhibet Leyser, p. 737;
       2° Epilogum. Excipit Epilogum narratio quædam quæ quidem Ægidii nomen non præfert, ei tamen non incommode adjudicatur.
          Eam edidit laudatus Leyserus, p. 740.
       Adnectitur et Auroræ ejusdem Ægidii tractatus De pœna infernali metricus, ibid., p. 742.]'
   - The reference 'ibid., p. 742' is an editorial citation referring back to Polycarpi Leyseri
     'Historia poetarum et poematum medii aevi' (Halae Magd., 1721), p. 742, cited directly in the preceding sentences.
   - The text and verse additions of Aegidius to the Aurora follow immediately in cols 19-42 (physical pp. 14-25).
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
    "group:16a35f34a44772caf0111bd33b559acb4e269bf83d402dc30744f96c33818293": {
        "label": "Adnectitur et Auroræ ejusdem Ægidii tractatus De pœna infernali metricus",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL212/text/f6676cdc-a8c9-4cea-9bd9-51a7d8e5b019-013.txt",
                "physical": 13,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "primary_source_rubric",
                    "notitia_historico_litteraria",
                    "pl212_cols_17_18",
                    "leyser_historia_poetarum_ibid_citation"
                ],
                "snippet": "Adnectitur et Auroræ ejusdem Ægidii tractatus De pœna infernali metricus, ibid., p. 742.",
                "witness": "PL212 cols 17-18, Notitia in Fragmenta ex Aurora (physical p. 13)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL212/text/f6676cdc-a8c9-4cea-9bd9-51a7d8e5b019-014.txt",
                "physical": 14,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "aurora_carmina_aegidii",
                    "pl212_cols_19_20"
                ],
                "snippet": "19 PETRUS DE RIGA ET ÆGIDIUS PARIS. 20... fateor suasionem animus meus in dubio pependit...",
                "witness": "PL212 cols 19-20, Fragmenta ex Aurora (physical p. 14)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL212/text/f6676cdc-a8c9-4cea-9bd9-51a7d8e5b019-015.txt",
                "physical": 15,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "aurora_carmina_aegidii_continuatio",
                    "pl212_cols_21_22"
                ],
                "snippet": "21 FRAGMENTA EX AURORA. 22... Ut magis adverti, quidquid abesset ibi...",
                "witness": "PL212 cols 21-22, Fragmenta ex Aurora (physical p. 15)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_172_pl212_aegidius"):
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
        meta["resolution_campaign"] = "sprint_172_pl212_aegidius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_172_pl212_aegidius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL212.")

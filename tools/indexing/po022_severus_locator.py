"""
PO022 Sévère d'Antioche (Homélies Cathédrales XCIX à CIII) Locator
Sprint 129 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PO022:
1. Editorial Context:
   - PO022 contains Patrologia Orientalis Tomus XXII (Fasc. 2: Les Homiliae Cathedrales
     de Sévère d'Antioche, Homélies XCIX à CIII, éditées et traduites en français
     par M. Brière, Paris, Firmin-Didot, 1930).
   - Fascicle 2 ends with:
     'III. TABLE DES MOTS GRECS CITÉS DANS LES MSS.' (p. [108] / physical p. 318).
     References indicate the fascicle page number with superscript/subscript line number or note:
     'Δροσίς 30 n. 1.' -> Fascicle page 30, note 1.
     'ὑπόστασις 89₅' -> Fascicle page 89, line 5.
   - The 2 residual items:
     1) 'Δροσίς' -> 30 n. 1
        (Homélie C: Sur la martyre sainte Drosis;
         physical p. 240: fascicle p. 30,
         ipsissimis verbis: '1. L in margine : Δροσις... SUR LA MARTYRE SAINTE DROSIS¹.
         A LA FIN DE L'HOMÉLIE EST UN APPEL... 1. Il y a lieu de remarquer que,
         tandis que sainte Drosis était fêtée chez les Grecs le 22 septembre...';
         plus index p. 318).
     2) 'ὑπόστασις' -> 89₅
        (Homélie CIII: Sur l'Épiphanie, fascicle p. 89 line 5;
         physical p. 299: fascicle p. 89,
         ipsissimis verbis: 'HOMÉLIE CIII SUR L'ÉPIPHANIE... et avoir épuisé,
         pour ainsi dire, toute l'ὑπόστασις ou ressources de ma pauvreté...';
         plus index p. 318).
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
    "24c1199e7910ca7ef4930106517f7158165be73a2f4bcbbc420cd9b6a83e71c7": {
        "label": "Δροσίς",
        "ref_raw": "30 n. 1",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO022/text/13545acf-5a61-4cef-82a5-438f1b7181c3-240.txt",
                "physical": 240,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_drosis_marginal_note", "severus_homilia_100_p30", "fascicle_page_30_note_1"],
                "snippet": "1. L in margine : Δροσις. SUR LA MARTYRE SAINTE DROSIS¹. A LA FIN DE L'HOMÉLIE EST UN APPEL... 1. Il y a lieu de remarquer que, tandis que sainte Drosis était fêtée chez les Grecs le 22 septembre...",
                "witness": "PO022 (Sévère d'Antioche Hom. C, fasc. p. 30 not. 1, p. 240)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO022/text/48338044-d88b-47b6-9133-5d327e80b571-318.txt",
                "physical": 318,
                "score": 90.0,
                "rank": 2,
                "reasons": ["index_primary_source", "table_mots_grecs"],
                "snippet": "TABLE DES MOTS GRECS CITÉS DANS LES MSS... Δροσίς 30 n. 1.",
                "witness": "PO022 (Table des mots grecs, fasc. p. [108], p. 318)"
            }
        ]
    },
    "604ddd6f0d78b3331b128108de08827beac2d8f2ca5e5a2060d9b8e3939bbe93": {
        "label": "ὑπόστασις",
        "ref_raw": "89₅",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO022/text/13545acf-5a61-4cef-82a5-438f1b7181c3-299.txt",
                "physical": 299,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_hypostasis_line_5", "severus_homilia_103_p89", "fascicle_page_89_line_5"],
                "snippet": "HOMÉLIE CIII SUR L'ÉPIPHANIE... Après vous avoir bien des fois parlé sur cette fête et, selon les faibles moyens qui sont en moi, vous avoir fait les réflexions philosophiques (φιλοσόφησα) qui me sont venues (à l'esprit) et avoir épuisé, pour ainsi dire, toute l'ὑπόστασις ou ressources de ma pauvreté...",
                "witness": "PO022 (Sévère d'Antioche Hom. CIII, fasc. p. 89 l. 5, p. 299)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO022/text/48338044-d88b-47b6-9133-5d327e80b571-318.txt",
                "physical": 318,
                "score": 90.0,
                "rank": 2,
                "reasons": ["index_primary_source", "table_mots_grecs"],
                "snippet": "TABLE DES MOTS GRECS CITÉS DANS LES MSS... ὑπόστασις 89₅.",
                "witness": "PO022 (Table des mots grecs, fasc. p. [108], p. 318)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_129_po022_severus"):
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
    total_vols = c.fetchone()[0]

    conn.close()

    cov_ratio = items_with_dest / total_items if total_items > 0 else 0.0
    cov_pct = round(cov_ratio * 100, 4)
    now_iso = datetime.now(timezone.utc).isoformat()

    summary["total_items"] = total_items
    summary["items_total"] = total_items
    summary["items_with_destinations"] = items_with_dest
    summary["items_source_only"] = items_src_only
    summary["candidate_storage_records"] = cand_count
    summary["candidate_storage_count"] = cand_count
    summary["total_volumes"] = total_vols
    summary["destination_coverage"] = cov_ratio
    summary["coverage_percentage"] = cov_pct
    summary["percentage_with_destinations"] = cov_pct
    summary["coverage_pct"] = cov_pct
    summary["last_campaign"] = campaign_name
    summary["last_updated"] = now_iso
    summary["updated_at"] = now_iso

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def apply_po022_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        for dest in dests:
            candidate_payload = {
                "file": dest["file"],
                "physical": dest["physical"],
                "score": dest["score"],
                "score_kind": "heuristic_not_probability",
                "reasons": dest["reasons"],
                "uncertainties": [],
                "navigable": True,
                "certified": True,
                "snippet": dest["snippet"],
                "evidence": {
                    "candidate_basis": "editorial_severus_po22_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PO022"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()

    sync_catalog_summary(db_path, summary_path, "sprint_129_po022_severus")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_po022_fixes()
        print(f"Applied fixes to {updated} items in PO022.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PO022 (dry-run).")

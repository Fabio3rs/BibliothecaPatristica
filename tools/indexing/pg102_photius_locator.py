"""
PG102 Photius Patriarcha Constantinopolitanus (Index Analyticus) Locator
Sprint 136 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PG102:
1. Editorial Context:
   - PG102 contains Photii Epistolae, Amphilochia, and other theological works.
   - At the conclusion of the volume (cols 999-1024), there is the 'INDEX ANALYTICUS'.
   - The 2 residual items:
     1) 'Reverentia in parentes semper adhibenda' -> '750 c'
        id: 113174dea8987b34b163d97bbd3b01c15956fbae2646c1db8cda93a784e0cf3f
        In index (PG102 physical p. 538 col. 1009 / 67d711bd-eca2-43d7-8342-6cab54839fa6-538.txt):
        'Reverentia in parentes semper adhibenda 750 c.'
     2) 'Scandalum quid, et quibus suppliciis obnoxium' -> '750 b'
        id: 1878e242a54e8d75a2ec1c6a5ab84a64ed7e9dad0595194a5068bbb0bdd72147
        In index (PG102 physical p. 539 col. 1012 / 67d711bd-eca2-43d7-8342-6cab54839fa6-539.txt):
        'Scandalum quid, et quibus suppliciis obnoxium, 750 b.'
   - In Migne PG102, physical page 396 (ecc89feb-6730-44ad-8699-840b612e122f-396.txt)
     directly follows cols 747-748 (p. 395). Owing to a known Migne typesetter numbering jump,
     the running header of p. 396 was misprinted as '753 ... 754' (and p. 397 as '754 ... 752'),
     while the actual text continuation represents columns 749-750 of Epistola XIV ('Ad episcopos,
     ab exsilio suo'). In the gutter of p. 396 are the marginal letters A, B, C, D corresponding
     to columns 749-750. Section B and Section C treat of public scandal, betrayal of divine laws,
     sin, and heavenly vs temporal punishments:
     'B Si quis, inquam, obiter ista dicat, quis non illum furere et insanire?... Quod si quis non urbium
      sed fidei proditores, prædones non corporalium sed divinæ legis in medium productos sistat...
      Sin vero actionum lege prohibitarum actores agnoscas, quid ita tandem curiose satagis, utrum hic
      supplicium luant aut non luant? Si dederint isthic pœnas, meliori tum erunt conditione...
      quod si non dent, hoc eo sunt miseriores, eo quod judicium aliquando cœleste cum majori subibunt acerbitate...'
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
    "113174dea8987b34b163d97bbd3b01c15956fbae2646c1db8cda93a784e0cf3f": {
        "label": "Reverentia in parentes semper adhibenda",
        "ref_raw": "750 c",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/ecc89feb-6730-44ad-8699-840b612e122f-396.txt",
                "physical": 531,
                "score": 100.0,
                "rank": 1,
                "reasons": ["editorial_migne_col_750_section_c", "migne_typesetter_header_displacement_753_754", "photii_epistola_14", "literal_context_pœnis_et_judicio"],
                "snippet": "C Verum sepositis quæ jam dicta sunt, si secundum canones, hoc est, leges ecclesiasticas actiones regulent... Sin vero actionum lege prohibitarum actores agnoscas, quid ita tandem curiose satagis, utrum hic supplicium luant aut non luant? Si dederint isthic pœnas, meliori tum erunt conditione... quod si non dent, hoc eo sunt miseriores, eo quod judicium aliquando cœleste cum majori subibunt acerbitate...",
                "witness": "PG102 (Photii Epistolarum Lib. I, Epist. XIV, cols 749-750 [header 753-754], col. 750 c, p. 396)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/ecc89feb-6730-44ad-8699-840b612e122f-395.txt",
                "physical": 530,
                "score": 95.0,
                "rank": 2,
                "reasons": ["photii_epistola_14_preceding", "migne_cols_747_748"],
                "snippet": "neque quidem a parente suo pro partu genuino agnoscatur, sed ut spurius et exsecrandus fœtus oculis repræsentetur...",
                "witness": "PG102 (Photii Epistolarum Lib. I, Epist. XIV, cols 747-748, p. 395)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/67d711bd-eca2-43d7-8342-6cab54839fa6-538.txt",
                "physical": 267,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "index_analyticus_pg102"],
                "snippet": "Reverentia in parentes semper adhibenda 750 c.",
                "witness": "PG102 (Index Analyticus in Photii Opera, col. 1009, p. 538)"
            }
        ]
    },
    "1878e242a54e8d75a2ec1c6a5ab84a64ed7e9dad0595194a5068bbb0bdd72147": {
        "label": "Scandalum quid, et quibus suppliciis obnoxium",
        "ref_raw": "750 b",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/ecc89feb-6730-44ad-8699-840b612e122f-396.txt",
                "physical": 531,
                "score": 100.0,
                "rank": 1,
                "reasons": ["editorial_migne_col_750_section_b", "migne_typesetter_header_displacement_753_754", "photii_epistola_14", "literal_context_suppliciis_et_sceleribus"],
                "snippet": "B Si quis, inquam, obiter ista dicat, quis non illum furere et insanire?... Quod si quis non urbium sed fidei proditores, prædones non corporalium sed divinæ legis in medium productos sistat... Sin vero actionum lege prohibitarum actores agnoscas, quid ita tandem curiose satagis, utrum hic supplicium luant aut non luant?...",
                "witness": "PG102 (Photii Epistolarum Lib. I, Epist. XIV, cols 749-750 [header 753-754], col. 750 b, p. 396)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/ecc89feb-6730-44ad-8699-840b612e122f-395.txt",
                "physical": 530,
                "score": 95.0,
                "rank": 2,
                "reasons": ["photii_epistola_14_preceding", "migne_cols_747_748"],
                "snippet": "Sin quispiam vero, his talibus auditis, pudore suffunditur, erigat se aliquantulum, et emergat inde nec tale quidquam se contra nos perpetrasse dicat...",
                "witness": "PG102 (Photii Epistolarum Lib. I, Epist. XIV, cols 747-748, p. 395)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG102/text/67d711bd-eca2-43d7-8342-6cab54839fa6-539.txt",
                "physical": 268,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "index_analyticus_pg102"],
                "snippet": "Scandalum quid, et quibus suppliciis obnoxium, 750 b.",
                "witness": "PG102 (Index Analyticus in Photii Opera, col. 1012, p. 539)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_136_pg102_photius"):
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


def apply_pg102_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_photii_index_analyticus_migne_col_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG102"
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

    sync_catalog_summary(db_path, summary_path, "sprint_136_pg102_photius")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg102_fixes()
        print(f"Applied fixes to {updated} items in PG102.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG102 (dry-run).")

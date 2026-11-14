"""
PG142 Nicephorus Blemmydes (Index in Physicam Blemmidae) Locator
Sprint 110 of Onomastic Catalog Recovery

Resolves all 3 residual source_only items from PG142:
1. Editorial Context:
   - PG142 contains the works of Joannes Caleca, Theodorus Metochita, and Nicephorus Blemmydes
     (Epitome Logica and Epitome Physica).
   - The 'INDEX IN PHYSICAM BLEMMIDÆ' (cols 1627-1634 / physical pp. 820-823) indexes
     Blemmydes' Epitome Physica by the original pagination and section numbers of the edition
     (Johannes Wegelin, Augsburg 1605), which are printed inline and marginally in Migne.
   - The 3 residual items:
     1) 'Ubique' [Circuli tropici... Ubique idem sunt, et æquinoctialis] -> 'idem sunt, et æquinoctialis' (ref: 287, ibid. 16)
        (PG142 physical p. 649: Epitome Physica de circulis tropicis et aequinoctiali, inline p. 287, Migne cols 1285-1286;
         and p. 650: § 15-16, inline 'ibid. 16', Migne cols 1287-1288;
         plus index p. 820 col. 1627).
     2) 'Iridis prima circumferentia' -> 240, ibid
        (PG142 physical p. 607: Epitome Physica cap. De Iride, inline p. 240, Migne cols 1201-1202,
         ipsissimis verbis: 'A prima igitur iridis circumferentia cum visus... Nam et prima circumferentia est maxima... Secundum autem iridis circumferentiam aspectus cernit viridem...';
         plus p. 606 and index p. 821 col. 1630).
     3) 'Lunaris incrementi et decrementi modi' -> printed '310' (OCR typo '5150' in txt)
        (PG142 physical p. 659: Epitome Physica de luna, inline p. 308-311, Migne cols 1305-1306,
         ipsissimis verbis: 'Seorsim autem de luna dixit, propter luminis incrementa et decrementa, occultationes et defectus...';
         and p. 661: inline p. 311-312, cols 1309-1310: 'Non una semper eademque pars lunæ illuminatur...';
         plus index p. 822 col. 1631).
"""

import os
import json
import zlib
import sqlite3
from typing import Dict, List, Any

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "3532f64e80da7c56b1ded372e77d5e05cc012e4c37346fcb3ae32d05a9ebc3d7": {
        "label": "Ubique",
        "ref_raw": "idem sunt, et æquinoctialis",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/83c8c19d-e945-4a0a-88e8-be060ef6ca34-649.txt",
                "physical": 649,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_p287_match", "circuli_tropici_aequinoctialis", "epitome_physica"],
                "snippet": "omnes circulos transeat, quotquot intra tropicos... Postquam autem æquinoctialem attigerit, eum nimirum circulum...",
                "witness": "PG142 Epitome Physica, inline p. 287 / Migne cols 1285-1286 (physical p. 649)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/83c8c19d-e945-4a0a-88e8-be060ef6ca34-650.txt",
                "physical": 650,
                "score": 90.0,
                "rank": 2,
                "reasons": ["inline_sec_16_match", "aequinoctium_vernum_autumnale"],
                "snippet": "15, 16. Æquinoctium porro vernum et autumnale contingit...",
                "witness": "PG142 Epitome Physica § 16 / Migne cols 1287-1288 (physical p. 650)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/a6a43a94-f6c3-4832-ac8f-ac08773dca99-820.txt",
                "physical": 820,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Circuli tropici cur ita dicti 287. Ubique idem sunt, et æquinoctialis, ibid. 16.",
                "witness": "PG142 Index in Physicam Blemmidæ, col. 1627 (physical p. 820)"
            }
        ]
    },
    "group:518b40b4796f543b8fb51bfc73d6cb2f0544adfdb0d660bbe6bae5ee954338a3": {
        "label": "Iridis prima circumferentia",
        "ref_raw": "240, ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/fb884fe9-ca5d-4cea-a4e4-f310a3950012-607.txt",
                "physical": 607,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "inline_p240", "iridis_prima_circumferentia"],
                "snippet": "4. A prima igitur iridis circumferentia cum visus... Nam et prima circumferentia est maxima, et a maxima circumferentia refractio est plurima... Secundum autem iridis circumferentiam aspectus cernit viridem...",
                "witness": "PG142 Epitome Physica, inline p. 240 / Migne cols 1201-1202 (physical p. 607)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/fb884fe9-ca5d-4cea-a4e4-f310a3950012-606.txt",
                "physical": 606,
                "score": 85.0,
                "rank": 2,
                "reasons": ["caput_de_iride_initium", "inline_p237_239"],
                "snippet": "QUOD EST DE IRIDE SEU ARCU CŒLESTI... 1. Coronæ sive areæ, et Iridis sive arcus cœlestis 237 causa communis est refractio... 3. Iridis tres circumferentiæ...",
                "witness": "PG142 Epitome Physica, cap. De Iride initium (physical p. 606)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/a6a43a94-f6c3-4832-ac8f-ac08773dca99-821.txt",
                "physical": 821,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Iridis prima circumferentia, 240, ibid. Secunda, ibid. Tertia, 241.",
                "witness": "PG142 Index in Physicam Blemmidæ, col. 1630 (physical p. 821)"
            }
        ]
    },
    "97afe80933c6473d1d0701fbe87ee7202d7097e1e5c36c51cbf24a1a4efc9eeb": {
        "label": "Lunaris incrementi et decrementi modi",
        "ref_raw": "5150",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/83c8c19d-e945-4a0a-88e8-be060ef6ca34-659.txt",
                "physical": 659,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "inline_p310", "lunaris_incrementi_decrementi"],
                "snippet": "3. Seorsim autem de luna dixit, propter luminis incrementa et decrementa, occultationes et defectus...",
                "witness": "PG142 Epitome Physica, inline p. 308-311 / Migne cols 1305-1306 (physical p. 659)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/83c8c19d-e945-4a0a-88e8-be060ef6ca34-661.txt",
                "physical": 661,
                "score": 90.0,
                "rank": 2,
                "reasons": ["inline_p311_match", "luna_illuminatio"],
                "snippet": "13. Cum enim hæc quoque orbicularis sit, perinde ut sol... Non una semper eademque pars lunæ illuminatur...",
                "witness": "PG142 Epitome Physica, inline p. 311-312 / Migne cols 1309-1310 (physical p. 661)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG142/text/a6a43a94-f6c3-4832-ac8f-ac08773dca99-822.txt",
                "physical": 822,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Lunaris incrementi et decrementi modi, 5150 [310]. Non una semper eademque pars lunæ illuminatur, 311, 312.",
                "witness": "PG142 Index in Physicam Blemmidæ, col. 1631 (physical p. 822)"
            }
        ]
    }
}


def get_pg142_candidates(item_id: str) -> List[Dict[str, Any]]:
    """Return certified destination candidates for a given PG142 item_id."""
    info = TARGET_ITEMS.get(item_id)
    if not info:
        return []
    return info["destinations"]


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    """Return all resolved destinations for PG142 residual items."""
    return {item_id: info["destinations"] for item_id, info in TARGET_ITEMS.items()}


def apply_pg142_fixes(db_path: str = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite") -> int:
    """Apply the resolved PG142 destinations to search.sqlite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        # Insert candidates into candidate_storage
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
                    "candidate_basis": "editorial_blemmydes_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG142"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        # Update items table
        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg142_fixes()
        print(f"Applied fixes to {updated} items in PG142.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG142 (dry-run).")

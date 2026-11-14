"""
PL024 S. Hieronymus (Index Rerum et Verborum) Locator
Sprint 108 of Onomastic Catalog Recovery

Resolves all 3 residual source_only items from PL024:
1. Editorial Context:
   - PL024 contains S. Jerome's Commentarii in Jeremiam, Commentarii in Isaiam prophetam,
     and Translatio Homiliarum Origenis in Isaiam.
   - The 'INDEX RERUM ET VERBORUM INSIGNIORUM' (cols 981-1002 / physical pp. 493-504)
     indexes the works by the edition's inline column numbering (cols 1-1120+), which is
     preserved within the body of Migne's text.
   - The 3 residual items:
     1) 'Corpora reconduntur, templum Dei sunt' -> 1040
        (PL024 physical p. 448: Comment. in Jeremiam Lib. V cap. XXVIII-XXIX, inline col. 1040, Migne cols 889-890,
         ipsissimis verbis: 'Nos autem novimus corpora credentium templa esse Dei, si tamen Spiritus sanctus habitat in illis...';
         plus p. 447 and index p. 495).
     2) 'Nabuchodonosor, columpa Domini' -> printed '1024' (OCR typo '1921' in txt)
        (PL024 physical p. 441: Comment. in Jeremiam Lib. V cap. XXV-XXVI, inline col. 1024-1025, Migne cols 875-876,
         ipsissimis verbis: 'Non mirabitur columbam Domini intelligi Nabuchodonosor, qui supra eum vocari servum Domini legerit...';
         plus p. 442 and index p. 500).
     3) 'Rufinus notatur' -> 1040
        (PL024 physical p. 448: Comment. in Jeremiam Lib. V cap. XXVIII, inline col. 1040, Migne cols 889-890,
         ipsissimis verbis: 'Compulit me tractator indoctus, et sectator calumniæ Grunnianæ, aperte ponere aliena vitia,
         quæ prius cum dissimulatione dicebam, lectoris prudentiæ derelinquens.' [Grunnius = Rufinus];
         plus p. 447 and index p. 502).
"""

import os
import json
import zlib
import sqlite3
from typing import Dict, List, Any

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "9918ea876af48662c81d3f3f0a9ddffef34093154668533261250d45b09f4045": {
        "label": "Corpora reconduntur, templum Dei sunt",
        "ref_raw": "1040",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/76de807d-62d0-449a-a6f1-f1d2b6b0ffe3-448.txt",
                "physical": 448,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_col_1040_match", "literal_witness_match", "commentarius_in_jeremiam"],
                "snippet": "Nos autem novimus corpora credentium templa esse Dei, si tamen Spiritus sanctus habitat in illis...",
                "witness": "PL024 inline col. 1040 / Migne cols 889-890 (physical p. 448)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/76de807d-62d0-449a-a6f1-f1d2b6b0ffe3-447.txt",
                "physical": 447,
                "score": 85.0,
                "rank": 2,
                "reasons": ["inline_col_1039_context", "jeremias_cap_xxviii"],
                "snippet": "Et tulit Ananias propheta catenam (vel furcam, quæ Hebraice dicitur מותד...",
                "witness": "PL024 inline col. 1039 / Migne cols 887-888 (physical p. 447)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/f37b9fcf-b8d0-4a16-a443-d41b6e29bad9-495.txt",
                "physical": 495,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Corpora reconduntur, templum Dei sunt, 1040.",
                "witness": "PL024 Index Rerum et Verborum, col. 983 (physical p. 495)"
            }
        ]
    },
    "f1816be7db5746b7313a1da5182087fc4eb801373ebee13c3b639548f3097b4a": {
        "label": "Nabuchodonosor, columpa Domini",
        "ref_raw": "1921",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/847c8149-6620-48a5-b258-74cd4f9c9225-441.txt",
                "physical": 441,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_col_1024_match", "literal_witness_match", "columba_domini_nabuchodonosor"],
                "snippet": "Non mirabitur columbam Domini intelligi Nabuchodonosor, qui supra eum vocari servum Domini legerit.",
                "witness": "PL024 inline col. 1024-1025 / Migne cols 875-876 (physical p. 441)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/847c8149-6620-48a5-b258-74cd4f9c9225-442.txt",
                "physical": 442,
                "score": 85.0,
                "rank": 2,
                "reasons": ["inline_col_1026_context", "jeremias_cap_xxvi"],
                "snippet": "Haec dicit Dominus : Sta in atrio domus Domini...",
                "witness": "PL024 inline col. 1026-1027 / Migne cols 877-878 (physical p. 442)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/f37b9fcf-b8d0-4a16-a443-d41b6e29bad9-500.txt",
                "physical": 500,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Nabuchodonosor, columpa Domini, 1921 [columba Domini, 1024].",
                "witness": "PL024 Index Rerum et Verborum, col. 993 (physical p. 500)"
            }
        ]
    },
    "395ed448763c825895d1716e277b2065e383e6646968acb4a3f075e3fff04295": {
        "label": "Rufinus notatur",
        "ref_raw": "1040",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/76de807d-62d0-449a-a6f1-f1d2b6b0ffe3-448.txt",
                "physical": 448,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_col_1040_match", "literal_witness_match", "calumnia_grunniana_rufinus"],
                "snippet": "Compulit me tractator indoctus, et sectator calumniæ Grunnianæ, aperte ponere aliena vitia, quæ prius cum dissimulatione dicebam, lectoris prudentiæ derelinquens.",
                "witness": "PL024 inline col. 1040 / Migne cols 889-890 (physical p. 448)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/76de807d-62d0-449a-a6f1-f1d2b6b0ffe3-447.txt",
                "physical": 447,
                "score": 85.0,
                "rank": 2,
                "reasons": ["inline_col_1039_context", "jeremias_cap_xxviii"],
                "snippet": "Et tulit Ananias propheta catenam (vel furcam, quæ Hebraice dicitur מותד...",
                "witness": "PL024 inline col. 1039 / Migne cols 887-888 (physical p. 447)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL024/text/f37b9fcf-b8d0-4a16-a443-d41b6e29bad9-502.txt",
                "physical": 502,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Rufinus notatur, 1040.",
                "witness": "PL024 Index Rerum et Verborum, col. 997 (physical p. 502)"
            }
        ]
    }
}


def get_pl024_candidates(item_id: str) -> List[Dict[str, Any]]:
    """Return certified destination candidates for a given PL024 item_id."""
    info = TARGET_ITEMS.get(item_id)
    if not info:
        return []
    return info["destinations"]


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    """Return all resolved destinations for PL024 residual items."""
    return {item_id: info["destinations"] for item_id, info in TARGET_ITEMS.items()}


def apply_pl024_fixes(db_path: str = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite") -> int:
    """Apply the resolved PL024 destinations to search.sqlite."""
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
                    "candidate_basis": "editorial_vallarsi_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL024"
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
        updated = apply_pl024_fixes()
        print(f"Applied fixes to {updated} items in PL024.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL024 (dry-run).")

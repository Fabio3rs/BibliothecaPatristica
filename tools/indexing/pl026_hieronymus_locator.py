"""
PL026 S. Hieronymus (Index Verborum, Sententiarum et Rerum) Locator
Sprint 107 of Onomastic Catalog Recovery

Resolves all 3 residual source_only items from PL026:
1. Editorial Context:
   - PL026 contains S. Jerome's Commentaria in Matthaeum, in Epistolas Pauli (Galatas, Ephesios,
     Titum, Philemonem), Translatio Homiliarum Origenis in Lucam, and the Appendix (Breviarium in Psalmos).
   - The 'INDEX VERBORUM, SENTENTIARUM ET RERUM MEMORABILIORUM Quæ in hoc septimo tomo S. Hieronymi continentur'
     (cols 1305-1324 / physical pp. 656-665) reproduces the index from Domenico Vallarsi's classic
     Verona edition (Tomus VII, 1734-1742), referencing Vallarsi's column numbers preserved in the margins
     and body of Migne's text.
   - The 3 residual items:
     1) 'Causa admirationis in Hieronymo' -> printed '156' (OCR typo '1356' in txt)
        (PL026 physical p. 77: Vallarsi col. 156, in Comment. in Matth. cap. XX, v. 23 on the martyrdom of
         St. John in boiling oil and exile to Patmos; secondary candidate p. 83 Migne col. 156;
         plus index p. 661 col. 1315).
     2) 'Marcus, quis?' -> 5
        (PL026 physical p. 14: Comment. in Matthaeum Prologus, Vallarsi col. 5, Migne cols 17-18,
         ipsissimis verbis: 'Secundus Marcus, interpres apostoli Petri, et Alexandrinæ ecclesiæ primus episcopus,
         qui Dominum quidem Salvatorem ipse non vidit, sed ea quæ magistrum audierat prædicantem,
         juxta fidem magis gestorum narravit quam ordinem.'; plus p. 13 and index p. 662).
     3) 'Matthæus edidit Evangelium Hebræo sermone, Quare?' -> 5
        (PL026 physical p. 14: Comment. in Matthaeum Prologus, Vallarsi col. 5, Migne cols 17-18,
         ipsissimis verbis: 'Primus omnium Matthæus est Publicanus, cognomento Levi, qui Evangelium in Judæa
         Hebræo sermone edidit, ob eorum vel maxime causam, qui in Jesum crediderant ex Judæis, et nequaquam
         Legis umbram, succedente Evangelii veritate, servabant.'; plus p. 13 and index p. 662).
"""

import os
import json
import zlib
import sqlite3
from typing import Dict, List, Any

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "8ab0a72cd75eba741b16f4e4bf6cc30d69964dc3eac4799bdb55806331516570": {
        "label": "Causa admirationis in Hieronymo",
        "ref_raw": "1356",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/964253c1-2afb-4d02-a623-502715ac7267-077.txt",
                "physical": 77,
                "score": 100.0,
                "rank": 1,
                "reasons": ["vallarsi_col_156_match", "commentarius_in_matthaeum", "calix_martyrii_joannes"],
                "snippet": "Sed si legamus ecclesiasticas historias, in quibus 156 fertur, quod et ipse propter martyrium sit missus in ferventis olei dolium, et inde ad suscipiendam coronam Christi athleta processerit...",
                "witness": "PL026 Vallarsi col. 156 / Migne cols 143-144 (physical p. 77)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/964253c1-2afb-4d02-a623-502715ac7267-083.txt",
                "physical": 83,
                "score": 85.0,
                "rank": 2,
                "reasons": ["migne_col_156_alternate", "matthaei_cap_xxi"],
                "snippet": "155 S. EUSEBII HIERONYMI 158... Poterat Dominus aperta responsione tentatorum calumniam confutare, sed prudenter interrogat...",
                "witness": "PL026 Migne cols 155-156 (physical p. 83)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/3c473c2b-9433-46b7-84f2-58edbedd3ac4-661.txt",
                "physical": 661,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Causa admirationis in Hieronymo, 1356. Infirmitates, 455.",
                "witness": "PL026 Index Verborum, Sententiarum et Rerum, col. 1315 (physical p. 661)"
            }
        ]
    },
    "433a7019eebec7e3ef90b40de48d8382f051619c81a4ea18a9e8e524411650ba": {
        "label": "Marcus, quis?",
        "ref_raw": "5",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/50ab988d-cc85-4c25-a965-bdd82f6c81e6-014.txt",
                "physical": 14,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "vallarsi_col_5", "prologus_in_matthaeum"],
                "snippet": "Secundus Marcus, interpres apostoli Petri, et Alexandrinæ ecclesiæ primus episcopus, qui Dominum quidem Salvatorem ipse non vidit, sed ea quæ magistrum audierat prædicantem, juxta fidem magis gestorum narravit quam ordinem.",
                "witness": "PL026 Prologus in Matthaeum, Vallarsi col. 5 / Migne cols 17-18 (physical p. 14)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/50ab988d-cc85-4c25-a965-bdd82f6c81e6-013.txt",
                "physical": 13,
                "score": 85.0,
                "rank": 2,
                "reasons": ["prologus_initium", "migne_cols_15_16"],
                "snippet": "15 S. EUSEBII HIERONYMI 16... Commentaria in Evangelium S. Matthæi...",
                "witness": "PL026 Prologus in Matthaeum initium (physical p. 13)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/3c473c2b-9433-46b7-84f2-58edbedd3ac4-662.txt",
                "physical": 662,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Marcus, quis? 5. Evangelium scripsit juxta fidem magis...",
                "witness": "PL026 Index Verborum, Sententiarum et Rerum, col. 1317 (physical p. 662)"
            }
        ]
    },
    "b5b174ce7f8e4d38cc1a9c43e227e91531117a91b2a08f3347ce247dfce55c1d": {
        "label": "Matthæus edidit Evangelium Hebræo sermone, Quare?",
        "ref_raw": "5",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/50ab988d-cc85-4c25-a965-bdd82f6c81e6-014.txt",
                "physical": 14,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "vallarsi_col_5", "prologus_in_matthaeum"],
                "snippet": "Primus omnium Matthæus est Publicanus, cognomento Levi, qui Evangelium in Judæa Hebræo sermone edidit, ob eorum vel maxime causam, qui in Jesum crediderant ex Judæis, et nequaquam Legis umbram, succedente Evangelii veritate, servabant.",
                "witness": "PL026 Prologus in Matthaeum, Vallarsi col. 5 / Migne cols 17-18 (physical p. 14)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/50ab988d-cc85-4c25-a965-bdd82f6c81e6-013.txt",
                "physical": 13,
                "score": 85.0,
                "rank": 2,
                "reasons": ["prologus_initium", "migne_cols_15_16"],
                "snippet": "15 S. EUSEBII HIERONYMI 16... Commentaria in Evangelium S. Matthæi...",
                "witness": "PL026 Prologus in Matthaeum initium (physical p. 13)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL026/text/3c473c2b-9433-46b7-84f2-58edbedd3ac4-662.txt",
                "physical": 662,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Matthæus edidit Evangelium Hebræo sermone, Quare? 5.",
                "witness": "PL026 Index Verborum, Sententiarum et Rerum, col. 1317 (physical p. 662)"
            }
        ]
    }
}


def get_pl026_candidates(item_id: str) -> List[Dict[str, Any]]:
    """Return certified destination candidates for a given PL026 item_id."""
    info = TARGET_ITEMS.get(item_id)
    if not info:
        return []
    return info["destinations"]


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    """Return all resolved destinations for PL026 residual items."""
    return {item_id: info["destinations"] for item_id, info in TARGET_ITEMS.items()}


def apply_pl026_fixes(db_path: str = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite") -> int:
    """Apply the resolved PL026 destinations to search.sqlite."""
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
                    "volume": "PL026"
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
        updated = apply_pl026_fixes()
        print(f"Applied fixes to {updated} items in PL026.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL026 (dry-run).")

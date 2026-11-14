"""
PG042 S. Epiphanius Constantiensis (Panarion seu Adversus Haereses - Index Analyticus) Locator
Sprint 94 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PG042:
1. Editorial Context:
   - PG042 contains S. Epiphanius Constantiensis: Panarion seu Adversus Haereses (Part 2),
     Anacephalaeosis, and Petavius' Appendix Dissertationum.
   - At the end of PG042 (pp. 774-787), Migne printed the 'Index Analyticus in duos priores tomos'
     with the note: 'Paginae notantur editionis Dionysii Petavii quas typis grandioribus in nostra expressimus'.
   - The Panarion spans Tomus I (PG041) and Tomus II (PG042).
   - The residual items:
     1) 'Passio ejusdem, qua sola humana natura non divinitas passa est' -> 1138
        (PG041 cols 1137-1138, Haeres. LXIV, physical p. 581, plus index p. 775)
     2) 'In baptismo Patris vocem audivit et columbæ specie Spiritum sanctum delapsum vidit' -> 1137
        (PG041 cols 1137-1138, Haeres. LXIV, physical p. 581, plus index p. 775)
     3) 'Tum resurrectio et conversatio cum discipulis' -> 1138
        (PG041 cols 1137-1138, Haeres. LXIV, physical p. 581, plus index p. 775)
     4) 'Christi vitæ summa et divina: dotes' -> 1156
        (PG041 cols 1155-1156, Haeres. LXIV, physical p. 590, plus index p. 776)
     5) 'Resurrectio mortuorum astruitur' -> 1139
        (PG041 cols 1139-1140, Haeres. LXIV, physical p. 582, plus index p. 785)
"""

import argparse
import glob
import json
import os
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

PG042_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Passio ejusdem, qua sola humana natura non divinitas passa est -> 1138
    '45c11871e08ed770de0f20046ae5b3d3c52ae30cd94d4ee6bf770cb544bffc26': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG041', 581, 'PG041 cols 1137-1138 / Adversus Haereses lib. II tom. I, Haeres. LXIV (De Passione)', 'pg041_panarion_col1138_p581'),
            ('PG041', 580, 'PG041 cols 1135-1136 / Adversus Haereses lib. II tom. I (locus praecedens)', 'pg041_panarion_col1136_p580'),
            ('PG042', 775, 'PG042 cols 1105-1106 / Index Analyticus in duos priores tomos (locus verbetis)', 'pg042_index_analyticus_p775'),
        ]
    ),

    # 2. In baptismo Patris vocem audivit et columbæ specie Spiritum sanctum delapsum vidit -> 1137
    'd58557b72734628edc34d6b5b963eb9e583775a6fbd66908e87c7237c491cb63': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG041', 581, 'PG041 cols 1137-1138 / Adversus Haereses lib. II tom. I, Haeres. LXIV (De Baptismo Christi)', 'pg041_panarion_col1137_p581'),
            ('PG041', 580, 'PG041 cols 1135-1136 / Adversus Haereses lib. II tom. I (locus contiguus)', 'pg041_panarion_col1136_p580'),
            ('PG042', 775, 'PG042 cols 1105-1106 / Index Analyticus in duos priores tomos (locus verbetis)', 'pg042_index_analyticus_p775'),
        ]
    ),

    # 3. Tum resurrectio et conversatio cum discipulis -> 1138
    'd835f3145011d7a8e096aac4d9846a3ad8aacb7768ba878a997c3f3f0d252821': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG041', 581, 'PG041 cols 1137-1138 / Adversus Haereses lib. II tom. I, Haeres. LXIV (De Resurrectione Christi)', 'pg041_panarion_col1138_p581'),
            ('PG041', 582, 'PG041 cols 1139-1140 / Adversus Haereses lib. II tom. I (locus sequens)', 'pg041_panarion_col1139_p582'),
            ('PG042', 775, 'PG042 cols 1105-1106 / Index Analyticus in duos priores tomos (locus verbetis)', 'pg042_index_analyticus_p775'),
        ]
    ),

    # 4. Christi vitæ summa et divina: dotes -> 1156
    '203dfcd91fbb1fd03aa3f3cdae56bad755ef3327e2684756f11ecaee781a2656': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG041', 590, 'PG041 cols 1155-1156 / Adversus Haereses lib. II tom. I, Haeres. LXIV (De Christi vita et dotibus)', 'pg041_panarion_col1156_p590'),
            ('PG041', 591, 'PG041 cols 1157-1158 / Adversus Haereses lib. II tom. I (locus contiguus)', 'pg041_panarion_col1157_p591'),
            ('PG042', 776, 'PG042 cols 1107-1108 / Index Analyticus in duos priores tomos (locus verbetis)', 'pg042_index_analyticus_p776'),
        ]
    ),

    # 5. Resurrectio mortuorum astruitur -> 1139
    'd7361d2a5dd57a3f000d1b098e50a8b25f3b2fd2358206f9c0b42fb861b8ba2c': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG041', 582, 'PG041 cols 1139-1140 / Adversus Haereses lib. II tom. I, Haeres. LXIV (Resurrectio mortuorum astruitur)', 'pg041_panarion_col1139_p582'),
            ('PG041', 581, 'PG041 cols 1137-1138 / Adversus Haereses lib. II tom. I (locus praecedens)', 'pg041_panarion_col1138_p581'),
            ('PG042', 785, 'PG042 cols 1121-1122 / Index Analyticus in duos priores tomos (locus verbetis)', 'pg042_index_analyticus_p785'),
        ]
    ),
}


def find_physical_file(volume: str, physical_page: int) -> Optional[str]:
    """Find the exact physical text file in teste/ for a volume and physical page number."""
    candidates = (
        glob.glob(f'teste/{volume}/text/*-{physical_page:03d}.txt') +
        glob.glob(f'teste/{volume}/text/*-{physical_page}.txt')
    )
    if candidates:
        return os.path.abspath(candidates[0])
    return None


def resolve_pg042_items() -> Dict[str, dict]:
    """Resolve all PG042 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PG042_ITEMS_RAW.items():
        candidates = []
        for rank, (vol, phys, description, anchor_id) in enumerate(destinations, 1):
            file_path = find_physical_file(vol, phys)
            if not file_path:
                raise FileNotFoundError(f"Missing physical file for {vol} page {phys}")

            candidate_payload = {
                'item_id': item_id,
                'group_id': group_id,
                'file': file_path,
                'volume': vol,
                'physical_page': phys,
                'rank': rank,
                'score': round(1.0 - (rank - 1) * 0.05, 2),
                'navigable': True,
                'method': 'pg042_epiphanius_panarion_locator_v1',
                'description': description,
                'anchor_id': anchor_id,
            }
            candidates.append(candidate_payload)

        resolved[item_id] = {
            'item_id': item_id,
            'group_id': group_id,
            'state': 'with_destinations',
            'candidates': candidates,
        }
    return resolved


def apply_pg042_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PG042 items to the SQLite database and update summary."""
    resolved_data = resolve_pg042_items()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    count_applied = 0
    for item_id, data in resolved_data.items():
        cur.execute(
            "UPDATE items SET state = 'with_destinations' WHERE id = ? AND state = 'source_only'",
            (item_id,)
        )
        if cur.rowcount > 0:
            count_applied += 1

        cur.execute("DELETE FROM candidate_storage WHERE item_id = ?", (item_id,))

        for cand in data['candidates']:
            raw_json = json.dumps(cand, ensure_ascii=False)
            compressed = zlib.compress(raw_json.encode('utf-8'))
            cur.execute(
                """
                INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cand['item_id'],
                    cand['file'],
                    cand['rank'],
                    cand['score'],
                    1 if cand['navigable'] else 0,
                    compressed,
                )
            )

    conn.commit()

    # Recalculate summary metrics
    cur.execute("SELECT COUNT(*) FROM items")
    total_items = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    items_source_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['total_items'] = total_items
        summary['items_with_destinations'] = items_with_dest
        summary['items_source_only'] = items_source_only
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return count_applied


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Resolve PG042 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pg042_items()
    print(f"Resolved {len(results)} items for PG042.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pg042_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

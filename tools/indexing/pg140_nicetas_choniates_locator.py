"""
PG140 Nicetas Choniates (Index I & II in Nicetam Choniatem) Locator
Sprint 99 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PG140:
1. Editorial Context:
   - PG140 contains the Indexes to Nicetas Choniates' Historia (which is printed in PG139 cols 287-1056).
   - The page citations in Index I and II refer to the standard Paris/Wolf pagination of Nicetas Choniates
     reproduced in brackets '[P. 259]', '[P. 260]', '[P. 285]', '[P. 289]' through the Greek/Latin text of PG139.
   - Residual items:
     1) 'Theodorus Balsamon... ab Isaacio Angelo spe patriarchatus CPol. deluditur' -> 259 seq
        (PG139 cols 769-774 / physical pp. 391, 392: Isaacius Angelus lib. II [P. 259-260], plus index p. 795)
     2) 'Mariæ imagines singulari devotione coluit Isaacus Angelus' -> 284 seq., 289
        (PG139 cols 811-824 / physical pp. 412, 413, 416: Isaacius Angelus lib. III [P. 284-289], plus index p. 796)
     3) 'Phœnicis instar raro conspici' -> 282 seq
        (PG139 cols 809-816 / physical pp. 411, 413: Isaacius Angelus lib. III [P. 282] φοίνικος δίκην, plus index p. 797)
     4) 'Præsagium de Manuelis imp. morte' -> 2814 (OCR/print typo for 281 / pp. 281-282)
        (PG139 cols 317-318 / physical p. 162 Argumentum 'De Manuelis interitu præsagium',
         and cols 573-574 / physical p. 292 mors imperatoris, plus index p. 808)
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

PG140_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Theodorus Balsamon... -> 259 seq (PG139 pp. 391, 392, PG140 p. 795)
    'group:456b5d046aa8fd109b9cc36799464adb0d15e280cc524bb9fdc50107d0d56d74': (
        '456b5d046aa8fd109b9cc36799464adb0d15e280cc524bb9fdc50107d0d56d74',
        [
            ('PG139', 391, 'PG139 cols 769-772 / Nicetas Choniates: Isaacius Angelus lib. II [P. 259-260] (Theodorus Balsamon)', 'pg139_balsamon_259_p391'),
            ('PG139', 392, 'PG139 cols 773-774 / Nicetas Choniates: Isaacius Angelus lib. II locus sequens', 'pg139_balsamon_260_p392'),
            ('PG140', 795, 'PG140 Index I in Nicetam Choniatem (locus verbetis Theodorus Balsamon)', 'pg140_index_balsamon_p795'),
        ]
    ),

    # 2. Mariæ imagines singulari devotione coluit Isaacus Angelus -> 284 seq., 289 (PG139 pp. 412, 413, 416, PG140 p. 796)
    'group:0fdb685b4c28977ff3319ebb30638e53bc9f13fbed706bf78c958f12d07990ec': (
        '0fdb685b4c28977ff3319ebb30638e53bc9f13fbed706bf78c958f12d07990ec',
        [
            ('PG139', 412, 'PG139 cols 811-814 / Nicetas Choniates: Isaacius Angelus lib. III [P. 284] (de imaginibus Deiparae)', 'pg139_mariae_imagines_284_p412'),
            ('PG139', 413, 'PG139 cols 815-816 / Nicetas Choniates: Isaacius Angelus lib. III [P. 285] locus sequens', 'pg139_mariae_imagines_285_p413'),
            ('PG139', 416, 'PG139 cols 821-824 / Nicetas Choniates: Isaacius Angelus lib. III [P. 289]', 'pg139_mariae_imagines_289_p416'),
            ('PG140', 796, 'PG140 Index I in Nicetam Choniatem (locus verbetis S. Mariae imagines)', 'pg140_index_mariae_p796'),
        ]
    ),

    # 3. Phœnicis instar raro conspici -> 282 seq (PG139 pp. 411, 413, PG140 p. 797)
    'group:0d9cbaf0f62c2a08ada73473091dd9b439547bbf78e73ffb493ce9c760580fe9': (
        '0d9cbaf0f62c2a08ada73473091dd9b439547bbf78e73ffb493ce9c760580fe9',
        [
            ('PG139', 411, 'PG139 cols 809-810 / Nicetas Choniates: Isaacius Angelus lib. III [P. 282] (Phoenicis instar)', 'pg139_phoenix_282_p411'),
            ('PG139', 413, 'PG139 cols 815-816 / Nicetas Choniates: Isaacius Angelus lib. III [P. 285] (phoenix raro conspici)', 'pg139_phoenix_285_p413'),
            ('PG140', 797, 'PG140 Index I in Nicetam Choniatem (locus verbetis Phoenicis instar)', 'pg140_index_phoenix_p797'),
        ]
    ),

    # 4. Præsagium de Manuelis imp. morte -> 2814 (OCR/print typo pro 281) (PG139 pp. 162, 292, PG140 p. 808)
    '5c7076160fc60ed264d52e9075c685187df2c3fff70329eea897c825e677943d': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PG139', 162, 'PG139 cols 317-318 / Nicetas Choniates Argumentum: De Manuelis interitu praesagium', 'pg139_manuel_praesagium_p162'),
            ('PG139', 292, 'PG139 cols 573-574 / Nicetas Choniates: De Manuele Comneno lib. VII (mors imperatoris)', 'pg139_manuel_mors_p292'),
            ('PG140', 808, 'PG140 Index II in Nicetam Choniatem (locus verbetis Praesagium de Manuelis morte)', 'pg140_index_praesagium_p808'),
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


def resolve_pg140_items() -> Dict[str, dict]:
    """Resolve all PG140 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PG140_ITEMS_RAW.items():
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
                'method': 'pg140_nicetas_choniates_locator_v1',
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


def apply_pg140_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PG140 items to the SQLite database and update summary."""
    resolved_data = resolve_pg140_items()
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
    parser = argparse.ArgumentParser(description="Resolve PG140 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pg140_items()
    print(f"Resolved {len(results)} items for PG140.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pg140_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

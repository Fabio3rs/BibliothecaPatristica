"""
PG112 Constantinus Porphyrogenitus (Index in librum de Cerimoniis Aulae Byzantinae) Locator
Sprint 100 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PG112:
1. Editorial Context:
   - PG112 contains Constantinus Porphyrogenitus: De Cerimoniis Aulae Byzantinae libri duo.
   - The page citations in the 'Index in librum de Cerimoniis' refer to the standard Reiske edition
     pagination reproduced through the Greek and Latin text of PG112.
   - Residual items:
     1) 'Formulæ inscriptionum in litteris ad exteros' -> 396 et seqq
        (PG112 cols 1263-1286 / physical pp. 642, 653: De Ceremoniis lib. II cap. 48 [P. 396 et seqq.],
         plus index p. 736)
     2) 'Imperatori quando nascitur filius quæ observanda' -> 356 et seqq
        (PG112 cols 1145-1148 / physical pp. 582, 583: De Ceremoniis lib. II cap. 21-22 [P. 356 et seqq.],
         plus index p. 737)
     3) 'Cum in castra ire et in iis versari decrevit, quæ observanda et agenda' -> 263 et seqq
        (PG112 cols 825-834 / physical pp. 419, 423: Appendix ad lib. I: De expeditionibus bellicis [P. 263 et seqq.],
         plus index p. 737)
     4) 'Prohedrus seu præses universi senatus; illius promotio et in ea observandum' -> 255 et seqq
        (PG112 cols 811-824 / physical pp. 412, 418: De Ceremoniis lib. I cap. 97: De Proedri promotione [P. 255 et seqq.],
         plus index p. 738)
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

PG112_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Formulæ inscriptionum in litteris ad exteros -> 396 et seqq (PG112 pp. 642, 653, p. 736)
    'group:78624dafbb7975e242446dd080589e0327d77496537e5de2c18d4baeb21ba680': (
        '78624dafbb7975e242446dd080589e0327d77496537e5de2c18d4baeb21ba680',
        [
            ('PG112', 642, 'PG112 cols 1263-1264 / De Ceremoniis lib. II cap. 48: Formulæ inscriptionum in litteris ad exteros', 'pg112_inscriptiones_396_p642'),
            ('PG112', 653, 'PG112 cols 1285-1286 / De Ceremoniis lib. II cap. 48 [P. 396 et seqq.] locus continuatus', 'pg112_inscriptiones_396_p653'),
            ('PG112', 736, 'PG112 Index in librum de Cerimoniis (locus verbetis Formulæ inscriptionum)', 'pg112_index_inscriptiones_p736'),
        ]
    ),

    # 2. Imperatori quando nascitur filius quæ observanda -> 356 et seqq (PG112 pp. 582, 583, p. 737)
    'group:4f969536fa0cafe46ffb741e3303872ef98e0227d4fb051fdaddb3a0f63115d1': (
        '4f969536fa0cafe46ffb741e3303872ef98e0227d4fb051fdaddb3a0f63115d1',
        [
            ('PG112', 582, 'PG112 cols 1145-1146 / De Ceremoniis lib. II cap. 21: Imperatori quando nascitur filius [P. 356]', 'pg112_filius_356_p582'),
            ('PG112', 583, 'PG112 cols 1147-1148 / De Ceremoniis lib. II cap. 21-22 locus sequens', 'pg112_filius_356_p583'),
            ('PG112', 737, 'PG112 Index in librum de Cerimoniis (locus verbetis Imperatori quando nascitur filius)', 'pg112_index_filius_p737'),
        ]
    ),

    # 3. Cum in castra ire et in iis versari decrevit, quæ observanda et agenda -> 263 et seqq (PG112 pp. 419, 423, p. 737)
    'group:bfd19c1e5c8c3d2004f12ce9a0118264d8fac58d0bad3ac8a8cb5aacf54d2c6e': (
        'bfd19c1e5c8c3d2004f12ce9a0118264d8fac58d0bad3ac8a8cb5aacf54d2c6e',
        [
            ('PG112', 419, 'PG112 cols 825-826 / Appendix ad lib. I: De expeditionibus bellicis / Cum in castra ire decrevit [P. 263]', 'pg112_castra_263_p419'),
            ('PG112', 423, 'PG112 cols 833-834 / Appendix ad lib. I locus continuatus', 'pg112_castra_263_p423'),
            ('PG112', 737, 'PG112 Index in librum de Cerimoniis (locus verbetis Cum in castra ire decrevit)', 'pg112_index_castra_p737'),
        ]
    ),

    # 4. Prohedrus seu præses universi senatus; illius promotio et in ea observandum -> 255 et seqq (PG112 pp. 412, 418, p. 738)
    'group:a518aae256fb40340c726531d6e976dd5f01a470af224dc2afbe72d5cbf2378b': (
        'a518aae256fb40340c726531d6e976dd5f01a470af224dc2afbe72d5cbf2378b',
        [
            ('PG112', 418, 'PG112 cols 823-824 / De Ceremoniis lib. I cap. 97: De Proedri promotione [P. 255]', 'pg112_prohedrus_255_p418'),
            ('PG112', 412, 'PG112 cols 811-812 / De Ceremoniis lib. I locus contiguus', 'pg112_prohedrus_255_p412'),
            ('PG112', 738, 'PG112 Index in librum de Cerimoniis (locus verbetis Prohedrus)', 'pg112_index_prohedrus_p738'),
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


def resolve_pg112_items() -> Dict[str, dict]:
    """Resolve all PG112 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PG112_ITEMS_RAW.items():
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
                'method': 'pg112_constantinus_porphyrogenitus_locator_v1',
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


def apply_pg112_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PG112 items to the SQLite database and update summary."""
    resolved_data = resolve_pg112_items()
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
    parser = argparse.ArgumentParser(description="Resolve PG112 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pg112_items()
    print(f"Resolved {len(results)} items for PG112.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pg112_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

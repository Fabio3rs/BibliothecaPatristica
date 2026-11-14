"""
PL041 S. Augustinus De Civitate Dei Locator
Sprint 72 of Onomastic Catalog Recovery

Resolves all 8 residual source_only items from PL041:
1. Editorial Context:
   - PL041 contains Saint Augustine's De Civitate Dei contra Paganos Libri XXII:
     * Libri I-X: Pars Prima (De Civitate Terrena, cols 13-312)
     * Libri XI-XXII: Pars Secunda (De Civitate Dei, cols 313-804)
     * Cols 805-830 (physical pp. 431-440): INDEX CAPITULORUM IN LIBROS XXII DE CIVITATE DEI
     * Cols 831-928: INDEX RERUM ET VERBORUM
   - The 8 residual items are chapter titles whose page reference was captured as 'Ibid.' / 'ibid.':
     * Lib. I, cap. XXII ('Quod nunquam possit mors voluntaria...') -> text p. 22, index p. 431
     * Lib. II, cap. XXVIII ('De christianæ religionis salubritate') -> text p. 42, index p. 432
     * Lib. II, cap. XXX ('Quam pudendis prosperitatibus affluere velint...') -> text p. 42, index p. 431
     * Lib. IV, cap. V ('De fugitivis gladiatoribus...') -> text p. 62, index p. 432
     * Lib. IX, cap. III ('Quæ dæmonibus Apuleius adscribat...') -> text p. 133, index p. 434
     * Lib. IX, cap. XIII ('Quomodo dæmones, si nec cum diis beati...') -> text p. 137, index p. 434
     * Lib. X, cap. V ('De sacrificiis quæ Deus non requirit...') -> text p. 145, index p. 434
     * Lib. XX, cap. XXIX ('De adventu Eliæ ante judicium...') -> text p. 356, index p. 439
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

PL041_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. XXII. Quod nunquam possit mors voluntaria -> text p. 22, index p. 431
    'region-entry:e7749aa4253a6179b9392b5e34faaa3cbf8b36d56e9fd568a0c8d819457674cc': (
        'region-entry:e7749aa4253a6179b9392b5e34faaa3cbf8b36d56e9fd568a0c8d819457674cc',
        [
            ('PL041', 22, 'PL041 col 35 (De Civitate Dei lib. I cap. XXII: Quod nunquam possit mors voluntaria ad magnitudinem animi pertinere)', 'pl041_augustinus_civ_lib1_c22_p22'),
            ('PL041', 431, 'PL041 (Index Capitulorum in lib. I cap. XXII)', 'pl041_augustinus_civ_idx_c22_p431')
        ]
    ),

    # 2. XXVIII. De christianæ religionis salubritate -> text p. 42, index p. 432
    'region-entry:a146279b5a0d576a624986be4b82284ec73a7f7ed78bdd3cd6c30273f5c84e99': (
        'region-entry:a146279b5a0d576a624986be4b82284ec73a7f7ed78bdd3cd6c30273f5c84e99',
        [
            ('PL041', 42, 'PL041 col 75 (De Civitate Dei lib. II cap. XXVIII: De christianae religionis salubritate)', 'pl041_augustinus_civ_lib2_c28_p42'),
            ('PL041', 432, 'PL041 (Index Capitulorum in lib. II cap. XXVIII)', 'pl041_augustinus_civ_idx_c28_p432')
        ]
    ),

    # 3. XXX. Quam pudendis prosperitatibus affluere velint -> text p. 42, index p. 431
    'region-entry:c3586ed3c121701cb9f67875558d5023eee7bb476f92fbcddb0ac5cc1cdcb277': (
        'region-entry:c3586ed3c121701cb9f67875558d5023eee7bb476f92fbcddb0ac5cc1cdcb277',
        [
            ('PL041', 42, 'PL041 col 76 (De Civitate Dei lib. II cap. XXX: Quam pudendis prosperitatibus affluere velint)', 'pl041_augustinus_civ_lib2_c30_p42'),
            ('PL041', 431, 'PL041 (Index Capitulorum in lib. II cap. XXX)', 'pl041_augustinus_civ_idx_c30_p431')
        ]
    ),

    # 4. V. De fugitivis gladiatoribus -> text p. 62, index p. 432
    'region-entry:b718c296eaa289ea3255f5f598cf686ed0a3d905561c078a6f7a0c12384c40f7': (
        'region-entry:b718c296eaa289ea3255f5f598cf686ed0a3d905561c078a6f7a0c12384c40f7',
        [
            ('PL041', 62, 'PL041 col 116 (De Civitate Dei lib. IV cap. V: De fugitivis gladiatoribus)', 'pl041_augustinus_civ_lib4_c5_p62'),
            ('PL041', 432, 'PL041 (Index Capitulorum in lib. IV cap. V)', 'pl041_augustinus_civ_idx_c5_p432')
        ]
    ),

    # 5. III. Quæ dæmonibus Apuleius adscribat -> text p. 133, index p. 434
    'region-entry:7a9a9cdcc5f24506a46f9e310612ee0c9605a6e5774d567cdd8dc4cef05c826c': (
        'region-entry:7a9a9cdcc5f24506a46f9e310612ee0c9605a6e5774d567cdd8dc4cef05c826c',
        [
            ('PL041', 133, 'PL041 col 257 (De Civitate Dei lib. IX cap. III: Quae daemonibus Apuleius adscribat)', 'pl041_augustinus_civ_lib9_c3_p133'),
            ('PL041', 434, 'PL041 (Index Capitulorum in lib. IX cap. III)', 'pl041_augustinus_civ_idx_c3_p434')
        ]
    ),

    # 6. XIII. Quomodo dæmones, si nec cum diis beati -> text p. 137, index p. 434
    'region-entry:e41bc597c2ab87f012afed1cf202de58cae4d510fe244fec2c54d2a5c7385753': (
        'region-entry:e41bc597c2ab87f012afed1cf202de58cae4d510fe244fec2c54d2a5c7385753',
        [
            ('PL041', 137, 'PL041 col 265 (De Civitate Dei lib. IX cap. XIII: Quomodo daemones inter utramque partem sint medii)', 'pl041_augustinus_civ_lib9_c13_p137'),
            ('PL041', 434, 'PL041 (Index Capitulorum in lib. IX cap. XIII)', 'pl041_augustinus_civ_idx_c13_p434')
        ]
    ),

    # 7. V. De sacrificiis quæ Deus non requirit -> text p. 145, index p. 434
    'region-entry:ba8fc808d495c5ad7c0f8ddbf050cc8c2aa98b53c572c0f3472c3efb7ee6fa5a': (
        'region-entry:ba8fc808d495c5ad7c0f8ddbf050cc8c2aa98b53c572c0f3472c3efb7ee6fa5a',
        [
            ('PL041', 145, 'PL041 col 281 (De Civitate Dei lib. X cap. V: De sacrificiis quae Deus non requirit)', 'pl041_augustinus_civ_lib10_c5_p145'),
            ('PL041', 434, 'PL041 (Index Capitulorum in lib. X cap. V)', 'pl041_augustinus_civ_idx_c5_p434')
        ]
    ),

    # 8. XXIX. De adventu Eliæ ante judicium -> text p. 356, index p. 439
    'region-entry:860edd220340ea742285921ccd88ad3ecc5cf88d86a24c3b632f5cde6fde248a': (
        'region-entry:860edd220340ea742285921ccd88ad3ecc5cf88d86a24c3b632f5cde6fde248a',
        [
            ('PL041', 356, 'PL041 col 703 (De Civitate Dei lib. XX cap. XXIX: De adventu Eliae ante judicium)', 'pl041_augustinus_civ_lib20_c29_p356'),
            ('PL041', 439, 'PL041 (Index Capitulorum in lib. XX cap. XXIX)', 'pl041_augustinus_civ_idx_c29_p439')
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    if matches:
        return matches[0]
    return f'teste/{volume}/text/{volume}-{page_num:03d}.txt'


def build_candidate_json(
    volume: str,
    page_num: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int = 1,
) -> bytes:
    """Builds candidate compressed JSON payload."""
    payload = {
        'target_volume': volume,
        'page_num': page_num,
        'file': file_path,
        'col_desc': col_desc,
        'locator_reason': reason,
        'rank': rank,
        'score': 10.0,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_pl041_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL041 item to physical destination files."""
    if item_id not in PL041_ITEMS_RAW:
        return None
    group_id, dests = PL041_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint72(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 72 recovery for PL041 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL041' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL041',
        'sprint': 72,
        'total_source_only_initial': total_source_only,
        'resolved_count': 0,
        'candidates_created': 0,
        'applied': apply,
        'items': [],
    }

    if not source_only_rows:
        conn.close()
        return report

    items_to_update = []
    for r in source_only_rows:
        item_id, group_id, label, ref_raw, source = r
        res = resolve_pl041_item(item_id)
        if res:
            _, resolved_dests = res
            items_to_update.append((item_id, label, ref_raw, resolved_dests))

    report['resolved_count'] = len(items_to_update)

    if not apply:
        conn.close()
        return report

    candidates_inserted = 0
    for item_id, label, ref_raw, dests in items_to_update:
        for rank, (vol, page, col_desc, reason, fpath) in enumerate(dests, 1):
            cand_blob = build_candidate_json(vol, page, fpath, col_desc, reason, rank)
            cur.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, fpath, rank, 10.0, 1, cand_blob))
            candidates_inserted += 1

        cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (item_id,))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    total_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    total_source_only_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_source_only_after
    report['candidate_storage_records'] = cand_count

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['items_with_destinations'] = total_with_dest
        summary['items_source_only'] = total_source_only_after
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sprint 72: PL041 S. Augustinus De Civitate Dei Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint72(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))

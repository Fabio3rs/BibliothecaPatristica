"""
PL020 S. Chromatius Aquileiensis Locator
Sprint 76 of Onomastic Catalog Recovery

Resolves all 7 residual source_only items from PL020:
1. Editorial Context:
   - PL020 contains:
     * Cols 1-88: S. Damasus Papa (Epistolae et Decreta)
     * Cols 89-246: Sulpicius Severus (Historia Sacra, Vita S. Martini, Dialogi, Epistolae)
     * Cols 247-432: S. Chromatius Episcopus Aquileiensis (Scripta sive Opuscula, Praefatio Petri Braidae, Notae Justi Fontanini et B. M. de Rubeis)
     * Cols 433-596: S. Innocentius I Papa (Epistolae et Decreta)
     * Cols 597-620 (physical pp. 598-610): INDEX RERUM ET VERBORUM IN S. CHROMATII SCRIPTA
   - Citations in the INDEX RERUM ET VERBORUM cite pages of the Udine edition (1816) reprinted in PL020 cols 247-432:
     * 'Editiones varias Scriptorum S. Chromatii post primam Basileensem... 121 et seqq' -> physical p. 132
     * 'postremam Gallandii, indicat Fontaninus, 121 et seqq' -> physical p. 132
     * 'De episcopi etiam Romani titulo S. Chromatio attributo, 122 et seqq' -> physical p. 134
     * 'Doxologiarum usus... In Epistola quoque S. Clementis papæ ad Corinthios' -> physical p. 139
     * 'Papæ appellatio S. Chromatio attributa a S. Hieronymo, LXXI, LXXII, LXXIII...' -> physical p. 152
     * 'Num Rufini Aquileiensis sit libellus palinodiæ, quem vulgavit Norisius... 149 et seqq' -> physical p. 165
     * 'Quanta cum laude de Rufino Aquileiensi scripserit Palladius Helenopolitanus? 154 et seqq' -> physical p. 162
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

PL020_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Editiones varias Scriptorum S. Chromatii post primam Basileensem -> 121 et seqq (p. 132)
    'fd878da6bbccacedb0fc5dccbd19da6368d4d1fad37f134580bb01b2cde1836d': (
        'fd878da6bbccacedb0fc5dccbd19da6368d4d1fad37f134580bb01b2cde1836d',
        [
            ('PL020', 132, 'PL020 col 253 (Praefatio Editoris in S. Chromatium: Editiones varias post primam Basileensem)', 'pl020_chromatius_editiones_p132')
        ]
    ),

    # 2. postremam Gallandii, indicat Fontaninus -> 121 et seqq (p. 132)
    '86bb3a614b849d803899714bd675b531d23881b686ffb14fa046164b9c380db7': (
        '86bb3a614b849d803899714bd675b531d23881b686ffb14fa046164b9c380db7',
        [
            ('PL020', 132, 'PL020 col 253 (Praefatio Editoris in S. Chromatium: Fontaninus de editione Gallandii)', 'pl020_chromatius_fontaninus_p132')
        ]
    ),

    # 3. De episcopi etiam Romani titulo S. Chromatio attributo -> 122 et seqq (p. 134)
    'group:2be27c98abf3522a6f0d0464e94e23ecc4ab6f850b7501ffabe333278af58229': (
        'group:2be27c98abf3522a6f0d0464e94e23ecc4ab6f850b7501ffabe333278af58229',
        [
            ('PL020', 134, 'PL020 cols 257-258 (Praefatio Editoris: De episcopi Romani titulo S. Chromatio attributo)', 'pl020_chromatius_episcopus_romanus_p134')
        ]
    ),

    # 4. Doxologiarum usus... In Epistola quoque S. Clementis papæ ad Corinthios -> p. 139
    '3e9e169ae6c7408b14de1194b1433a3c92dcaf5466f3a9b4d413bb17db58dd5b': (
        '3e9e169ae6c7408b14de1194b1433a3c92dcaf5466f3a9b4d413bb17db58dd5b',
        [
            ('PL020', 139, 'PL020 cols 267-268 (S. Chromatius: Doxologiarum usus in Epistola S. Clementis ad Corinthios)', 'pl020_chromatius_doxologia_p139')
        ]
    ),

    # 5. Papæ appellatio S. Chromatio attributa a S. Hieronymo... -> p. 152
    'e8b4fdfe0a2b944819e6ab265aae58f8a6d76a677305c0ad8ef230e1267bac6f': (
        'e8b4fdfe0a2b944819e6ab265aae58f8a6d76a677305c0ad8ef230e1267bac6f',
        [
            ('PL020', 152, 'PL020 col 293 (Praefatio Editoris: Papae appellatio S. Chromatio attributa a S. Hieronymo)', 'pl020_chromatius_papae_titulus_p152')
        ]
    ),

    # 6. Num Rufini Aquileiensis sit libellus palinodiæ, quem vulgavit Norisius... -> 149 et seqq (p. 165)
    'group:82c07085de318c1f4488848d3f258bc93f950a6f325a4639dbb885c9f939b556': (
        'group:82c07085de318c1f4488848d3f258bc93f950a6f325a4639dbb885c9f939b556',
        [
            ('PL020', 165, 'PL020 col 319 (Praefatio Editoris: Rufini Aquileiensis libellus palinodiae vulgatus a Norisio)', 'pl020_chromatius_rufinus_palinodia_p165')
        ]
    ),

    # 7. Quanta cum laude de Rufino Aquileiensi scripserit Palladius Helenopolitanus... -> 154 et seqq (p. 162)
    'group:32fee8162013960f5fbcdec9194693fa4748ef2db80818ea3206412a56d1ca04': (
        'group:32fee8162013960f5fbcdec9194693fa4748ef2db80818ea3206412a56d1ca04',
        [
            ('PL020', 162, 'PL020 col 313 (De S. Chromatio Veterum Testimonia: Palladius Helenopolitanus de Rufino Aquileiensi)', 'pl020_chromatius_palladius_rufinus_p162')
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


def resolve_pl020_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL020 item to physical destination files."""
    if item_id not in PL020_ITEMS_RAW:
        return None
    group_id, dests = PL020_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint76(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 76 recovery for PL020 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL020' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL020',
        'sprint': 76,
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
        res = resolve_pl020_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 76: PL020 S. Chromatius Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint76(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))

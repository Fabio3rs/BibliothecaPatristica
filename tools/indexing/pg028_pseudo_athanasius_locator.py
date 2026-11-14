"""
PG028 Pseudo-Athanasius Locator
Sprint 82 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PG028:
1. Editorial Context:
   - PG028 contains S. Athanasii Opera Omnia - Spuria, Dubia et Fragmenta (reprinting Montfaucon Tomus II):
     * Cols 11-282: Dialogi V de Sancta Trinitate
     * Cols 281-438: Quaestiones ad Antiochum Ducem
     * Cols 437-558: De Incarnatione Dei Verbi & Contra Apollinarium
     * Cols 557-796: Synopsis Scripturae Sacrae
     * Cols 797-1428: Fragmenta et Spuria varia
     * Cols 1427-1654: Index Rerum & Ordo Editionum
   - The 6 residual items:
     1) 'Apollinarii doctrina confutatur' -> 458 et seqq (Montfaucon Tomus II p. 458 in Dialogus IV contra Apollinaristas, PG028 physical pp. 631, 633)
     2) 'Imaginis crucifixi Berytensis historia' -> 289 et seqq (Montfaucon Tomus II p. 289 in Historia Imaginis Berytensis, PG028 physical pp. 405, 407)
     3) 'Novitii admittendi ritus' -> 310 et seqq (Montfaucon Tomus II p. 310 in Syntagma Doctrinae ad Monachos, PG028 physical pp. 433, 434)
     4) 'Spiritus sanctus non procedit ex Filio' -> 281 et seqq (Montfaucon Tomus II p. 281 in Quaestiones ad Antiochum Ducem q. 4, PG028 physical pp. 395, 396)
     5) 'Spiritus sancti deitas probatur' -> 176 et seqq (Montfaucon Tomus I p. 176 in Epistola I ad Serapionem de Spiritu Sancto, PG026 physical pp. 269, 271, and Tomus II Dialogus III in PG028 p. 611)
     6) 'T. II' -> 4513 (typo in Ordo Editionum for Tomus II cols 1321-1330: Fragmenta / Dialogi contra Macedonianos, PG028 physical pp. 671, 672)
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

PG028_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Apollinarii doctrina confutatur -> 458 et seqq (PG028 pp. 631, 633)
    'group:d7d5723472850eaac62779fce6f1709b7619b6816f7f34b2750ed9a8cf98839c': (
        'group:d7d5723472850eaac62779fce6f1709b7619b6816f7f34b2750ed9a8cf98839c',
        [
            ('PG028', 631, 'PG028 col 1247 / Montfaucon p. 458 (De S. Trinitate Dialogus IV: Apollinarii doctrina confutatur)', 'pg028_apollinarius_confutatur_p631'),
            ('PG028', 633, 'PG028 col 1251 / Montfaucon p. 459 (De S. Trinitate Dialogus IV continuatio)', 'pg028_apollinarius_confutatur_p633'),
        ]
    ),

    # 2. Imaginis crucifixi Berytensis historia -> 289 et seqq (PG028 pp. 405, 407)
    'group:c5a936ff819b44ace1251fbd65fd926b93ffef16749df7c06acf1840a8bf1644': (
        'group:c5a936ff819b44ace1251fbd65fd926b93ffef16749df7c06acf1840a8bf1644',
        [
            ('PG028', 405, 'PG028 cols 797-798 / Montfaucon p. 289 (Historia Imaginis Berytensis: Tempore Constantini et Irenae)', 'pg028_imago_berytensis_historia_p405'),
            ('PG028', 407, 'PG028 cols 801-802 / Montfaucon p. 290 (Historia Imaginis Berytensis continuatio)', 'pg028_imago_berytensis_historia_p407'),
        ]
    ),

    # 3. Novitii admittendi ritus -> 310 et seqq (PG028 pp. 433, 434)
    'group:c1a96b162009e4eb61f69aa7e6c7669a5c690efb81273c867ec707a25581b427': (
        'group:c1a96b162009e4eb61f69aa7e6c7669a5c690efb81273c867ec707a25581b427',
        [
            ('PG028', 433, 'PG028 cols 851-852 / Montfaucon p. 310 (Syntagma Doctrinae ad Monachos: Novitii admittendi ritus)', 'pg028_novitii_admittendi_ritus_p433'),
            ('PG028', 434, 'PG028 cols 853-854 / Montfaucon p. 311 (Syntagma Doctrinae ad Monachos continuatio)', 'pg028_novitii_admittendi_ritus_p434'),
        ]
    ),

    # 4. Spiritus sanctus non procedit ex Filio -> 281 et seqq (PG028 pp. 395, 396)
    '3a12e0d25bf2f7093a0c87203dff1d37d10e5838c7af35f6c6660207f1c8ba6d': (
        '3a12e0d25bf2f7093a0c87203dff1d37d10e5838c7af35f6c6660207f1c8ba6d',
        [
            ('PG028', 395, 'PG028 col 777 / Montfaucon p. 281 (Quaestiones ad Antiochum Ducem q. 4: Spiritus sanctus non procedit ex Filio)', 'pg028_spiritus_sanctus_procedit_p395'),
            ('PG028', 396, 'PG028 col 779 / Montfaucon p. 282 (Quaestiones ad Antiochum Ducem continuatio)', 'pg028_spiritus_sanctus_procedit_p396'),
        ]
    ),

    # 5. Spiritus sancti deitas probatur -> 176 et seqq (PG026 pp. 269, 271; PG028 p. 611)
    'group:8c2f2637a0c9daccdb57aacbe379106e029b377aceb9a913050cce34a0edc105': (
        'group:8c2f2637a0c9daccdb57aacbe379106e029b377aceb9a913050cce34a0edc105',
        [
            ('PG026', 269, 'PG026 cols 525-526 / Montfaucon p. 176 (Epistola I ad Serapionem: Contra eos qui dicunt Spiritum sanctum rem creatam esse, deitas probatur)', 'pg028_athanasius_spiritus_sancti_deitas_p269'),
            ('PG026', 271, 'PG026 cols 529-530 (Epistola I ad Serapionem continuatio)', 'pg028_athanasius_spiritus_sancti_deitas_p271'),
            ('PG028', 611, 'PG028 col 1207 (Dialogus III de Sancta Trinitate: De Spiritus Sancti deitate probanda)', 'pg028_athanasius_spiritus_sancti_deitas_p611'),
        ]
    ),

    # 6. T. II -> 4513 (PG028 pp. 671, 672)
    'c41762eb0d55b7f53d63fe9621ba3bbc2259a37b430f2bfc107fffe17fed96b6': (
        'c41762eb0d55b7f53d63fe9621ba3bbc2259a37b430f2bfc107fffe17fed96b6',
        [
            ('PG028', 671, 'PG028 col 1327 (Contra Macedonianos Dialogus I: Orthodoxus et Macedonianus)', 'pg028_contra_macedonianos_p671'),
            ('PG028', 672, 'PG028 cols 1329-1330 (Contra Macedonianos Dialogus II)', 'pg028_contra_macedonianos_p672'),
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    matches = list(dict.fromkeys(matches))
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


def resolve_pg028_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG028 item to physical destination files."""
    if item_id not in PG028_ITEMS_RAW:
        return None
    group_id, dests = PG028_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint82(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 82 recovery for PG028 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG028' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG028',
        'sprint': 82,
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
        res = resolve_pg028_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 82: PG028 Pseudo-Athanasius Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    rep = execute_sprint82(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(rep, indent=2, ensure_ascii=False))

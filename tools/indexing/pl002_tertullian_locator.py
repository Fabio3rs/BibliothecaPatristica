"""
PL002 Tertullianus Index Scriptorum Veterum Locator
Sprint 40 of Onomastic Catalog Recovery

Resolves all 18 residual source_only items from PL002 (Tertullianus, Tomus II):
1. Editorial Context:
   - PL001 and PL002 contain the complete works of Tertullian (*Tertulliani Opera Omnia*),
     edited by Jacobus Pamelius and Rigaltius.
   - The *Index Scriptorum Veterum a Tertulliano Citatorum* (pp. 704-706 in PL002) classifies
     ancient authors cited by Tertullian into categories:
     * Sacrorum (e.g. Enoch)
     * Magorum (e.g. Berenice, Damigeron, Dardanus, Hostanes/Rostanes, Typhon)
     * Philosophorum (e.g. Plato and his dialogues)
   - The index cites volume by Roman numeral (I = PL001, II = PL002) and Migne column numbers.
2. Verified physical mappings:
   - Enoch citations: PL001 page 675 (col 1307-1308, De Cultu Feminarum I, 3)
   - Magorum citations: PL002 page 382 (col 747 B, De Anima cap. 57)
   - Plato & Dialogues citations: PL002 pages 256, 259 (De Carne Christi),
     and pages 338, 339, 342, 346, 347, 351, 352, 355 (De Anima)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

# Mapping: item_id -> (target_vol, physical_page, col_or_desc, reason)
PL002_MAP: Dict[str, Tuple[str, int, Optional[str], str]] = {
    # 1. Enoch: De Cultu Feminarum I, 3 (col 1307, misread 2507) -> PL001, p. 675
    'group:800b9019faef74a2000b72a63b6fd24dd0d8d536eeff4c65c9068cf4dc99537f': (
        'PL001', 675, 'col 1307', 'tertullian_enoch_armarium_judaicum'
    ),
    # 2. Enoch: De Cultu Feminarum I, 3 (col 1308, Judas Apostolus) -> PL001, p. 675
    'group:f942344ba7a4135c7d1d155d7363eddb201592323b3c209beb262c048b0b3e58': (
        'PL001', 675, 'col 1308', 'tertullian_enoch_judas_apostolus'
    ),
    # 3. Magorum: Berenice / Damigeron: De Anima cap. 57 (col 747 B) -> PL002, p. 382
    'group:edb8e854689bf1f14572eec099588086be74805174beed4e95b2e06df1d4e12f': (
        'PL002', 382, 'col 747 B', 'tertullian_magi_berenice_damigeron'
    ),
    # 4. Magorum: Dardanus: De Anima cap. 57 (col 747 B) -> PL002, p. 382
    'group:bab0567f783cf866dcf46082a9bac2c91dbd2e89106652e93592a57f187ee7e9': (
        'PL002', 382, 'col 747 B', 'tertullian_magi_dardanus'
    ),
    # 5. Magorum: Nectabis: De Anima cap. 57 (col 747 B) -> PL002, p. 382
    'group:fbd98e88d12de6e02d6c4a7977946e363c95c0dfefaa8f74cd45ae6b2bec9ad9': (
        'PL002', 382, 'col 747 B', 'tertullian_magi_nectabis'
    ),
    # 6. Magorum: Rostanes (Hostanes): De Anima cap. 57 (col 747 B) -> PL002, p. 382
    'group:a87c280cdedc32c1cca1b597975ee8a61e953a1069e316ef1f846ba9368727bc': (
        'PL002', 382, 'col 747 B', 'tertullian_magi_rostanes_hostanes'
    ),
    # 7. Magorum: Typhon: De Anima cap. 57 (col 747 B) -> PL002, p. 382
    'group:dbbe081de52b0f3f9ea7a8da8198c0c57941cbbf26a5a2c1ffa87d86ff4ebc4d': (
        'PL002', 382, 'col 747 B', 'tertullian_magi_typhon'
    ),
    # 8. Plato: in Socrate, De Carne Christi (col 503 A) -> PL002, p. 256
    'group:6142fee5939e609f7850b1b40f2ccb1dd8c27ed36cd738826f4093f7fc3d9215': (
        'PL002', 256, 'col 503 A', 'tertullian_plato_socrate_carne_christi'
    ),
    # 9. Plato: in Socrate, De Carne Christi (col 509 A) -> PL002, p. 259
    'group:7c7b80849afdc27d46292db4cb195a63c8a14d20db16607789ace0652c35c216': (
        'PL002', 259, 'col 509 A', 'tertullian_plato_socrate_col509'
    ),
    # 10. Plato: in Phaedro, De Anima (col 659 A) -> PL002, p. 338
    'group:0b4fb1e4d51d040c41ecde9a245889eab7dbf9ba74b81d359e24600a40722c8c': (
        'PL002', 338, 'col 659 A', 'tertullian_plato_phaedro_col659'
    ),
    # 11. Plato: in Phaedone, De Anima (col 661 A) -> PL002, p. 339
    'group:1c5b37f22c549ac02ac02899b6dd02caa385b8a04dda7c0ab9eb430b8c0260c0': (
        'PL002', 339, 'col 661 A', 'tertullian_plato_phaedone_col661'
    ),
    # 12. Plato: in Phaedro, De Anima (col 668 B) -> PL002, p. 342
    'group:da819dc7f71a15aae79a62be6ca2b1f9b0b85164fd8eac09367cdafc3f23a7a5': (
        'PL002', 342, 'col 668 B', 'tertullian_plato_phaedro_col668'
    ),
    # 13. Plato: in Parmenide et Timaeo de Ideis, De Anima (col 676 A) -> PL002, p. 346
    'group:806ba431987b0f8aeda8dd729f5c774411b874a8b37a67b9ee59dd351f5c76a8': (
        'PL002', 346, 'col 676 A', 'tertullian_plato_parmenide_timaeo_ideis'
    ),
    # 14. Plato: et II, 678 A, Timaeo 683 B, Legibus, De Anima -> PL002, p. 347
    'group:9150e09551ee5823b14c73c798d2a16002c76f7a62229ebf909cfd92efecee5a': (
        'PL002', 347, 'col 678 A', 'tertullian_plato_timaeo_legibus_col678'
    ),
    # 15. Plato: in Phaedone 686 C, De Anima -> PL002, p. 351
    'group:c641570910c40ba552b7fbdb532aa8d0236582f8300bb096bc6ce289ea3bcc63': (
        'PL002', 351, 'col 686 C', 'tertullian_plato_phaedone_col686'
    ),
    # 16. Plato: in Philebo 688 A, De Anima -> PL002, p. 352
    'group:f11a94c0be14357c0558c786165cb1a25fdc88fc18f4dbea6599d22f9b126c7e': (
        'PL002', 352, 'col 688 A', 'tertullian_plato_philebo_col688'
    ),
    # 17. Plato: in Parmenide, De Anima (col 693 A) -> PL002, p. 355
    'group:13f3415b68b0c7cf25b7615cf20e79dc9a9816da1b5c9a5bba7c58884e1a0ea3': (
        'PL002', 355, 'col 693 A', 'tertullian_plato_parmenide_col693'
    ),
    # 18. Plato: et II, 693 A, in Legibus, De Anima -> PL002, p. 355
    'group:093b2440a432e1a14cf6e96a03945444211da24ebd454d2626d292799a148b77': (
        'PL002', 355, 'col 693 A', 'tertullian_plato_legibus_col693'
    ),
}


class PL002TertullianLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self.summary_path = summary_path
        self._page_file_cache: Dict[Tuple[str, int], str] = {}
        self._init_file_cache('PL001')
        self._init_file_cache('PL002')

    def _init_file_cache(self, vol: str):
        text_dir = self.root_dir / 'teste' / vol / 'text'
        if not text_dir.exists():
            return
        for f in text_dir.glob('*.txt'):
            try:
                p_num = int(f.stem.split('-')[-1])
                self._page_file_cache[(vol, p_num)] = str(f.resolve())
            except ValueError:
                continue

    def get_file_for_page(self, vol: str, page_num: int) -> Optional[str]:
        return self._page_file_cache.get((vol, page_num))

    def resolve_item(self, item_id: str, label: str, ref_raw: str) -> List[Dict[str, Any]]:
        clean_id = item_id.strip()
        if clean_id not in PL002_MAP:
            return []

        target_vol, p_num, cap_or_col, reason = PL002_MAP[clean_id]
        fpath = self.get_file_for_page(target_vol, p_num)
        if not fpath:
            return []

        cand_payload = {
            'file': fpath,
            'physical': p_num,
            'rank': 1,
            'score': 10.0,
            'score_kind': 'heuristic_not_probability',
            'reasons': [
                'tertullianus_opera_omnia_locator',
                'pamelius_scriptores_veteres_validation',
                reason
            ],
            'uncertainties': [],
            'certified': True,
            'snippet': f'PL002 Tertullianus mapping: {label} ({ref_raw}) -> {target_vol} p. {p_num} ({cap_or_col})',
            'evidence': {
                'file': fpath,
                'target_volume': target_vol,
                'target_page': p_num,
                'chapter_or_col': cap_or_col,
                'reason': reason,
                'timestamp': datetime.now(timezone.utc).isoformat()
            },
            'navigable': True
        }

        return [{
            'item_id': item_id,
            'target_volume': target_vol,
            'physical_page': p_num,
            'target_file': fpath,
            'reason': reason,
            'candidate_payload': cand_payload
        }]

    def evaluate_all(self, db_conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        cur = db_conn.cursor()
        cur.execute('''
            SELECT id, label, ref_raw
            FROM items
            WHERE volume = 'PL002' AND state = 'source_only'
        ''')
        rows = cur.fetchall()
        promotions = []

        for item_id, label, ref_raw in rows:
            resolutions = self.resolve_item(item_id, label, ref_raw or '')
            if resolutions:
                res = resolutions[0]
                promotions.append({
                    'item_id': item_id,
                    'label': label,
                    'ref_raw': ref_raw,
                    'target_volume': res['target_volume'],
                    'physical_page': res['physical_page'],
                    'target_file': res['target_file'],
                    'reason': res['reason'],
                    'payload': res['candidate_payload']
                })

        return promotions

    def apply_promotions(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        try:
            con.execute('PRAGMA foreign_keys = ON;')
            promotions = self.evaluate_all(con)
            promoted_count = len(promotions)

            if dry_run:
                return {
                    'status': 'dry_run_complete',
                    'dry_run': True,
                    'total_evaluated': promoted_count,
                    'total_promoted': promoted_count,
                    'sample_promotions': promotions[:10]
                }

            cur = con.cursor()
            for promo in promotions:
                item_id = promo['item_id']
                payload = promo['payload']
                cand_json = json.dumps(payload, ensure_ascii=False)
                compressed = zlib.compress(cand_json.encode('utf-8'))

                cur.execute(
                    'DELETE FROM candidate_storage WHERE item_id = ? AND file = ?',
                    (item_id, promo['target_file'])
                )
                cur.execute('''
                    INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                    VALUES (?, ?, 1, 10.0, 1, ?)
                ''', (item_id, promo['target_file'], compressed))

                cur.execute(
                    'UPDATE items SET state = "with_destinations" WHERE id = ?',
                    (item_id,)
                )

            con.commit()
            self._update_summary(len(promotions))

            return {
                'status': 'success',
                'dry_run': False,
                'total_evaluated': promoted_count,
                'total_promoted': promoted_count,
                'sample_promotions': promotions[:10]
            }
        finally:
            con.close()

    def _update_summary(self, newly_resolved: int):
        if not os.path.exists(self.summary_path):
            return

        with open(self.summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)

        con = sqlite3.connect(self.db_path)
        try:
            cur = con.cursor()
            cur.execute('SELECT count(*) FROM items')
            tot = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM items WHERE state="with_destinations"')
            with_d = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM items WHERE state="source_only"')
            src_o = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM candidate_storage')
            cands = cur.fetchone()[0]

            summary['total_items'] = tot
            summary['items_with_destinations'] = with_d
            summary['items_source_only'] = src_o
            summary['percentage_with_destinations'] = round(with_d / tot * 100, 4) if tot > 0 else 0.0
            summary['candidate_storage_records'] = cands
            summary['last_updated'] = datetime.now(timezone.utc).isoformat()
            summary['last_campaign'] = 'Sprint 40: PL002 Tertullianus Opera Omnia Locator (+18 items)'

            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        finally:
            con.close()


def main():
    parser = argparse.ArgumentParser(description='Sprint 40: PL002 Tertullianus Opera Omnia Locator')
    parser.add_argument('--apply', action='store_true', help='Apply promotions to database')
    parser.add_argument('--db-path', default=DB_PATH, help='Path to search.sqlite')
    args = parser.parse_args()

    locator = PL002TertullianLocator(db_path=args.db_path)
    res = locator.apply_promotions(dry_run=not args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

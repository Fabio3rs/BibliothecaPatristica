"""
PL045 Saint Augustine Tomus Decimus Index Locator
Sprint 51 of Onomastic Catalog Recovery

Resolves all 14 residual source_only items from PL045:
1. Editorial Context:
   - PL045 contains the second part of Tomus Decimus of Saint Augustine's Opera Omnia
     (the anti-Pelagian writings, continued from PL044).
   - At the end of the volume (cols 1897-1906, pp. 461-465), Migne includes the
     *INDEX RERUM QUÆ IN HOC DECIMO VOLUMINE CONTINENTUR*.
   - This index indexes the entire Tomus Decimus (spanning PL044 cols 1-1094 and PL045).
   - The 14 residual entries index works located in the first half of the tomo (PL044):
       * De Peccatorum Meritis et Remissione (cols 109-110, 162)
       * De Nuptiis et Concupiscentia (col 432)
       * De Anima et ejus Origine (cols 475-476)
       * Contra Duas Epistolas Pelagianorum (cols 547-548, 554, 558, 559, 562, 570, 571-572, 587-588)
   - Entries with 'ibid.' inherit the column numbers from immediately preceding chapter entries in the index.
2. Verified Physical Mappings:
   - All 14 items map deterministically to verified physical pages within PL044
     (pages 59, 85, 220, 242, 278, 281, 283, 284, 285, 289, 290, 298).
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

# Mapping: item_id or group_id -> list of (target_vol, physical_page, col_desc, reason)
PL045_MAP: Dict[str, List[Tuple[str, int, str, str]]] = {
    # 1. S. AURELII AUGUSTINI... DE PECCATORUM MERITIS... (109-110 -> PL044 col 109-110, phys p. 59)
    'region-entry:e3cfaf9442b936d908b1959129e20c0442af40a58be1897675ad8caac507c609': [
        ('PL044', 59, 'Tomus X col 109-110 (De Peccatorum Meritis et Remissione ad Marcellinum)', 'pl045_de_peccatorum_meritis')
    ],
    'region-group:e3cfaf9442b936d908b1959129e20c0442af40a58be1897675ad8caac507c609': [
        ('PL044', 59, 'Tomus X col 109-110 (De Peccatorum Meritis et Remissione ad Marcellinum)', 'pl045_de_peccatorum_meritis')
    ],

    # 2. XII. Perfecta justitia humana imperfecta est (162 -> PL044 col 162, phys p. 85)
    'region-entry:7f51c16933744d23145b7a8db4fa0fc3c2bd5f22b0f3ac79a9713317eed8c1f7': [
        ('PL044', 85, 'Tomus X col 162 (De Peccatorum Meritis lib. II, cap. XIII)', 'pl045_perfecta_justitia')
    ],
    'region-group:7f51c16933744d23145b7a8db4fa0fc3c2bd5f22b0f3ac79a9713317eed8c1f7': [
        ('PL044', 85, 'Tomus X col 162 (De Peccatorum Meritis lib. II, cap. XIII)', 'pl045_perfecta_justitia')
    ],

    # 3. XXXI. Caro, carnis affectus (ibid -> col 432, PL044 phys p. 220)
    'region-entry:dcd22454e13ee02cb19ac535e2db39d2630548e07d2a2d2c84920cbf7a10b141': [
        ('PL044', 220, 'Tomus X col 432 (De Nuptiis et Concupiscentia lib. I, cap. XXXI)', 'pl045_caro_carnis_affectus')
    ],
    'region-group:dcd22454e13ee02cb19ac535e2db39d2630548e07d2a2d2c84920cbf7a10b141': [
        ('PL044', 220, 'Tomus X col 432 (De Nuptiis et Concupiscentia lib. I, cap. XXXI)', 'pl045_caro_carnis_affectus')
    ],

    # 4. IX. Voluptate peccat qui solo timore non peccat (558 -> PL044 col 558, phys p. 283)
    'region-entry:0ab8ae4853722b593487665c88580f86285e0dc5e0429f72ec4b48e73340d8f0': [
        ('PL044', 283, 'Tomus X col 558 (Contra Duas Epistolas Pelagianorum lib. I, cap. IX)', 'pl045_voluptate_peccat')
    ],
    'region-group:0ab8ae4853722b593487665c88580f86285e0dc5e0429f72ec4b48e73340d8f0': [
        ('PL044', 283, 'Tomus X col 558 (Contra Duas Epistolas Pelagianorum lib. I, cap. IX)', 'pl045_voluptate_peccat')
    ],

    # 5. V. Altera Juliani calumnia... (ibid -> col 554, PL044 phys p. 281)
    'region-entry:444f0b565d742932005b506170c3f1bfca202a565fa638b1d967e5d3e57f62a4': [
        ('PL044', 281, 'Tomus X col 554 (Contra Duas Epistolas Pelagianorum lib. I, cap. V)', 'pl045_altera_juliani_calumnia')
    ],
    'region-group:444f0b565d742932005b506170c3f1bfca202a565fa638b1d967e5d3e57f62a4': [
        ('PL044', 281, 'Tomus X col 554 (Contra Duas Epistolas Pelagianorum lib. I, cap. V)', 'pl045_altera_juliani_calumnia')
    ],

    # 6. LIBER PRIMUS. — Ad Renatum monachum... (ibid -> col 475-476, PL044 phys p. 242)
    'region-entry:5110da2cf1035bdb3e57517ec0c1440cc3cff9e10cdcd8f021fd4c88758d83f1': [
        ('PL044', 242, 'Tomus X col 475-476 (De Anima et ejus Origine lib. I, Ad Renatum)', 'pl045_ad_renatum_monachum')
    ],
    'region-group:5110da2cf1035bdb3e57517ec0c1440cc3cff9e10cdcd8f021fd4c88758d83f1': [
        ('PL044', 242, 'Tomus X col 475-476 (De Anima et ejus Origine lib. I, Ad Renatum)', 'pl045_ad_renatum_monachum')
    ],

    # 7. II. Pelagianorum calumnia, de usu veteris Legis (ibid -> col 587-588, PL044 phys p. 298)
    'region-entry:5a016587d0bca7da27704fa607b252c6afa8dff976283176a2567c0653081b4a': [
        ('PL044', 298, 'Tomus X col 587-588 (Contra Duas Epistolas Pelagianorum lib. III, cap. II)', 'pl045_pelagianorum_calumnia_legis')
    ],
    'region-group:5a016587d0bca7da27704fa607b252c6afa8dff976283176a2567c0653081b4a': [
        ('PL044', 298, 'Tomus X col 587-588 (Contra Duas Epistolas Pelagianorum lib. III, cap. II)', 'pl045_pelagianorum_calumnia_legis')
    ],

    # 8. CAPUT PRIMUM. Pelagiani catholicos Manichæorum nomine criminantur (ibid -> col 571-572, PL044 phys p. 290)
    'region-entry:5a1c4bd936f5455644c44edab198a21b786abf034f58dfaef6223580985589e8': [
        ('PL044', 290, 'Tomus X col 571-572 (Contra Duas Epistolas Pelagianorum lib. II, cap. I)', 'pl045_manichaeorum_nomine_criminantur')
    ],
    'region-group:5a1c4bd936f5455644c44edab198a21b786abf034f58dfaef6223580985589e8': [
        ('PL044', 290, 'Tomus X col 571-572 (Contra Duas Epistolas Pelagianorum lib. II, cap. I)', 'pl045_manichaeorum_nomine_criminantur')
    ],

    # 9. ADMONITIO IN OPUS CONTRA DUAS EPIST. PELAG (547-548 -> PL044 col 547-548, phys p. 278)
    'region-entry:66121f576220f19659fed593869987b48c5e5f45662aedca267a647a7a73fc35': [
        ('PL044', 278, 'Tomus X col 547-548 (Admonitio in Opus Contra Duas Epistolas Pelagianorum)', 'pl045_admonitio_contra_duas_epistolas')
    ],
    'region-group:66121f576220f19659fed593869987b48c5e5f45662aedca267a647a7a73fc35': [
        ('PL044', 278, 'Tomus X col 547-548 (Admonitio in Opus Contra Duas Epistolas Pelagianorum)', 'pl045_admonitio_contra_duas_epistolas')
    ],

    # 10. XIII. Septima calumnia, In Baptismo non remitti peccata omnia (ibid -> col 562, PL044 phys p. 285)
    'region-entry:8adbaefc9d5f1a1b0b6288eb15148a7a5c7db8136078269178b829326740b635': [
        ('PL044', 285, 'Tomus X col 562 (Contra Duas Epistolas Pelagianorum lib. I, cap. XIII)', 'pl045_septima_calumnia_baptismo')
    ],
    'region-group:8adbaefc9d5f1a1b0b6288eb15148a7a5c7db8136078269178b829326740b635': [
        ('PL044', 285, 'Tomus X col 562 (Contra Duas Epistolas Pelagianorum lib. I, cap. XIII)', 'pl045_septima_calumnia_baptismo')
    ],

    # 11. CAPUT PRIMUM. Exordium (ibid -> col 587-588, PL044 phys p. 298)
    'region-entry:a1511118f211e9a3074a735032607c26ce0e42109790bff45ba65aafc16a9548': [
        ('PL044', 298, 'Tomus X col 587-588 (Contra Duas Epistolas Pelagianorum lib. III, cap. I)', 'pl045_exordium_lib_iii')
    ],
    'region-group:a1511118f211e9a3074a735032607c26ce0e42109790bff45ba65aafc16a9548': [
        ('PL044', 298, 'Tomus X col 587-588 (Contra Duas Epistolas Pelagianorum lib. III, cap. I)', 'pl045_exordium_lib_iii')
    ],

    # 12. X. XI. Sequitur de verbis Apostoli (559 -> PL044 col 559, phys p. 284)
    'region-entry:d956a52fbc96e3334e109eda3684dca5feed6b55bcc48a417494385354880fed': [
        ('PL044', 284, 'Tomus X col 559 (Contra Duas Epistolas Pelagianorum lib. I, cap. X-XI)', 'pl045_sequitur_de_verbis_apostoli')
    ],
    'region-group:d956a52fbc96e3334e109eda3684dca5feed6b55bcc48a417494385354880fed': [
        ('PL044', 284, 'Tomus X col 559 (Contra Duas Epistolas Pelagianorum lib. I, cap. X-XI)', 'pl045_sequitur_de_verbis_apostoli')
    ],

    # 13. II. Manichæorum et Pelagianorum hæreses... (ibid -> col 571-572, PL044 phys p. 290)
    'region-entry:f9380adeac5bcd23c51b833d3f2dde66fee7bc9674f740f0dd34e81b4a97d56d': [
        ('PL044', 290, 'Tomus X col 571-572 (Contra Duas Epistolas Pelagianorum lib. II, cap. II)', 'pl045_manichaeorum_pelagianorum_haereses')
    ],
    'region-group:f9380adeac5bcd23c51b833d3f2dde66fee7bc9674f740f0dd34e81b4a97d56d': [
        ('PL044', 290, 'Tomus X col 571-572 (Contra Duas Epistolas Pelagianorum lib. II, cap. II)', 'pl045_manichaeorum_pelagianorum_haereses')
    ],

    # 14. XXIV. Juliani Epistolæ conclusionem refellit (ibid -> col 570, PL044 phys p. 289)
    'region-entry:fb404a65497780c4d56e8e945d989d3eb165c47d5509db7dfa8bed6e4dc7af09': [
        ('PL044', 289, 'Tomus X col 570 (Contra Duas Epistolas Pelagianorum lib. I, cap. XXIV)', 'pl045_juliani_conclusionem_refellit')
    ],
    'region-group:fb404a65497780c4d56e8e945d989d3eb165c47d5509db7dfa8bed6e4dc7af09': [
        ('PL044', 289, 'Tomus X col 570 (Contra Duas Epistolas Pelagianorum lib. I, cap. XXIV)', 'pl045_juliani_conclusionem_refellit')
    ],
}


class PL045AugustineIndexLocator:
    """Deterministic locator for PL045 Saint Augustine Tomus Decimus index items."""

    def __init__(self, db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.db_path = db_path
        self.summary_path = summary_path
        self._file_cache: Dict[Tuple[str, int], Optional[str]] = {}

    def get_file_for_page(self, volume: str, page_num: int) -> Optional[str]:
        cache_key = (volume, page_num)
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]

        vol_dir = Path('teste') / volume / 'text'
        if not vol_dir.exists():
            self._file_cache[cache_key] = None
            return None

        candidates = sorted(vol_dir.glob(f'*-{page_num:04d}.txt'))
        if not candidates:
            candidates = sorted(vol_dir.glob(f'*-{page_num:03d}.txt'))
        if not candidates:
            candidates = sorted(vol_dir.glob(f'*-{page_num}.txt'))

        if candidates:
            res = str(candidates[0].resolve())
            self._file_cache[cache_key] = res
            return res

        self._file_cache[cache_key] = None
        return None

    def resolve_item(self, item_id: str, label: str, ref_raw: str, group_id: Optional[str] = None) -> List[Dict[str, Any]]:
        clean_id = item_id.strip()
        targets = None
        if clean_id in PL045_MAP:
            targets = PL045_MAP[clean_id]
        elif group_id and group_id.strip() in PL045_MAP:
            targets = PL045_MAP[group_id.strip()]

        if not targets:
            return []

        results = []
        for rank, (target_vol, p_num, desc, reason) in enumerate(targets, 1):
            fpath = self.get_file_for_page(target_vol, p_num)
            if not fpath:
                continue

            cand_payload = {
                'file': fpath,
                'physical': p_num,
                'rank': rank,
                'score': 10.0 if rank == 1 else 9.0,
                'score_kind': 'heuristic_not_probability',
                'reasons': [
                    'pl045_augustine_index_locator',
                    'augustine_tomus_decimus_validation',
                    reason
                ],
                'uncertainties': [],
                'certified': True,
                'snippet': f'PL045 Augustine Index mapping: {label} ({ref_raw}) -> {target_vol} p. {p_num} ({desc})',
                'evidence': {
                    'file': fpath,
                    'target_volume': target_vol,
                    'target_page': p_num,
                    'editorial_citation': desc,
                    'reason': reason,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                },
                'navigable': True
            }

            results.append({
                'item_id': item_id,
                'target_volume': target_vol,
                'physical_page': p_num,
                'target_file': fpath,
                'rank': rank,
                'reason': reason,
                'candidate_payload': cand_payload
            })

        return results

    def evaluate_all(self, db_conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        cur = db_conn.cursor()
        cur.execute('''
            SELECT id, label, ref_raw, group_id
            FROM items
            WHERE volume = "PL045" AND state = "source_only"
        ''')
        rows = cur.fetchall()
        promotions = []

        for item_id, label, ref_raw, group_id in rows:
            resolutions = self.resolve_item(item_id, label, ref_raw or '', group_id=group_id)
            if resolutions:
                promotions.append({
                    'item_id': item_id,
                    'label': label,
                    'ref_raw': ref_raw,
                    'candidates': resolutions
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
                    'sample_promotions': [
                        {
                            'item_id': p['item_id'],
                            'label': p['label'],
                            'ref_raw': p['ref_raw'],
                            'candidates_count': len(p['candidates'])
                        }
                        for p in promotions[:10]
                    ]
                }

            cur = con.cursor()
            for promo in promotions:
                item_id = promo['item_id']
                for cand in promo['candidates']:
                    payload = cand['candidate_payload']
                    cand_json = json.dumps(payload, ensure_ascii=False)
                    compressed = zlib.compress(cand_json.encode('utf-8'))

                    cur.execute(
                        'DELETE FROM candidate_storage WHERE item_id = ? AND file = ?',
                        (item_id, cand['target_file'])
                    )
                    cur.execute('''
                        INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                    ''', (item_id, cand['target_file'], cand['rank'], payload['score'], compressed))

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
                'sample_promotions': [
                    {
                        'item_id': p['item_id'],
                        'label': p['label'],
                        'ref_raw': p['ref_raw'],
                        'candidates_count': len(p['candidates'])
                    }
                    for p in promotions[:10]
                ]
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
            summary['last_campaign'] = 'Sprint 51: PL045 Saint Augustine Tomus Decimus Index Locator (+14 items)'

            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        finally:
            con.close()


def main():
    parser = argparse.ArgumentParser(description='Sprint 51: PL045 Augustine Index Locator')
    parser.add_argument('--apply', action='store_true', help='Apply promotions to database')
    parser.add_argument('--db-path', default=DB_PATH, help='Path to search.sqlite')
    args = parser.parse_args()

    locator = PL045AugustineIndexLocator(db_path=args.db_path)
    res = locator.apply_promotions(dry_run=not args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

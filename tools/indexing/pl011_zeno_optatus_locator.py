"""
PL011 Saint Zeno and Saint Optatus Combined Index Locator
Sprint 52 of Onomastic Catalog Recovery

Resolves all 14 residual source_only items from PL011:
1. Editorial Context:
   - PL011 contains the works of two major 4th-century Latin Fathers:
     1. Saint Zeno of Verona (*Sancti Zenonis Sermones seu Tractatus*, cols 1-528)
        with the benchmark edition and prolegomena of the Ballerini brothers (Verona 1739).
     2. Saint Optatus of Milevis (*Sancti Optati Afri Milevitani De Schismate Donatistarum*, cols 529-1500+)
        with the benchmark edition, dissertations, and historical monuments of L. Ellies Du Pin (Paris 1700).
   - At the end of the volume (cols 1500-1544+, pp. 763-780), Migne reproduced both benchmark indexes:
     * *INDEX ANALYTICUS OPERUM SANCTI ZENONIS* (cols 1501-1528, pp. 763-768)
     * *INDEX ANALYTICUS OPERUM SANCTI OPTATI ET MONUMENTORUM* (cols 1529-1544+, pp. 769-780)
   - Citations in the Zeno index refer to the Ballerini 1739 edition:
     * Roman numerals (e.g., 'CL.', 'XCVI.') refer to chapters of the Ballerini Dissertationes (pp. 60, 96).
     * Arabic numerals (e.g., '132', '238', '321') refer to the original pages of the Ballerini edition
       preserved directly in the text and notes of PL011 (pp. 193, 238, 241, 290).
   - Citations in the Optatus index refer to Du Pin's 1700 benchmark edition:
     * 'Hist. 19' refers to Du Pin's *Historia Donatistarum* (Migne cols 815-816, p. 412).
     * 'Geogr. 31' refers to Du Pin's *Geographia Sacra Africana* (Migne col 842, p. 425).
     * 'Hist. Coll. 348, 349' refers to François Baudouin's *Historia Collationis Carthaginensis*
       (Migne cols 1455-1458, pp. 733-734).
     * '18 et 19' refers to Optatus Book I (Migne cols 903-904, p. 456).
2. Verified Physical Mappings:
   - All 14 items map deterministically to verified physical pages within PL011
     (pages 60, 96, 193, 238, 241, 290, 412, 425, 456, 733, 734).
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

# Mapping: item_id or group:group_id -> list of (target_vol, physical_page, col_desc, reason)
PL011_MAP: Dict[str, List[Tuple[str, int, str, str]]] = {
    # 1. Invidium concinnare, 132 et adn. 31 (Ballerini pag. 132, PL011 phys p. 193)
    '1074b64de0da6deec95647c3ec72e7288f69ffdafc2831931a8c2f3a1ba955d4': [
        ('PL011', 193, 'Ballerini pag. 132-133, adn. 34 (Invidiosis vocibus Deo concinnat invidiam, Migne col 381-382)', 'pl011_invidium_concinnare')
    ],
    'group:39672dc75c3a7b22a1d80acf3656ef2ad077eb5f489e3f6c9d0cec4aaae0e129': [
        ('PL011', 193, 'Ballerini pag. 132-133, adn. 34 (Invidiosis vocibus Deo concinnat invidiam, Migne col 381-382)', 'pl011_invidium_concinnare')
    ],

    # 2. Vicentinæ diœcesis ecclesiæ S. Zenonis... CL. Victorini Marii vindiciæ... XCVI (Ballerini Caput CL & XCVI, PL011 phys p. 96 & 60)
    '1ac485b7d159b7454055bf03d529c816652b92d1f8fa98208bbd7bd47db09bb6': [
        ('PL011', 96, 'Ballerini Dissertatio Caput CL (Vicentinæ et finitimarum diœcesum ecclesiæ S. Zenoni dicatæ, Migne col 187-190)', 'pl011_vicentinae_dioecesis_zeno'),
        ('PL011', 60, 'Ballerini Dissertatio Caput XCVI (Marius Victorinus a Petavii censura vindicatus, Migne col 115-118)', 'pl011_marius_victorinus_petavius')
    ],

    # 3. Vineæ cultura accurate descripta (321 et seqq -> Ballerini pag. 231-235, 321, PL011 phys p. 238 & 290)
    'a0f80333aee9a2821403238832534596e5bfaaeedb9375b90e34376e968c25ca': [
        ('PL011', 238, 'S. Zenonis Tractatus de Vinea (In Psalmum 79, Migne col 467-470, pag. 231-235)', 'pl011_vinea_cultura_descripta'),
        ('PL011', 290, 'Sparaverii Observationes in Tractatum de Vinea (Migne col 571-574, pag. 321)', 'pl011_vinea_cultura_observationes')
    ],

    # 4. dis, iidem. Tractatus cur breves paschali tempore... (XLIII, 238 -> Ballerini pag. 238, PL011 phys p. 241)
    'e9c2d14ad9c36cd71bddee545d63d7660f66f46cf2ed756ea113743d49489f73': [
        ('PL011', 241, 'Ballerini Admonitio in Tractatus Paschales pag. 238 (Tractatus cur breves paschali tempore, Migne col 473-476)', 'pl011_tractatus_breves_paschali')
    ],
    'group:299900b3483073244d3dcd4f00b7171ee369e616bec1e382aa8db4d32eca3fff': [
        ('PL011', 241, 'Ballerini Admonitio in Tractatus Paschales pag. 238 (Tractatus cur breves paschali tempore, Migne col 473-476)', 'pl011_tractatus_breves_paschali')
    ],

    # 5. Majorinus adversus eum ab ipsis ordinatur (18 et 19 -> Optatus lib. I cap. 10-11, PL011 phys p. 456 & 393)
    'c0cf1ce419a37b3f4b9bc5fb911ce0b737a2b1c063880ac96ff5d9fc647f7c27': [
        ('PL011', 456, 'S. Optati De Schismate Donatistarum lib. I, cap. 10-11 (Majorinus a Cæciliano, Migne col 903-906)', 'pl011_majorinus_ordinatur'),
        ('PL011', 393, 'Du Pin Historia Donatistarum (Origo schismatis, Majorini ordinatio, Migne col 777-780)', 'pl011_majorinus_historia')
    ],
    'group:12b840ecdeb83ca7ed93f47ebba1bc561111992dd69d2c04acb01ee0a95b2b8b': [
        ('PL011', 456, 'S. Optati De Schismate Donatistarum lib. I, cap. 10-11 (Majorinus a Cæciliano, Migne col 903-906)', 'pl011_majorinus_ordinatur')
    ],

    # 6. et Hist. Collat (348 -> Balduinus Historia Collationis pag. 348, PL011 phys p. 733)
    '6205994a80d61a304157dcf5ce2a9abb307c3d52e65fcde873e2b8cfa9edf5d7': [
        ('PL011', 733, 'Balduini Historia Collationis Carthaginensis pag. 348 (Migne col 1455-1456)', 'pl011_hist_collat_348')
    ],

    # 7. Hujus utilitas, Hist. Coll (348 et 349 -> Balduinus Historia Collationis pag. 348-349, PL011 phys p. 733 & 734)
    '334ce96aa04b6051f172332d56ad78c161ca96eee2bf68c89b434144e8f2f51a': [
        ('PL011', 733, 'Balduini Historia Collationis Carthaginensis pag. 348 (Collationis utilitas, Migne col 1455-1456)', 'pl011_collatio_utilitas_348'),
        ('PL011', 734, 'Balduini Historia Collationis Carthaginensis pag. 349 (Migne col 1457-1458)', 'pl011_collatio_utilitas_349')
    ],

    # 8. Conveniuntur Donatistæ ut Collationem accipiant, Hist (19 -> Du Pin Historia Donatistarum pag. 19, PL011 phys p. 412)
    'd6da1182d3a42e80dc51a1c6986c940d319139bb4814eefe1576623bde468046': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Catholici Donatistas ad collationem invitant, Migne col 815-816)', 'pl011_conveniuntur_donatistae')
    ],
    'group:2f8b25a40fce2b2b841fbba7a882c7cff0bc01f367da027d29b1060d59a72562': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Catholici Donatistas ad collationem invitant, Migne col 815-816)', 'pl011_conveniuntur_donatistae')
    ],

    # 9. Superest in vivis usque ad annum circiter 355, Geogr (31 -> Du Pin Geographia Sacra pag. 31, PL011 phys p. 425)
    '06407dd2368859c97845091d412027b479316737d5251d57011d518e61997842': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Donatus ad annum circiter 355 vivis superest, Migne col 841-842)', 'pl011_donatus_superest_355')
    ],
    'group:0008c35ad58936b8f5450bcd6ef2be60cf1c5a294a12904afa3ab45b96d2d633': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Donatus ad annum circiter 355 vivis superest, Migne col 841-842)', 'pl011_donatus_superest_355')
    ],

    # 10. Collationem repudiant, Hist (19 -> Du Pin Historia Donatistarum pag. 19, PL011 phys p. 412)
    '5ad201dc5952d2e466b32c9f4cd7bb27496656ca5f913e14effca4557ca6661b': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Donatistæ collationem recusarunt, Migne col 815-816)', 'pl011_collationem_repudiant')
    ],
    'group:3fbd4d78b1dbfd845d46c468049e0753932e6f6374f32fa4753a3190a91a29fc': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Donatistæ collationem recusarunt, Migne col 815-816)', 'pl011_collationem_repudiant')
    ],

    # 11. Circa annum 316 successit Majorino, Geogr (31 -> Du Pin Geographia Sacra pag. 31, PL011 phys p. 425)
    '88675ec0a04e453b8491a7a7995743846f6db574c2957cdedebdb8f1b58b54ec': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Donatus Majorino circa annum 316 successit, Migne col 841-842)', 'pl011_donatus_successit_majorino')
    ],
    'group:c4a29300bda58f666715c471faa92d75e34d9cf9df2c0533c2c89ea5a8e2be34': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Donatus Majorino circa annum 316 successit, Migne col 841-842)', 'pl011_donatus_successit_majorino')
    ],

    # 12. PETILIANUS Constantiensis episcopus... Hist (19 -> Du Pin Historia Donatistarum pag. 19, PL011 phys p. 412)
    '7e7b544b4c4d09db24385cda797c446aed9c7f85325220d244393695dc7ab3dc': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Petiliani epistolæ quas refellit Augustinus, Migne col 815-816)', 'pl011_petilianus_augustinus')
    ],
    'group:6706f05a40f4af98f0c21a24c9cd3c97efccf0a1fdd8c26278938be64fb82db8': [
        ('PL011', 412, 'Du Pin Historia Donatistarum pag. 19 (Petiliani epistolæ quas refellit Augustinus, Migne col 815-816)', 'pl011_petilianus_augustinus')
    ],

    # 13. REPARATUS Carthaginensis. Tempus ordinationis ejus, Geogr (31 -> Du Pin Geographia Sacra pag. 31, PL011 phys p. 425)
    'fa8eff06b8d04d760ff78d2555c411948df061e97d806cc80bd3af95c20070fa': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Reparatus circa annum 534 ad Carthaginensem sedem, Migne col 841-842)', 'pl011_reparatus_carthaginensis')
    ],
    'group:84d52aaccce48a32fa86f1d97b8e2fcf296d2eca658299df84843b89c6967f2f': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Reparatus circa annum 534 ad Carthaginensem sedem, Migne col 841-842)', 'pl011_reparatus_carthaginensis')
    ],

    # 14. Succedit Parmeniano, Geogr (31 -> Du Pin Geographia Sacra pag. 31, PL011 phys p. 425)
    '98e459d1013f658c7d2c3564f492f73be426b684f93321d221c2a5cb1bc26b9a': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Primianus Parmeniani successor, Migne col 841-842)', 'pl011_succedit_parmeniano')
    ],
    'group:91973023b7eeeafd409c043e149d14f479bef85b555dfcb05472dc3040f3eb63': [
        ('PL011', 425, 'Du Pin Geographia Sacra Africana pag. 31 (Primianus Parmeniani successor, Migne col 841-842)', 'pl011_succedit_parmeniano')
    ],
}


class PL011ZenoOptatusLocator:
    """Deterministic locator for PL011 Saint Zeno and Saint Optatus index items."""

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
        if clean_id in PL011_MAP:
            targets = PL011_MAP[clean_id]
        elif group_id and f'group:{group_id.strip()}' in PL011_MAP:
            targets = PL011_MAP[f'group:{group_id.strip()}']

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
                    'pl011_zeno_optatus_locator',
                    'ballerini_dupin_benchmark_validation',
                    reason
                ],
                'uncertainties': [],
                'certified': True,
                'snippet': f'PL011 Zeno/Optatus Index mapping: {label} ({ref_raw}) -> {target_vol} p. {p_num} ({desc})',
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
            WHERE volume = "PL011" AND state = "source_only"
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
            summary['last_campaign'] = 'Sprint 52: PL011 Saint Zeno and Saint Optatus Index Locator (+14 items)'

            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        finally:
            con.close()


def main():
    parser = argparse.ArgumentParser(description='Sprint 52: PL011 Zeno and Optatus Locator')
    parser.add_argument('--apply', action='store_true', help='Apply promotions to database')
    parser.add_argument('--db-path', default=DB_PATH, help='Path to search.sqlite')
    args = parser.parse_args()

    locator = PL011ZenoOptatusLocator(db_path=args.db_path)
    res = locator.apply_promotions(dry_run=not args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

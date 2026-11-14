"""
PG012 & PG013 Origen Opera Omnia Locator
Sprint 32 of Onomastic Catalog Recovery

Resolves the 49 residual source_only items from PG012 (23 items) and PG013 (26 items):
1. Editorial Context:
   - Indexes cite Charles Delarue's 1733 edition of Origen (Paris, 4 tomi).
   - PG012 corresponds to Delarue Tomus II (Hexapla, Homilies on Genesis, Exodus,
     Leviticus, Numbers, Joshua, Judges, Ruth, Selecta in Psalmos).
   - PG013 corresponds to Delarue Tomus III (Commentaries on Matthew, Homilies on Luke).
   - Delarue page numbers are embedded directly in the body text of Migne's PG edition
     and in the printed index entries.
   - Suffixes like 'et seqq', 'et seq', 'et\nseqq' prevented basic regex matching.
2. Verified physical mappings:
   - PG012: 23 items (Genesis, Exodus, Psalms) mapped to physical pages 28-535.
   - PG013: 26 items (Matthew, Luke) mapped to physical pages 5-942.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'

PG012_MAP: Dict[str, Tuple[int, str]] = {
    'group:82691786b0aea6ed72b155a4b0cae18ce80ade8b1a80d72a406a5bf5fc946a6e': (175, 'delarue_151_152_merrha_exod_hom7'),
    'group:db026c749428842cdd38af556839bd501ae3692476e65a2f60dbbed221b9e701': (132, 'delarue_110_119_plagis_exod_hom4'),
    'group:6c90409eb3f50cd5100a679e433b463a86bf7031312d18894b09bafe736c9bc7': (136, 'delarue_111_induratio_exod_hom4'),
    'group:0c49feab1cb3bad56f802ee01c6f445c738fa45b343b88253390513499444542': (112, 'delarue_86_88_dies_festi_gen_hom10'),
    'group:39f52e6f7b316ea7696795f12af190233d5285ef525aca09e16733fde39241e2': (113, 'delarue_86_fideles_audiendum_gen_hom10'),
    'group:42ab73af241d1ada4f85f74b8853115ce2c169caa32eff8decc4fa563e978280': (100, 'delarue_74_filiae_lot_gen_hom5'),
    'group:903b6247d77840d76dbceabcc3f09f27d77cf56afb055f00bbe2838feb0c6e7c': (114, 'delarue_86_ecclesiam_conveniendum_gen_hom10'),
    'group:9c0ca21a47bbd4312bb444e6151cd796b0a7c577f1ec1e859d639a77072e618c': (30, 'delarue_4_fati_necessitate_gen_hom1'),
    'group:f13885e73da6d59f577b35c961015900552beba8ae98ce4c0e74ce969a329207': (101, 'delarue_74_fine_saeculi_terra_gen_hom5'),
    'group:5637b857c072de489bc480686914558bcd8c85e751f1146810383c5a50ea7785': (137, 'delarue_111_112_induratio_pharao_exod_hom4'),
    'group:36c9c6e18989fed4c8637666f93946d826990c56ef0ad625fc2429ee71f567e9': (100, 'delarue_74_lot_filiae_culpabiles_gen_hom5'),
    'group:668267c9ed70f4dd2d4395a18ea996d136692a6b7529100ca0f936a97b2f67c0': (101, 'delarue_74_lot_concubitus_gen_hom5'),
    'group:7a6845223c07761f397433f3b6b7130b05f24a544744dd26890dfcee220544a5': (100, 'delarue_74_parte_culpabilis_gen_hom5'),
    'group:cb869e9973928762a48cede7ed8f71057de8093e66989ebc8343272bf521d198': (101, 'delarue_74_lot_uritur_gen_hom5'),
    'group:db8f9dc9594f12f283b228fe9a3ac874bb0d2accc5a3f50d185b7f41fcfd0af8': (137, 'delarue_112_marcionitarum_error_exod_hom4'),
    'group:fe4e5f4a2058a43aab08b64010fff12d9476d8fef948a2653856599eddeaafc1': (28, 'delarue_2_materia_non_ortu_gen_hom1'),
    'group:19e7f205ad174ba812937521da6b026f52ea997645baedf7ed5e5be7aac74834': (136, 'delarue_111_pharaonis_cor_exod_hom4'),
    'group:5524ac28e4208d303ea122a268114a7e16c247950dd697d65ca3c8670dcefbb9': (176, 'delarue_155_pascha_duplex_exod_hom7'),
    'group:ae53c080691a080be1d71fc5381ef12fd2c66fb25b128c519ec8654b1fe7cca8': (174, 'delarue_151_merrahe_aqua_amara_exod_hom7'),
    'group:4ddb722a8057cb882713aa6cf9f9714e3f464008e7557b89ac039dd9ebf7d3f7': (163, 'delarue_140_plagae_aegypti_exod_hom4'),
    'group:4ec63d0b08ab2607532d3d5317f59d0d4f8c80bf5a42c85d6ff0945cc6439c7b': (535, 'delarue_533_resurrectio_corporum_psalmos'),
    'group:77e9b6518eb4704e823cfdfb0b4b6390081c59cf97a50379f6f8ff265515f6e0': (530, 'delarue_516_517_psalmorum_inscriptiones'),
    'group:a7af793e45d8f8c32d802300bf56b4e0b4b7cf738b47765406237ba025173cb3': (535, 'delarue_532_533_resurrectionis_impii_psalmos'),
}

PG013_MAP: Dict[str, Tuple[int, str]] = {
    'group:ad9b7e6c3c27b43440b470f507d4d7b859d8762ec7564ce4471058950a1ee662': (933, 'delarue_953_in_secundas_nuptias_luc_hom17'),
    'group:f3cbcc31133138755f439f33cab11d8589a4cce822be3d735aa275e8edec0a9c': (933, 'delarue_953_baptismum_adulti_luc_hom17'),
    'group:369a9ead4ae7181e4a0e5e5d5be7965b0adddcc8b684c9080e431f0c1dc02ce1': (919, 'delarue_938_christus_virgine_luc_hom6'),
    'group:a13145b031540c45f68743fb3c5f1441ed430230696c63cf4ae0a7c966e795a2': (919, 'delarue_938_baptismum_accedere_luc_hom6'),
    'group:0767776a4230372f4d26f5a39926f6452d1976c0e021a5feb62ca18ca6b3265b': (5, 'delarue_410_commentarii_matthaeum_praef'),
    'group:3de59ac9f13d1f9edda23e51ea77f7aa1dd71c75f05c6bf9af6ea62b46d8173a': (919, 'delarue_938_quare_luc_hom6'),
    'group:ae1a400bb7177350bbae02cba21c1ea5bb10a1dc7ec5df74b352997f95df9212': (919, 'delarue_938_daemonem_in_aere_luc_hom6'),
    'group:2e00066478f66afc5aa51661997870933bd0881c858c232e4af90a2eadc8ef04': (921, 'delarue_939_gentiles_baptismati_luc_hom7'),
    'group:44253727a4b3654d3c541982cb99075b9ea9f975b39033f42c94584d1bfb6e19': (921, 'delarue_939_lapides_vocentur_luc_hom7'),
    'group:552aad2d46b9055deec5e0fbd117cb08965e12bc6f412499a0008fcdd307afb8': (921, 'delarue_939_vocatio_gentium_luc_hom7'),
    'group:e3048fdd6c08cbf881ab8d22a1ec36130d3b7b72a78c852ee3421140c27aa5a3': (921, 'delarue_939_de_lapidibus_istis_luc_hom7'),
    'group:1634886c035bbcb1359f767ad8e6a97c432121c3b97fc67cf044917db635c9bc': (913, 'delarue_932_hieronymus_interpres_luc_hom1'),
    'group:4947c11b5d9af46a4ab1ea31340485315568a2b9b065f7a7422d7c7480b5778c': (726, 'delarue_449_hosanna_vox_laetitiam_matth'),
    'group:d665e83b50aeab1c827c2f1630dbdc5a9aef5c78dcbcdece38fffe786600aa97': (942, 'delarue_959_ira_dei_consumptura_luc_hom23'),
    'group:ee0f0fcaa49604c572412d17b83adef1190c67f1bf3891d87f925f50ca097f7b': (673, 'delarue_692_homines_angelis_superiores_matth'),
    'group:cb4b9460ee0e89dd695b9dbbe1e94b18fe02e59618f4befe8f14a61cc1be4611': (153, 'delarue_691_israelitae_primi_postremi_matth'),
    'group:e809e499049c8b58b97c75214dd5d29ce7098bb0b0ec449fa88c3e3e3f390a47': (939, 'delarue_956_quomodo_intelligendum_luc_hom21'),
    'group:b33bb0ae6115517d7a818e93e5bb173842919aa4005456c41932d951a03175ed': (942, 'delarue_959_lapides_baptista_gentiles_luc_hom23'),
    'group:20b681ebd46df3ff1de29119f68d4af18e46a1baec5cb448ba2b03640500cb1e': (921, 'delarue_940_filii_joseph_non_maria_luc_hom7'),
    'group:9b7e483e40cfb00e964389c49c83337d0f1587755f31f9f5ca5b70f31b7e6a60': (526, 'delarue_605_membra_scandalo_matth_tom13'),
    'group:4839aecb7bc26cf41d45c96fe6820ac38060cd45c1d88304bf6f0d61a6edae98': (75, 'delarue_448_negotiator_margaritas_matth_tom10'),
    'group:e43d1892167ee7f9de1b7249918635002353bc52a6088be22eaaabd71c8a86fb': (933, 'delarue_953_insurgit_origenes_luc_hom17'),
    'group:c848ff6a72b71351b329bd201f49c5a36433f6cd905f0844ffecf8a9ad972ab0': (19, 'delarue_879_prudentia_eleemosynis_matth_series'),
    'group:3729f4f0925663f6fe74daf62a882d2efd43e222cc58499c2cdf63622ad3d829': (939, 'delarue_956_subjectio_erga_praepositos_luc_hom21'),
    'group:e006ce833635772c5bc2af56a464b54826a7e420da09681091a6036aa872e077': (903, 'delarue_922_sol_defecit_passione_matth_series'),
    'group:2ee87c459fa88910cac6ce6dc2fe0d3de7b69dec8bde4cd930491676b038a621': (436, 'delarue_773_vocatio_gentium_matth_series'),
}


class PG012_013_OrigenLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._page_file_cache: Dict[str, Dict[int, str]] = {'PG012': {}, 'PG013': {}}
        self._init_file_caches()

    def _init_file_caches(self):
        for vol in ['PG012', 'PG013']:
            text_dir = self.root_dir / 'teste' / vol / 'text'
            if not text_dir.exists():
                continue
            for f in text_dir.glob('*.txt'):
                try:
                    p_num = int(f.stem.split('-')[-1])
                    self._page_file_cache[vol][p_num] = str(f.resolve())
                except ValueError:
                    continue

    def get_file_for_page(self, volume: str, physical_page: int) -> Optional[str]:
        return self._page_file_cache.get(volume, {}).get(physical_page)

    def resolve_item(self, item_id: str, volume: str, label: str, ref_raw: str, metadata_json: str = '') -> List[Dict[str, Any]]:
        target_map = PG012_MAP if volume == 'PG012' else (PG013_MAP if volume == 'PG013' else {})
        if item_id in target_map:
            p_num, reason = target_map[item_id]
            fpath = self.get_file_for_page(volume, p_num)
            if fpath:
                return [{
                    'target_volume': volume,
                    'physical_page': p_num,
                    'target_file': fpath,
                    'reason': reason,
                    'confidence': 98.0
                }]

        return []

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            """
            SELECT id, volume, section, label, ref_raw, source, metadata_json
            FROM items
            WHERE volume IN ('PG012', 'PG013') AND state = 'source_only'
            ORDER BY volume, id
            """
        )
        rows = cur.fetchall()

        promotions = []
        vol_counts: Dict[str, int] = {}
        for r in rows:
            item_id, vol, section, label, ref_raw, src, meta_json = r
            cands = self.resolve_item(item_id, vol, label or '', ref_raw or '', meta_json or '')
            if cands:
                promotions.append((r, cands))
                vol_counts[vol] = vol_counts.get(vol, 0) + 1

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for r, cands in promotions:
                item_id, vol, section, label, ref_raw, src, meta_json = r
                best = cands[0]

                # 1. Update candidate_storage
                for rank, cand in enumerate(cands, start=1):
                    cand_payload = {
                        "source_type": f"origen_delarue_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_page": cand["physical_page"],
                        "target_file": cand["target_file"],
                    }
                    compressed = zlib.compress(json.dumps(cand_payload).encode("utf-8"))

                    cur.execute(
                        """
                        INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                        ON CONFLICT(item_id, file) DO UPDATE SET
                            rank=excluded.rank,
                            score=excluded.score,
                            candidate_json=excluded.candidate_json
                        """,
                        (item_id, cand["target_file"], rank, cand["confidence"], compressed),
                    )

                # 2. Update items metadata_json and state
                meta = json.loads(meta_json) if meta_json else {}
                meta["state"] = "with_destinations"
                meta["target_volume"] = best["target_volume"]
                meta["target_page"] = best["physical_page"]
                meta["matched_file"] = best["target_file"]
                meta["locator"] = f"origen_delarue_{best['reason']}"
                updated_meta = json.dumps(meta, ensure_ascii=False)

                cur.execute(
                    'UPDATE items SET state="with_destinations", metadata_json=? WHERE id=?',
                    (updated_meta, item_id),
                )

            con.commit()

        con.close()

        return {
            "status": "success",
            "dry_run": dry_run,
            "total_evaluated": len(rows),
            "total_promoted": len(promotions),
            "promotions_by_volume": vol_counts,
            "sample_promotions": [
                {
                    "item_id": p[0][0],
                    "volume": p[0][1],
                    "label": p[0][3],
                    "ref_raw": p[0][4],
                    "physical_page": p[1][0]["physical_page"],
                    "target_file": p[1][0]["target_file"],
                    "reason": p[1][0]["reason"]
                }
                for p in promotions[:10]
            ]
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PG012 and PG013 Origen Opera Omnia.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PG012_013_OrigenLocator(db_path=args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

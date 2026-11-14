"""
PL050 Cassian Opera Omnia Locator
Sprint 34 of Onomastic Catalog Recovery

Resolves all 24 residual source_only items from PL050 (St. John Cassian / Gazaeus edition):
1. Editorial Context:
   - PL049 and PL050 contain the complete works of St. John Cassian with the commentaries
     of Alardus Gazaeus (Atrebati, 1628; Paris, 1642).
   - PL049 contains:
     * De Institutis Coenobiorum libri XII (cols 53-476, pp. 37-246)
     * Collationes I-X (cols 477-844, pp. 247-430)
     * Collationes XI-XXIV (cols 845-1328, pp. 431-658)
     * Collatio XXIV appendix / Gazaeus notae (cols 1504-1522, pp. 659-672)
   - PL050 contains:
     * De Incarnatione Domini contra Nestorium libri VII (cols 9-272, pp. 1-136)
     * Index Rerum et Vocum Notabilium in textu Cassiani (cols 1297-1308, pp. 677-682)
       citing works by book and chapter: Collat. VI, 2; Instit. XI, 3; Incarn. VI, 3; etc.
     * Index Rerum in Commentariis Gazaei (cols 1309-1328, pp. 683-690)
       citing columns with volume prefix 'I' (PL049) or unadorned columns.
2. Verified physical mappings:
   - All 24 items mapped deterministically to verified physical pages in PL049 and PL050.
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

# Mapping: item_id -> (target_volume, physical_page, column_or_cap, reason)
PL050_MAP: Dict[str, Tuple[str, int, Optional[str], str]] = {
    # 1. Afflictiones justorum (Collat. VI cap. 2 -> PL049 page 330)
    'group:171bf25a9546a9beb46b076678c268c306d0eaa06d57786575a225ed70a10ede': (
        'PL049', 330, 'Collat. VI, 2', 'cassian_collat6_cap2_afflictiones'
    ),
    # 2. Cenodoxia: Multiplex et multiformis (Instit. XI cap. 3 -> PL049 page 207)
    'group:2d0836396035a23a2d533c8a7ca721e8bdb39389f58c4a7af5c6d5d8fc262762': (
        'PL049', 207, 'Instit. XI, 3', 'cassian_instit11_cap3_cenodoxia'
    ),
    # 3. Antiocheni symboli fides (Incarn. VI cap. 3 -> PL050 page 82)
    'group:b1238d349e6b9221a0428bf46ab6c5024bdfdc661fc1ffc00d61eae7e5b4746f': (
        'PL050', 82, 'Incarn. VI, 3', 'cassian_incarn6_cap3_symbolum_antiochenum'
    ),
    # 4. Anthropomorphitarum haeresis (Collat. X cap. 2 -> PL049 page 416)
    'group:bdabebc1ed5acc627a15ebd05526fc826c6fad1afd77b8aeb091f9e8c5d6eaa1': (
        'PL049', 416, 'Collat. X, 2', 'cassian_collat10_cap2_anthropomorphitae'
    ),
    # 5. Ira Dei quasi causa sui castigandi (Institut. VIII cap. 2 -> PL049 page 169)
    'group:37e1b936827630208f4594616eeb4ac9ffd8fd0b34979af11f240f102ba8277d': (
        'PL049', 169, 'Institut. VIII, 2', 'cassian_instit8_cap2_ira_dei'
    ),
    # 6. Horariae preces (Institut. III cap. 2 -> PL049 page 82)
    'ad96483cac6b688c4f409a60d282941973e873b78b8f94b2bc07208c63cf3a38': (
        'PL049', 82, 'Institut. III, 2', 'cassian_instit3_cap2_horariae_preces'
    ),
    # 7. Fornicatio: Abstinentia et solitudine coercentur (Institut. VI cap. 3 -> PL049 page 141)
    'group:e874fcd3b04943b9389be9629f9fdda8444ab9b597bcfd56a4e88394725f54b1': (
        'PL049', 141, 'Institut. VI, 3', 'cassian_instit6_cap3_abstinentia_solitudine'
    ),
    # 8. Practice: Per eam ad Theoriam pervenitur (Collat. XIV cap. 2 -> PL049 page 484)
    'group:1291a74196589577760b1460fb4d1345588433b23adac806077b0307b0137a63': (
        'PL049', 484, 'Collat. XIV, 2', 'cassian_collat14_cap2_theoria'
    ),
    # 9. Practice: Quomodo acquiratur (Collat. XIV cap. 3 -> PL049 page 484)
    'group:5dfaf3c886eb83761000bfb0edb8373bf0e6fdbadfe8dbda8fab9efebbd742a8': (
        'PL049', 484, 'Collat. XIV, 3', 'cassian_collat14_cap3_practice_acquisitio'
    ),
    # 10. Orationes cur certis horis in Ecclesia institutae (Instit. III cap. 2 -> PL049 page 82)
    'group:e7b5cdc65c250d54d451fc83b0aa70625a0fb9b135eaef27efc84eba3a3acb94': (
        'PL049', 82, 'Instit. III, 2', 'cassian_instit3_cap2_orationes_certis_horis'
    ),
    # 11. Practice in quo consistat (Collat. XIV cap. 2 -> PL049 page 484)
    'group:edcf21b4a75e20ce4b02c979fe05adb9b90420099f214167fa2476404194a437': (
        'PL049', 484, 'Collat. XIV, 2', 'cassian_collat14_cap2_practice_consistat'
    ),
    # 12. Sanctorum afflictiones justae (Collat. VI cap. 2 -> PL049 page 330)
    'group:cca1f8ed60038ec5237abc8f715d6bf025bce25af015140f509aca6e570f4d74': (
        'PL049', 330, 'Collat. VI, 2', 'cassian_collat6_cap2_sanctorum_afflictiones'
    ),
    # 13. Animi relaxatio honesta et necessaria (Gazaeus I, col 1511 -> PL049 page 662)
    '1a1647f35e3de57dca321613e4a42dfef1706e186057e4530c7a350b5c9aa536': (
        'PL049', 662, 'col 1511', 'gazaeus_col1511_animi_relaxatio'
    ),
    # 14. Balaam propheta avarus et malignus (Gazaeus I, col 1507 -> PL049 page 660)
    'a705230d3b825766d665b6db2a383d07496b9141a2d665cc525d3f9ba5c73eba': (
        'PL049', 660, 'col 1507', 'gazaeus_col1507_balaam_propheta'
    ),
    # 15. Tres facultates (Gazaeus col 1506 -> PL049 page 659)
    'e730664c92b25be0c0e00dabeecd94c86868538a1c8aae0001ddecae42ee73fd': (
        'PL049', 659, 'col 1506', 'gazaeus_col1506_tres_facultates'
    ),
    # 16. Abstinentia: Ejus necessitas (Gazaeus col 551 -> PL049 page 282)
    'group:09f50b1bf172d53b2e7a6926a728c0a4435a33c19336c9f177226f8d8d349a88': (
        'PL049', 282, 'col 551', 'gazaeus_col551_abstinentia_necessitas'
    ),
    # 17. Corpus peccati et membra ejus (Gazaeus col 869 -> PL049 page 441)
    'group:0996921ab19d29ad8670b0758e9ca76386531915150a017813fc5bceb7da9e78': (
        'PL049', 441, 'col 869', 'gazaeus_col869_corpus_peccati'
    ),
    # 18. Eremitae illis et perfectio (Gazaeus I, col 1157 misread 1457 -> PL049 page 585)
    '4840fea579d767bbce02caf0d09267afaf0ebba5fb8f0a3ffb8112ecc6b32058': (
        'PL049', 585, 'col 1157', 'gazaeus_col1157_eremitae_perfectio'
    ),
    # 19. Fornicatio: Difficile superatur (Gazaeus col 266 -> PL049 page 139)
    'group:750328592e488e92174452b1742b0af905517b71ef0a5d2f6e03d6e6593a34dc': (
        'PL049', 139, 'col 266', 'gazaeus_col266_fornicatio_superatur'
    ),
    # 20. Familiaritas monachis vitanda (Gazaeus col 1145 -> PL049 page 579)
    'group:be3a1945d1e715a5df6c3e4bdb34f50dddd7bc625b3c7005e4e8e25a3886f8e3': (
        'PL049', 579, 'col 1145', 'gazaeus_col1145_familiaritas_vitanda'
    ),
    # 21. Ejus officium (Gazaeus col 1504 -> PL049 page 659)
    '05373c3e523bb1e246c04fb2e052077ffeeaad80d79bf3a0c593d5373c6c2ddd': (
        'PL049', 659, 'col 1504', 'gazaeus_col1504_officium'
    ),
    # 22. Morbi otalgicus et ophthalmicus (Gazaeus I, col 1505 -> PL049 page 659)
    '182ec74db1184b36d4100bcc24202c7c813a7b25a09c1284f38f03b4bb19e06c': (
        'PL049', 659, 'col 1505', 'gazaeus_col1505_morbi_otalgicus'
    ),
    # 23. An conveniat praedicationis officium (Gazaeus col 1505 -> PL049 page 659)
    '3173ab2520ea00412c2bf84ad5da9ca2c5be35946a67ad3beb0acef9e48a5bc0': (
        'PL049', 659, 'col 1505', 'gazaeus_col1505_praedicatio_officium'
    ),
    # 24. Centenarius quid designet (Gazaeus I, col 1521 -> PL049 page 667)
    '77bf6138c2703cd548090950c6c468df5ff41df9fd55cbc8eeed83411baaecff': (
        'PL049', 667, 'col 1521', 'gazaeus_col1521_centenarius'
    ),
}


class PL050CassianLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._page_file_cache: Dict[str, Dict[int, str]] = {'PL049': {}, 'PL050': {}}
        self._init_file_caches()

    def _init_file_caches(self):
        for vol in ['PL049', 'PL050']:
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

    def resolve_item(self, item_id: str, label: str, ref_raw: str, metadata_json: str = '') -> List[Dict[str, Any]]:
        if item_id in PL050_MAP:
            target_vol, p_num, cap_or_col, reason = PL050_MAP[item_id]
            fpath = self.get_file_for_page(target_vol, p_num)
            if fpath:
                return [{
                    'target_volume': target_vol,
                    'physical_page': p_num,
                    'target_cap_or_col': cap_or_col,
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
            WHERE volume = 'PL050' AND state = 'source_only'
            ORDER BY id
            """
        )
        rows = cur.fetchall()

        promotions = []
        for r in rows:
            item_id, vol, section, label, ref_raw, src, meta_json = r
            cands = self.resolve_item(item_id, label or '', ref_raw or '', meta_json or '')
            if cands:
                promotions.append((r, cands))

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for r, cands in promotions:
                item_id, vol, section, label, ref_raw, src, meta_json = r
                best = cands[0]

                # 1. Update candidate_storage
                for rank, cand in enumerate(cands, start=1):
                    cand_payload = {
                        "source_type": f"cassian_gazaeus_{cand['reason']}",
                        "confidence": "high",
                        "target_volume": cand["target_volume"],
                        "target_page": cand["physical_page"],
                        "target_file": cand["target_file"],
                    }
                    if cand.get("target_cap_or_col"):
                        cand_payload["target_cap_or_col"] = cand["target_cap_or_col"]

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
                if best.get("target_cap_or_col"):
                    meta["target_cap_or_col"] = best["target_cap_or_col"]
                meta["locator"] = f"cassian_gazaeus_{best['reason']}"
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
            "sample_promotions": [
                {
                    "item_id": p[0][0],
                    "label": p[0][3],
                    "ref_raw": p[0][4],
                    "target_volume": p[1][0]["target_volume"],
                    "physical_page": p[1][0]["physical_page"],
                    "target_file": p[1][0]["target_file"],
                    "reason": p[1][0]["reason"]
                }
                for p in promotions[:10]
            ]
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PL050 Cassian Opera Omnia.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL050CassianLocator(db_path=args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""
PG132 Theophanes Cerameus Homiliae Locator
Sprint 38 of Onomastic Catalog Recovery

Resolves all 22 residual source_only items from PG132 (Theophanes Cerameus, Homiliae):
1. Editorial Context:
   - PG132 contains:
     * Theophanis Ceramei Homiliae in Evangelia Dominicalia et Festa (cols 121-1078, pp. 81-588)
       with the prolegomena and notes of Franciscus Scorsus (cols 1-120, pp. 1-80)
     * Index Rerum Notabilium quae in Homiliis Theophanis Ceramei, itemque in Prooemiis
       ac Notis continentur (cols 1265-1282, pp. 692-700)
   - The index explicitly directs the reader to the columns of this Migne edition:
     "(Revocatur lector ad columnas editionis nostrae; littera n, notas significat.)"
   - All 22 unresolved items from PG132 represent citations with notes ('n. 47', 'n. 86'),
     qualifiers ('et seqq', 'B.'), or contextual 'ibid.' / 'idem' remissions that reference
     exact columns and notes of Theophanes Cerameus and Franciscus Scorsus in PG132.
2. Verified physical mappings:
   - All 22 items mapped deterministically to verified physical pages in PG132
     (pages 166, 211, 259, 339, 355, 376, 386, 394, 437, 441, 450, 488, 514, 581).
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

# Mapping: item_id -> (physical_page, chapter_or_col, reason)
PG132_MAP: Dict[str, Tuple[int, Optional[str], str]] = {
    # 1. Cur sero discipulis, mane mulieribus appareat redivivus (col 682 -> p. 386)
    '1cd00bf5358e0de7a011fec4bc14a1336b271765e89f25de2ef7264fbd3395a4': (
        386, 'col 682', 'theophanes_col682_discipulis_mulieribus'
    ),
    # 2. Ferebatur super aquas ad litteram explicatum (col 775, n. 47 -> p. 437)
    '4d68138d90250f018bb245011d5df1c53701f6a4fe93322b652faf06c7f64b9f': (
        437, 'col 775', 'theophanes_col775_ferebatur_super_aquas'
    ),
    # 3. Gadareni et Gergeseni / Eorum etymon an idem (col 271, n. 64 -> p. 166)
    '58c69c06e4dda7947565af1f9aa252afd6d6d25e27b0e48acd122145558768bd': (
        166, 'col 271', 'theophanes_col271_gadareni_gergeseni'
    ),
    # 4. Ad Adam Deus quo diei tempore in paradisum venerit (col 621, n. 86 -> p. 355)
    '5b4d736fdcf3600d6b695cad3d79bb42fa678c036544ec972bfb5ea3aa2e8d07': (
        355, 'col 621', 'theophanes_col621_adam_paradisus'
    ),
    # 5. Qua de causa ejus sacrificium non impeditum a Deo (col 363 [563], n. 76 -> p. 211)
    '68727ce44399e8f84e76c305a1603f306acea1ab9ff73a4029eea7469365a470': (
        211, 'col 363', 'theophanes_col363_jephte_sacrificium'
    ),
    # 6. Aetates mundi qua ratione distinguantur (col 764, n. 38 -> p. 394)
    '765349b88f12894b356fbc978e53193a5ce6879c25b6405385783063f2fd59d2': (
        394, 'col 764', 'theophanes_col764_aetates_mundi'
    ),
    # 7. Coelum etiam elementum Latinis dictum (col 791, n. 68 -> p. 441)
    '7e01bcf1f40b21808b493ad00f461df1f16cc4266b99d50d68d0feeae58aad58': (
        441, 'col 791', 'theophanes_col791_coelum_elementum'
    ),
    # 8. Euthymius Theophane posterior: ejus error de anima latronis (col 590, n. 51 -> p. 339)
    '80a5051da178612969bbd4e68510cc8fd160337e84f89bcb8117998a219b3445': (
        339, 'col 590', 'theophanes_col590_euthymius_latro'
    ),
    # 9. Paradisi duo a Theophane distincti, sensibilis et intelligibilis (col 590, n. 54 -> p. 339)
    '8b16dcb8380ea09e3a58e836ee95da7c9fe3ff2f1b9278c2fc314bf405afe9a8': (
        339, 'col 590', 'theophanes_col590_paradisi_duo'
    ),
    # 10. Aquas supercoelestes angelos intellexit Origenes (col 773, n. 47 -> p. 437)
    '903316371d58fd8b15d0387d015555561f44fd0bab434722faf1bb8a7a09156d': (
        437, 'col 773', 'theophanes_col773_aquas_supercoelestes'
    ),
    # 11. Jephte historia narratur (col 362 [562] et seqq -> p. 211)
    'c02264ff203aa18b928bfb9b28de9c31456c801d0ed121b720692082e8c773e6': (
        211, 'col 362', 'theophanes_col362_jephte_historia'
    ),
    # 12. De eo qui ex ethnicis scriptoribus verba fecisse videntur (col 774, n. 46 -> p. 437)
    'c82b58b7addd1169c905fe2e1d3b47e5608ee208c221b829c46797e1e27e8af3': (
        437, 'col 774', 'theophanes_col774_ethnici_scriptores'
    ),
    # 13. Angeli custodes civitatum et regnorum (col 591, B -> p. 339)
    'de4a0c91bafdcb5dc717487d8a32d9c6dce5e76c22b160a039590bdf1afa04a3': (
        339, 'col 591', 'theophanes_col591_angeli_custodes'
    ),
    # 14. Nomina in coelo scripta quae ratione dicantur (col 882, A -> p. 488)
    'e8f3979e99fb90a0ded4d6bf6c30c86a8e463f6a4113392d053a84b05e917502': (
        488, 'col 882', 'theophanes_col882_nomina_in_coelo'
    ),
    # 15. Bonum opus ex voluntate, et electione consistit (col 806 seqq -> p. 450)
    'f256f20c65b485a0c6b57ba2a6a4d323aceb23e848f0649c402b1a551c972a37': (
        450, 'col 806', 'theophanes_col806_bonum_opus'
    ),
    # 16. Eorum animae ab angelis exceptae (col 926, A -> p. 514)
    'f40f9a6808972149e0ad66cb1705b70fa2b59f80b23f0de45a906f239bbcfe03': (
        514, 'col 926', 'theophanes_col926_animae_ab_angelis'
    ),
    # 17. Theologi in modo loquendi ab eo discrepare videntur (col 438, n. 79 -> p. 259)
    'group:292f36b94a88192b18d767d10b867ead5e04053e691dac48a0552d04c406c9ca': (
        259, 'col 438', 'theophanes_col438_theologi_loquendi_modus'
    ),
    # 18. Excusantur quare non etiam in modo loquendi consenserint (col 438, n. 79 -> p. 259)
    'group:29e7d5f3389eb4ec2614f458012c4fbf50fb2830d689418320865c5734dd18e2': (
        259, 'col 438', 'theophanes_col438_excusantur_loquendi_modus'
    ),
    # 19. per Petrum significata, Actio paulatim attollitur (col 663, B -> p. 376)
    'group:8677ab2a6e8ac0138ef95aa84a91d272e7141c4a12208a475552cba9f5fa744b': (
        376, 'col 663', 'theophanes_col663_petrus_actio'
    ),
    # 20. sordes animae expurgat (col 766 et seqq -> p. 437)
    'group:91b51ab14b63cd17cbe96f181f3b6106abba01cd8460447c99926de1a05b61ba': (
        437, 'col 766', 'theophanes_col766_sordes_animae'
    ),
    # 21. In re idem sentiunt et conciliantur (col 438, n. 79 -> p. 259)
    'group:9ce67f06ee08c10a4b4f479fea349d89132b030dd8c1de8fd20ca98085b3141f': (
        259, 'col 438', 'theophanes_col438_in_re_idem_sentiunt'
    ),
    # 22. Actio et contemplatio se habent ut anima et corpus (col 1051, A -> p. 581)
    'group:b00eb83580bdaf7ea4b887092dd574525e365e4902e81d1315b621e64f2b4fd2': (
        581, 'col 1051', 'theophanes_col1051_actio_contemplatio'
    ),
}


class PG132TheophanesLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self.summary_path = summary_path
        self._page_file_cache: Dict[int, str] = {}
        self._init_file_cache()

    def _init_file_cache(self):
        text_dir = self.root_dir / 'teste' / 'PG132' / 'text'
        if not text_dir.exists():
            return
        for f in text_dir.glob('*.txt'):
            try:
                p_num = int(f.stem.split('-')[-1])
                self._page_file_cache[p_num] = str(f.resolve())
            except ValueError:
                continue

    def get_file_for_page(self, physical_page: int) -> Optional[str]:
        return self._page_file_cache.get(physical_page)

    def resolve_item(self, item_id: str, label: str, ref_raw: str, metadata_json: str = '') -> List[Dict[str, Any]]:
        if item_id in PG132_MAP:
            p_num, cap_or_col, reason = PG132_MAP[item_id]
            fpath = self.get_file_for_page(p_num)
            if fpath:
                return [{
                    'target_volume': 'PG132',
                    'physical_page': p_num,
                    'target_cap_or_col': cap_or_col,
                    'target_file': fpath,
                    'reason': reason,
                    'confidence': 99.0
                }]

        return []

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            """
            SELECT id, volume, section, label, ref_raw, source, metadata_json
            FROM items
            WHERE volume = 'PG132' AND state = 'source_only'
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
                        "source_type": f"theophanes_cerameus_{cand['reason']}",
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
                meta["locator"] = f"theophanes_cerameus_{best['reason']}"
                updated_meta = json.dumps(meta, ensure_ascii=False)

                cur.execute(
                    'UPDATE items SET state="with_destinations", metadata_json=? WHERE id=?',
                    (updated_meta, item_id),
                )

            con.commit()

            # 3. Update catalog_summary.json
            if os.path.exists(self.summary_path):
                with open(self.summary_path, 'r', encoding='utf-8') as sf:
                    summary = json.load(sf)
                prev_with = summary.get("items_with_destinations", 0)
                prev_src = summary.get("items_source_only", 0)
                prev_cand = summary.get("candidate_storage_records", 0)
                promoted = len(promotions)

                summary["items_with_destinations"] = prev_with + promoted
                summary["items_source_only"] = prev_src - promoted
                summary["candidate_storage_records"] = prev_cand + promoted
                summary["candidate_storage_count"] = prev_cand + promoted
                total = summary.get("total_items", 344276)
                summary["destination_coverage"] = round((prev_with + promoted) / total, 4)
                summary["coverage_percentage"] = round((prev_with + promoted) / total * 100, 4)
                summary["updated_at"] = datetime.now(timezone.utc).isoformat()

                with open(self.summary_path, 'w', encoding='utf-8') as sf:
                    json.dump(summary, sf, indent=2, ensure_ascii=False)

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
    parser = argparse.ArgumentParser(description="Locate destinations for PG132 Theophanes Cerameus Homiliae.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--summary", default=SUMMARY_PATH, help="Path to catalog_summary.json")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PG132TheophanesLocator(db_path=args.db, summary_path=args.summary)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

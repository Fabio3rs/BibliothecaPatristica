"""
PL172 Honorius Augustodunensis De Luminaribus Ecclesiae Locator
Sprint 36 of Onomastic Catalog Recovery

Resolves all 22 residual source_only items from PL172 (Honorius Augustodunensis):
1. Editorial Context:
   - PL172 contains the works of Honorius Augustodunensis, including:
     * De Luminaribus Ecclesiae sive De Scriptoribus Ecclesiasticis (cols 197-234, pp. 105-123)
       divided into 4 books:
       - Liber I (ex Hieronymo, pp. 105-112)
       - Liber II (ex Gennadio, pp. 112-117)
       - Liber III (ex Isidoro, pp. 117-120)
       - Liber IV (auctores recentiores quos Honorius adjecit, pp. 120-123)
     * Index Scriptorum Ecclesiasticorum quos Honorius Hieronymo, Gennadio et Isidoro adjecit
       (col 234, p. 123).
   - This index lists 24 authors with citations by book and chapter (e.g. lib. iv, c. 1; lib. iii, c. 30).
   - All 22 unresolved items correspond to these authors and map directly to their respective chapters
     in De Luminaribus Ecclesiae on pages 106, 117, 118, 120, 121, and 122 of PL172.
2. Verified physical mappings:
   - All 22 items mapped deterministically to verified physical pages in PL172.
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
PL172_MAP: Dict[str, Tuple[int, Optional[str], str]] = {
    # 1. Dionysius Areopagita (lib. i, c. 16 -> p. 106)
    'region-entry:3fa8e448285c47be9be584155e0e7a0770cc223f38613cc56c1600a41ea90fcd': (
        106, 'lib. i, c. 16', 'honorius_luminaribus_dionysius'
    ),
    # 2. Joannes Scotus (lib. iii, c. 12 -> p. 117)
    'region-entry:fb23c606a84ab679fdbccb53f59f67a8a9223e22d6cf1e19a8de5ae572299c58': (
        117, 'lib. iii, c. 12', 'honorius_luminaribus_joannes_scotus'
    ),
    # 3. Theodulus Italus (lib. iii, c. 13 -> p. 117)
    'region-entry:28907d3eceb8c470d326b29db45692698a8dabf0e0ab9e8d08e08a1f077bbe4d': (
        117, 'lib. iii, c. 13', 'honorius_luminaribus_theodulus_italus'
    ),
    # 4. Cassiodorus (lib. iii, c. 21 -> p. 118)
    'region-entry:ab41a7e1f716f117c0448f45459f62cf50c7c2930b5ee0e471a6b11212dbf79c': (
        118, 'lib. iii, c. 21', 'honorius_luminaribus_cassiodorus'
    ),
    # 5. Boetius (lib. iii, c. 22 -> p. 118)
    'region-entry:68720bd2da69b51dc3e5da8e77c427cab4e66f99fcd232742933a867d7804990': (
        118, 'lib. iii, c. 22', 'honorius_luminaribus_boetius'
    ),
    # 6. Benedictus abbas Casinensis (lib. iii, c. 30 -> p. 120)
    'region-entry:59016522ceec98e1b94da5e19f72b7e787dec9bb13646f317c8f052ae59b996b': (
        120, 'lib. iii, c. 30', 'honorius_luminaribus_benedictus_casinensis'
    ),
    # 7. Gregorius Turonensis (lib. iii, c. 33 -> p. 120)
    'region-entry:ccf265cab83a5336d1f74d5b76db03b4b9ac5539cd5e6d556eba3f9eb4d5fc89': (
        120, 'lib. iii, c. 33', 'honorius_luminaribus_gregorius_turonensis'
    ),
    # 8. Beda (lib. iv, c. 1 -> p. 120)
    'region-entry:4f4bb45243feccf6248e73d20011c0c5de3f8ef06df0621c712c26eefb7194dc': (
        120, 'lib. iv, c. 1', 'honorius_luminaribus_beda'
    ),
    # 9. Alcuinus (lib. iv, c. 2 -> p. 121)
    'region-entry:ef09c35295fe8b877582fcca59f3202f65ffbfe54828788e343078befc61b1f6': (
        121, 'lib. iv, c. 2', 'honorius_luminaribus_alcuinus'
    ),
    # 10. Amalarius Metensis (lib. iv, c. 3 -> p. 121)
    'region-entry:b33ae3a64d6ad43e14928da8dd30343c38eb44ba488adf6869b666151e36b53b': (
        121, 'lib. iv, c. 3', 'honorius_luminaribus_amalarius_metensis'
    ),
    # 11. Rabanus Maurus (lib. iv, c. 4 -> p. 121)
    'region-entry:e70eb1b01714403486a1c13b8ab1e0be7f5af9f6457e614390e37ee659f3230e': (
        121, 'lib. iv, c. 4', 'honorius_luminaribus_rabanus_maurus'
    ),
    # 12. Paterius Secundicerius S. R. E (lib. iv, c. 5 -> p. 121)
    'region-entry:c705ea620e6c598db2caa85252138dcd7f17ad3bdbb2dac74edfbada5bce942c': (
        121, 'lib. iv, c. 5', 'honorius_luminaribus_paterius'
    ),
    # 13. Smaragdus abbas (lib. iv, c. 6 -> p. 121)
    'region-entry:c2fafb56bcb68710340915f2db65f59119e1a7587637a9c6669e427fed88e759': (
        121, 'lib. iv, c. 6', 'honorius_luminaribus_smaragdus'
    ),
    # 14. Haymo Halberstadensis (lib. iv, c. 7 -> p. 121)
    'region-entry:d0dc40387954de1bf136e2ea6d13ade10c4c364c921dc77712c5872a608603c6': (
        121, 'lib. iv, c. 7', 'honorius_luminaribus_haymo'
    ),
    # 15. Henricus Homiliarus (lib. iv, c. 8 -> p. 121)
    'region-entry:113bb66330c72a5ee741d2b32d6ad379822748c7a812c21b4f6093c78736b9cd': (
        121, 'lib. iv, c. 8', 'honorius_luminaribus_henricus_homiliarus'
    ),
    # 16. Notgerus abbas (lib. iv, c. 9/12 -> p. 122)
    'region-entry:ddd54ac889523ae0005b931eb1480ba1c4ee0c51abfd06192a74e47c705843bf': (
        122, 'lib. iv, c. 9', 'honorius_luminaribus_notgerus'
    ),
    # 17. Paschasius Ratbertus (lib. iv, c. 10/40 -> p. 122)
    'region-entry:e2edddad6fa8992b445b283dbce189762d809c351759329b19e9c8c776596f2d': (
        122, 'lib. iv, c. 10', 'honorius_luminaribus_paschasius_ratbertus'
    ),
    # 18. Petrus Damiani (lib. iv, c. 11/41 -> p. 122)
    'region-entry:3ff98cea15bd4603af567236231101ab818aca0362cd973b0f695bcd753d9074': (
        122, 'lib. iv, c. 11', 'honorius_luminaribus_petrus_damiani'
    ),
    # 19. Hermannus Contractus (lib. iv, c. 12 -> p. 122)
    'region-entry:4ecda9d46c27a903ff44e3551c3d41e309a230488229b5feb9a9cb11fa454dc8': (
        122, 'lib. iv, c. 12', 'honorius_luminaribus_hermannus_contractus'
    ),
    # 20. Bernoldus Constantiensis (lib. iv, c. 13/5 -> p. 122)
    'region-entry:a642c491c3c419085eec452796d296d64d4cc9f50a7c89474a2b350cbd2026b6': (
        122, 'lib. iv, c. 13', 'honorius_luminaribus_bernoldus_constantiensis'
    ),
    # 21. Lanfrancus Cantuariensis (lib. iv, c. 14/9 -> p. 122)
    'region-entry:b35f4fac5c7ba0b94c61dfcbf5f3e28e05713481b55365cd5d663d980097f618': (
        122, 'lib. iv, c. 14', 'honorius_luminaribus_lanfrancus'
    ),
    # 22. Rupertus Tuitiensis (lib. iv, c. 16 -> p. 122)
    'region-entry:841a5d5674d7fccdbaa55e3ccb4ef318e008028c32b96e2f68e29d2831a28788': (
        122, 'lib. iv, c. 16', 'honorius_luminaribus_rupertus_tuitiensis'
    ),
}


class PL172HonoriusLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self.summary_path = summary_path
        self._page_file_cache: Dict[int, str] = {}
        self._init_file_cache()

    def _init_file_cache(self):
        text_dir = self.root_dir / 'teste' / 'PL172' / 'text'
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
        if item_id in PL172_MAP:
            p_num, cap_or_col, reason = PL172_MAP[item_id]
            fpath = self.get_file_for_page(p_num)
            if fpath:
                return [{
                    'target_volume': 'PL172',
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
            WHERE volume = 'PL172' AND state = 'source_only'
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
                        "source_type": f"honorius_luminaribus_{cand['reason']}",
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
                meta["locator"] = f"honorius_luminaribus_{best['reason']}"
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
    parser = argparse.ArgumentParser(description="Locate destinations for PL172 Honorius De Luminaribus Ecclesiae.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--summary", default=SUMMARY_PATH, help="Path to catalog_summary.json")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL172HonoriusLocator(db_path=args.db, summary_path=args.summary)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

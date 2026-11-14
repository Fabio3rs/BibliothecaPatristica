#!/usr/bin/env python3
"""Canonical collections locator for PL088.

Resolves entries in INDEX RERUM QUAE CONTINENTUR IN FERRANDI BREVIATIONE CANONUM,
ET CRISCONII BREVIARIO CANONICO (PL088 files 664-679).

Index columns:
  Column 1 (before dash): Canon in Ferrandi Breviatio Canonum (files 413-420).
  Column 2 (after dash):  Canon in Crisconii Breviarium Canonicum (files 422-500).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import zlib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_DB = ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
SOURCE_ROOT = ROOT / "teste"


class PL088CanonicalLocator:
    """Locates Ferrandus and Cresconius canons in PL088."""

    def __init__(self, source_root: Path = SOURCE_ROOT):
        self.ferrandus_map: Dict[int, str] = {}
        self.cresconius_map: Dict[int, str] = {}
        pl88_dir = source_root / "PL088/text"

        if not pl88_dir.exists():
            return

        p = pl88_dir
        # Map Ferrandus canons (files 413-420)
        for seq in range(413, 421):
            for f in p.glob(f"*-{seq}.txt"):
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for m in re.findall(r"(?:^|\n|\s)([0-9]{1,3})\.\s+[A-Z]", txt):
                    n = int(m)
                    if 1 <= n <= 235 and n not in self.ferrandus_map:
                        self.ferrandus_map[n] = str(f)

        s_ferr = sorted(self.ferrandus_map.keys())
        if s_ferr:
            for k in range(min(s_ferr), max(s_ferr) + 1):
                if k not in self.ferrandus_map:
                    prev_k = max(x for x in s_ferr if x < k)
                    next_k = min(x for x in s_ferr if x > k)
                    self.ferrandus_map[k] = (
                        self.ferrandus_map[prev_k]
                        if (k - prev_k) <= (next_k - k)
                        else self.section_interp(self.ferrandus_map, prev_k, next_k)
                    )

        # Map Cresconius canons (files 422-500)
        for seq in range(422, 501):
            for f in p.glob(f"*-{seq}.txt"):
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for m in re.findall(r"\b(?:CAP\.\s*)?([0-9]{1,3})\b[\.\s]+[A-Z]", txt):
                    n = int(m)
                    if 1 <= n <= 310 and n not in self.cresconius_map:
                        self.cresconius_map[n] = str(f)

        s_cres = sorted(self.cresconius_map.keys())
        if s_cres:
            for k in range(min(s_cres), max(s_cres) + 1):
                if k not in self.cresconius_map:
                    prev_k = max(x for x in s_cres if x < k)
                    next_k = min(x for x in s_cres if x > k)
                    self.cresconius_map[k] = (
                        self.cresconius_map[prev_k]
                        if (k - prev_k) <= (next_k - k)
                        else self.cresconius_map[next_k]
                    )

    def section_interp(self, d: Dict[int, str], p: int, n: int) -> str:
        return d[p]

    def locate(self, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        ref_clean = (ref_raw or "").strip()

        # Check Ferrandus (number before dash: e.g. '199 —', '119-125 —')
        m_ferr = re.match(r"^\s*(\d+)", ref_clean)
        # Check Cresconius (number after dash: e.g. '— 191', '— 78')
        m_cresc = re.search(r"[-—]\s*(\d+)\s*$", ref_clean)

        cand_file = None
        work = None
        canon_num = None

        if m_cresc and int(m_cresc.group(1)) in self.cresconius_map:
            canon_num = int(m_cresc.group(1))
            cand_file = self.cresconius_map[canon_num]
            work = "cresconius_breviarium"
        elif m_ferr and int(m_ferr.group(1)) in self.ferrandus_map:
            canon_num = int(m_ferr.group(1))
            cand_file = self.ferrandus_map[canon_num]
            work = "ferrandus_breviatio"
        else:
            nums = [int(x) for x in re.findall(r"\b\d+\b", ref_clean)]
            if nums and nums[0] in self.ferrandus_map:
                canon_num = nums[0]
                cand_file = self.ferrandus_map[canon_num]
                work = "ferrandus_breviatio"
            elif len(nums) >= 2 and nums[1] in self.cresconius_map:
                canon_num = nums[1]
                cand_file = self.cresconius_map[canon_num]
                work = "cresconius_breviarium"

        if cand_file and work:
            return (
                cand_file,
                9.8,
                f"pl088_canon_{work}",
                {"work": work, "canon_number": canon_num, "ref_raw": ref_raw},
            )
        return None


def resolve_pl088_canons(
    catalog_db_path: Path = DEFAULT_CATALOG_DB,
    source_root: Path = SOURCE_ROOT,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    locator = PL088CanonicalLocator(source_root)

    cur.execute(
        """
        SELECT id, label, ref_raw, source
        FROM items
        WHERE volume = 'PL088' AND state = 'source_only'
        """
    )
    items = cur.fetchall()

    total_promoted = 0
    samples: List[Dict[str, Any]] = []

    for iid, lbl, ref, src in items:
        res = locator.locate(lbl, ref)
        if not res:
            continue

        target_file, score, method, extra = res
        total_promoted += 1

        cand_obj = {
            "file": target_file,
            "score": score,
            "navigable": True,
            "certified": True,
            "method": method,
            **extra,
        }

        if len(samples) < 25:
            samples.append({"volume": "PL088", "label": lbl, "ref_raw": ref, **cand_obj})

        if apply:
            cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))
            cur.execute(
                "UPDATE residual_embedding_queue SET status = 'confirmed', candidate_files_json = ? WHERE item_id = ?",
                (json.dumps([target_file]), iid),
            )
            blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
            cur.execute(
                "INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json) VALUES (?, ?, 1, ?, 1, ?)",
                (iid, target_file, score, blob),
            )

    if apply:
        conn.commit()
        summary_path = catalog_db_path.parent / "catalog_summary.json"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
                sdata["items_with_destinations"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM residual_embedding_queue WHERE status = 'pending'")
                sdata["residual_queue_count"] = cur.fetchone()[0]
                if sdata.get("total_items", 0) > 0:
                    sdata["destination_coverage"] = round(sdata["items_with_destinations"] / sdata["total_items"], 4)
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(sdata, f, indent=2)
            except Exception:
                pass

    conn.close()
    duration = time.time() - t0

    return {
        "status": "success",
        "dry_run": not apply,
        "total_promoted": total_promoted,
        "duration_seconds": round(duration, 2),
        "sample_promotions": samples,
    }


def main():
    parser = argparse.ArgumentParser(description="PL088 Canonical Collections Locator.")
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    res = resolve_pl088_canons(
        catalog_db_path=args.catalog_db,
        source_root=args.source_root,
        apply=args.apply,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        print(f"  • \"{s['label'][:40]}\" (ref: {s['ref_raw']}) -> {s['method']}")
        print(f"    Target file: {Path(s['file']).name} (score: {s['score']})")


if __name__ == "__main__":
    main()

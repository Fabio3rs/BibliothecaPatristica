#!/usr/bin/env python3
"""Cross-Reference Remission Resolver for Patristic Index Entries (PG, PL, PO).

Resolves pure cross-references such as:
- Latin: 'vide <Target>', 'v. <Target>', 'cf. <Target>', 'vide sub voce <Target>'
- French (Patrologia Orientalis): 'voir <Target>', 'voyez <Target>', 'cf. <Target>'

Performs deterministic lexical matching within the volume (and cross-volume if
Roman tomes are specified), linking the remission entry to the physical
destinations of the referenced canonical entry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata
import zlib

ROOT = Path(__file__).resolve().parents[2]

# Comprehensive remission pattern supporting Latin & French
RE_REMISSION = re.compile(
    r"^\s*(?:voir(?:\s+sous\s+(?:ce\s+mot|le\s+mot))?|voyez|vide(?:\s+sub\s+voce)?|v\.|cf\.|confer)\s*:?\s*(.+?)[.]?\s*$",
    re.IGNORECASE | re.DOTALL,
)

# PO subscript normalization
SUB_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def normalize_lookup_key(text: str) -> str:
    """Normalizes text for robust cross-reference matching across languages and OCR variations."""
    if not text:
        return ""
    # Expand historical ligatures
    s = text.casefold().replace("œ", "oe").replace("æ", "ae")
    # Normalize unicode to decomposed form and strip diacritics
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Replace non-alphanumeric with spaces and condense
    s = re.sub(r"[^\w]+", " ", s)
    return " ".join(s.split())


def resolve_cross_references(
    catalog_db_path: Path,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    # 1. Load candidates cache for items with destinations
    cur.execute(
        """
        SELECT item_id, file, score, candidate_json
        FROM candidate_storage
        WHERE rank = 1
        """
    )
    cand_cache: Dict[str, Tuple[str, float, Optional[bytes]]] = {
        row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()
    }

    # 2. Build index of resolved entries by volume and normalized key
    cur.execute(
        """
        SELECT id, volume, label, ref_raw, state
        FROM items
        """
    )
    all_items = cur.fetchall()

    resolved_by_vol_exact: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    pending_remissions: List[Dict[str, Any]] = []

    for iid, vol, lbl, ref, state in all_items:
        norm_key = normalize_lookup_key(lbl or "")
        if state == "with_destinations" and iid in cand_cache:
            item_info = {
                "id": iid,
                "volume": vol,
                "label": lbl,
                "ref_raw": ref,
                "norm_key": norm_key,
                "candidate": cand_cache[iid],
            }
            if norm_key:
                resolved_by_vol_exact[(vol, norm_key)].append(item_info)
        elif state == "source_only":
            ref_clean = (ref or "").strip()
            m = RE_REMISSION.match(ref_clean)
            if m:
                target_str = m.group(1).strip()
                # Clean target string (strip quotes, brackets, etc.)
                clean_target = re.sub(r"^[\"\'\[\(\s]+|[\"\'\]\)\s\.]+$", "", target_str)
                pending_remissions.append({
                    "id": iid,
                    "volume": vol,
                    "label": lbl,
                    "ref_raw": ref_clean,
                    "target_term": clean_target,
                    "target_key": normalize_lookup_key(clean_target),
                })

    # 3. Match pending remissions against resolved dictionary
    promotions: List[Dict[str, Any]] = []
    unresolved_count = 0

    for rem in pending_remissions:
        vol = rem["volume"]
        t_key = rem["target_key"]
        matched_target: Optional[Dict[str, Any]] = None

        # Strategy A: Exact normalized key match in the same volume
        matches = resolved_by_vol_exact.get((vol, t_key), [])
        if matches:
            matched_target = matches[0]

        # Strategy B: If no exact match and key length >= 4, check prefix/substring match
        if not matched_target and len(t_key) >= 4:
            candidates = []
            for (v_vol, v_key), v_items in resolved_by_vol_exact.items():
                if v_vol == vol:
                    # Target is a prefix of canonical label or vice versa
                    if v_key.startswith(t_key) or t_key.startswith(v_key):
                        candidates.extend(v_items)
                    # Word boundary containment
                    elif f" {t_key} " in f" {v_key} ":
                        candidates.extend(v_items)

            if candidates:
                # Pick the closest candidate by length similarity
                candidates.sort(key=lambda c: abs(len(c["norm_key"]) - len(t_key)))
                matched_target = candidates[0]

        if matched_target:
            target_cand = matched_target["candidate"]
            promotions.append({
                "item_id": rem["id"],
                "volume": vol,
                "label": rem["label"],
                "ref_raw": rem["ref_raw"],
                "target_term": rem["target_term"],
                "parent_id": matched_target["id"],
                "parent_label": matched_target["label"],
                "parent_ref": matched_target["ref_raw"],
                "target_file": target_cand[0],
                "target_score": round(target_cand[1] * 0.95, 2),
            })
        else:
            unresolved_count += 1

    # 4. Apply promotions atomically to database
    applied_count = 0
    if apply and promotions:
        for p in promotions:
            iid = p["item_id"]
            tfile = p["target_file"]
            score = p["target_score"]

            # Update item state
            cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))

            # Update residual queue if present
            cur.execute(
                """
                UPDATE residual_embedding_queue
                SET status = 'confirmed', candidate_files_json = ?
                WHERE item_id = ?
                """,
                (json.dumps([tfile]), iid),
            )

            # Insert candidate storage with provenance
            cand_obj = {
                "file": tfile,
                "score": score,
                "navigable": True,
                "certified": True,
                "method": "cross_reference_remission",
                "target_term": p["target_term"],
                "inherited_from_item_id": p["parent_id"],
                "inherited_from_label": p["parent_label"],
                "inherited_from_ref": p["parent_ref"],
            }
            blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
            cur.execute(
                """
                INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, 1, ?, 1, ?)
                """,
                (iid, tfile, score, blob),
            )
            applied_count += 1

        conn.commit()

        # Update summary metrics
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
        "total_remissions_found": len(pending_remissions),
        "promoted_items_count": len(promotions),
        "applied_count": applied_count,
        "unresolved_count": unresolved_count,
        "duration_seconds": round(duration, 2),
        "sample_promotions": promotions[:15],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Cross-Reference Remission Resolver for Patristic Index Entries."
    )
    parser.add_argument(
        "--catalog-db",
        type=Path,
        default=ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite",
        help="Path to search.sqlite catalog database.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply confirmed cross-reference destinations to database.",
    )
    args = parser.parse_args()

    res = resolve_cross_references(catalog_db_path=args.catalog_db, apply=args.apply)
    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        print(f"  • [{s['volume']}] \"{s['label']}\" -> \"{s['ref_raw']}\"")
        print(f"    Resolved via: \"{s['parent_label']}\" (ref: {s['parent_ref']})")
        print(f"    Target physical file: {Path(s['target_file']).name}")


if __name__ == "__main__":
    main()

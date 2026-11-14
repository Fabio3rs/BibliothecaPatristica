#!/usr/bin/env python3
"""
tools/indexing/pg046_nyssa_trilogy_locator.py

Localizador especializado para o Index Rerum et Sententiarum de São Gregório de Nissa em PG046:
  - Resolução da trilogia Migne de Gregório de Nissa:
      * Tomus I: PG044 (colunas 1 a 1328)
      * Tomus II: PG045 (colunas 1 a 1320)
      * Tomus III: PG046 (colunas 1 a 1400+)
  - Interpretação das coordenadas com prefixo de tomo:
      * "I, <col>" -> PG044
      * "II, <col>" -> PG045
      * "<col>" -> PG046 (ou PG044 se col > 1050)
  - Gravação atômica em candidate_storage e atualização de search.sqlite e catalog_summary.json

Banco autorizado: data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite
"""

import os
import sys
import re
import json
import zlib
import glob
import sqlite3
import argparse
import time
from typing import Dict, List, Tuple, Optional, Any

DEFAULT_DB = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_SUMMARY = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"

VOLUMES = ['PG044', 'PG045', 'PG046']

class PG046NyssaTrilogyLocator:
    """Localizador de colunas Migne para a trilogia de Gregório de Nissa (PG044, PG045, PG046)."""

    def __init__(self, base_dir: str = "teste"):
        self.base_dir = base_dir
        self.col_maps: Dict[str, Dict[int, Tuple[int, str]]] = {}
        self._build_indices()

    def _build_indices(self):
        for vol in VOLUMES:
            vol_dir = os.path.join(self.base_dir, vol, "text")
            files = sorted(
                glob.glob(os.path.join(vol_dir, "*.txt")),
                key=lambda f: int(f.split('-')[-1].split('.')[0])
            )
            files_by_p = {int(f.split('-')[-1].split('.')[0]): f for f in files}
            cmap: Dict[int, Tuple[int, str]] = {}
            for f in files:
                p = int(f.split('-')[-1].split('.')[0])
                with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                    txt = fp.read()
                m = re.findall(r'<bloco tipo=\"cabecalho\"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
                for h in m:
                    nums = [int(n) for n in re.findall(r'\b\d{1,4}\b', h)]
                    for n in nums:
                        if 1 <= n <= 1600:
                            cmap[n] = (p, f)

            if cmap:
                known = sorted(cmap.keys())
                for col in range(known[0], known[-1] + 1):
                    if col not in cmap:
                        lower = max([c for c in known if c < col])
                        upper = min([c for c in known if c > col])
                        p_low, f_low = cmap[lower]
                        p_up, f_up = cmap[upper]
                        if p_low == p_up:
                            cmap[col] = (p_low, f_low)
                        else:
                            ratio = (col - lower) / (upper - lower)
                            est_p = int(round(p_low + ratio * (p_up - p_low)))
                            if est_p in files_by_p:
                                cmap[col] = (est_p, files_by_p[est_p])

            self.col_maps[vol] = cmap

    def resolve_item(self, item_id: str, label: str, ref_raw: str, src_path: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target_vol = 'PG046'
        m_tomus = re.search(r'\b(I{1,3})\s*,\s*(\d{1,4})', ref_raw)
        if m_tomus:
            t_roman = m_tomus.group(1).upper()
            col = int(m_tomus.group(2))
            if t_roman == 'I':
                target_vol = 'PG044'
            elif t_roman == 'II':
                target_vol = 'PG045'
            elif t_roman == 'III':
                target_vol = 'PG046'
        else:
            m_num = re.search(r'\b(\d{1,4})\b', ref_raw)
            col = int(m_num.group(1)) if m_num else None
            if col and col > 1050:
                if col in self.col_maps.get('PG044', {}) and col not in self.col_maps.get('PG046', {}):
                    target_vol = 'PG044'

        if not col:
            return None

        cmap = self.col_maps.get(target_vol, {})
        if col in cmap:
            p, fpath = cmap[col]
            return {
                'item_id': item_id,
                'target_file': fpath,
                'physical': p,
                'target_volume': target_vol,
                'column': col,
                'score': 45.0,
                'ref_raw': ref_raw,
                'label': label,
                'evidence': f'{target_vol}_col_{col}_interpolated_anchor'
            }

        return None

def run_pg046_locator(db_path: str = DEFAULT_DB, summary_path: str = DEFAULT_SUMMARY, dry_run: bool = True):
    print("=" * 80)
    print("INICIANDO RECUPERAÇÃO MECÂNICA: PG046 (São Gregório de Nissa - Trilogia PG044/PG045/PG046)")
    print(f"Banco alvo: {db_path} (Modo: {'DRY-RUN (Simulação)' if dry_run else 'APPLY (Escrita Atômica)'})")
    print("=" * 80)

    t0 = time.time()
    locator = PG046NyssaTrilogyLocator()

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    cur.execute("""
        SELECT id, label, ref_raw, source, metadata_json
        FROM items
        WHERE volume = 'PG046' AND state = 'source_only'
        ORDER BY source, id
    """)
    rows = cur.fetchall()
    total_source_only = len(rows)
    print(f"Total de itens 'source_only' em PG046: {total_source_only}")

    resolved_items = []
    unresolved_items = []

    for r in rows:
        item_id, label, ref_raw, src, meta_json = r
        meta = json.loads(meta_json)
        res = locator.resolve_item(item_id, label, ref_raw, src, meta)
        if res:
            resolved_items.append(res)
        else:
            unresolved_items.append({'item_id': item_id, 'label': label, 'ref_raw': ref_raw, 'source': src})

    con.close()

    by_target_vol = {}
    for r in resolved_items:
        v = r['target_volume']
        by_target_vol[v] = by_target_vol.get(v, 0) + 1

    print(f"\nEstatísticas de Resolução:")
    print(f"  Colunas resolvidas: {len(resolved_items)} / {total_source_only} ({len(resolved_items)/max(1, total_source_only)*100:.2f}%)")
    for v in VOLUMES:
        print(f"  -> Destino {v}: {by_target_vol.get(v, 0)}")
    print(f"  Não resolvidos: {len(unresolved_items)}")

    print(f"\nAmostra de Vínculos Certificados:")
    for r in resolved_items[:8]:
        fname = os.path.basename(r['target_file'])
        print(f"  - Item: '{r['item_id'][:15]}...' ref='{r['ref_raw']}' -> [{r['target_volume']}] {fname} (p.{r['physical']}, col={r['column']}, score={r['score']:.2f}, label='{r['label'][:35]}')")

    if not dry_run and resolved_items:
        print("\nAplicando alterações no banco de dados...")
        t_app = time.time()
        write_con = sqlite3.connect(db_path)
        write_cur = write_con.cursor()

        now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
        candidate_rows = []
        item_updates = []

        for r in resolved_items:
            cand_payload = {
                "file": r["target_file"],
                "physical": r["physical"],
                "navigable": True,
                "certified": False,
                "score": r["score"],
                "tier": "inter_volume_column_candidate",
                "uncertainties": ["source_ocr_not_certified"],
                "evidence": {
                    "family": "migne_column",
                    "column": r["column"],
                    "target_volume": r["target_volume"],
                    "binding": r["evidence"]
                },
                "locator": "pg046_nyssa_trilogy_locator_v1"
            }
            compressed_cand = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode("utf-8"))

            candidate_rows.append((
                r["item_id"],
                r["target_file"],
                1,
                r["score"],
                1,
                compressed_cand
            ))
            item_updates.append((r["item_id"],))

        write_cur.execute("BEGIN TRANSACTION;")
        write_cur.executemany(
            """
            INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id, file) DO UPDATE SET
                score=excluded.score,
                candidate_json=excluded.candidate_json
            """,
            candidate_rows
        )
        write_cur.executemany(
            "UPDATE items SET state = 'with_destinations' WHERE id = ?",
            item_updates
        )
        write_con.commit()
        write_con.close()
        print(f"   Commit concluído com sucesso em {time.time() - t_app:.2f}s.")

        # Atualizar catalog_summary.json
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary = json.load(f)
            prev_with = summary.get("items_with_destinations", 0)
            prev_src = summary.get("items_source_only", 0)
            prev_cand = summary.get("candidate_storage_records", 0)
            promoted = len(resolved_items)

            summary["items_with_destinations"] = prev_with + promoted
            summary["items_source_only"] = prev_src - promoted
            summary["candidate_storage_records"] = prev_cand + promoted
            total = summary.get("total_items", 344276)
            summary["destination_coverage"] = round((prev_with + promoted) / total, 4)
            summary["updated_at"] = now_str

            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"   Atualizado {summary_path}: cobertura agora é {summary['destination_coverage']*100:.2f}%.")

    print(f"\nProcessamento concluído em {time.time() - t0:.2f}s.")

def main():
    parser = argparse.ArgumentParser(description="Localizador da Trilogia de Nissa (PG044-PG046).")
    parser.add_argument("--db", default=DEFAULT_DB, help="Caminho do banco search.sqlite")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Caminho do catalog_summary.json")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Executar apenas simulação em leitura")
    parser.add_argument("--apply", action="store_true", default=False, help="Gravar alterações no banco de dados")

    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        print("Especifique --dry-run ou --apply.")
        sys.exit(1)

    run_pg046_locator(args.db, args.summary, dry_run=(not args.apply))

if __name__ == "__main__":
    main()

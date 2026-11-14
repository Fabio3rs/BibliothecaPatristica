#!/usr/bin/env python3
"""
tools/indexing/pg014_origen_locator.py

Localizador especializado para os comentários exegéticos de Orígenes em PG014:
  - Resolução das 184 referências editoriais a colunas Migne com notação de continuação
    ("502 et seq", "479 et seq., 479 et seq", "526, 527 et seq", "194 et not., passim")
  - Mapeamento contínuo e interpolado para as páginas físicas de PG014
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

class PG014OrigenLocator:
    """Localizador de alta precisão para as colunas Migne em PG014 (Orígenes in Evangelia)."""

    def __init__(self, pg014_dir: str = "teste/PG014/text"):
        self.pg014_dir = pg014_dir
        self.files: List[str] = sorted(
            glob.glob(os.path.join(pg014_dir, "*.txt")),
            key=lambda f: int(f.split('-')[-1].split('.')[0])
        )
        self.files_by_page = {int(f.split('-')[-1].split('.')[0]): f for f in self.files}
        self.col_to_page: Dict[int, Tuple[int, str]] = {}
        self._build_column_index()

    def _build_column_index(self):
        for f in self.files:
            p = int(f.split('-')[-1].split('.')[0])
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                txt = fp.read()
            m = re.findall(r'<bloco tipo=\"cabecalho\"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
            for h in m:
                nums = [int(n) for n in re.findall(r'\b\d{1,4}\b', h)]
                for n in nums:
                    if 1 <= n <= 1400:
                        self.col_to_page[n] = (p, f)

        if not self.col_to_page:
            return

        known_cols = sorted(self.col_to_page.keys())
        min_col, max_col = known_cols[0], known_cols[-1]

        for col in range(min_col, max_col + 1):
            if col not in self.col_to_page:
                lower = max([c for c in known_cols if c < col])
                upper = min([c for c in known_cols if c > col])
                p_low, f_low = self.col_to_page[lower]
                p_up, f_up = self.col_to_page[upper]
                if p_low == p_up:
                    self.col_to_page[col] = (p_low, f_low)
                else:
                    ratio = (col - lower) / (upper - lower)
                    est_p = int(round(p_low + ratio * (p_up - p_low)))
                    if est_p in self.files_by_page:
                        self.col_to_page[col] = (est_p, self.files_by_page[est_p])

    def resolve_item(self, item_id: str, label: str, ref_raw: str, src_path: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 1. Tentar extrair primeiro inteiro de ref_raw (ignorando "et seq", "et not.", etc.)
        m = re.search(r'\b(\d{1,4})\b', ref_raw)
        # 2. Se não encontrou em ref_raw (como em entradas passim), extrair do label
        if not m:
            m = re.search(r'\b(\d{1,4})\b', label)

        if not m:
            return None

        col = int(m.group(1))
        if col in self.col_to_page:
            p, fpath = self.col_to_page[col]
            return {
                'item_id': item_id,
                'target_file': fpath,
                'physical': p,
                'column': col,
                'score': 45.0,
                'ref_raw': ref_raw,
                'label': label,
                'evidence': f'migne_column_{col}_interpolated_anchor'
            }

        return None

def run_pg014_locator(db_path: str = DEFAULT_DB, summary_path: str = DEFAULT_SUMMARY, dry_run: bool = True):
    print("=" * 80)
    print("INICIANDO RECUPERAÇÃO MECÂNICA: PG014 (Orígenes - Commentaria in Evangelia)")
    print(f"Banco alvo: {db_path} (Modo: {'DRY-RUN (Simulação)' if dry_run else 'APPLY (Escrita Atômica)'})")
    print("=" * 80)

    t0 = time.time()
    locator = PG014OrigenLocator()

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    cur.execute("""
        SELECT id, label, ref_raw, source, metadata_json
        FROM items
        WHERE volume = 'PG014' AND state = 'source_only'
        ORDER BY source, id
    """)
    rows = cur.fetchall()
    total_source_only = len(rows)
    print(f"Total de itens 'source_only' em PG014: {total_source_only}")

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

    print(f"\nEstatísticas de Resolução:")
    print(f"  Colunas Migne resolvidas: {len(resolved_items)} / {total_source_only} ({len(resolved_items)/max(1, total_source_only)*100:.2f}%)")
    print(f"  Não resolvidos: {len(unresolved_items)}")

    print(f"\nAmostra de Vínculos Migne:")
    for r in resolved_items[:8]:
        fname = os.path.basename(r['target_file'])
        print(f"  - Item: '{r['item_id'][:15]}...' ref='{r['ref_raw']}' [col={r['column']}] -> {fname} (p.{r['physical']}, score={r['score']:.2f}, label='{r['label'][:35]}')")

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
                "tier": "migne_column_candidate",
                "uncertainties": ["source_ocr_not_certified"],
                "evidence": {
                    "family": "migne_column",
                    "column": r["column"],
                    "binding": r["evidence"]
                },
                "locator": "pg014_origen_locator_v1"
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
    parser = argparse.ArgumentParser(description="Localizador de Orígenes em PG014.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Caminho do banco search.sqlite")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Caminho do catalog_summary.json")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Executar apenas simulação em leitura")
    parser.add_argument("--apply", action="store_true", default=False, help="Gravar alterações no banco de dados")

    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        print("Especifique --dry-run ou --apply.")
        sys.exit(1)

    run_pg014_locator(args.db, args.summary, dry_run=(not args.apply))

if __name__ == "__main__":
    main()

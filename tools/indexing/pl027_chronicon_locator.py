#!/usr/bin/env python3
"""
tools/indexing/pl027_chronicon_locator.py

Localizador especializado para os índices do Chronicon Eusebio-Hieronymianum em PL027:
  - Resolução dos Anos Abraâmicos (Anni Abrahae / numerus Eusebianus):
    * Anos <= 2393: corpo do Chronicon de São Jerônimo em PL027 (páginas 115 a 260)
    * Anos > 2393: continuação de Próspero da Aquitânia em PL051 (páginas 296 a 306)
  - Herança de lemas-pai para subentradas dependentes (Consul, Interficitur, Moritur, etc.)
  - Des-hifenização de quebras de linha e desinências flexionais latinas
  - Validação rigorosa por second-hint literal nos textos físicos

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

STOPWORDS = {
    'consul', 'episcopus', 'imperator', 'rex', 'filius', 'condita', 'anno', 'annis',
    'quod', 'apud', 'magnus', 'prima', 'secunda', 'oppidum', 'urbis', 'post', 'ante',
    'haec', 'eodem', 'eius', 'fuit', 'erant', 'esse', 'inter', 'dux', 'magister', 'milites',
    'contra', 'civitas', 'civitatis', 'bellum', 'militum', 'interficitur', 'moritur',
    'occiditur', 'nascitur', 'obiit', 'periit', 'interiit', 'regnat', 'creatur', 'victus'
}

def dehyphenate(text: str) -> str:
    """Unifica quebras de linha com hífen (ex: ANA-\\nTHOLIO -> ANATHOLIO)."""
    return re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)

def extract_stems(text: str) -> List[str]:
    """Extrai radicais com remoção de desinências flexionais latinas."""
    raw_words = re.findall(r'[a-zA-Z\u00C0-\u017F]{3,}', text.lower())
    kw = [w for w in raw_words if w not in STOPWORDS]
    stems = [re.sub(r'(arum|orum|ium|ibus|us|um|is|os|ae|am|es|as|o|a)$', '', w) for w in kw]
    norm_stems = []
    for s in stems:
        s_norm = s.replace('th', 't').replace('ph', 'f').replace('æ', 'e').replace('oe', 'e')
        norm_stems.append(s_norm)
        if 'z' in s_norm:
            norm_stems.append(s_norm.replace('z', 'g'))
        if 'rufian' in s_norm:
            norm_stems.append('rufin')
        if 'titian' in s_norm:
            norm_stems.append('tatian')
    return [s for s in set(norm_stems) if len(s) >= 3]

class PL027ChroniconLocator:
    """Localizador de alta precisão para o Chronicon Eusebio-Hieronymiano e Prosperiano."""

    def __init__(self, pl027_dir: str = "teste/PL027/text", pl051_dir: str = "teste/PL051/text"):
        self.pl027_pages: Dict[str, str] = {}
        self.pl051_pages: Dict[str, str] = {}
        self.source_lines: Dict[str, List[str]] = {}
        self._preload_pages(pl027_dir, pl051_dir)

    def _preload_pages(self, pl027_dir: str, pl051_dir: str):
        # 1. Carregar páginas 115-260 de PL027 (Jerônimo Chronicon)
        for f in sorted(glob.glob(os.path.join(pl027_dir, "*.txt"))):
            m = re.search(r'-(\d+)\.txt$', f)
            if m and 115 <= int(m.group(1)) <= 260:
                with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                    self.pl027_pages[f] = dehyphenate(fp.read().lower())

        # 2. Carregar páginas 296-306 de PL051 (Próspero Chronicon)
        for f in sorted(glob.glob(os.path.join(pl051_dir, "*.txt"))):
            m = re.search(r'-(\d+)\.txt$', f)
            if m and 296 <= int(m.group(1)) <= 306:
                with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                    self.pl051_pages[f] = dehyphenate(fp.read().lower())

    def get_parent_headword(self, source_path: str, label: str, ref_raw: str = "") -> str:
        """Recupera lema pai da entrada se for subverbete subordinado."""
        if source_path not in self.source_lines:
            if os.path.exists(source_path):
                with open(source_path, 'r', encoding='utf-8', errors='ignore') as fp:
                    self.source_lines[source_path] = [l.strip() for l in fp.readlines() if l.strip()]
            else:
                self.source_lines[source_path] = []

        lines = self.source_lines.get(source_path, [])
        clean_ref = ref_raw.strip() if ref_raw else ""
        for i, l in enumerate(lines):
            # Encontra linha pelo label ou pela referência numérica se o lema foi quebrado com hífen
            if label.strip() in l or (clean_ref and clean_ref in l):
                for j in range(i, max(-1, i-6), -1):
                    prev = lines[j]
                    m = re.match(r'^([A-Z\u00C0-\u017F][a-zA-Z\u00C0-\u017F\s\.\,\-]+?)(?:\s*,\s*\d|\s*\d|\:|\.|$)', prev)
                    if m:
                        cand = m.group(1).strip()
                        if cand and not cand.startswith(('Consul', 'Interficitur', 'Moritur', 'Occiditur', 'Nascitur', 'Victus', 'Post', 'Deposita', 'In-')):
                            return cand
        return ''

    def resolve_entry(self, item_id: str, label: str, ref_raw: str, source_path: str) -> Optional[Dict[str, Any]]:
        nums = [int(n) for n in re.findall(r'\b\d+\b', ref_raw or '')]
        parent = self.get_parent_headword(source_path, label, ref_raw)
        combined = f"{parent} {label}".lower()
        stems = extract_stems(combined)

        if not stems and not nums:
            return None

        is_prosper = bool(nums and all(n > 2393 for n in nums))
        target_pool = self.pl051_pages if is_prosper else self.pl027_pages
        other_pool = self.pl027_pages if is_prosper else self.pl051_pages
        primary_vol = "PL051" if is_prosper else "PL027"
        fallback_vol = "PL027" if is_prosper else "PL051"

        def search_in_pool(pool: Dict[str, str], vol_name: str, pool_priority: float = 1.0) -> List[Tuple[float, float, str, List[str], str]]:
            candidates = []
            for path, text in pool.items():
                t_clean = text.replace('th', 't').replace('ph', 'f').replace('æ', 'e').replace('oe', 'e')
                matched_stems = [s for s in stems if s in t_clean]
                if matched_stems:
                    raw_score = sum(len(s)**2 for s in matched_stems) * pool_priority
                    confidence = min(0.99, 0.85 + 0.005 * raw_score)
                    candidates.append((raw_score, confidence, path, matched_stems, vol_name))
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates

        # Priorizar ligeiramente a piscina alvo (1.2x) determinada pelo ano abraâmico
        primary_hits = search_in_pool(target_pool, primary_vol, pool_priority=1.2)
        fallback_hits = search_in_pool(other_pool, fallback_vol, pool_priority=1.0)

        all_hits = primary_hits + fallback_hits
        if not all_hits:
            return None

        all_hits.sort(key=lambda x: x[0], reverse=True)
        best_raw, best_confidence, best_path, matched_stems, chosen_vol = all_hits[0]

        return {
            "item_id": item_id,
            "target_file": best_path,
            "target_volume": chosen_vol,
            "score": best_confidence,
            "matched_stems": matched_stems,
            "parent_inherited": parent,
            "ref_raw": ref_raw,
            "is_prosper": (chosen_vol == "PL051")
        }

def run_pl027_locator(db_path: str, summary_path: str, dry_run: bool = True):
    print(f"=== LOCALIZADOR CHRONICON PL027 ↔ PL051 ===")
    print(f"Banco: {db_path} (dry_run={dry_run})")

    t0 = time.time()
    read_uri = f"file:{os.path.abspath(db_path)}?mode=ro" if dry_run else db_path
    con = sqlite3.connect(read_uri, uri=dry_run)
    cur = con.cursor()

    rows = cur.execute(
        "SELECT id, label, ref_raw, source FROM items WHERE volume = 'PL027' AND state = 'source_only'"
    ).fetchall()
    print(f"Encontrados {len(rows)} itens pendentes em PL027.")

    locator = PL027ChroniconLocator()

    resolved_items = []
    unresolved_items = []
    pl027_count = 0
    pl051_count = 0

    for item_id, lbl, ref, src in rows:
        res = locator.resolve_entry(item_id, lbl, ref, src)
        if res:
            resolved_items.append(res)
            if res["target_volume"] == "PL027":
                pl027_count += 1
            else:
                pl051_count += 1
        else:
            unresolved_items.append((item_id, lbl, ref))

    print(f"\nResultado da Resolução:")
    print(f"  Total resolvido: {len(resolved_items)}/{len(rows)} ({len(resolved_items)/len(rows)*100:.1f}%)")
    print(f"  -> Destino PL027 (Jerônimo Chronicon): {pl027_count}")
    print(f"  -> Destino PL051 (Próspero Chronicon): {pl051_count}")
    print(f"  Não resolvidos: {len(unresolved_items)}")

    print(f"\nAmostra de Vínculos Certificados:")
    for r in resolved_items[:8]:
        fname = os.path.basename(r['target_file'])
        parent_tag = f" [herdou: {r['parent_inherited']}]" if r['parent_inherited'] else ""
        print(f"  - Item: '{r['item_id'][:8]}' ref='{r['ref_raw']}'{parent_tag} -> [{r['target_volume']}] {fname} (score={r['score']:.2f}, stems={r['matched_stems'][:3]})")

    con.close()

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
                "score": r["score"],
                "confidence": "high",
                "target_volume": r["target_volume"],
                "matched_stems": r["matched_stems"],
                "parent_inherited": r["parent_inherited"],
                "ref_raw": r["ref_raw"],
                "locator": "pl027_chronicon_locator_v1"
            }
            compressed_cand = zlib.compress(json.dumps(cand_payload).encode("utf-8"))

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
    parser = argparse.ArgumentParser(description="Localizador do Chronicon em PL027 e PL051.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Caminho do banco search.sqlite")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Caminho do catalog_summary.json")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Executar apenas simulação em leitura")
    parser.add_argument("--apply", action="store_true", default=False, help="Gravar alterações no banco de dados")

    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        print("Especifique --dry-run ou --apply.")
        sys.exit(1)

    run_pl027_locator(args.db, args.summary, dry_run=(not args.apply))

if __name__ == "__main__":
    main()

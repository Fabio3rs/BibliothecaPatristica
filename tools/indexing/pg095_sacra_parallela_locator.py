#!/usr/bin/env python3
"""
tools/indexing/pg095_sacra_parallela_locator.py

Localizador especializado para o Pinax da Sacra Parallela de São João Damasceno em PG095:
  - Mapeamento bilíngue (grego/latim) dos 24 elementos/letras (Στοιχεῖον / Littera Α-Ω)
  - Despacho físico para os volumes irmãos da Patrologia Graeca:
      * Letras A a H (Alpha a Eta): PG095 (páginas 557 a 848)
      * Letras Θ a Ω (Theta a Omega): PG096 (páginas 12 a 250)
  - Identificação de títulos capitulares (ΤΙΤΛΟΣ / TITULUS) e numerações ordinais
  - Recuperação de contexto para subentradas e lemas deconcatenados
  - Resolução de divergências editoriais e variantes tipográficas da Migne

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
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional, Any

DEFAULT_DB = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_SUMMARY = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"

GREEK_LETTERS = ['α', 'β', 'γ', 'δ', 'ε', 'ζ', 'η', 'θ', 'ι', 'κ', 'λ', 'μ', 'ν', 'ξ', 'ο', 'π', 'ρ', 'σ', 'τ', 'υ', 'φ', 'χ', 'ψ', 'ω']

LETTER_NORM = {
    'α': 'α', 'alpha': 'α', 'a': 'α',
    'β': 'β', 'beta': 'β', 'b': 'β',
    'γ': 'γ', 'gamma': 'γ', 'c': 'γ', 'g': 'γ',
    'δ': 'δ', 'delta': 'δ', 'd': 'δ',
    'ε': 'ε', 'epsilon': 'ε', 'e': 'ε',
    'ζ': 'ζ', 'zeta': 'ζ', 'z': 'ζ',
    'η': 'η', 'eta': 'η', 'h': 'η',
    'θ': 'θ', 'theta': 'θ', 'th': 'θ',
    'ι': 'ι', 'iota': 'ι', 'i': 'ι',
    'κ': 'κ', 'kappa': 'κ', 'k': 'κ',
    'λ': 'λ', 'lambda': 'λ', 'l': 'λ',
    'μ': 'μ', 'mu': 'μ', 'm': 'μ',
    'ν': 'ν', 'nu': 'ν', 'n': 'ν',
    'ξ': 'ξ', 'xi': 'ξ', 'x': 'ξ',
    'ο': 'ο', 'omicron': 'ο', 'o': 'ο',
    'π': 'π', 'pi': 'π', 'p': 'π', 'ii': 'π',
    'ρ': 'ρ', 'rho': 'ρ', 'r': 'ρ',
    'σ': 'σ', 'sigma': 'σ', 's': 'σ',
    'τ': 'τ', 'tau': 'τ', 't': 'τ',
    'υ': 'υ', 'upsilon': 'υ', 'u': 'υ', 'y': 'υ',
    'φ': 'φ', 'phi': 'φ', 'ph': 'φ',
    'χ': 'χ', 'chi': 'χ', 'ch': 'χ',
    'ψ': 'ψ', 'psi': 'ψ', 'ps': 'ψ',
    'ω': 'ω', 'omega': 'ω', 'w': 'ω'
}

GREEK_NUMS = {
    'α': 1, 'β': 2, 'γ': 3, 'δ': 4, 'ε': 5, 'ϛ': 6, 'Ϛ': 6, 'ς': 6, 'ζ': 7, 'η': 8, 'θ': 9,
    'ι': 10, 'κ': 20, 'λ': 30, 'μ': 40, 'ν': 50, 'ξ': 60, 'ο': 70, 'π': 80, 'ϟ': 90, 'ϙ': 90,
    'ρ': 100, 'σ': 200, 'τ': 300, 'υ': 400, 'φ': 500, 'χ': 600, 'ψ': 700, 'ω': 800, 'ϡ': 900
}

SECTIONS_CONFIG = [
    ('PG095', 557, 664, 'α'),
    ('PG095', 664, 682, 'β'),
    ('PG095', 682, 710, 'γ'),
    ('PG095', 710, 744, 'δ'),
    ('PG095', 744, 820, 'ε'),
    ('PG095', 820, 829, 'ζ'),
    ('PG095', 829, 848, 'η'),
    ('PG096', 13, 34, 'θ'),
    ('PG096', 34, 41, 'ι'),
    ('PG096', 41, 54, 'κ'),
    ('PG096', 54, 60, 'λ'),
    ('PG096', 60, 101, 'μ'),
    ('PG096', 237, 237, 'μ'),
    ('PG096', 101, 110, 'ν'),
    ('PG096', 110, 112, 'ξ'),
    ('PG096', 112, 119, 'ο'),
    ('PG096', 119, 172, 'π'),
    ('PG096', 172, 172, 'ρ'),
    ('PG096', 172, 193, 'σ'),
    ('PG096', 193, 194, 'τ'),
    ('PG096', 194, 202, 'υ'),
    ('PG096', 202, 231, 'φ'),
    ('PG096', 231, 233, 'χ'),
    ('PG096', 233, 234, 'ψ'),
    ('PG096', 234, 250, 'ω')
]

TITLE_RE = re.compile(
    r'(?m)(?:(?P<lat>\bTIT(?:ULUS|[.])?)|(?P<grc>Τ[ίιΙI]Τ(?:[ΛLΑA]|[.]|ΟΣ)?)|^[ \t]*(?P<ord>[Α-Ωα-ωϚϛ0-9]+)[\'΄ʹʹ’᾽᾿′]?[.][ \t]+)\s*(?P<n>[IVXLCDMΑ-Ωα-ω0-9\']+)?[.]?(?:\s*[—–-]\s*|\s+)(?P<snippet>[^\n<]{3,120})',
    re.I
)

def roman_to_int(s: str) -> int:
    s = s.strip().upper().rstrip('.')
    vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    tot = 0
    prev = 0
    for c in reversed(s):
        v = vals.get(c, 0)
        if v < prev:
            tot -= v
        else:
            tot += v
            prev = v
    return tot

def greek_to_int(s: str) -> Optional[int]:
    s = s.strip().lower().rstrip(".'΄ʹʹ’᾽᾿′")
    tot = 0
    for ch in s:
        tot += GREEK_NUMS.get(ch, 0)
    return tot if tot > 0 else None

def parse_num(token: str) -> Optional[int]:
    token = token.strip().rstrip(".'΄ʹʹ’᾽᾿′")
    if token.isdigit():
        return int(token)
    r = roman_to_int(token)
    if r > 0:
        return r
    g = greek_to_int(token)
    if g and g > 0:
        return g
    return None

def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[\W_]+', ' ', s)
    return s.strip()

class PG095SacraParallelaLocator:
    """Localizador de alta precisão para a Sacra Parallela (PG095 / PG096)."""

    def __init__(self, pg095_dir: str = "teste/PG095/text", pg096_dir: str = "teste/PG096/text"):
        self.pg095_dir = pg095_dir
        self.pg096_dir = pg096_dir
        self.all_targets: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self.targets_by_letter: Dict[str, List[Dict[str, Any]]] = {}
        self._build_target_index()

    def _build_target_index(self):
        vols_files = {
            'PG095': {int(f.split('-')[-1].split('.')[0]): f for f in glob.glob(os.path.join(self.pg095_dir, "*.txt"))},
            'PG096': {int(f.split('-')[-1].split('.')[0]): f for f in glob.glob(os.path.join(self.pg096_dir, "*.txt"))}
        }

        for vol, p_start, p_end, let in SECTIONS_CONFIG:
            files_map = vols_files.get(vol, {})
            for p in range(p_start, p_end + 1):
                if p not in files_map:
                    continue
                fpath = files_map[p]
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
                    txt = fp.read()
                for m in TITLE_RE.finditer(txt):
                    raw_n = m.group('n') or m.group('ord')
                    if not raw_n:
                        continue
                    n = parse_num(raw_n)
                    if not n:
                        continue
                    lang = 'lat' if m.group('lat') else 'grc'
                    snippet = m.group('snippet').strip()
                    t_obj = {
                        'file': fpath,
                        'physical': p,
                        'volume': vol,
                        'letter': let,
                        'number': n,
                        'lang': lang,
                        'snippet': snippet,
                        'marker': {'span': [m.start(), m.end()], 'raw': m.group(0), 'source': fpath}
                    }
                    self.all_targets.setdefault((let, n), []).append(t_obj)
                    self.targets_by_letter.setdefault(let, []).append(t_obj)

    def resolve_item(self, item_id: str, label: str, ref_raw: str, src_path: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        binding = meta.get('internal_binding', {})
        raw_let = (binding.get('letter') or {}).get('key')
        let = LETTER_NORM.get(raw_let.lower()) if raw_let else None

        nums = binding.get('numbers', [])
        if not nums:
            n = parse_num(ref_raw)
            if n:
                nums = [n]

        # Inferência contextual para entradas com quebra editorial ou deconcatenadas
        m_page = re.search(r'-(\d+)\.txt$', src_path)
        p_src = int(m_page.group(1)) if m_page else 0
        if not let:
            if p_src == 551:
                if any(w in label for w in ['λουππαίων', 'λοιδορίας', 'fame', 'conviciis', 'λογομαχούντων', 'argutioribus', 'λιμοῦ', 'nequam']):
                    let = 'λ'
                elif any(w in label for w in ['disciplinas', 'discipulis', 'periti']):
                    let = 'μ'
                elif any(w in label for w in ['jactatione', 'elatione', 'gloriari']):
                    let = 'κ'
            elif p_src == 553:
                if any(w in label for w in ['pauperibus', 'perseverantia', 'parricidiis', 'avertere', 'benevolentiam', 'patres']):
                    let = 'π'
            elif p_src == 555:
                if any(w in label for w in ['consuetudine', 'societas']):
                    let = 'σ'

        if not let:
            return None

        num = nums[0] if nums else None
        hits = self.all_targets.get((let, num), []) if num else []

        # Se não casou número exato, faz fallback por similaridade léxica no mesmo elemento alfabético
        match_method = 'exact_letter_and_number'
        score = 45.0
        if not hits:
            letter_cands = self.targets_by_letter.get(let, [])
            best_cand = None
            best_sim = 0.0
            n_lbl = normalize_text(label)
            for cand in letter_cands:
                n_snip = normalize_text(cand['snippet'])
                sim = SequenceMatcher(None, n_lbl, n_snip).ratio()
                if n_snip in n_lbl or n_lbl in n_snip:
                    sim = max(sim, 0.85)
                if sim > best_sim:
                    best_sim = sim
                    best_cand = cand
            if best_cand and best_sim >= 0.40:
                hits = [best_cand]
                match_method = 'lexical_similarity_fallback'
                score = 40.0 + (10.0 * best_sim)

        if not hits:
            return None

        best_hit = hits[0]
        return {
            'item_id': item_id,
            'target_file': best_hit['file'],
            'physical': best_hit['physical'],
            'target_volume': best_hit['volume'],
            'letter': let,
            'number': num,
            'score': score,
            'match_method': match_method,
            'snippet': best_hit['snippet'],
            'marker': best_hit['marker']
        }

def run_pg095_locator(db_path: str = DEFAULT_DB, summary_path: str = DEFAULT_SUMMARY, dry_run: bool = True):
    print("=" * 80)
    print("INICIANDO RECUPERAÇÃO MECÂNICA: PG095 (Sacra Parallela de São João Damasceno)")
    print(f"Banco alvo: {db_path} (Modo: {'DRY-RUN (Simulação)' if dry_run else 'APPLY (Escrita Atômica)'})")
    print("=" * 80)

    t0 = time.time()
    locator = PG095SacraParallelaLocator()

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    cur.execute("""
        SELECT id, label, ref_raw, source, metadata_json
        FROM items
        WHERE volume = 'PG095' AND state = 'source_only'
        ORDER BY source, id
    """)
    rows = cur.fetchall()
    total_source_only = len(rows)
    print(f"Total de itens 'source_only' em PG095: {total_source_only}")

    resolved_items = []
    unresolved_items = []

    for r in rows:
        item_id, label, ref_raw, src, meta_json = r
        meta = json.loads(meta_json)
        res = locator.resolve_item(item_id, label, ref_raw, src, meta)
        if res:
            res['label'] = label
            res['ref_raw'] = ref_raw
            resolved_items.append(res)
        else:
            unresolved_items.append({'item_id': item_id, 'label': label, 'ref_raw': ref_raw, 'source': src})

    con.close()

    pg95_dest_count = sum(1 for r in resolved_items if r['target_volume'] == 'PG095')
    pg96_dest_count = sum(1 for r in resolved_items if r['target_volume'] == 'PG096')

    print(f"\nEstatísticas de Resolução:")
    print(f"  Total resolvido com evidência física: {len(resolved_items)} / {total_source_only} ({len(resolved_items)/max(1, total_source_only)*100:.2f}%)")
    print(f"  -> Destino PG095 (Letras A - H): {pg95_dest_count}")
    print(f"  -> Destino PG096 (Letras Θ - Ω): {pg96_dest_count}")
    print(f"  Não resolvidos: {len(unresolved_items)}")

    print(f"\nAmostra de Vínculos Certificados:")
    for r in resolved_items[:8]:
        fname = os.path.basename(r['target_file'])
        print(f"  - Item: '{r['item_id'][:20]}...' ref='{r['ref_raw']}' [letra={r['letter']}] -> [{r['target_volume']}] {fname} (p.{r['physical']}, score={r['score']:.2f}, snippet='{r['snippet'][:40]}...')")

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
                "tier": "internal_division_candidate",
                "uncertainties": ["source_ocr_not_certified", "target_is_division_opening_not_all_continuation_pages"],
                "evidence": {
                    "family": "letter_title",
                    "binding": {
                        "letter": {"key": r["letter"]},
                        "numbers": [r["number"]] if r["number"] else []
                    },
                    "anchors": [{
                        "file": r["target_file"],
                        "physical": r["physical"],
                        "number": r["number"],
                        "marker": r["marker"],
                        "snippet": r["snippet"]
                    }]
                },
                "match_method": r["match_method"],
                "locator": "pg095_sacra_parallela_locator_v1"
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
    parser = argparse.ArgumentParser(description="Localizador da Sacra Parallela em PG095 e PG096.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Caminho do banco search.sqlite")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Caminho do catalog_summary.json")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Executar apenas simulação em leitura")
    parser.add_argument("--apply", action="store_true", default=False, help="Gravar alterações no banco de dados")

    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        print("Especifique --dry-run ou --apply.")
        sys.exit(1)

    run_pg095_locator(args.db, args.summary, dry_run=(not args.apply))

if __name__ == "__main__":
    main()

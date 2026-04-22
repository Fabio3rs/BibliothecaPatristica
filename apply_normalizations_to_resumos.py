#!/usr/bin/env python3
"""Aplica normalizações salvas (tabela `normalizations`) sobre os JSONs em
`resumos.keywords_json` do banco `patristica_resumos.db`.

Funcionalidade:
- Lê normalizações do DB (padrão: data/patristica_normalizations.db).
- Lê cada linha da tabela `resumos` (campo `keywords_json`) do DB de resumos
  (padrão: data/patristica_resumos.db).
- Aplica substituições em todos os valores de string no JSON (substituição por
  igualdade e por ocorrência em strings). Grava apenas quando `--apply`.

Uso:
  python apply_normalizations_to_resumos.py [--norm-db PATH] [--resumos-db PATH] [--group-id ID] [--apply]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import logging
from typing import Any, Dict, Tuple
from pathlib import Path

DEFAULT_NORM_DB = Path(__file__).resolve().parent / "data" / "patristica_normalizations.db"
DEFAULT_RESUMOS_DB = Path(__file__).resolve().parent / "data" / "patristica_resumos.db"


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_normalizations(conn: sqlite3.Connection, group_id: int | None = None) -> Dict[str, str]:
    cur = conn.cursor()

    # Filtrando para evitar valores que ficaram literalmente como '(REVISÃO NECESSÁRIA)' sem texto da keyword original
    if group_id is None:
        cur.execute("SELECT original, canonical FROM normalizations WHERE canonical IS NOT NULL AND canonical != '(REVISÃO NECESSÁRIA)' AND canonical != 'REVISÃO NECESSÁRIA'")
    else:
        cur.execute("SELECT original, canonical FROM normalizations WHERE group_id = ? AND canonical != '(REVISÃO NECESSÁRIA)' AND canonical != 'REVISÃO NECESSÁRIA'", (group_id,))
    rows = cur.fetchall()
    mapping: Dict[str, str] = {}
    for r in rows:
        orig = r[0]
        canon = r[1]
        if orig is None or canon is None:
            continue
        # armazenar chave em lowercase para comparações case-insensitive
        key = str(orig).strip().lower()
        if not key:
            continue
        val = str(canon)
        # logar se houver conflito (mesma chave com canonical diferente)
        if key in mapping and mapping[key] != val:
            logging.warning("Conflicting normalizations for %r: %r vs %r", key, mapping[key], val)
        mapping[key] = val
    return mapping


def build_regexes(mapping: Dict[str, str]):
    # Para cada original, compilar uma regex que corresponda a palavra/frase isolada
    # usando lookarounds para evitar casar dentro de palavras (comportamento robusto
    # para pontuação). Ordenamos por comprimento decrescente para priorizar matches
    # mais longos quando houver sobreposição.
    items = sorted(mapping.items(), key=lambda x: -len(x[0]))
    compiled: list[Tuple[re.Pattern, str]] = []
    for orig, canon in items:
        esc = re.escape(orig)
        # (?<!\w) and (?!\w) to assert not within word characters
        pattern = re.compile(r"(?i)(?<!\w)" + esc + r"(?!\w)")
        compiled.append((pattern, canon))
    return compiled


def replace_in_string(s: str, regexes) -> Tuple[str, int]:
    count = 0
    out = s
    for pat, canon in regexes:
        # subn returns (new_string, num_subs)
        new, n = pat.subn(canon, out)
        if n:
            out = new
            count += n
    return out, count


def replace_in_obj(obj: Any, mapping: Dict[str, str], regexes, exact_only: bool = False) -> Tuple[Any, int]:
    """Recursively percorre o objeto JSON e aplica substituições em strings.

    Retorna (novo_obj, total_substitutions)
    """
    total = 0
    if isinstance(obj, str):
        s = obj
        # primeiro substituições por igualdade exata (item inteiro é igual a orig)
        key = s.strip().lower()
        if key in mapping:
            return mapping[key], 1
        # se estamos no modo exact_only, não procuramos por ocorrências parciais
        if exact_only:
            return s, 0
        # senão, aplicar substituições por ocorrência
        new, n = replace_in_string(s, regexes)
        return new, n
    elif isinstance(obj, list):
        changed = False
        new_list = []
        for item in obj:
            new_item, n = replace_in_obj(item, mapping, regexes, exact_only=exact_only)
            total += n
            new_list.append(new_item)
            if n:
                changed = True
        return new_list, total
    elif isinstance(obj, dict):
        new_d = {}
        for k, v in obj.items():
            new_v, n = replace_in_obj(v, mapping, regexes, exact_only=exact_only)
            total += n
            new_d[k] = new_v
        return new_d, total
    else:
        return obj, 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Aplicar normalizações do DB ao campo keywords_json dos resumos")
    parser.add_argument("--norm-db", default=str(DEFAULT_NORM_DB), help="Path to normalizations DB")
    parser.add_argument("--resumos-db", default=str(DEFAULT_RESUMOS_DB), help="Path to resumos DB")
    parser.add_argument("--group-id", type=int, default=None, help="Opcional: filtrar normalizações por group_id")
    parser.add_argument("--apply", action="store_true", help="Se setado, grava as alterações no DB. Caso contrário faz dry-run")
    parser.add_argument("--limit", type=int, default=0, help="Limitar número de linhas processadas (0 = todos)")
    parser.add_argument("--exact-only", action="store_true", help="Assume que as chaves originais são idênticas aos valores em keywords_json; usa only lookup sem regexes (mais rápido)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    norm_conn = open_db(args.norm_db)
    mapping = load_normalizations(norm_conn, group_id=args.group_id)
    if not mapping:
        logging.info("Nenhuma normalização encontrada em %s (group_id=%s)", args.norm_db, args.group_id)
        return
    logging.info("Loaded %d normalizations", len(mapping))

    exact_only = bool(args.exact_only)
    regexes = None if exact_only else build_regexes(mapping)

    resum_conn = open_db(args.resumos_db)
    cur = resum_conn.cursor()
    cur.execute("SELECT rowid, keywords_json FROM resumos")
    rows = cur.fetchall()
    total_rows = len(rows)
    logging.info("Found %d rows in resumos", total_rows)

    changed_rows = 0
    total_subs = 0
    to_update = []

    limit = args.limit or 0
    processed = 0
    for r in rows:
        if limit and processed >= limit:
            break
        processed += 1
        rowid = r[0]
        raw = r[1]
        if raw is None:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            logging.warning("Skipping row %s: invalid JSON", rowid)
            continue

        new_obj, n = replace_in_obj(obj, mapping, regexes, exact_only=exact_only)
        if n > 0:
            # print(f"Row {rowid}: {n} substitutions {obj} -> {new_obj}")
            new_raw = json.dumps(new_obj, ensure_ascii=False)
            to_update.append((new_raw, rowid))
            changed_rows += 1
            total_subs += n

    logging.info("Prepared %d updates (total substitutions=%d)", changed_rows, total_subs)

    if args.apply and to_update:
        logging.info("Writing %d updates to DB %s", len(to_update), args.resumos_db)
        ucur = resum_conn.cursor()
        for new_raw, rowid in to_update:
            ucur.execute("UPDATE resumos SET keywords_json = ? WHERE rowid = ?", (new_raw, rowid))
        resum_conn.commit()
        logging.info("Applied updates.")
    else:
        if to_update:
            logging.info("Dry-run: use --apply to write changes")

    logging.info("Done. rows_changed=%d total_subs=%d", changed_rows, total_subs)


if __name__ == "__main__":
    main()

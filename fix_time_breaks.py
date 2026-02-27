#!/usr/bin/env python3
"""
Detecta quebras de ordem temporal nos resumos (página com "criado_em" anterior ao
máximo já visto no volume) e reprocessa o volume a partir da primeira página
quebrada, garantindo que o resumo_global seja recalculado em sequência.

Uso típico:
    python fix_time_breaks.py --workers 6 --provider openai --model gpt-5-mini

Modo dry-run só lista o plano:
    python fix_time_breaks.py --dry-run
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_ROOT = PROJECT_ROOT / "teste"


def find_time_breaks(con: sqlite3.Connection) -> List[Tuple[str, int]]:
    sql = """
    WITH ordered AS (
        SELECT documento,
               pagina_num,
               datetime(criado_em) AS ts,
               MAX(datetime(criado_em)) OVER (
                   PARTITION BY documento
                   ORDER BY pagina_num
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS max_prev_ts
        FROM resumos
    ),
    first_break AS (
        SELECT documento, MIN(pagina_num) AS restart_from
        FROM ordered
        WHERE ts < max_prev_ts
        GROUP BY documento
    )
    SELECT documento, restart_from FROM first_break ORDER BY documento;
    """
    rows = con.execute(sql).fetchall()
    return [(row[0], int(row[1])) for row in rows]


def page_file_exists(doc: str, page_num: int, root: Path = DEFAULT_ROOT) -> bool:
    doc_dir = root / doc / "text"
    if not doc_dir.exists():
        return False
    pattern_list = list(doc_dir.glob(f"*-{page_num}.txt")) + list(doc_dir.glob(f"*{page_num}.txt"))
    for f in pattern_list:
        # evita falso positivo (ex: 1234.txt quando procuramos 34.txt)
        if re.search(rf"[^0-9]?{page_num}\.txt$", f.name):
            if f.stat().st_size > 0:
                return True
    return False


def count_from_page(con: sqlite3.Connection, doc: str, start_page: int) -> int:
    cur = con.execute(
        "SELECT COUNT(*) FROM resumos WHERE documento = ? AND pagina_num >= ?",
        (doc, start_page),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def delete_from_page(con: sqlite3.Connection, doc: str, start_page: int) -> int:
    cur = con.execute(
        "DELETE FROM resumos WHERE documento = ? AND pagina_num >= ?",
        (doc, start_page),
    )
    con.commit()
    return cur.rowcount


def process_doc(doc: str, start_page: int, args: argparse.Namespace, idx: int, total: int) -> Tuple[str, bool, str]:
    log_dir = PROJECT_ROOT / "logs" / "fix_time_breaks"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{doc}.log"

    try:
        con = sqlite3.connect(args.db)
        if args.dry_run:
            would_delete = count_from_page(con, doc, start_page)
            con.close()
            return doc, True, f"[dry-run] apagaria {would_delete} linhas e reprocessaria a partir da página {start_page}"
        deleted = delete_from_page(con, doc, start_page)
        con.close()
    except Exception as exc:
        return doc, False, f"Erro ao preparar/deletar páginas >= {start_page}: {exc}"

    cmd = [
        "python3",
        "resumo_serial.py",
        "--volume-dir",
        str(args.root / doc),
        "--provider",
        args.provider,
        "--model",
        args.model,
        "--reasoning-effort",
        args.reasoning_effort,
    ]

    if args.provider == "ollama" and args.ollama_url:
        cmd += ["--ollama-url", args.ollama_url]
    if args.provider == "openai" and args.openai_url:
        cmd += ["--openai-url", args.openai_url]
    if args.api_key_env:
        cmd += ["--api-key-env", args.api_key_env]
    if args.retries:
        cmd += ["--retries", str(args.retries)]

    try:
        with open(log_file, "w") as f:
            subprocess.run(cmd, check=True, stdout=f, stderr=f)
        return doc, True, f"Reprocessado desde p{start_page} (linhas apagadas: {deleted}). Log: {log_file}"
    except subprocess.CalledProcessError:
        return doc, False, f"Falha ao reprocessar; veja {log_file}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reprocessa volumes com quebra temporal nos resumos.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite de resumos.")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Diretório raiz dos volumes.")
    p.add_argument("--workers", type=int, default=4, help="Volumes em paralelo (threads).")
    p.add_argument("--provider", default="openai", choices=["openai", "ollama"], help="Provider do LLM.")
    p.add_argument("--model", default="gpt-5-mini", help="Modelo do LLM.")
    p.add_argument("--reasoning-effort", default="low", choices=["", "none", "minimal", "low", "medium", "high"], help="Reasoning effort para OpenAI.")
    p.add_argument("--openai-url", default="https://api.openai.com/v1", help="URL base OpenAI.")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="URL base Ollama.")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Variável de ambiente da chave OpenAI.")
    p.add_argument("--retries", type=int, default=20, help="Tentativas por página em resumo_serial.")
    p.add_argument("--series", nargs="*", default=None, help="Filtra séries (ex: PG PL PO).")
    p.add_argument("--limit", type=int, default=None, help="Limita quantidade de volumes a reprocessar.")
    p.add_argument("--dry-run", action="store_true", help="Não executa resumo_serial; apenas mostra plano e apaga nada.")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    breaks = find_time_breaks(con)
    con.close()

    if args.series:
        prefix = tuple(args.series)
        breaks = [(doc, start) for doc, start in breaks if doc.startswith(prefix)]

    # mantém só volumes que possuem arquivo da página inicial e diretório de texto
    tasks = [(doc, start) for doc, start in breaks if page_file_exists(doc, start, args.root)]

    if args.limit is not None:
        tasks = tasks[: args.limit]

    if not tasks:
        print("Nenhum volume com quebra temporal elegível para reprocessar.")
        return

    print(f"Encontrados {len(tasks)} volumes com quebra temporal.")
    for doc, start in tasks:
        print(f" - {doc}: reiniciar a partir da página {start}")

    if args.dry_run:
        print("Dry-run: nada será executado.")
        return

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_doc, doc, start, args, i + 1, len(tasks)): (doc, start)
            for i, (doc, start) in enumerate(tasks)
        }
        for fut in as_completed(futures):
            doc, ok, msg = fut.result()
            status = "OK" if ok else "ERRO"
            print(f"[{status}] {doc}: {msg}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CLI para consultar e manipular o banco de versionamento OCR.

Uso:
    python scripts/ocr_versions_cli.py history PL001 42
    python scripts/ocr_versions_cli.py diff PL001 42
    python scripts/ocr_versions_cli.py diff --ids 1234 5678
    python scripts/ocr_versions_cli.py current PL001 42
    python scripts/ocr_versions_cli.py revert 1234 --txt-dir teste/PL001/text
    python scripts/ocr_versions_cli.py stats
    python scripts/ocr_versions_cli.py engines
    python scripts/ocr_versions_cli.py search --volume PL001 --engine ollama
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as vdb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_hash(h: str | None, n: int = 12) -> str:
    if not h:
        return "-"
    return h[:n]


def _format_row(r, ev=None, verbose: bool = False) -> str:
    """Formata um ocr_result como linha legível."""
    current = "★" if r["is_current"] else " "
    engine_info = ""
    if ev:
        model = ev["model"] or "-"
        engine_info = f"{ev['engine']}/{model}  pk={ev['prompt_key']}"
    else:
        engine_info = f"ev_id={r['engine_version_id']}"

    reason = r["reprocess_reason"] or "-"
    text_hash = _short_hash(r["text_hash"])
    image_hash = _short_hash(r["image_hash"])
    dur = f"{r['duration_ms']:.0f}ms" if r["duration_ms"] else "-"

    line = (
        f" {current} [{r['id']:>7}]  {r['created_at']}  "
        f"{engine_info}  "
        f"reason={reason}  "
        f"txt={text_hash}  img={image_hash}  dur={dur}"
    )

    if verbose and r["meta_json"]:
        line += f"\n           meta={r['meta_json']}"

    return line


# ---------------------------------------------------------------------------
# Subcomandos
# ---------------------------------------------------------------------------


def cmd_history(args: argparse.Namespace) -> None:
    """Mostra o histórico de versões de uma página."""
    con = vdb.open_versions_db(args.db)
    history = vdb.get_history(con, args.volume, args.page)
    if not history:
        print(f"Nenhum registro para {args.volume} p.{args.page}")
        return

    print(f"Histórico de {args.volume} p.{args.page} ({len(history)} versões):")
    print()
    for r in history:
        ev = vdb.get_engine_version(con, r["engine_version_id"])
        print(_format_row(r, ev, verbose=args.verbose))

    con.close()


def cmd_current(args: argparse.Namespace) -> None:
    """Mostra a versão corrente de uma página."""
    con = vdb.open_versions_db(args.db)
    r = vdb.get_current_result(con, args.volume, args.page)
    if not r:
        print(f"Nenhuma versão corrente para {args.volume} p.{args.page}")
        return

    ev = vdb.get_engine_version(con, r["engine_version_id"])
    print(_format_row(r, ev, verbose=True))

    if args.text:
        print()
        print("─" * 60)
        print(r["text_content"])

    con.close()


def cmd_diff(args: argparse.Namespace) -> None:
    """Mostra diffs entre versões."""
    con = vdb.open_versions_db(args.db)

    if args.ids:
        # Diff entre dois IDs específicos
        if len(args.ids) != 2:
            print("Erro: --ids requer exatamente 2 IDs")
            sys.exit(1)
        r_a = con.execute(
            "SELECT * FROM ocr_results WHERE id = ?", (args.ids[0],)
        ).fetchone()
        r_b = con.execute(
            "SELECT * FROM ocr_results WHERE id = ?", (args.ids[1],)
        ).fetchone()
        if not r_a or not r_b:
            print(f"Erro: resultado(s) não encontrado(s)")
            sys.exit(1)

        d = vdb.compute_diff(r_a, r_b)
        _print_diff(d)
    else:
        # Diff chain da página
        if not args.volume or args.page is None:
            print("Erro: forneça VOLUME PAGE ou --ids ID1 ID2")
            sys.exit(1)

        chain = vdb.compute_diff_chain(con, args.volume, args.page)
        if not chain:
            print(f"Menos de 2 versões para {args.volume} p.{args.page} — nada para comparar")
            return

        for i, d in enumerate(chain):
            if d["text_equal"] and not args.show_equal:
                continue
            print(f"── Diff {i + 1}/{len(chain)}: v{d['from_id']} → v{d['to_id']} ──")
            _print_diff(d)
            print()

    con.close()


def _print_diff(d: dict) -> None:
    """Imprime um diff formatado."""
    if d["text_equal"]:
        print(f"  v{d['from_id']} → v{d['to_id']}: textos idênticos (hash match)")
        return

    stat = d.get("stat", {})
    ins = stat.get("insertions", 0)
    dels = stat.get("deletions", 0)
    print(f"  +{ins} -{dels} linhas  (old={stat.get('old_len', '?')} → new={stat.get('new_len', '?')})")

    if d["unified"]:
        print()
        print(d["unified"])


def cmd_revert(args: argparse.Namespace) -> None:
    """Reverte uma página para uma versão anterior."""
    con = vdb.open_versions_db(args.db)

    # Verificar que o resultado existe
    row = con.execute(
        "SELECT * FROM ocr_results WHERE id = ?", (args.result_id,)
    ).fetchone()
    if not row:
        print(f"Erro: ocr_result id={args.result_id} não encontrado")
        sys.exit(1)

    txt_dir = args.txt_dir
    if txt_dir is None:
        txt_dir = Path("teste") / row["volume_id"] / "text"

    print(f"Revertendo {row['volume_id']} p.{row['page_num']} para versão id={args.result_id}")
    print(f"  text_hash: {_short_hash(row['text_hash'])}")
    print(f"  created_at: {row['created_at']}")
    print(f"  txt_dir: {txt_dir}")

    if not args.yes:
        resp = input("\nConfirma? [y/N] ").strip().lower()
        if resp not in ("y", "yes", "s", "sim"):
            print("Cancelado.")
            return

    vdb.revert_to(con, args.result_id, txt_dir)
    print("✓ Revertido com sucesso.")
    con.close()


def cmd_stats(args: argparse.Namespace) -> None:
    """Mostra estatísticas globais do banco."""
    con = vdb.open_versions_db(args.db)

    total = con.execute("SELECT COUNT(*) FROM ocr_results").fetchone()[0]
    current = con.execute(
        "SELECT COUNT(*) FROM ocr_results WHERE is_current = 1"
    ).fetchone()[0]
    historical = total - current
    volumes = con.execute(
        "SELECT COUNT(DISTINCT volume_id) FROM ocr_results"
    ).fetchone()[0]
    pages = con.execute(
        "SELECT COUNT(DISTINCT volume_id || ':' || page_num) FROM ocr_results"
    ).fetchone()[0]
    engines = con.execute(
        "SELECT COUNT(*) FROM ocr_engine_versions"
    ).fetchone()[0]

    # Rows por engine
    engine_stats = con.execute("""
        SELECT ev.engine, ev.model, COUNT(*) as cnt,
               SUM(CASE WHEN r.is_current = 1 THEN 1 ELSE 0 END) as cur
        FROM ocr_results r
        JOIN ocr_engine_versions ev ON ev.id = r.engine_version_id
        GROUP BY ev.engine, ev.model
        ORDER BY cnt DESC
    """).fetchall()

    # Volumes com mais versões
    multi_version = con.execute("""
        SELECT volume_id, page_num, COUNT(*) as cnt
        FROM ocr_results
        GROUP BY volume_id, page_num
        HAVING cnt > 1
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

    # Reprocess reasons
    reasons = con.execute("""
        SELECT reprocess_reason, COUNT(*) as cnt
        FROM ocr_results
        GROUP BY reprocess_reason
        ORDER BY cnt DESC
    """).fetchall()

    import os
    db_size = os.path.getsize(args.db) / 1024**2

    print(f"═══ OCR Versions DB ═══")
    print(f"  DB:          {args.db}  ({db_size:.1f} MB)")
    print(f"  Rows total:  {total:,}")
    print(f"  Correntes:   {current:,}")
    print(f"  Históricas:  {historical:,}")
    print(f"  Volumes:     {volumes}")
    print(f"  Páginas:     {pages:,}")
    print(f"  Engines:     {engines}")

    print(f"\n─── Por engine/modelo ───")
    for row in engine_stats:
        model = row["model"] or "-"
        print(f"  {row['engine']:>10}/{model:<30} total={row['cnt']:>7,}  current={row['cur']:>7,}")

    print(f"\n─── Reprocess reasons ───")
    for row in reasons:
        reason = row["reprocess_reason"] or "(initial/null)"
        print(f"  {reason:<25} {row['cnt']:>7,}")

    if multi_version:
        print(f"\n─── Páginas com mais versões (top 10) ───")
        for row in multi_version:
            print(f"  {row['volume_id']} p.{row['page_num']:<5}  {row['cnt']} versões")

    con.close()


def cmd_engines(args: argparse.Namespace) -> None:
    """Lista todas as engine_versions registradas."""
    con = vdb.open_versions_db(args.db)

    rows = con.execute("""
        SELECT ev.*, COUNT(r.id) as result_count
        FROM ocr_engine_versions ev
        LEFT JOIN ocr_results r ON r.engine_version_id = ev.id
        GROUP BY ev.id
        ORDER BY ev.id
    """).fetchall()

    if not rows:
        print("Nenhuma engine registrada.")
        return

    print(f"{'ID':>4}  {'Engine':<12} {'Model':<30} {'Prompt Key':<35} {'sys_hash':<14} {'usr_hash':<14} {'Results':>8}")
    print("─" * 125)
    for r in rows:
        model = r["model"] or "-"
        sys_h = _short_hash(r["system_prompt_hash"])
        usr_h = _short_hash(r["user_prompt_hash"]) if r["user_prompt_hash"] else "-"
        print(
            f"{r['id']:>4}  {r['engine']:<12} {model:<30} {r['prompt_key']:<35} "
            f"{sys_h:<14} {usr_h:<14} {r['result_count']:>8,}"
        )

    if args.verbose and rows:
        print(f"\n─── Prompts completos ───")
        for r in rows:
            print(f"\n[{r['id']}] {r['engine']}/{r['model'] or '-'} — {r['prompt_key']}")
            if r["system_prompt_content"]:
                preview = r["system_prompt_content"][:300]
                if len(r["system_prompt_content"]) > 300:
                    preview += "…"
                print(f"  system: {preview}")
            if r["user_prompt_content"]:
                preview = r["user_prompt_content"][:300]
                if len(r["user_prompt_content"]) > 300:
                    preview += "…"
                print(f"  user:   {preview}")

    con.close()


def cmd_search(args: argparse.Namespace) -> None:
    """Busca resultados por filtros."""
    con = vdb.open_versions_db(args.db)

    clauses: list[str] = []
    params: list = []

    if args.volume:
        clauses.append("r.volume_id = ?")
        params.append(args.volume)
    if args.page is not None:
        clauses.append("r.page_num = ?")
        params.append(args.page)
    if args.engine:
        clauses.append("ev.engine = ?")
        params.append(args.engine)
    if args.model:
        clauses.append("ev.model = ?")
        params.append(args.model)
    if args.reason:
        clauses.append("r.reprocess_reason = ?")
        params.append(args.reason)
    if args.current_only:
        clauses.append("r.is_current = 1")

    if not clauses:
        print("Erro: forneça pelo menos um filtro (--volume, --engine, --reason, etc.)")
        sys.exit(1)

    where = " AND ".join(clauses)
    limit = args.limit or 50

    rows = con.execute(f"""
        SELECT r.*, ev.engine, ev.model, ev.prompt_key
        FROM ocr_results r
        JOIN ocr_engine_versions ev ON ev.id = r.engine_version_id
        WHERE {where}
        ORDER BY r.created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    if not rows:
        print("Nenhum resultado encontrado.")
        return

    print(f"{len(rows)} resultado(s):\n")
    for r in rows:
        current = "★" if r["is_current"] else " "
        model = r["model"] or "-"
        reason = r["reprocess_reason"] or "-"
        txt_h = _short_hash(r["text_hash"])
        print(
            f" {current} [{r['id']:>7}]  {r['volume_id']} p.{r['page_num']:<4}  "
            f"{r['created_at']}  {r['engine']}/{model}  "
            f"reason={reason}  txt={txt_h}"
        )

    con.close()


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr_versions_cli",
        description="CLI para consultar e manipular o banco de versionamento OCR.",
    )
    parser.add_argument(
        "--db", type=Path, default=vdb.DEFAULT_DB_PATH,
        help="Caminho para o ocr_versions.db",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- history ---
    p_hist = sub.add_parser("history", aliases=["hist", "h"],
                            help="Histórico de versões de uma página")
    p_hist.add_argument("volume", help="Volume ID (ex: PL001)")
    p_hist.add_argument("page", type=int, help="Número da página")
    p_hist.add_argument("-v", "--verbose", action="store_true",
                        help="Mostrar meta_json")
    p_hist.set_defaults(func=cmd_history)

    # --- current ---
    p_cur = sub.add_parser("current", aliases=["cur", "c"],
                           help="Versão corrente de uma página")
    p_cur.add_argument("volume", help="Volume ID")
    p_cur.add_argument("page", type=int, help="Número da página")
    p_cur.add_argument("-t", "--text", action="store_true",
                       help="Imprimir o text_content completo")
    p_cur.set_defaults(func=cmd_current)

    # --- diff ---
    p_diff = sub.add_parser("diff", aliases=["d"],
                            help="Diff entre versões")
    p_diff.add_argument("volume", nargs="?", help="Volume ID")
    p_diff.add_argument("page", nargs="?", type=int, help="Número da página")
    p_diff.add_argument("--ids", nargs=2, type=int, metavar=("ID1", "ID2"),
                        help="Comparar dois result IDs específicos")
    p_diff.add_argument("--show-equal", action="store_true",
                        help="Mostrar pares com textos idênticos")
    p_diff.set_defaults(func=cmd_diff)

    # --- revert ---
    p_rev = sub.add_parser("revert", aliases=["rev"],
                           help="Reverter página para uma versão anterior")
    p_rev.add_argument("result_id", type=int, help="ID do ocr_result a restaurar")
    p_rev.add_argument("--txt-dir", type=Path,
                       help="Diretório do .txt (default: teste/<vol>/text)")
    p_rev.add_argument("-y", "--yes", action="store_true",
                       help="Não pedir confirmação")
    p_rev.set_defaults(func=cmd_revert)

    # --- stats ---
    p_stats = sub.add_parser("stats", aliases=["s"],
                             help="Estatísticas globais do banco")
    p_stats.set_defaults(func=cmd_stats)

    # --- engines ---
    p_eng = sub.add_parser("engines", aliases=["eng", "e"],
                           help="Listar engine_versions registradas")
    p_eng.add_argument("-v", "--verbose", action="store_true",
                       help="Mostrar preview dos prompts")
    p_eng.set_defaults(func=cmd_engines)

    # --- search ---
    p_search = sub.add_parser("search", aliases=["find", "f"],
                              help="Buscar resultados por filtros")
    p_search.add_argument("--volume", help="Filtrar por volume_id")
    p_search.add_argument("--page", type=int, help="Filtrar por page_num")
    p_search.add_argument("--engine", help="Filtrar por engine (tesseract, ollama, ...)")
    p_search.add_argument("--model", help="Filtrar por model")
    p_search.add_argument("--reason", help="Filtrar por reprocess_reason")
    p_search.add_argument("--current-only", action="store_true",
                          help="Apenas is_current=1")
    p_search.add_argument("--limit", type=int, default=50,
                          help="Máximo de resultados (default: 50)")
    p_search.set_defaults(func=cmd_search)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

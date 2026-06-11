#!/usr/bin/env python3
"""Run keywords_serial.py --llm-judge over occurrences for suspect keywords.

Reads a JSONL file (e.g. bad_citations.jsonl) produced by the checker, extracts
keyword ids and for each keyword queries the `keyword_occurrence` table to
find document/page occurrences. For each occurrence it invokes
`keywords_serial.py --llm-judge` with the document and page.

This is intentionally conservative: default is dry-run (prints commands).
Set --run to actually execute the judge.

Usage examples:
  # dry run (default)
  python3 scripts/run_llm_judge_on_bad.py --json bad_citations.jsonl

  # real run, provide provider, url and model
  python3 scripts/run_llm_judge_on_bad.py --json bad_citations.jsonl --db data/patristica_keywords.db \
    --provider openai --openai-url "https://api.openai.com/v1" --model gpt-4 --run

Options:
  --json PATH     JSONL file with bad keyword records (one JSON per line)
  --db PATH       SQLite DB (default: data/patristica_keywords.db)
  --script PATH   Path to keywords_serial.py (default: ./keywords_serial.py)
  --provider NAME Provider name passed to keywords_serial.py (required for --run)
  --openai-url URL OpenAI base URL passed through (optional)
  --model NAME    Model name passed to keywords_serial.py (optional)
  --limit-keywords N  Limit number of distinct keywords to process (for tests)
  --limit-occurrences N  Limit number of occurrences per keyword
  --run           Actually execute the commands instead of printing (default: dry-run)
  --extra-args STR Extra args appended to keywords_serial.py invocation (quoted)

"""
import argparse
import json
import sqlite3
import subprocess
import shlex
import os
import sys
import multiprocessing
from collections import OrderedDict


def parse_args():
    p = argparse.ArgumentParser(
        description="Run llm-judge on occurrences for suspect keywords"
    )
    p.add_argument("--json", required=True, help="Path to bad_citations.jsonl")
    p.add_argument(
        "--db", default="data/patristica_keywords.db", help="Path to sqlite DB"
    )
    p.add_argument(
        "--script", default="./keywords_serial.py", help="Path to keywords_serial.py"
    )
    p.add_argument(
        "--provider", default=None, help="Provider to pass to keywords_serial.py"
    )
    p.add_argument("--openai-url", default=None, help="OpenAI URL to pass through")
    p.add_argument(
        "--model", default=None, help="Model name to pass to keywords_serial.py"
    )
    p.add_argument(
        "--limit-keywords",
        type=int,
        default=None,
        help="Limit number of distinct keywords to process",
    )
    p.add_argument(
        "--limit-occurrences",
        type=int,
        default=None,
        help="Limit number of occurrences per keyword",
    )
    p.add_argument(
        "--run",
        action="store_true",
        help="Actually execute commands (default: dry-run)",
    )
    p.add_argument(
        "--extra-args",
        default="",
        help="Extra args appended to keywords_serial.py call",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes for parallel execution (default: 1)",
    )
    return p.parse_args()


def load_keyword_ids(jsonl_path, limit=None):
    ids = OrderedDict()
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as e:
                print(f"Warning: skipping malformed line: {e}", file=sys.stderr)
                continue
            kid = obj.get("id")
            if kid is None:
                continue
            ids[kid] = True
            if limit and len(ids) >= limit:
                break
    return list(ids.keys())


def query_occurrences(db_path, keyword_id, limit=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = "SELECT id, keyword_id, pagina_id, documento, pagina_num, keywords_modelo, rank_in_page FROM keyword_occurrence WHERE keyword_id = ? ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    cur.execute(q, (keyword_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def build_command(
    script,
    provider,
    openai_url,
    model,
    documento,
    pagina_num,
    keyword_id,
    pagina_id,
    extra_args,
):
    cmd = [sys.executable, script, "--llm-judge"]
    if provider:
        cmd += ["--provider", provider]
    if openai_url:
        cmd += ["--openai-url", openai_url]
    if model:
        cmd += ["--model", model]
    # pass document and page
    cmd += ["--doc", str(documento), "--page", str(pagina_num)]
    # pass occurrence identifiers for traceability
    # cmd += ['--keyword-id', str(keyword_id), '--pagina-id', str(pagina_id)]
    if extra_args:
        # split respecting quotes
        cmd += shlex.split(extra_args)
    return cmd


def _run_cmd(cmd):
    """Run a command list with subprocess and return (returncode, cmd_str)."""
    cmd_str = " ".join(shlex.quote(x) for x in cmd)
    print(f"RUN: {cmd_str}")
    try:
        res = subprocess.run(cmd)
        return (res.returncode, cmd_str)
    except Exception as e:
        return (1, f"{cmd_str} (exception: {e})")


def main():
    args = parse_args()
    if not os.path.exists(args.json):
        print(f"JSONL file not found: {args.json}", file=sys.stderr)
        sys.exit(2)
    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(2)
    if args.run and not args.provider:
        print(
            "Error: --provider is required when --run is set (safety)", file=sys.stderr
        )
        sys.exit(2)

    keyword_ids = load_keyword_ids(args.json, limit=args.limit_keywords)
    print(
        f"Found {len(keyword_ids)} distinct keyword ids (limit={args.limit_keywords})"
    )

    cmds = []  # list of (cmd_list, metadata)
    skipped_duplicates = 0
    seen_pages = set()  # dedupe global by (documento, pagina_num)
    for kid in keyword_ids:
        occs = query_occurrences(args.db, kid, limit=args.limit_occurrences)
        if not occs:
            print(f"keyword_id={kid}: no occurrences found in keyword_occurrence table")
            continue
        for occ in occs:
            pagina_id = occ["pagina_id"]
            documento = occ["documento"]
            pagina_num = occ["pagina_num"]
            key_page = (documento, pagina_num)
            if key_page in seen_pages:
                skipped_duplicates += 1
                continue
            seen_pages.add(key_page)
            cmd = build_command(
                args.script,
                args.provider,
                args.openai_url,
                args.model,
                documento,
                pagina_num,
                kid,
                pagina_id,
                args.extra_args,
            )
            cmds.append((cmd, kid, pagina_id, documento, pagina_num))

    total_cmds = len(cmds)

    if not args.run:
        # Dry run: just print the commands
        for cmd, kid, pagina_id, documento, pagina_num in cmds:
            cmd_str = " ".join(shlex.quote(x) for x in cmd)
            print(f"DRYRUN: {cmd_str}")
    else:
        # Run: execute commands, possibly in parallel
        if args.workers and args.workers > 1:
            print(f"Executing {total_cmds} commands with {args.workers} workers...")
            try:
                with multiprocessing.Pool(processes=args.workers) as pool:
                    # map only the command lists
                    cmd_lists = [c for c, *_ in cmds]
                    results = pool.map(_run_cmd, cmd_lists)
            except KeyboardInterrupt:
                print("Interrupted by user; terminating workers", file=sys.stderr)
                raise
            # report failures
            for returncode, cmd_str in results:
                if returncode != 0:
                    print(
                        f"Command failed (exit {returncode}): {cmd_str}",
                        file=sys.stderr,
                    )
        else:
            print(f"Executing {total_cmds} commands serially...")
            for cmd, kid, pagina_id, documento, pagina_num in cmds:
                returncode, cmd_str = _run_cmd(cmd)
                if returncode != 0:
                    print(
                        f"Command failed (exit {returncode}): {cmd_str}",
                        file=sys.stderr,
                    )

    print(
        f"Total commands prepared: {total_cmds} (run={args.run}), skipped duplicate pages: {skipped_duplicates}, workers={args.workers}"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Select current PG/PL OCR by exact model and invoke main2 once per volume.

Default: offline plan only. --execute explicitly starts OCR requests and writes
the selected corpus pages through main2, which versions results using SQLite WAL.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_current_ocr_xml import connect_read_only
from tools.corpus_utils import choose_page_file, now_iso, page_files

DEFAULT_DB = ROOT / "data/ocr_versions.db"
VOLUME = re.compile(r"P[GL]\d+(?:[.-]\d+)*\Z")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("deve ser positivo")
    return number


def current_rows(con, model, collections, volumes=None):
    filters = ["r.is_current=1", "e.model=?",
               "(" + " OR ".join("r.volume_id GLOB ?" for _ in collections) + ")"]
    params = [model, *(prefix + "[0-9]*" for prefix in collections)]
    if volumes:
        filters.append("r.volume_id IN (" + ",".join("?" for _ in volumes) + ")")
        params.extend(volumes)
    yield from con.execute(
        """SELECT r.id, r.volume_id, r.page_num, r.image_hash, r.text_hash,
                  e.engine, e.model,
                  (SELECT COUNT(*) FROM ocr_results c WHERE c.volume_id=r.volume_id
                   AND c.page_num=r.page_num AND c.is_current=1) AS current_count
           FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
           WHERE """ + " AND ".join(filters) + " ORDER BY r.volume_id,r.page_num,r.id", params)


def command(args, volume, rows):
    argv = [sys.executable, "-u", str(ROOT / "main2.py"),
            "--algorithm", args.algorithm, "--llm-model", args.llm_model,
            "--procs", str(args.procs), "--omp-threads", str(args.omp_threads),
            "--verify-fix", "--force", "--out", str(args.out), "--lang", args.lang,
            "--pages", ",".join(str(row["page_num"]) for row in rows)]
    if args.do_not_reprocess_compare:
        argv.append("--do-not-reprocess-compare")
    return [*argv, str(args.pdf_dir / f"{volume}.pdf")]


def prepare(args, directory):
    """Stream metadata only; keep at most one volume in memory."""
    jobs, counts, blocked = [], Counter(), Counter()
    remaining = args.limit
    with closing(connect_read_only(args.db)) as con, (directory / "excluded.jsonl").open(
            "w", encoding="utf-8") as excluded:
        journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        for volume, group in itertools.groupby(
                current_rows(con, args.source_model, args.collections, args.volumes),
                key=lambda row: row["volume_id"]):
            rows = []
            for record in group:
                row = dict(record)
                problem = ("invalid_locator" if not VOLUME.fullmatch(volume) or row["page_num"] < 1
                           else "ambiguous_current" if row["current_count"] != 1 else None)
                if problem:
                    blocked[problem] += 1
                    excluded.write(json.dumps({**row, "reason": problem}) + "\n")
                    continue
                if remaining is not None and remaining == 0:
                    break
                rows.append(row)
                if remaining is not None:
                    remaining -= 1
            if rows:
                pdf = args.pdf_dir / f"{volume}.pdf"
                status = "ready" if pdf.is_file() else "missing_pdf"
                job = {"volume": volume, "pdf": str(pdf), "status": status, "pages": rows,
                       "argv": command(args, volume, rows)}
                path = directory / "volumes" / f"{volume}.json"
                write_json(path, job)
                counts[volume[:2]] += len(rows)
                if status == "ready":
                    jobs.append(path)
                else:
                    blocked[status] += len(rows)
            if remaining == 0:
                break
    summary = {"created_at": now_iso(), "dry_run": not args.execute,
               "source_db": str(args.db), "journal_mode": journal_mode,
               "source_model": args.source_model, "algorithm": args.algorithm,
               "target_model": args.llm_model, "collections": dict(counts),
               "selected_pages": sum(counts.values()), "ready_volumes": len(jobs),
               "blocked": dict(blocked), "jobs": args.jobs, "procs": args.procs,
               "max_page_workers": args.jobs * args.procs, "directory": str(directory)}
    write_json(directory / "plan.json", summary)
    return jobs, summary


def volume_current(db, volume):
    with closing(connect_read_only(db)) as con:
        grouped = {}
        for row in con.execute(
            """SELECT r.id,r.page_num,r.text_hash,r.image_hash,e.engine,e.model
               FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
               WHERE r.volume_id=? AND r.is_current=1""", (volume,)):
            grouped.setdefault(row["page_num"], []).append(dict(row))
        return grouped


def still_selected(rows, current):
    selected, skipped = [], []
    for row in rows:
        live = current.get(row["page_num"], [])
        if len(live) == 1 and all(live[0][key] == row[key] for key in (
                "id", "text_hash", "image_hash", "engine", "model")):
            selected.append(row)
        else:
            skipped.append(row["page_num"])
    return selected, skipped


def confirm_outputs(args, volume, rows):
    """main2 can swallow worker errors: exit code zero alone is insufficient."""
    current = volume_current(args.db, volume)
    files = page_files(args.out / volume / "text", suffixes=(".txt",))
    results = []
    for old in rows:
        page = old["page_num"]
        live = current.get(page, [])
        confirmed = (len(live) == 1 and live[0]["id"] != old["id"]
                     and live[0]["engine"] == args.algorithm and live[0]["model"] == args.llm_model)
        if confirmed:
            path, ambiguous = choose_page_file(files.get(page, []), volume_id=volume, page_num=page)
            confirmed = bool(path and not ambiguous and
                             hashlib.sha256(path.read_bytes()).hexdigest() == live[0]["text_hash"])
        results.append({"page": page, "source_id": old["id"],
                        "current_ids": [r["id"] for r in live],
                        "status": "recorded" if confirmed else "unconfirmed"})
    return results


def stop_process(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.wait()
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def run_volume(args, path, stop):
    job = json.loads(path.read_text(encoding="utf-8"))
    volume = job["volume"]
    result = {"volume": volume, "status": "stopped", "started_at": now_iso()}
    if stop.is_set():
        return result
    # All invocations use the same volume lock, including different log directories.
    lock_dir = ROOT / ".work/ocr_reprocess_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{volume}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**result, "status": "locked"}
        rows, skipped = still_selected(job["pages"], volume_current(args.db, volume))
        result["skipped_changed_pages"] = skipped
        if not rows:
            return {**result, "status": "skipped_changed"}
        if stop.is_set():
            return result
        argv = command(args, volume, rows)
        log_path = path.parent.parent / "logs" / f"{volume}.log"
        write_json(path.with_suffix(".execution.json"), {"argv": argv, "source_pages": rows})
        emit({"event": "started", "volume": volume, "pages": len(rows), "log": str(log_path)})
        with log_path.open("wb") as log:
            proc = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                while proc.poll() is None:
                    if stop.wait(.5):
                        stop_process(proc)
                        break
            finally:
                stop_process(proc)
        outcomes = confirm_outputs(args, volume, rows)
        write_json(path.with_suffix(".results.json"), outcomes)
        counts = Counter(row["status"] for row in outcomes)
        status = ("stopped" if stop.is_set() else "completed"
                  if proc.returncode == 0 and not counts["unconfirmed"] else "incomplete")
        return {**result, "status": status, "returncode": proc.returncode,
                "pages": dict(counts), "log": str(log_path), "finished_at": now_iso()}


def execute(args, jobs, directory, stop):
    """Threads supervise independent OS processes; OCR runs in main2's process pools."""
    pending = iter(jobs)
    states = Counter()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool, (directory / "results.jsonl").open(
            "w", encoding="utf-8") as journal:
        active = {}
        while True:
            while not stop.is_set() and len(active) < args.jobs:
                path = next(pending, None)
                if path is None:
                    break
                active[pool.submit(run_volume, args, path, stop)] = path
            if not active:
                break
            finished, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
            for future in finished:
                path = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"volume": path.stem, "status": "error", "error": str(exc)}
                states[result["status"]] += 1
                journal.write(json.dumps(result, ensure_ascii=False) + "\n")
                journal.flush()
                emit({"finished_volumes": sum(states.values()), "total_volumes": len(jobs), **result})
    summary = {"states": dict(states), "stopped": stop.is_set(), "directory": str(directory)}
    write_json(directory / "execution_summary.json", summary)
    emit(summary)
    return 130 if stop.is_set() else int(any(key not in {"completed", "skipped_changed"} for key in states))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Inicia OCR (pode consumir API paga).")
    mode.add_argument("--dry-run", action="store_true", help="Somente plano; comportamento padrão.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Banco de origem; alternativo somente para simulação.")
    parser.add_argument("--source-model", default="gpt-5-mini", help="Correspondência exata do modelo atual.")
    parser.add_argument("--collections", nargs="+", choices=["PG", "PL"], default=["PG", "PL"])
    parser.add_argument("--volumes", nargs="+", help="Restringe a volumes como PG001 PL020.")
    parser.add_argument("--limit", type=positive, help="Máximo de páginas, em ordem de volume/página.")
    parser.add_argument("--pdf-dir", type=Path, default=Path("/homessddata/patristica"))
    parser.add_argument("--out", type=Path, default=ROOT / "teste")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work/ocr_reprocess_current")
    parser.add_argument("--algorithm", choices=["ollama", "openai"], default=os.getenv("ALGORITHM", "ollama"))
    parser.add_argument("--llm-model", default=os.getenv("LLM_MODEL"), help="Modelo destino; padrão Ollama igual ao shell.")
    parser.add_argument("--jobs", type=positive, default=os.getenv("JOBS", "11"), help="Volumes simultâneos.")
    parser.add_argument("--procs", type=positive, default=os.getenv("PROCS", "1"), help="Processos de OCR por volume.")
    parser.add_argument("--omp-threads", type=positive, default=os.getenv("OMP_THREADS", "2"))
    parser.add_argument("--lang", default="migne")
    parser.add_argument("--do-not-reprocess-compare", action="store_true")
    args = parser.parse_args(argv)
    for key in ("db", "pdf_dir", "out", "work_dir"):
        setattr(args, key, getattr(args, key).resolve())
    if args.llm_model is None:
        if args.algorithm == "openai":
            parser.error("Informe --llm-model para --algorithm openai")
        args.llm_model = "qwen3.5:397b-cloud"
    if args.volumes and any(not VOLUME.fullmatch(v) for v in args.volumes):
        parser.error("--volumes requer identificadores PG/PL, por exemplo PG007.01 PL020")
    if args.execute and args.db != DEFAULT_DB.resolve():
        parser.error("main2.py grava em data/ocr_versions.db; --db alternativo só permite --dry-run")
    return args


def main(argv=None):
    args = parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d_%H%M%S_"), dir=args.work_dir))
    (directory / "volumes").mkdir()
    (directory / "logs").mkdir()
    try:
        jobs, summary = prepare(args, directory)
        emit(summary)
        if not args.execute:
            for path in jobs[:3]:
                emit({"example_command": shlex.join(json.loads(path.read_text())["argv"])})
            return 0
        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            code = execute(args, jobs, directory, stop)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        return code or int(bool(summary["blocked"]))
    except (OSError, ValueError, sqlite3.Error) as exc:
        emit({"error": str(exc), "directory": str(directory)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Explicit JSONL manifests, bounded process pools, one SQLite connection per worker."""
import json
import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from .store import connect, ingest


def process_page(job):
    db_path, item, defaults = job
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    con = None
    try:
        con = connect(db_path)
        options = {**defaults, **item}
        if bool(options.get("text_path")) == (options.get("result_id") is not None):
            raise ValueError("Cada item requer exatamente text_path ou result_id")
        return {"image": str(options["image_path"]), **ingest(con, **options)}
    except Exception as exc:
        return {"image": str(item.get("image_path", "")), "error": str(exc)}
    finally:
        if con is not None:
            con.close()


def run_batch(db_path, manifest, workers, defaults):
    if not 1 <= workers <= 32:
        raise ValueError("workers deve estar entre 1 e 32")
    con = connect(db_path)
    con.close()
    allowed = {"image_path", "text_path", "result_id", "versions_db", "lang", "psm", "oem",
               "tessdata", "tsv_path", "dehyphenate", "tolerance"}
    with Path(manifest).open(encoding="utf-8") as stream, ProcessPoolExecutor(
        max_workers=workers, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        pending = set()
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or set(item) - allowed or "image_path" not in item:
                    raise ValueError("Item de manifesto inválido")
            except ValueError as exc:
                yield {"line": number, "error": str(exc)}
                continue
            pending.add(pool.submit(process_page, (str(db_path), item, defaults)))
            if len(pending) >= workers * 2:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()

#!/usr/bin/env python3
"""Replay opening-guided index grammars from OCR into an isolated SQLite database.

The plan selects historical sections, not parsed entries. Opening notices decide
the numeric namespace; this experiment never converts an unknown number to a page.
"""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.corpus_utils import page_files
from tools.indexing.reproducible_selection import source_path

GRAMMARS = {
    "pg104-openings-v1": "tools.indexing.pg104_opening_grammar",
    "pl217-openings-v1": "tools.indexing.pl217_opening_grammar",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(value):
    return hashlib.sha256(value).hexdigest()


def span(record):
    if "char_start" in record:
        return record["char_start"], record["char_end"]
    if "span" in record:
        return tuple(record["span"])
    raise ValueError("Record lacks literal offsets")


def parse_job(job):
    section, opening, source, physical = job
    raw = Path(source).read_text(encoding="utf-8")
    module = importlib.import_module(GRAMMARS[section["grammar"]])
    page = module.parse_index_page(
        raw, source=source, physical=physical, opening=opening
    )
    # A new printed opening may supersede inherited context inside the parser.
    # Never store those references under the previous section's numbering system.
    actual_namespace = page.get("namespace") or page.get("opening", {}).get("namespace")
    if actual_namespace and actual_namespace != section["namespace"]:
        raise ValueError("Page opening contradicts section namespace: " + source)
    previous = -1
    for entry in sorted(page["entries"], key=lambda r: span(r)):
        a, b = span(entry)
        if not 0 <= a < b <= len(raw) or a < previous or raw[a:b] != entry["entry_raw"]:
            raise ValueError("Invalid/overlapping literal entry: " + source)
        previous = b
        for group in entry.get("reference_groups", []):
            x, y = span(group)
            if not a <= x < y <= b or raw[x:y] != group["raw"]:
                raise ValueError("Reference outside its printed owner: " + source)
    return {
        "section": section["id"],
        "source": source,
        "physical": physical,
        "source_sha256": checksum(raw.encode()),
        "page": page,
    }


def run(plan, root, out, workers=2):
    spec = json.loads(plan.read_text())
    if spec.get("version") != "index-opening-study-v1":
        raise ValueError("Unsupported plan")
    if workers < 1:
        raise ValueError("workers must be positive")
    out.mkdir(parents=True, exist_ok=False)
    sections, jobs = [], []
    if len({s["id"] for s in spec["sections"]}) != len(spec["sections"]):
        raise ValueError("Duplicate section IDs")
    for section in spec["sections"]:
        if section["first"] > section["last"]:
            raise ValueError("Reversed section range: " + section["id"])
        module = importlib.import_module(GRAMMARS[section["grammar"]])
        opening_path = source_path(root, section["opening_source"])
        opening = module.analyze_opening(
            opening_path.read_text(encoding="utf-8"), source=str(opening_path)
        )
        recognized = opening.get("matched", opening.get("status") == "recognized")
        if not recognized or opening.get("namespace") != section["namespace"]:
            raise ValueError(
                "Opening does not support the declared namespace: " + section["id"]
            )
        context = []
        for title_source in section.get("title_sources", []):
            title_path = source_path(root, title_source)
            context.append(
                module.analyze_title_page(
                    title_path.read_text(encoding="utf-8"), source=str(title_path)
                )
            )
        sections.append(
            {
                "selection": section,
                "opening": opening,
                "bibliographic_context": context,
                "grammar_sha256": checksum(Path(module.__file__).read_bytes()),
            }
        )
        inventory = page_files(root / section["volume"] / "text", suffixes=[".txt"])
        for physical in range(section["first"], section["last"] + 1):
            paths = inventory.get(physical, [])
            if len(paths) != 1:
                raise ValueError(
                    f'{section["id"]}: expected one OCR file at {physical}, found {len(paths)}'
                )
            jobs.append((section, opening, str(paths[0].resolve()), physical))
    counts, by_section = Counter(), {}
    digest = hashlib.sha256()
    with closing(sqlite3.connect(out / "extraction.sqlite")) as db, (
        out / "pages.jsonl"
    ).open("w") as pages, (out / "entries.jsonl").open("w") as entries:
        db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE sections(id TEXT PRIMARY KEY,namespace TEXT,evidence_json TEXT);
            CREATE TABLE pages(id TEXT PRIMARY KEY,section_id TEXT,source TEXT,physical INTEGER,source_sha256 TEXT,page_json TEXT);
            CREATE TABLE entries(id TEXT PRIMARY KEY,page_id TEXT,char_start INTEGER,char_end INTEGER,label TEXT,entry_json TEXT);
            CREATE TABLE reference_groups(id TEXT PRIMARY KEY,entry_id TEXT,char_start INTEGER,char_end INTEGER,raw TEXT,namespace TEXT,evidence_json TEXT);
            CREATE TABLE residuals(id TEXT PRIMARY KEY,page_id TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            CREATE VIRTUAL TABLE entries_fts USING fts5(id UNINDEXED,label,reference_raw,tokenize='unicode61 remove_diacritics 2');
        """)
        for section in sections:
            s = section["selection"]
            db.execute(
                "INSERT INTO sections VALUES(?,?,?)",
                (s["id"], s["namespace"], encoded(section)),
            )
        namespaces = {
            s["selection"]["id"]: s["selection"]["namespace"] for s in sections
        }
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(parse_job, jobs):
                page = result["page"]
                sid = result["section"]
                namespace = namespaces[sid]
                stats = by_section.setdefault(sid, Counter())
                pid = checksum(
                    encoded([sid, result["source"], result["source_sha256"]]).encode()
                )
                payload = encoded(result)
                digest.update((payload + "\n").encode())
                pages.write(payload + "\n")
                db.execute(
                    "INSERT INTO pages VALUES(?,?,?,?,?,?)",
                    (
                        pid,
                        sid,
                        result["source"],
                        result["physical"],
                        result["source_sha256"],
                        encoded(page),
                    ),
                )
                stats["pages"] += 1
                for entry in page["entries"]:
                    a, b = span(entry)
                    eid = checksum(encoded([pid, a, b]).encode())
                    groups = entry.get("reference_groups", [])
                    label = entry.get("text_raw") or next(
                        (
                            g.get("owner_text_raw")
                            for g in groups
                            if g.get("owner_text_raw")
                        ),
                        entry["entry_raw"],
                    )
                    db.execute(
                        "INSERT INTO entries VALUES(?,?,?,?,?,?)",
                        (eid, pid, a, b, label, encoded(entry)),
                    )
                    db.execute(
                        "INSERT INTO entries_fts VALUES(?,?,?)",
                        (eid, label, " ".join(g["raw"] for g in groups)),
                    )
                    entries.write(
                        encoded(
                            {
                                "id": eid,
                                "section_id": sid,
                                "source": result["source"],
                                "source_sha256": result["source_sha256"],
                                "namespace": namespace,
                                "entry": entry,
                            }
                        )
                        + "\n"
                    )
                    stats["entries"] += 1
                    for group in groups:
                        x, y = span(group)
                        gid = checksum(encoded([pid, x, y]).encode())
                        db.execute(
                            "INSERT INTO reference_groups VALUES(?,?,?,?,?,?,?)",
                            (gid, eid, x, y, group["raw"], namespace, encoded(group)),
                        )
                        stats["reference_groups"] += 1
                for i, residual in enumerate(page.get("residuals", [])):
                    db.execute(
                        "INSERT INTO residuals VALUES(?,?,?)",
                        (f"{pid}:{i}", pid, encoded(residual)),
                    )
                    stats["residual_records"] += 1
                db.commit()
        for stats in by_section.values():
            counts.update(stats)
        summary = {
            "version": spec["version"],
            "plan": str(plan.resolve()),
            "plan_sha256": checksum(plan.read_bytes()),
            "workers": workers,
            "counts": dict(counts),
            "by_section": {k: dict(v) for k, v in by_section.items()},
            "logical_sha256": digest.hexdigest(),
            "sections": sections,
            "old_database_required": False,
            "canonical_updates": 0,
            "destination_mapping_attempted": False,
            "quality": "mechanical candidates with explicit namespaces; no exhaustive visual certification",
        }
        db.execute("INSERT INTO metadata VALUES(?,?)", ("summary", encoded(summary)))
        db.commit()
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Invalid experiment database")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(encoded({k: v for k, v in summary.items() if k != "sections"}))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--root", type=Path, default=Path("teste"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()
    run(args.plan, args.root, args.out, args.workers)

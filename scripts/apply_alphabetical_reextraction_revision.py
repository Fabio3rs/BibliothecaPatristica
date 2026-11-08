#!/usr/bin/env python3
"""Explicitly archive a reviewed staged revision in SQLite, preserving canonical rows.

This separate step creates additive revision tables only. Existing entries, targets
and Jev evidence remain untouched; a later reconciliation can consume the revision.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.multimodal.contract import digest, write_json
from tools.indexing.multimodal.sources import check_sources
from tools.indexing.multimodal.staging import canonical, db_snapshot
from tools.indexing.multimodal.validation import validate


def load_revision(directory):
    names = ['task.json', 'extraction.json', 'validation.json', 'canonical_candidate.json', 'db_snapshot.json', 'diff.json']
    artifacts = {n: json.loads((directory/n).read_text()) for n in names}
    task, dto = artifacts['task.json'], artifacts['extraction.json']
    check_sources(task)
    checked = validate(dto, task)
    if not checked['valid']:
        raise ValueError('Revision no longer passes local evidence validation')
    candidate, report = canonical(dto, checked, task)
    if not report['valid']:
        raise ValueError('Revision does not pass canonical contract validation')
    old = artifacts['canonical_candidate.json']
    if {k: v for k, v in candidate.items() if k != 'generated_at'} != {k: v for k, v in old.items() if k != 'generated_at'}:
        raise ValueError('Canonical candidate differs from current deterministic assembly')
    return artifacts


def prepare(directory, plan):
    artifacts = load_revision(directory)
    if plan.exists():
        raise ValueError('Review plan already exists')
    write_json(plan, {'version': 1, 'task_directory': str(directory.resolve()),
        'revision_sha256': digest(artifacts), 'approved': False, 'reviewer': '', 'reason': '',
        'effect': 'Store immutable reviewed revision; no replacement of canonical entries or resolved targets'})


def apply(plan_path, db, backup):
    plan = json.loads(plan_path.read_text())
    if plan.get('approved') is not True or not plan.get('reviewer', '').strip() or not plan.get('reason', '').strip():
        raise ValueError('Review plan requires approved=true, reviewer and a concrete review reason')
    artifacts = load_revision(Path(plan['task_directory']))
    revision_hash = digest(artifacts)
    if revision_hash != plan['revision_sha256']:
        raise ValueError('Staging changed after review plan preparation')
    if not db.is_file() or backup.resolve() == db.resolve():
        raise ValueError('Existing DB and separate new backup path required')
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open('xb'):
        pass
    con = sqlite3.connect(db, timeout=30)
    try:
        con.execute('BEGIN IMMEDIATE')
        if db_snapshot(db, artifacts['task.json']) != artifacts['db_snapshot.json']:
            raise ValueError('Database source records changed since staging; rebuild diff and review')
        source = sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True)
        dest = sqlite3.connect(backup)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        con.execute('''CREATE TABLE IF NOT EXISTS alphabetical_reextraction_revisions (
            revision_sha256 TEXT PRIMARY KEY, volume_id TEXT NOT NULL, task_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
            artifacts_json TEXT NOT NULL, backup_path TEXT NOT NULL)''')
        task = artifacts['task.json']
        con.execute('INSERT INTO alphabetical_reextraction_revisions VALUES (?,?,?,?,?,?,?,?)',
            (revision_hash, task['volume'], task['task_id'], datetime.now(timezone.utc).isoformat(),
             plan['reviewer'], plan['reason'], json.dumps(artifacts, ensure_ascii=False), str(backup.resolve())))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return {'stored_revision': revision_hash, 'backup': str(backup), 'canonical_rows_modified': 0,
            'resolutions_modified': 0, 'effect': 'reviewed parallel revision available for later reconciliation'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare-review', type=Path, help='Validated task directory')
    group.add_argument('--apply', type=Path, help='Explicitly reviewed plan JSON')
    p.add_argument('--plan', type=Path)
    p.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    p.add_argument('--backup', type=Path)
    a = p.parse_args()
    try:
        if a.prepare_review:
            if not a.plan:
                p.error('--prepare-review requires --plan')
            prepare(a.prepare_review, a.plan)
            print(json.dumps({'review_plan': str(a.plan), 'db_updates': 0}))
        else:
            if not a.backup:
                p.error('--apply requires --backup')
            print(json.dumps(apply(a.apply, a.db, a.backup)))
    except (ValueError, OSError, sqlite3.Error) as exc:
        p.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()

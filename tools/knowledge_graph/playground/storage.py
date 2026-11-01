"""Isolated SQLite research history; production databases are never writable."""
from __future__ import annotations

import json
import sqlite3
import uuid
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .common import ROOT, digest, dumps, now
from .pricing import run_estimate

DDL = '''
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
 evaluation INTEGER NOT NULL, plan_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
 id INTEGER PRIMARY KEY, snapshot_hash TEXT UNIQUE NOT NULL,
 page TEXT NOT NULL, synthetic INTEGER NOT NULL, split TEXT,
 snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompt_variants (
 id TEXT PRIMARY KEY, variant_id TEXT NOT NULL, mode TEXT NOT NULL,
 system_prompt TEXT NOT NULL, schema_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 case_id INTEGER NOT NULL REFERENCES cases(id), prompt_id TEXT NOT NULL REFERENCES prompt_variants(id),
 repetition INTEGER NOT NULL, input_hash TEXT NOT NULL, config_json TEXT NOT NULL,
 prepared_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned',
 cached_from INTEGER REFERENCES runs(id), result_json TEXT, created_at TEXT NOT NULL,
 started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS run_cache ON runs(input_hash,status);
CREATE INDEX IF NOT EXISTS run_experiment ON runs(experiment_id,id);
CREATE TABLE IF NOT EXISTS run_validation (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
 version TEXT NOT NULL, validation_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), object_id TEXT NOT NULL,
 decision TEXT NOT NULL, dimension TEXT NOT NULL, comment TEXT NOT NULL,
 reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
'''
DECISIONS = {'correct', 'incorrect', 'partially_correct', 'duplicate', 'unsupported', 'uncertain'}
DIMENSIONS = {'concept_correctness', 'redundancy', 'missing_subject', 'attribution', 'stance',
              'interpretation', 'too_broad', 'missing_relation', 'evidence', 'overall'}


class Store:
    def __init__(self, path, protected_paths=()):
        self.path = Path(path).resolve()
        protected = {ROOT / 'data/patristica_resumos.db', ROOT / 'data/patristic_indices.db', ROOT / 'data/ocr_versions.db'}
        protected.update(Path(p).resolve() for p in protected_paths if p)
        if self.path in protected or any(self.path.exists() and p.exists() and self.path.samefile(p) for p in protected):
            raise ValueError('Playground database must be separate from production databases')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse an existing database belonging to another pipeline, even under a new name.
        with self.connect() as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and not {'experiments','cases','prompt_variants','runs','run_validation','reviews'} <= tables:
                raise ValueError('Existing database is not a KG playground database')
            con.executescript(DDL)
            con.execute('PRAGMA journal_mode=WAL')
        self.assets = self.path.parent / (self.path.stem + '_assets')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def case(self, snapshot):
        snap = deepcopy(snapshot)
        image = snap['facsimile']
        if image.get('available'):
            original = Path(image.get('archived_path') or image['path'])
            blob = original.read_bytes()
            if digest(blob) != image['sha256']:
                raise ValueError('Facsimile changed after source loading')
            self.assets.mkdir(exist_ok=True)
            target = self.assets / (image['sha256'] + original.suffix.lower())
            if not target.exists():
                # Workers must never observe a partly written content-addressed image.
                fd, temporary = tempfile.mkstemp(dir=self.assets, prefix='.image-')
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(blob)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            if digest(target.read_bytes()) != image['sha256']:
                raise ValueError('Archived facsimile hash mismatch')
            image['archived_path'] = str(target)
        key = digest(snap)
        s = snap['source']
        with self.connect() as con:
            con.execute('INSERT OR IGNORE INTO cases(snapshot_hash,page,synthetic,split,snapshot_json,created_at) VALUES(?,?,?,?,?,?)',
                        (key, f"{s['documento']}:{s['physical_page']}", bool(s.get('synthetic')), s.get('split'), dumps(snap), now()))
            row = dict(con.execute('SELECT * FROM cases WHERE snapshot_hash=?', (key,)).fetchone())
        row['snapshot'] = json.loads(row.pop('snapshot_json'))
        return row

    def get_case(self, identifier):
        with self.connect() as con:
            row = con.execute('SELECT * FROM cases WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Case not found')
        result = dict(row)
        result['snapshot'] = json.loads(result.pop('snapshot_json'))
        return result

    def prompt(self, prepared):
        key = digest({k: prepared[k] for k in ('system_prompt', 'schema', 'variant', 'mode')})
        with self.connect() as con:
            con.execute('INSERT OR IGNORE INTO prompt_variants VALUES(?,?,?,?,?,?,?)',
                        (key, prepared['variant']['id'], prepared['mode'], prepared['system_prompt'],
                         dumps(prepared['schema']), dumps(prepared['variant']), now()))
        return key

    def experiment(self, name, plan, evaluation=False):
        identifier = 'EXP-' + uuid.uuid4().hex[:12]
        with self.connect() as con:
            con.execute('INSERT INTO experiments VALUES(?,?,?,?,?)', (identifier, name, now(), evaluation, dumps(plan)))
        return identifier

    def save_plan(self, name, plan, tasks):
        """Publish a complete plan atomically, keeping one prepared request in memory."""
        identifier = 'EXP-' + uuid.uuid4().hex[:12]
        with self.connect() as con:
            con.execute('INSERT INTO experiments VALUES(?,?,?,?,?)',
                        (identifier, name, now(), plan['evaluation'], dumps(plan)))
            count = 0
            for case, prepared, repetition in tasks:
                prompt_id = digest({k: prepared[k] for k in ('system_prompt', 'schema', 'variant', 'mode')})
                con.execute('INSERT OR IGNORE INTO prompt_variants VALUES(?,?,?,?,?,?,?)',
                            (prompt_id, prepared['variant']['id'], prepared['mode'], prepared['system_prompt'],
                             dumps(prepared['schema']), dumps(prepared['variant']), now()))
                con.execute('INSERT INTO runs(experiment_id,case_id,prompt_id,repetition,input_hash,config_json,prepared_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (identifier, case['id'], prompt_id, repetition, prepared['input_hash'],
                             dumps(prepared['config']), dumps(prepared), now()))
                count += 1
            if count != plan['size']:
                raise ValueError('Incomplete experiment matrix')
        return identifier

    def add_run(self, experiment, case, prepared, repetition, *, queue_job=None):
        prompt_id = self.prompt(prepared)
        with self.connect() as con:
            identifier = con.execute('INSERT INTO runs(experiment_id,case_id,prompt_id,repetition,input_hash,config_json,prepared_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                     (experiment, case['id'], prompt_id, repetition, prepared['input_hash'], dumps(prepared['config']), dumps(prepared), now())).lastrowid
            if queue_job is not None:
                changed = con.execute('UPDATE corpus_jobs SET run_id=?,updated_at=? WHERE id=? AND campaign_id=?',
                                      (identifier, now(), queue_job, experiment)).rowcount
                if changed != 1:
                    raise ValueError('Missing campaign job')
            return identifier

    def get_run(self, identifier):
        with self.connect() as con:
            row = con.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('Run not found')
            result = dict(row)
            for field in ('config', 'prepared', 'result'):
                raw = result.pop(field + '_json')
                result[field] = json.loads(raw) if raw else None
            result['validation_history'] = [json.loads(v[0]) for v in con.execute('SELECT validation_json FROM run_validation WHERE run_id=? ORDER BY id', (identifier,))]
            result['validation'] = result['validation_history'][-1] if result['validation_history'] else None
            result['reviews'] = [dict(r) for r in con.execute('SELECT * FROM reviews WHERE run_id=? ORDER BY id', (identifier,))]
        result['cost_estimate'] = run_estimate(result)
        return result

    def list_runs(self, experiment=None, *, limit=None, before=None):
        query = ('SELECT r.id,r.experiment_id,r.case_id,r.repetition,r.status,r.cached_from,r.created_at,'
                 'r.config_json,c.page,c.synthetic,c.split,p.variant_id,p.mode '
                 'FROM runs r JOIN cases c ON c.id=r.case_id JOIN prompt_variants p ON p.id=r.prompt_id')
        conditions, params = [], []
        if experiment:
            conditions.append('r.experiment_id=?'); params.append(experiment)
        if before is not None:
            conditions.append('r.id<?'); params.append(before)
        effective_limit = limit if limit is not None else (None if experiment else 10000)
        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        query += ' ORDER BY r.id DESC'
        if effective_limit is not None:
            query += ' LIMIT ?'; params.append(effective_limit)
        with self.connect() as con:
            rows = con.execute(query, params).fetchall()
        return [dict(r, config=json.loads(r['config_json'])) for r in rows]

    def list_experiments(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute('SELECT id,name,created_at,evaluation FROM experiments ORDER BY created_at DESC LIMIT 1000')]

    def experiment_plan(self, identifier):
        with self.connect() as con:
            row = con.execute('SELECT plan_json FROM experiments WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Experiment not found')
        return json.loads(row[0])

    def retried_runs(self, experiment):
        with self.connect() as con:
            return {json.loads(row[0])['retry_of'] for row in con.execute(
                "SELECT prepared_json FROM runs WHERE experiment_id=? AND prepared_json LIKE '%\"retry_of\"%'",
                (experiment,)) if 'retry_of' in json.loads(row[0])}

    def start(self, identifier):
        with self.connect() as con:
            changed = con.execute("UPDATE runs SET status='running',started_at=? WHERE id=? AND status='planned'", (now(), identifier)).rowcount
        if not changed:
            raise ValueError('Run is already started; historical runs cannot be overwritten')

    def finish(self, identifier, status, result, validation=None, cached_from=None):
        with self.connect() as con:
            changed = con.execute("UPDATE runs SET status=?,result_json=?,cached_from=?,finished_at=? WHERE id=? AND status='running'",
                                  (status, dumps(result), cached_from, now(), identifier)).rowcount
            if not changed:
                raise ValueError('Only a running execution can be completed')
            if validation:
                con.execute('INSERT INTO run_validation(run_id,version,validation_json,created_at) VALUES(?,?,?,?)',
                            (identifier, validation['version'], dumps(validation), now()))

    def add_validation(self, identifier, validation):
        with self.connect() as con:
            con.execute('INSERT INTO run_validation(run_id,version,validation_json,created_at) VALUES(?,?,?,?)',
                        (identifier, validation['version'], dumps(validation), now()))

    def cached(self, key, exclude_experiment=None):
        with self.connect() as con:
            row = con.execute("SELECT id FROM runs WHERE input_hash=? AND status='completed' AND (? IS NULL OR experiment_id<>?) ORDER BY id DESC LIMIT 1",
                              (key, exclude_experiment, exclude_experiment)).fetchone()
        return self.get_run(row[0]) if row else None

    def review(self, run_id, object_id, decision, dimension='overall', comment='', reviewer='local'):
        run = self.get_run(run_id)
        parsed = (run['validation'] or {}).get('parsed')
        known = {'page'}
        if isinstance(parsed, dict):
            targets = []
            for group in ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence'):
                if isinstance(parsed.get(group), list):
                    targets.extend((group, o['id']) for o in parsed[group]
                                   if isinstance(o, dict) and isinstance(o.get('id'), str))
            from collections import Counter
            counts = Counter(identifier for _, identifier in targets)
            qualified = Counter(f'{group}/{identifier}' for group, identifier in targets)
            known.update(identifier for identifier, count in counts.items() if count == 1)
            known.update(identifier for identifier, count in qualified.items()
                         if count == 1 and identifier not in counts)
        if object_id not in known or decision not in DECISIONS or dimension not in DIMENSIONS:
            raise ValueError('Invalid review target, decision or dimension')
        if not all(isinstance(v, str) and len(v) <= 10000 for v in (comment, reviewer)):
            raise ValueError('Invalid review text')
        with self.connect() as con:
            return con.execute('INSERT INTO reviews(run_id,object_id,decision,dimension,comment,reviewer,created_at) VALUES(?,?,?,?,?,?,?)',
                               (run_id, object_id, decision, dimension, comment, reviewer, now())).lastrowid

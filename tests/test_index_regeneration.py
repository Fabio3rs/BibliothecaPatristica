import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.audit_index_regeneration import database_digest
from tools.indexing.reproducible_selection import (
    VERSION, compare, freeze, rebuild_page, replay, source_path,
)


def fixture_page(tmp_path):
    root = tmp_path / 'raw'
    path = root / 'PL001/text/index-001.txt'
    path.parent.mkdir(parents=True)
    path.write_text('<bloco tipo="texto">Aaron 15, 18.\nAbel 20.</bloco>')
    row = dict(source='PL001/text/index-001.txt', volume='PL001', physical=1,
               profile='generic-literal-experimental', expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), context=[])
    return root, path, row


def test_freeze_selection_preserves_precedence_without_parsed_results(tmp_path):
    root, path, row = fixture_page(tmp_path)
    database = tmp_path / 'old.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE runs(run_id TEXT,status TEXT);'
                        'CREATE TABLE run_pages(run_id TEXT,source TEXT,volume TEXT,physical INTEGER,profile TEXT,metadata_json TEXT);')
        for run, profile in [('first', 'old'), ('second', 'generic-literal-experimental')]:
            db.execute('INSERT INTO runs VALUES (?,?)', (run, 'completed'))
            db.execute('INSERT INTO run_pages VALUES (?,?,?,?,?,?)',
                       (run, str(path), 'PL001', 1, profile, json.dumps({'source_sha256': row['expected_sha256']})))
    out = tmp_path / 'selection.json'
    freeze(database, ['first', 'second'], root, out)
    spec = json.loads(out.read_text())
    assert len(spec['pages']) == 1
    assert spec['pages'][0]['profile'] == 'generic-literal-experimental'
    # No tasks/candidates table exists: freezing must not depend on old extraction.
    database.unlink()
    assert rebuild_page((spec['pages'][0], root, 'exact'))['status'] in ('parsed', 'no_candidates')


def test_current_source_change_is_preserved_but_exact_replay_abstains(tmp_path):
    root, path, row = fixture_page(tmp_path)
    path.write_text(path.read_text().replace('Aaron', 'Moyses'))
    current = rebuild_page((row, root, 'current'))
    assert current['source_changed'] is True
    assert current['page'] is not None
    assert rebuild_page((row, root, 'exact'))['status'] == 'input_changed'


def test_alias_requires_evidence_and_does_not_assert_historical_identity(tmp_path):
    root, path, row = fixture_page(tmp_path)
    row['source_alias'] = {'source': row['source'], 'evidence': 'reviewed path correction'}
    row['source'] = 'PL001/text/old-001.txt'
    assert rebuild_page((row, root, 'current'))['page'] is not None
    assert rebuild_page((row, root, 'exact'))['status'] == 'input_identity_unverified'
    manifest = tmp_path / 'selection.json'
    manifest.write_text(json.dumps({'version': VERSION, 'pages': [row]}))
    aliases = tmp_path / 'aliases.json'
    aliases.write_text(json.dumps({'aliases': {row['source']: {'source': 'PL001/text/index-001.txt'}}}))
    with pytest.raises(ValueError, match='Alias must have evidence'):
        replay(manifest, root, tmp_path / 'out', aliases_path=aliases)


def test_source_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError, match='escapes'):
        source_path(tmp_path, '../outside.txt')


def test_repeatable_errors_do_not_count_as_verified(tmp_path):
    record = {'status': 'incomplete', 'signatures': [{'source': 'missing', 'status': 'error', 'sha256': 'a'}]}
    paths = [tmp_path / n for n in ('a.json', 'b.json')]
    for p in paths:
        p.write_text(json.dumps(record))
    result = compare(*paths)
    assert result['identical_records']
    assert result['status'] == 'not_verified'


def test_parallel_replay_matches_and_needs_no_database(tmp_path):
    root, path, row = fixture_page(tmp_path)
    other = {**row, 'source': 'PL001/text/index-002.txt', 'physical': 2}
    (root / other['source']).write_bytes(path.read_bytes())
    manifest = tmp_path / 'selection.json'
    manifest.write_text(json.dumps({'version': VERSION, 'pages': [other, row]}))
    dirs = [tmp_path / n for n in ('one', 'two')]
    for workers, out in zip((1, 2), dirs):
        assert replay(manifest, root, out, workers=workers)['status'] == 'completed'
    assert compare(*(p / 'verification.json' for p in dirs))['status'] == 'identical'
    assert (dirs[0] / 'pages.jsonl').read_bytes() == (dirs[1] / 'pages.jsonl').read_bytes()


def test_logical_comparison_detects_ranking_and_evidence_changes(tmp_path):
    a, b = (tmp_path / n for n in ('a.sqlite', 'b.sqlite'))
    for path, ids in [(a, [1, 2]), (b, [2, 1])]:
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE items(id INTEGER PRIMARY KEY, metadata_json TEXT);'
                            'CREATE TABLE candidates(item_id INTEGER,file TEXT,rank INTEGER,candidate_json TEXT);')
            for i in ids:
                db.execute('INSERT INTO items VALUES (?,?)', (i, '{"b":2,"a":1}' if path == a else '{"a":1,"b":2}'))
                db.execute('INSERT INTO candidates VALUES (?,?,?,?)', (i, 'p.txt', 1, '{"method":"literal"}'))
    assert database_digest(a) == database_digest(b)
    with sqlite3.connect(b) as db:
        db.execute('UPDATE candidates SET rank=2 WHERE item_id=1')
    assert database_digest(a) != database_digest(b)
    with sqlite3.connect(b) as db:
        db.execute('UPDATE candidates SET rank=1,candidate_json=? WHERE item_id=1', ('{"method":"approximate"}',))
    assert database_digest(a) != database_digest(b)

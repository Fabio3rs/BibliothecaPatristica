from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.bounded_index_recovery import parse
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.recovered_section_links import reconcile, route_groups, checked_map, run


def recovery_page(tmp_path, text='Victor, 55, 97.'):
    source = tmp_path/'teste/PL058/text/fixture-611.txt'
    source.parent.mkdir(parents=True, exist_ok=True)
    raw = f'<bloco tipo="cabecalho">{text}</bloco>'
    source.write_text(raw)
    spec = {'volume': 'PL058', 'physical': 611, 'section_id': 'test',
            'regions': [{'start_quote': text, 'end_quote': text, 'block_types': ['cabecalho']}]}
    page = parse(raw, spec, str(source))
    page['review'] = {}
    return page


def mapping(number=55, basis='two_reviewed_markers', start=611):
    return {'profile': {'id': 'test-map', 'volume': 'PL058',
                        'source': {'physical_start': start, 'physical_end': start}},
            'map_sha256': 'map-hash',
            'records': {number: {'number': number, 'basis': basis, 'status': 'bounded_window_candidate',
                                'files': [{'physical': 142, 'file': 'target', 'sha256': 'target-hash'}]}}}


def test_reconciliation_preserves_occurrences_and_rejects_overlaps_or_stale(tmp_path):
    page = recovery_page(tmp_path)
    merged, counts = reconcile(page, None)
    assert counts == {'baseline_units': 0, 'added_units': 1}
    assert merged['entries'][0]['entry_raw'] == page['entries'][0]['entry_raw']
    with pytest.raises(ValueError, match='overlaps'):
        reconcile(page, merged)
    stale = deepcopy(page)
    stale['sha256'] = 'changed'
    with pytest.raises(ValueError, match='Stale'):
        reconcile(page, stale)


def test_routes_keep_duplicate_and_diagnostic_separate(tmp_path):
    page = recovery_page(tmp_path)
    route = {'test': {'volume': 'PL058', 'duplicated_numbers': [55]}}
    maps = {'test': [mapping(), mapping(97, 'sequence_probe_not_verified')]}
    rows = route_groups(page, route, maps, set())
    a, b = rows[0]['occurrences']
    assert a['status'] == 'duplicated_number_alternatives_pending'
    assert a['candidate_files']
    assert b['status'] == 'sequence_probe_not_verified'
    assert not b['candidate_files'] and b['diagnostic_files']
    assert all(not o['verified'] and not o['import_ready'] for o in (a, b))


def test_source_conflict_blocks_whole_candidate_before_number_lookup(tmp_path):
    page = recovery_page(tmp_path, 'Abbir, 157. Successus, 73, 151.')
    rows = route_groups(page, {}, {}, {page['entries'][0]['id']})
    assert len(rows) == 2
    assert all(r['status'] == 'source_reading_conflict' and not r['occurrences'] for r in rows)


def test_unsupported_lists_and_remissions_never_expand_to_numeric_targets(tmp_path):
    page = recovery_page(tmp_path, 'Abbas, ibid.\nB., 1-4.\nC. Vid. D.')
    rows = route_groups(page, {'test': {'volume':'PL058','duplicated_numbers':[]}}, {'test': []}, set())
    assert len(rows) == 2
    assert all(r['status'] == 'unsupported_literal_or_inheritance' and not r['occurrences'] for r in rows)


def test_map_scope_cannot_be_replaced_by_numeric_coincidence(tmp_path):
    page = recovery_page(tmp_path)
    routes = {'test': {'volume': 'PL058', 'duplicated_numbers': []}}
    with pytest.raises(ValueError, match='outside map source scope'):
        route_groups(page, routes, {'test': [mapping(start=566)]}, set())
    rows = route_groups(page, {}, {}, set())
    assert rows[0]['status'] == 'unmapped_section'


def test_stale_map_and_unlisted_target_fail(tmp_path):
    folder = tmp_path/'map'
    folder.mkdir()
    source = tmp_path/'target.txt'
    source.write_text('current')
    checksum = digest(source.read_bytes())
    for name, value in {'sources': [[str(source), checksum]], 'proofs': [],
                        'config': {'profile': {'anchors': [], 'proofs': [], 'target': {}}},
                        'map': [{'number':1, 'files':[{'file':str(source),'sha256':checksum}]}]}.items():
        (folder/f'{name}.json').write_text(json.dumps(value))
    checked_map(folder, tmp_path)
    source.write_text('new OCR')
    with pytest.raises(ValueError, match='Stale target map'):
        checked_map(folder, tmp_path)
    source.write_text('current')
    (folder/'map.json').write_text(json.dumps([{'number':1,'files':[{'file':'unlisted','sha256':checksum}]}]))
    with pytest.raises(ValueError, match='outside checked'):
        checked_map(folder, tmp_path)


def test_end_to_end_isolated_relational_supplement(tmp_path):
    page = recovery_page(tmp_path)
    recovery = tmp_path/'recovery'
    recovery.mkdir()
    (recovery/'pages.json').write_text(json.dumps([page]))
    (recovery/'visual_review.json').write_text(json.dumps({'source_findings': []}))
    baseline = tmp_path/'baseline.sqlite'
    with sqlite3.connect(baseline) as db:
        db.executescript('CREATE TABLE runs(run_id,status); INSERT INTO runs VALUES("r","completed"); '
            'CREATE TABLE run_pages(source,run_id,fingerprint); CREATE TABLE tasks(fingerprint,result_json);')
    structured = tmp_path/'prior.sqlite'
    with sqlite3.connect(structured) as db:
        db.execute('CREATE TABLE sources(path,page_json)')
    config = tmp_path/'baseline.json'
    config.write_text(json.dumps({'experiment_db': str(baseline), 'structured_db': str(structured), 'parser_runs':['r']}))
    route = tmp_path/'routes.json'
    route.write_text(json.dumps({'version':'recovered-section-routes-v1', 'sections':{
        'test': {'volume':'PL058', 'duplicated_numbers':[], 'maps':[]}}}))
    before = {p: digest(p.read_bytes()) for p in (baseline, structured, Path(page['source']))}
    out = tmp_path/'out'
    result = run(recovery, config, route, out, tmp_path)
    assert result['units'] == 1 and result['reference_groups'] == 1
    assert result['numeric_occurrences'] == 2
    assert result['occurrence_statuses'] == {'outside_bounded_map': 2}
    with sqlite3.connect(out/'structured/index.sqlite') as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT count(*) FROM recovered_numeric_candidates').fetchone()[0] == 2
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert all(digest(p.read_bytes()) == checksum for p, checksum in before.items())

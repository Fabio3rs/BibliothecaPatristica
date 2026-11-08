import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from tools.indexing.entry_parser_bulk import open_experiment, save, work
from tools.indexing.parser_target_experiment import reference_numbers, locate_number, run, sha


def entry(raw='123, 123, 130', **kwargs):
    return dict(entry_type='material', references_raw=raw, flags=[], **kwargs)


def series():
    pages = {n: {'file': f'page-{n}.txt', 'sha256': 'test', 'header': '999',
                 'occurrences': {10: [{'body_offset': 50}], 11: [{'body_offset': 80}]}}
             for n in range(100, 103)}
    return {'spec': {'id': 'internal'}, 'pages': pages, 'nodes': {10: {100}, 11: {102}},
            'supported': {(10, 100): {'chain_length': 7}, (11, 102): {'chain_length': 7}}}


def test_reference_occurrences_preserved_and_compound_notation_not_flattened():
    assert reference_numbers(entry()) == [123, 123, 130]
    for raw in ['I, 123', '770, 9 Leo 8', '29₈', '123-125', 'ibid', '0']:
        assert reference_numbers(entry(raw)) is None
    e = entry('770, 9')
    e['flags'] = ['unsupported_editorial_namespace']
    assert reference_numbers(e) is None


def test_interval_keeps_all_physical_pages_and_no_header_substitution():
    r = locate_number(series(), 10)
    assert r['status'] == 'bounded_window_candidate'
    assert [f['physical'] for f in r['files']] == [100, 101, 102]
    assert not r['verified']
    assert locate_number(series(), 999)['files'] == []


def test_ambiguity_and_gaps_never_truncated_or_guessed():
    s = series()
    s['pages'][100]['occurrences'][10].append({'body_offset': 200})
    assert locate_number(s, 10)['status'] == 'ambiguous_markers'
    s = series()
    del s['pages'][101]
    assert locate_number(s, 10)['status'] == 'missing_intermediate_file'
    assert locate_number(series(), 10, 1)['status'] == 'window_exceeds_cap'


def test_same_page_reversed_offsets_rejected():
    s = series()
    s['nodes'][11] = {100}
    s['supported'][11, 100] = {'chain_length': 7}
    s['pages'][100]['occurrences'][11] = [{'body_offset': 20}]
    assert locate_number(s, 10)['status'] == 'reversed_markers'


def test_editorial_order_overrides_physical_order_without_dropping_files():
    s = series()
    s['nodes'][10] = {102}
    s['nodes'][11] = {100}
    s['supported'] = {(10, 102): {}, (11, 100): {}}
    # Nonempty evidence is required by marker_options.
    s['supported'] = {k: {'basis': 'reviewed'} for k in s['supported']}
    assert locate_number(s, 10)['status'] == 'reversed_markers'
    s['page_order'] = [102, 101, 100]
    r = locate_number(s, 10)
    assert [f['physical'] for f in r['files']] == [102, 101, 100]
    assert locate_number(s, 10, max_file_gap=1)['status'] == 'window_exceeds_cap'
    s['page_order'] = [102, 100]
    with pytest.raises(ValueError, match='cover each source'):
        locate_number(s, 10)


def test_closed_range_uses_boundary_after_last_page_and_never_truncates():
    s = series()
    s['nodes'][13] = {102}
    s['supported'][13, 102] = {'chain_length': 7}
    s['pages'][102]['occurrences'][13] = [{'body_offset': 100}]
    result = locate_number(s, 10, range_end=12)
    assert result['end_boundary_number'] == 13
    assert result['status'] == 'bounded_window_candidate'
    assert len(result['files']) == 3
    assert locate_number(s, 10, range_end=13)['status'] == 'missing_supported_boundary'
    assert locate_number(s, 10, 1, range_end=12)['files'] == []
    with pytest.raises(ValueError, match='Reversed'):
        locate_number(s, 10, range_end=9)


def fixture(tmp_path):
    root = tmp_path / 'teste'
    text = root / 'PG069' / 'text'
    text.mkdir(parents=True)
    for i in range(1, 10):
        (text/f'page-{i}.txt').write_text(
            f'<bloco tipo="cabecalho">999</bloco><bloco tipo="texto_principal" script="latino">{i} Body text.</bloco>')
    notice = 'Internal numbers, not headings.'
    raw = f'<bloco tipo="cabecalho">{notice}</bloco><bloco tipo="texto_principal">Rachel formosa, 4, 4.</bloco>'
    source = text/'page-740.txt'
    source.write_text(raw)
    db = tmp_path/'experiment.sqlite'
    production = tmp_path/'production.sqlite'
    production.write_bytes(b'untouched')
    con = open_experiment(db, production)
    con.execute("INSERT INTO runs VALUES('parser','now','{}','completed')")
    con.commit()
    task = {'fingerprint': 'fp', 'volume': 'PG069', 'physical': 740, 'source': str(source),
            'profile': 'pg069', 'old_rows': [], 'raw': raw}
    save(con, 'parser', task, work(task))
    con.close()
    profile = {'volume': 'PG069', 'method': 'body_sequence', 'notation': 'plain_page_list',
               'parser_profile': 'pg069', 'index_physical_start': 740, 'index_physical_end': 743,
               'notice': {'path': 'PG069/text/page-740.txt', 'quote': notice, 'sha256': sha(source.read_bytes())},
               'series': {'id': 'test', 'volume': 'PG069', 'physical_start': 1, 'physical_end': 9}}
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(profile))
    args = argparse.Namespace(profile=path, root=root, experiment_db=db, parser_run='parser',
                              limit=20, seed=42, include_term=[], max_file_gap=3, out=tmp_path/'out')
    return args, source, production


def test_end_to_end_separate_experiment_tables_and_repeat_occurrences(tmp_path):
    args, source, production = fixture(tmp_path)
    result = run(args)
    assert result['reference_occurrences'] == 2
    assert result['reference_statuses'] == {'bounded_window_candidate': 2}
    assert production.read_bytes() == b'untouched'
    con = sqlite3.connect(args.experiment_db)
    assert con.execute('SELECT COUNT(*) FROM parser_locator_results').fetchone()[0] == 1
    assert con.execute('SELECT status FROM runs').fetchone()[0] == 'completed'
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    con.close()
    from tools.indexing.parser_target_exports import validate_job_sources
    jobs = [json.loads(line) for line in (args.out/'semantic_candidates.jsonl').read_text().splitlines()]
    maps = json.loads((args.out/'editorial_maps.json').read_text())
    assert len(jobs) == 2
    assert jobs[0]['job_id'] != jobs[1]['job_id']
    assert jobs[0]['map_id'] == maps['default']['map_id']
    assert len(jobs[0]['candidate_files']) == 2
    assert jobs[0]['gold_label'] is None
    assert not jobs[0]['verified'] and not jobs[0]['application_supported']
    validate_job_sources(jobs[0])
    target = Path(jobs[0]['candidate_files'][0]['file'])
    target.write_text(target.read_text()+'changed')
    with pytest.raises(ValueError, match='Stale'):
        validate_job_sources(jobs[0])


def test_stale_notice_refused_before_writing(tmp_path):
    args, source, _ = fixture(tmp_path)
    source.write_text(source.read_text()+'changed')
    with pytest.raises(ValueError, match='notice'):
        run(args)
    assert not args.out.exists()


def test_stale_other_index_source_refused(tmp_path):
    args, source, _ = fixture(tmp_path)
    source.write_text(source.read_text()+'changed')
    profile = json.loads(args.profile.read_text())
    profile['notice']['sha256'] = sha(source.read_bytes())
    args.profile.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match='Stale parser'):
        run(args)


def test_duplicate_physical_files_refused(tmp_path):
    args, _, _ = fixture(tmp_path)
    (args.root/'PG069/text/duplicate-4.txt').write_text('other')
    with pytest.raises(ValueError, match='Ambiguous physical'):
        run(args)


def test_maps_are_stable_and_ambiguous_occurrences_only_enter_pending(tmp_path):
    from tools.indexing.parser_target_exports import editorial_maps, review_queues
    args, _, _ = fixture(tmp_path)
    run(args)
    records = json.loads((args.out/'results.json').read_text())
    maps = json.loads((args.out/'editorial_maps.json').read_text())
    records[0]['references'][0]['status'] = 'ambiguous_markers'
    jobs, pending = review_queues(records, maps)
    assert len(jobs) == len(pending) == 1
    assert pending[0]['reference']['status'] == 'ambiguous_markers'
    assert jobs[0]['query_context_status'] == 'unverified'
    assert jobs[0]['reference']['source_span'] != pending[0]['reference']['source_span']
    s = series()
    s.update(method='body_sequence', sources=[['path', 'hash']])
    a = editorial_maps({'x': s}, {'x': {}})
    assert a == editorial_maps({'x': s}, {'x': {}})
    s['sources'][0][1] = 'changed'
    assert a['x']['map_id'] != editorial_maps({'x': s}, {'x': {}})['x']['map_id']


def test_unsupported_entry_is_preserved_in_notation_pending(tmp_path):
    from tools.indexing.parser_target_experiment import load_candidates
    args, _, _ = fixture(tmp_path)
    profile = json.loads(args.profile.read_text())
    profile['notation'] = 'explicit_tome_list'
    pending = []
    candidates, counts, _ = load_candidates(args.experiment_db, 'parser', profile, pending)
    assert candidates == []
    assert counts['entry_or_notation_requires_review'] == len(pending) == 1
    assert pending[0]['entry']['entry_raw'] == 'Rachel formosa, 4, 4.'
    assert pending[0]['source_sha256']

"""Literal reconciliation must not hide changed digits or competing ownership."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from test_multimodal_reextraction import make_sources
from tools.indexing.multimodal.sources import prepare_task
from tools.indexing.multimodal.contract import digest, write_json
from tools.indexing.multimodal.literal_reconcile import reconcile, reconcile_run, unique_chain


def entry(text, name='Attilas', refs='34. 40.'):
    return {'type': 'entry', 'text': text, 'name': name, 'references_raw': refs,
            'children': [], 'continuation': 'none'}


def setup(tmp_path, ocr, entries):
    root, _ = make_sources(tmp_path, text=ocr)
    task = prepare_task(root, 'PG113', 1, tmp_path/'prep', 'ocr-image', 0, 'never')
    task['extraction_profile'] = 'literal-v1'
    return task, {'elements': entries, 'uncertainties': []}


def test_discontinuous_references_with_repeats_have_distinct_spans(tmp_path):
    text = 'Attilas rex mittit, 34. Honorium poscit, 40. Honorium recipit, 40.'
    task, dto = setup(tmp_path, text, [entry(text, refs='34. 40. 40.')])
    before = deepcopy(dto)
    result = reconcile(dto, task)
    row = result['entries'][0]
    assert row['anchor']['status'] == 'anchored'
    refs = row['references_raw_alignment']
    assert refs['status'] == 'aligned'
    assert refs['mode'] == 'discontinuous_numeric_groups'
    assert [s['ocr_literal'] for s in refs['segments']] == ['34.', '40.', '40.']
    assert len({s['char_start'] for s in refs['segments']}) == 3
    assert all(s['evidence'][0]['quote'] == s['ocr_literal'] for s in refs['segments'])
    assert dto == before and not result['import_ready']
    assert result['counts']['entries_with_discontinuous_groups_recovered'] == 1


def test_ambiguous_repeated_number_is_not_assigned_arbitrarily(tmp_path):
    text = 'Attilas mittit, 34. Recipit, 34.'
    task, dto = setup(tmp_path, text, [entry(text, refs='34.')])
    row = reconcile(dto, task)['entries'][0]
    assert row['references_raw_alignment']['status'] == 'ambiguous'


def test_name_is_located_only_in_own_entry(tmp_path):
    text = 'Roma, 34.\nRoma, civitas magna, 40.'
    task, dto = setup(tmp_path, text, [entry('Roma, civitas magna, 40.', 'Roma', '40.')])
    row = reconcile(dto, task)['entries'][0]
    assert row['name_alignment']['status'] == 'aligned'
    assert row['name_alignment']['segments'][0]['char_start'] == row['anchor']['char_start']


@pytest.mark.parametrize('ocr, ref', [('180.', '18.'), ('45₁₃.', '4513.'),
                                   ('12–15.', '12-15.'), ('12–15.', '12. 13. 14. 15.')])
def test_numeric_and_typographic_changes_never_verified(tmp_path, ocr, ref):
    text = 'Roma, ' + ocr
    task, dto = setup(tmp_path, text, [entry(text, 'Roma', ref)])
    assert reconcile(dto, task)['entries'][0]['references_raw_alignment']['status'] != 'aligned'


@pytest.mark.parametrize('ref', ['Leo 770, 9', 'I, 757; II, 547', '45₁₃', '12–15',
                                '12 seqq.; ibid.; 17 not. a'])
def test_editorial_notation_is_preserved_without_parsing(tmp_path, ref):
    text = 'Roma, ' + ref
    task, dto = setup(tmp_path, text, [entry(text, 'Roma', ref)])
    row = reconcile(dto, task)['entries'][0]
    assert row['references_raw_alignment']['status'] == 'aligned'
    assert row['references_raw_alignment']['segments'][0]['ocr_literal'] == ref
    assert 'page_start' not in row


def test_only_proven_alphabetic_line_wrap_is_joined(tmp_path):
    task, dto = setup(tmp_path, 'Corin-\nthios, 18-\n20.',
                      [entry('Corinthios, 18-20.', 'Corinthios', '18-20.')])
    row = reconcile(dto, task)['entries'][0]
    assert row['anchor']['status'] == 'anchored'
    assert 'ocr_alphabetic_line_wrap' in row['anchor']['segments'][0]['normalizations']
    assert row['references_raw_alignment']['segments'][0]['ocr_literal'] == '18-\n20.'
    dto['elements'][0]['text'] = 'Corinthios, 1820.'
    assert reconcile(dto, task)['entries'][0]['anchor']['status'] == 'unanchored'


def test_long_entry_partial_alignment_records_differences(tmp_path):
    text = ('Attilas Hunnorum rex litteras ad Theodosium mittit atque recipit, 34. '
            'Honorium poscit cum exercitu suo, 40. '
            'Filii ejus ad urbem veniunt atque pacem omnibus civibus offerunt, 72.')
    changed = text.replace('40.', '48.')
    task, dto = setup(tmp_path, text, [entry(changed, refs='34. 48. 72.')])
    row = reconcile(dto, task)['entries'][0]
    assert row['anchor']['status'] == 'partial'
    assert any('48' in d['query_tokens'] and '40' in d['ocr_tokens'] for d in row['anchor']['differences'])
    assert row['references_raw_alignment']['status'] == 'unmapped'
    assert all('48' not in s['ocr_literal'] for s in row['anchor']['segments'])


def test_roman_range_and_unproven_lexical_hyphen_are_not_joined(tmp_path):
    text = 'Tomus I-\nII. Franco-Byzantinus, 19.'
    task, dto = setup(tmp_path, text, [entry('Tomus III.', 'Tomus', 'III.'),
                                     entry('FrancoByzantinus, 19.', 'FrancoByzantinus', '19.')])
    assert all(e['anchor']['status'] == 'unanchored' for e in reconcile(dto, task)['entries'])


def test_overlapping_entries_both_remain_in_conflict(tmp_path):
    text = 'Roma magna, 12. Athenae clarae, 14.'
    task, dto = setup(tmp_path, text, [entry(text, 'Roma', '12. 14.'),
                                     entry('Athenae clarae, 14.', 'Athenae', '14.')])
    result = reconcile(dto, task)
    assert all(row['conflicts'] for row in result['entries'])
    assert result['counts'].get('entries_with_anchored_reference_groups', 0) == 0
    assert result['coverage']['entry_evidence_alphanumeric_chars'] == 0


def test_chain_requires_unique_ordered_nonoverlapping_assignment():
    assert unique_chain([[(1, 2), (3, 4)], [(3, 4)]]) == ('aligned', [(1, 2), (3, 4)])
    assert unique_chain([[(1, 2), (3, 4)], [(5, 6)]])[0] == 'ambiguous'
    assert unique_chain([[(3, 4)], [(1, 2)]])[0] == 'not_found'
    assert unique_chain([[(1, 3)], [(2, 4)]])[0] == 'not_found'


def archive(tmp_path):
    text = 'Attilas mittit, 34. Recipit, 40.'
    task, dto = setup(tmp_path, text, [entry(text)])
    run = tmp_path/'run'
    folder = run/'tasks'/'page'
    write_json(folder/'task.json', task)
    write_json(folder/'response.json', {'status': 'received', 'dto': dto})
    write_json(folder/'checkpoint.json', {'input_fingerprint': 'test',
                                          'response_sha256': digest((folder/'response.json').read_bytes())})
    write_json(run/'manifest.json', {'tasks': [{'directory': str(folder), 'input_fingerprint': 'test'}]})
    return run, folder


def test_offline_run_no_http_no_db_and_originals_unchanged(tmp_path, monkeypatch):
    import requests
    run, folder = archive(tmp_path)
    before = {str(p): p.read_bytes() for p in run.rglob('*.json')}
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('HTTP must not be called'))
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: pytest.fail('DB must not be opened'))
    report = reconcile_run(run, tmp_path/'out')
    assert report['calls'] == report['db_updates'] == 0
    assert report['counts']['entries_with_discontinuous_groups_recovered'] == 1
    assert before == {str(p): p.read_bytes() for p in run.rglob('*.json')}
    with pytest.raises(ValueError):
        reconcile_run(run, run/'nested')
    with pytest.raises(ValueError):
        reconcile_run(run, tmp_path/'out')


@pytest.mark.parametrize('corruption', ['response', 'ocr', 'schema'])
def test_corrupt_source_or_response_is_not_reconciled(tmp_path, corruption):
    run, folder = archive(tmp_path)
    if corruption == 'ocr':
        path = Path(json.loads((folder/'task.json').read_text())['pages'][0]['ocr_file'])
        path.write_text('changed')
    elif corruption == 'response':
        (folder/'response.json').write_text('{}')
    else:
        write_json(folder/'response.json', {'status': 'received', 'dto': {}})
        write_json(folder/'checkpoint.json', {'input_fingerprint': 'test',
                                              'response_sha256': digest((folder/'response.json').read_bytes())})
    report = reconcile_run(run, tmp_path/'out')
    assert report['statuses'] == {'source_or_response_error': 1}
    assert not (tmp_path/'out/tasks').exists()

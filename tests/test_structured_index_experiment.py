from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest

from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.structured_index_experiment import (
    SELECTION_VERSION, consolidate, readonly, search,
)


def fixture(tmp_path):
    source = tmp_path / 'teste/PG001/text/page-1.txt'
    source.parent.mkdir(parents=True)
    raw = ('Oculi sapientis, I, 294. Oculi distorti quid, 594.\n'
           'Alpha, 7.\nAlpha, 7.\nFragmentum sine numero')
    source.write_text(raw)
    entries = []
    offset = 0
    for n, line in enumerate(raw.splitlines()[:3]):
        start, end = offset, offset+len(line)
        groups = []
        clauses = [('Oculi sapientis', 'I, 294.'), ('Oculi distorti quid', '594.')] if n == 0 else [('Alpha', '7.')]
        cursor = start
        for owner, ref in clauses:
            oa = raw.index(owner, cursor); ra = raw.index(ref, oa+len(owner))
            groups.append({'raw': ref, **source_span(raw, ra, ra+len(ref)),
                           'owner_text_raw': owner, 'owner_span': source_span(raw, oa, oa+len(owner)),
                           'namespace_status': 'literal_uninterpreted', 'inheritance_status': 'not_inferred'})
            cursor = ra+len(ref)
        entries.append({'id': f'old-{n}', 'source': str(source), 'physical_page': 1,
                        'entry_raw': line, **source_span(raw, start, end),
                        'reference_groups': groups, 'section_candidate': 's1',
                        'text_display': clauses[0][0], 'unit_kind': 'ocr_paragraph_group',
                        'context_candidate_id': None, 'flags': [],
                        'quality': {'segmentation': 'candidate', 'destination': 'not_attempted'}})
        offset = end+1
    page = {'source': str(source), 'physical_page': 1, 'sha256': digest(raw.encode()),
            'entries': entries, 'residuals': [{'raw': raw[offset:], **source_span(raw, offset, len(raw)),
                                              'reason': 'fragment'}], 'events': []}
    artifact = tmp_path/'page.json'; artifact.write_text(json.dumps(page))
    selection = {'version': SELECTION_VERSION, 'sources': [
        {'source': str(source), 'sha256': page['sha256'], 'physical': 1,
         'artifact': str(artifact), 'units': 3}]}
    manifest = tmp_path/'selection.json'; manifest.write_text(json.dumps(selection))
    return source, raw, page, artifact, selection, manifest


def test_relational_export_search_preserves_later_clauses_and_repeated_occurrences(tmp_path):
    source, raw, page, artifact, selection, manifest = fixture(tmp_path)
    before = source.read_bytes(), artifact.read_bytes()
    out = tmp_path/'output'
    counts = consolidate(manifest, out, tmp_path, 3, 42)
    assert (counts['units'], counts['clauses'], counts['reference_groups'], counts['residuals']) == (3, 4, 4, 1)
    assert counts['publication_ready_count'] is None
    hits = search(out/'index.sqlite', '"Oculi distorti"')
    assert len(hits) == 1
    assert hits[0]['reference_groups'] == ['594.']
    assert 'Oculi sapientis' in hits[0]['group_text']
    assert hits[0]['publication_status'] == 'review_required'
    assert len(search(out/'index.sqlite', 'Alpha')) == 2
    assert not search(out/'index.sqlite', 'Alpha', volume='PL217')
    assert 'distorti' in search(out/'index.sqlite', 'distorti', scope='units')[0]['matched_text']
    docs = [json.loads(line) for line in (out/'search_documents.jsonl').read_text().splitlines()]
    assert docs[0]['search_text'].endswith('Oculi distorti quid, 594.')
    assert docs[0]['source']['ocr_path'] == 'teste/PG001/text/page-1.txt'
    assert docs[0]['destination_links'] == []
    assert docs[0]['quality']['destination'] == 'not_attempted'
    with closing(readonly(out/'index.sqlite')) as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        assert con.execute('SELECT title FROM sections').fetchone()[0] is None
        assert con.execute('SELECT count(*) FROM clause_reference_rows').fetchone()[0] == 4
    assert before == (source.read_bytes(), artifact.read_bytes())


def test_ids_and_sample_stable_across_outputs_and_input_ids_not_used_for_identity(tmp_path):
    _, _, page, artifact, _, manifest = fixture(tmp_path)
    consolidate(manifest, tmp_path/'a', tmp_path, 2, 73)
    for e in page['entries']:
        e['id'] += '-new-parser'
    artifact.write_text(json.dumps(page))
    consolidate(manifest, tmp_path/'b', tmp_path, 2, 73)
    a = json.loads((tmp_path/'a/sample.json').read_text())
    b = json.loads((tmp_path/'b/sample.json').read_text())
    assert a == b


@pytest.mark.parametrize('bad', ['stale', 'duplicate_page', 'physical', 'count', 'ambiguous_input'])
def test_invalid_selection_rejected_before_creating_output(tmp_path, bad):
    source, _, _, _, selection, manifest = fixture(tmp_path)
    if bad == 'stale': source.write_text('changed source')
    if bad == 'duplicate_page': selection['sources'] *= 2
    if bad == 'physical': selection['sources'][0]['physical'] = 2
    if bad == 'count': selection['sources'][0]['units'] = 9
    if bad == 'ambiguous_input': selection['sources'][0]['db'] = 'another.db'
    manifest.write_text(json.dumps(selection))
    with pytest.raises(ValueError): consolidate(manifest, tmp_path/'out', tmp_path)
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('bad', ['entry_span', 'owner_span', 'duplicate_ref', 'overlap', 'unknown_contract', 'residual_overlap'])
def test_bad_provenance_rolls_back_all_rows(tmp_path, bad):
    _, _, page, artifact, selection, manifest = fixture(tmp_path)
    e = page['entries'][0]
    if bad == 'entry_span': e['entry_raw'] += ' invented'
    if bad == 'owner_span': e['reference_groups'][0]['owner_text_raw'] = 'invented'
    if bad == 'duplicate_ref': e['reference_groups'].append(deepcopy(e['reference_groups'][0]))
    if bad == 'overlap':
        page['entries'].append({**deepcopy(e), 'id': 'another'})
        selection['sources'][0]['units'] = 4
    if bad == 'unknown_contract': del e['reference_groups']
    if bad == 'residual_overlap':
        page['residuals'][0] = {**e, 'raw': e['entry_raw'], 'reason': 'overlap'}
    artifact.write_text(json.dumps(page)); manifest.write_text(json.dumps(selection))
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        consolidate(manifest, tmp_path/'out', tmp_path)
    with closing(readonly(tmp_path/'out/index.sqlite')) as con:
        assert con.execute('SELECT count(*) FROM units').fetchone()[0] == 0
    assert (tmp_path/'out/FAILED.txt').exists()
    with pytest.raises(ValueError): search(tmp_path/'out/index.sqlite', 'Alpha')


def test_database_run_selected_readonly_and_explicit_replacement(tmp_path):
    source, raw, page, artifact, selection, manifest = fixture(tmp_path)
    db = tmp_path/'input.sqlite'
    with sqlite3.connect(db) as con:
        con.executescript('''CREATE TABLE runs(run_id,status);
            CREATE TABLE tasks(fingerprint,result_json);
            CREATE TABLE run_pages(run_id,source,fingerprint);''')
        con.executemany('INSERT INTO runs VALUES(?,?)', [('old','completed'), ('current','completed')])
        old = deepcopy(page); old['entries'] = []
        for run, data in [('old',old),('current',page)]:
            con.execute('INSERT INTO tasks VALUES(?,?)', (run,json.dumps({'page':data})))
            con.execute('INSERT INTO run_pages VALUES(?,?,?)', (run,str(source),run))
    before = db.read_bytes()
    s = selection['sources'][0]; del s['artifact']; s.update(db=str(db),run_id='current')
    manifest.write_text(json.dumps(selection))
    assert consolidate(manifest,tmp_path/'out',tmp_path)['units'] == 3
    assert db.read_bytes() == before
    with pytest.raises(FileExistsError): consolidate(manifest,tmp_path/'out',tmp_path)


def test_continuation_evidence_is_deduplicated_but_not_assembled(tmp_path):
    source, raw, page, artifact, selection, manifest = fixture(tmp_path)
    e = page['entries'][0]
    parts = [{'raw': g['owner_text_raw'], **g['owner_span']} for g in e['reference_groups']]
    link = {'source':str(source),'source_sha256':page['sha256'], 'applied':False,
            'left':parts[0],'right':parts[1],'status':'requires_review'}
    page['fragment_links'] = [link]; artifact.write_text(json.dumps(page))
    review = tmp_path/'links.json'; review.write_text(json.dumps([{**link,'visual_review':{'status':'supported'}}]))
    selection['continuation_artifacts'] = [str(review)]; manifest.write_text(json.dumps(selection))
    counts = consolidate(manifest,tmp_path/'out',tmp_path)
    assert counts['continuations'] == 1 and counts['continuation_fragments'] == 2
    assert counts['units'] == 3
    with closing(readonly(tmp_path/'out/index.sqlite')) as con:
        row = con.execute('SELECT * FROM continuations').fetchone()
        assert row['applied'] == 0 and len(json.loads(row['evidence_json'])) == 2


def test_explicit_parent_is_retained_as_candidate_only(tmp_path):
    _, _, page, artifact, _, manifest = fixture(tmp_path)
    page['entries'][1]['context_candidate_id'] = page['entries'][0]['id']
    page['entries'][2]['context_candidate_id'] = 'missing'
    artifact.write_text(json.dumps(page))
    consolidate(manifest,tmp_path/'out',tmp_path)
    with closing(readonly(tmp_path/'out/index.sqlite')) as con:
        rows = con.execute('SELECT context_status,parent_candidate_id FROM units ORDER BY ordinal').fetchall()
        assert rows[0]['context_status'] == 'not_assessed'
        assert rows[1]['context_status'] == 'candidate_relation_unverified'
        assert rows[1]['parent_candidate_id'] is not None
        assert rows[2]['context_status'] == 'unresolved_candidate_relation'


def test_unstructured_text_inside_a_group_stays_searchable_and_is_reported(tmp_path):
    _, _, page, artifact, _, manifest = fixture(tmp_path)
    page['entries'][0]['reference_groups'] = page['entries'][0]['reference_groups'][:1]
    artifact.write_text(json.dumps(page))
    counts = consolidate(manifest,tmp_path/'out',tmp_path)
    assert counts['units_with_unassigned_text'] == 1
    assert counts['unit_unassigned_text'] == 1
    assert not search(tmp_path/'out/index.sqlite', 'distorti')
    assert len(search(tmp_path/'out/index.sqlite', 'distorti', scope='units')) == 1
    docs = [json.loads(line) for line in (tmp_path/'out/search_documents.jsonl').read_text().splitlines()]
    assert docs[0]['unassigned_text'][0]['literal'] == 'Oculi distorti quid, 594.'


def test_stale_cross_page_continuation_cannot_be_imported(tmp_path):
    source, _, page, artifact, _, manifest = fixture(tmp_path)
    g = page['entries'][0]['reference_groups'][0]
    fragment = {'source':str(source),'source_sha256':'stale',
                'raw':g['owner_text_raw'], **g['owner_span']}
    page['fragment_links'] = [{'fragments':[fragment], 'applied':False}]
    artifact.write_text(json.dumps(page))
    with pytest.raises(ValueError,match='stale'):
        consolidate(manifest,tmp_path/'out',tmp_path)


def test_readonly_missing_database_is_not_created(tmp_path):
    _, _, _, _, selection, manifest = fixture(tmp_path)
    selection['sources'][0].pop('artifact')
    db = tmp_path/'missing.sqlite'
    selection['sources'][0].update(db=str(db),run_id='absent')
    manifest.write_text(json.dumps(selection))
    with pytest.raises(sqlite3.OperationalError): consolidate(manifest,tmp_path/'out',tmp_path)
    assert not db.exists() and not (tmp_path/'out').exists()

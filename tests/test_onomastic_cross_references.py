from tools.indexing.onomastic_cross_references import build_edges, name_matches, parse_remission, resolve


def entry(name, cross=None, section='authors', physical=1, year=None):
    data = dict(owner_raw=name, owner_span=[0, len(name)], source='/source', section_id=section, physical=physical)
    if cross:
        data['textual_cross_reference'] = dict(raw=cross, source='/source', span=[100, 100+len(cross)])
    if year:
        data['biographical_date_evidence'] = {'raw': year}
    return data


def base(file='page.txt'):
    return {'file': file, 'score': .90, 'method': 'numeric', 'evidence': [{'column': 5}]}


def test_normalization_is_not_fuzzy_or_substring():
    assert name_matches('Caelius', 'Cælius (S.) episcopus')
    assert name_matches('Sylvester II papa', 'Sylvester II papa')
    assert not name_matches('Leo', 'Leonardus')
    assert not name_matches('Gilbertus', 'Gislebertus')
    assert not name_matches('Joannes', 'Petrus Joannes')


def test_numeric_vide_is_not_a_name():
    assert parse_remission('vide CXC, 1459; CC, 1145.')['reason'] == 'numeric_vide_not_name_remission'


def test_truncation_and_unsupported_qualifiers_abstain():
    for value in ('vide Fortu-', 'vide Joannes ad', 'vide Joannes 1299', 'vide Joannes; extra'):
        assert not parse_remission(value)['targets']


def test_list_and_year():
    assert parse_remission('vide Calo, Evilo, Guido, Jacobus.')['targets'] == ['Calo', 'Evilo', 'Guido', 'Jacobus']
    assert parse_remission('vide Joannes, an. 1299.')['year'] == '1299'


def test_homonyms_preserved_and_other_sections_excluded():
    rows = {'a': entry('Cleris Hugo', 'vide Hugo.'), 'b': entry('Hugo de Cleris'), 'c': entry('Hugo abbas'), 'd': entry('Hugo rex', section='other')}
    edges, _ = build_edges(rows)
    assert [e['target_item_id'] for e in edges['a']] == ['b', 'c']
    candidates, _ = resolve('a', edges, {'b': [base('b')], 'c': [base('c')]})
    assert [c['file'] for c in candidates] == ['b', 'c']
    assert candidates[0]['score'] > candidates[1]['score']
    assert 'multiple_name_matches_preserved' in candidates[0]['uncertainties']


def test_supra_and_year_restrict_scope():
    rows = {'a': entry('Alias', 'vide supra Joannes, an. 1299.', physical=3),
            'b': entry('Joannes', physical=2, year='1299'), 'c': entry('Joannes', physical=4, year='1299'),
            'd': entry('Joannes', physical=2, year='1298')}
    assert [e['target_item_id'] for e in build_edges(rows)[0]['a']] == ['b']


def test_chain_inherits_evidence_and_cycles_stop():
    rows = {'a': entry('Alpha', 'vide Beta.'), 'b': entry('Beta', 'vide Gamma.'), 'c': entry('Gamma', 'vide Alpha.')}
    edges, _ = build_edges(rows)
    candidates, issues = resolve('a', edges, {'c': [base()]})
    assert len(candidates) == 1
    assert len(candidates[0]['evidence'][0]['chain']) == 2
    assert candidates[0]['evidence'][0]['inherited_candidate']['evidence'] == [{'column': 5}]
    assert 'remission_cycle' in issues


def test_same_file_retains_multiple_terminal_occurrences():
    rows = {'a': entry('Alias', 'vide Hugo.'), 'b': entry('Hugo abbas'), 'c': entry('Hugo rex')}
    candidates, _ = resolve('a', build_edges(rows)[0], {'b': [base()], 'c': [base()]})
    assert len(candidates) == 1 and len(candidates[0]['evidence']) == 2


def test_no_self_match_and_no_existing_inherited_base():
    rows = {'a': entry('Hugo', 'vide Hugo.'), 'b': entry('Hugo abbas')}
    from tools.indexing.onomastic_cross_references import VERSION
    edges, _ = build_edges(rows)
    assert [e['target_item_id'] for e in edges['a']] == ['b']
    assert resolve('a', edges, {'b': [{**base(), 'method': VERSION}]})[0] == []


def test_literal_soft_wrap_and_ad_year():
    assert parse_remission('vide Fortu-\n natus.')['targets'] == ['Fortunatus']
    assert parse_remission('vide Joannes ad\n an. 990.')['year'] == '990'


def test_concatenated_owners_are_not_inherited():
    rows = {'a': entry('Pseudo-Isidorus', 'vide Isidorus Mercator.'),
            'b': entry('Isidorus Mercator, auctor incertus, CX.X.\n Iso, monachus')}
    edges, diagnostic = build_edges(rows)
    assert edges['a'] == []
    assert diagnostic['a']['reason'] == 'target_owner_segmentation_suspect'


def test_readonly_campaign_and_additive_overlay(tmp_path):
    import hashlib
    import json
    import sqlite3
    from tools.indexing.onomastic_cross_references import run
    source = tmp_path/'source.txt'
    source.write_text('Alias, vide Beta.\nBeta, XX, 1.')
    destination = tmp_path/'target.txt'
    destination.write_text('Target OCR')
    extraction, catalog = tmp_path/'extract.sqlite', tmp_path/'catalog.sqlite'
    entries = [('a', 'Alias', 'vide Beta.', [0, 5], [7, 17]), ('b', 'Beta', 'XX, 1.', [18, 22], [24, 30])]
    for path in (extraction, catalog):
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE items(id TEXT,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT); CREATE TABLE candidates(item_id TEXT,file TEXT,rank INT,score REAL,navigable INT,candidate_json TEXT);')
            for ident, owner, ref, osp, rsp in entries:
                # Calculate evidence spans from literal data, independent of fixture offsets.
                osp = [source.read_text().index(owner), source.read_text().index(owner)+len(owner)]
                rsp = [source.read_text().index(ref), source.read_text().index(ref)+len(ref)]
                item = dict(owner_raw=owner, owner_span=osp, reference_raw=ref, reference_span=rsp, source=str(source), section_id='authors')
                if ident == 'a': item['textual_cross_reference'] = {'raw': ref, 'span': rsp, 'source': str(source)}
                db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', (ident, ident, 'PL218', owner, ref, str(source), 'source_only', json.dumps(item)))
            if path == catalog:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)', ('b', str(destination), 1, .9, 1, json.dumps(base(str(destination)))))
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (extraction, catalog, source)}
    result = run(extraction, catalog, tmp_path/'result', section='authors')
    assert result['counts']['newly_located_items'] == 1
    assert result['counts']['new_links'] == 1
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    repeated = run(extraction, catalog, tmp_path/'again', section='authors')
    assert repeated['logical_sha256'] == result['logical_sha256']


def test_printed_abbreviation_and_parenthetical_alias():
    assert name_matches('Joannes', 'Joan. Smera Polovecius')
    assert name_matches('Sylvester II papa', 'Sylvester II (Gerbertus) papa')
    assert not name_matches('Joannes', 'Joan Smera')


def test_full_name_has_priority_over_homonym_prefix():
    rows = {'a': entry('Alias', 'vide Hugo.'), 'b': entry('Hugo'), 'c': entry('Hugo rex')}
    candidates, _ = resolve('a', build_edges(rows)[0], {'b': [base('exact')], 'c': [base('prefix')]})
    assert candidates[0]['file'] == 'exact'
    assert candidates[0]['score'] > candidates[1]['score']

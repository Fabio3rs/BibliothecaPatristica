import json
from pathlib import Path
import sqlite3

from tools.indexing.global_target_survey import (
    load_pages, numeric_screen, scan_headers, scoped_location, survey_volume, aggregate, profile_references,
)
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.candidate_search import build
from scripts.rebuild_index_search_from_raw import finish


def test_raw_selection_to_search_needs_no_old_databases(tmp_path):
    config,source,_=fixture(tmp_path)
    source.write_text('<bloco tipo="texto_principal">Alpha, 102, 103.\nBeta, ibid.</bloco>')
    recipe=tmp_path/'selection.json'
    recipe.write_text(json.dumps({'version':'raw-index-replay-v1','pages':[{
        'source':'PG001/text/x-9.txt','volume':'PG001','physical':9,
        'profile':'generic-literal-experimental','context':[],
        'expected_sha256':'old-reading'}]}))
    config.update(raw_selection=str(recipe),omitted_legacy_layers=['structured_overrides'])
    # Delete obsolete campaigns; the raw route must not even open them.
    Path(config['experiment_db']).unlink()
    Path(config['structured_db']).unlink()
    result=survey_volume(config,'PG001')
    assert result['counts']['raw_source_changed']==1
    out=Path(config['out'])
    pages=[json.loads(line) for line in (out/'volumes/PG001/raw_pages.jsonl').read_text().splitlines()]
    assert pages[0]['page']['entries']
    assert 'residuals' in pages[0]['page']
    aggregate(out,config,[result])
    summary=build(None,out/'survey.sqlite',tmp_path/'search')
    assert not summary['lexical_audit_performed']
    assert summary['input_audit'] is None
    with sqlite3.connect(tmp_path/'search/search.sqlite') as db:
        refs=list(db.execute('SELECT label,ref_raw,state FROM items ORDER BY ref_raw'))
        assert ('Alpha','102','with_destinations') in refs
        assert ('Alpha','103','with_destinations') in refs
        assert any('ibid' in r[1] and r[2]=='source_only' for r in refs)
        candidates=[json.loads(r[0]) for r in db.execute('SELECT candidate_json FROM candidates')]
        assert all(not c['certified'] and 'no_lexical_support' in c['uncertainties'] for c in candidates)
    config.update(source_db_stats={},input_files={})
    (out/'config.json').write_text(json.dumps(config))
    complete=finish(out)
    assert complete['catalog']['with_destinations']>=2
    assert complete['source_database_dependencies']=={}
    assert (out/'catalog/search.sqlite').is_file()


def test_raw_selection_missing_source_remains_explicit(tmp_path):
    config,source,_=fixture(tmp_path)
    recipe=tmp_path/'selection.json'
    recipe.write_text(json.dumps({'version':'raw-index-replay-v1','pages':[{
        'source':'PG001/text/missing-9.txt','volume':'PG001','physical':9,
        'profile':'generic-literal-experimental','context':[]}]}))
    config.update(raw_selection=str(recipe),omitted_legacy_layers=[])
    result=survey_volume(config,'PG001')
    assert result['counts']['raw_error']==1
    summary=aggregate(Path(config['out']),config,[result])
    assert summary['status']=='partial'


def test_documented_index_scope_excluded_without_literal_proposals(tmp_path):
    config,source,page=fixture(tmp_path)
    # Page 2 carries 102 but belongs to an index even when its old extraction is absent.
    Path(config['literal_profile']).write_text(json.dumps({'index_scope':{
        'volume':'PG001','first':2,'last':2}}))
    result=survey_volume(config,'PG001')
    assert result['counts']['documented_index_sources_excluded_from_numeric_screen']==1
    occurrences=[json.loads(s) for s in (Path(config['out'])/'volumes/PG001/occurrences.jsonl').read_text().splitlines()]
    assert len(occurrences)==1 and not occurrences[0]['files']


def test_screen_preserves_repeated_tokens_and_never_expands_other_notation():
    by={17:{1:{'path':'one','physical':1},2:{'path':'two','physical':2}}}
    r=numeric_screen('17, 17.',30,by,'one')
    assert len(r)==2 and [x['char_start'] for x in r]==[30,34]
    assert all([p['path'] for p in x['files']]==['two'] for x in r)
    assert not numeric_screen('17',0,by,'one',{'two'})[0]['files']
    for raw in ['II,17','17–19','17 b','Leo 17,9','ibid.','0','17,']:
        assert numeric_screen(raw,0,by,'one') is None


def test_header_map_does_not_use_body_or_filename_as_editorial_number(tmp_path):
    root=tmp_path/'root';d=root/'PG001/text';d.mkdir(parents=True)
    out=tmp_path/'out';out.mkdir()
    for physical in range(1,9):
        (d/f'x-{physical}.txt').write_text(
            f'<cabecalho>{100+physical}</cabecalho><texto_principal>999</texto_principal>')
    (d/'alternate-4.txt').write_text('<cabecalho>104</cabecalho>')
    by,checksums,stats=scan_headers(root,'PG001',out)
    assert 999 not in by and 1 not in by and 104 not in by
    assert stats['duplicate_physical_files']==2
    assert len(checksums)==9


def fixture(tmp_path):
    root=tmp_path/'root';d=root/'PG001/text';d.mkdir(parents=True)
    for i in range(1,9):
        (d/f'x-{i}.txt').write_text(f'<cabecalho>{100+i}</cabecalho>')
    source=d/'x-9.txt';raw='Alpha 102.';source.write_text(raw)
    entry={'id':'e','char_start':0,'char_end':len(raw),'entry_raw':raw,'entry_type':'material',
           'text_raw':'Alpha','references_raw':'102','reference_span':{'char_start':6,'char_end':9},
           'flags':['unvalidated_profile']}
    page={'sha256':digest(raw.encode()),'entries':[entry]}
    db=tmp_path/'parser.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE runs(run_id TEXT,status TEXT); CREATE TABLE tasks(fingerprint TEXT,result_json TEXT);'
                       'CREATE TABLE run_pages(run_id TEXT,source TEXT,physical INTEGER,volume TEXT,fingerprint TEXT);')
        c.execute('INSERT INTO runs VALUES(?,?)',('r','completed'))
        c.execute('INSERT INTO tasks VALUES(?,?)',('fp',json.dumps({'page':page})))
        c.execute('INSERT INTO run_pages VALUES(?,?,?,?,?)',('r',str(source),9,'PG001','fp'))
    structured=tmp_path/'structured.sqlite'
    with sqlite3.connect(structured) as c:
        c.executescript('CREATE TABLE sources(path TEXT,physical INTEGER,page_json TEXT,volume TEXT,source_id TEXT);'
                       'CREATE TABLE units(unit_id TEXT,source_id TEXT);'
                       'CREATE TABLE reference_groups(reference_id TEXT,unit_id TEXT,char_start INTEGER,char_end INTEGER);'
                       'CREATE TABLE reference_claims(reference_id TEXT);')
    profile=tmp_path/'literal.json';profile.write_text(json.dumps({'index_scope':{'volume':'PG999'}}))
    return {'root':str(root),'out':str(tmp_path/'out'),'experiment_db':str(db),'parser_runs':['r'],
            'structured_db':str(structured),'profiles':[],'literal_profile':str(profile)},source,page


def test_readonly_selection_override_and_end_to_end_staging(tmp_path):
    config,source,page=fixture(tmp_path)
    with sqlite3.connect(config['structured_db']) as c:
        c.execute('INSERT INTO sources VALUES(?,?,?,?,?)',(str(source),9,json.dumps(page),'PG001','s'))
    before={k:Path(config[k]).read_bytes() for k in ['experiment_db','structured_db']}
    selected,claimed,counts=load_pages(config['experiment_db'],['r'],config['structured_db'],'PG001')
    assert selected[str(source)]['selection']=='consolidated' and counts['consolidated_page_override']==1
    result=survey_volume(config,'PG001')
    assert result['counts']['unmapped_namespace_screen_with_files']==1
    out=Path(config['out'])
    summary=aggregate(out,config,[result])
    assert summary['occurrence_statuses']=={'header_coincidence':1}
    assert summary['verified_links']==0
    with sqlite3.connect(out/'survey.sqlite') as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        r=json.loads(c.execute('select evidence_json from occurrences').fetchone()[0])
        assert r['namespace_status']=='unmapped' and not r['verified']
        assert r['files'][0]['physical']==2
    assert before=={k:Path(config[k]).read_bytes() for k in before}


def test_changed_source_is_pending_not_silently_reused(tmp_path):
    config,source,page=fixture(tmp_path)
    source.write_text('Beta 102.')
    result=survey_volume(config,'PG001')
    assert result['group_statuses']=={'source_or_span_invalid':1}
    assert not result['occurrence_statuses']


def test_unmapped_series_has_no_header_fallback():
    assert scoped_location({'number':9,'series_key':'leo'}, {'series':{}})=={
        'status':'namespace_not_mapped','files':[]}


def test_invalid_profile_blocks_generic_header_fallback(tmp_path):
    config,source,page=fixture(tmp_path)
    profile=tmp_path/'scoped.json'
    profile.write_text(json.dumps({'volume':'PG001','index_physical_start':9,'index_physical_end':9,
                                  'notice':{'path':'missing.txt','sha256':'bad','quote':'notice'}}))
    config['profiles']=[str(profile)]
    result=survey_volume(config,'PG001')
    assert result['group_statuses']=={'profile_evidence_invalid':1}
    assert result['counts']['profile_errors']==1
    assert not result['occurrence_statuses']


def test_flagged_numeric_list_remains_searchable_with_warning(tmp_path):
    config, source, page = fixture(tmp_path)
    page['entries'][0]['flags'] = ['numeric_text_before_reference_group', 'row_segmentation_candidate']
    with sqlite3.connect(config['structured_db']) as c:
        c.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (str(source),9,json.dumps(page),'PG001','s'))
    result = survey_volume(config, 'PG001')
    assert result['counts']['unmapped_namespace_screen_with_files'] == 1
    group = json.loads((Path(config['out'])/'volumes/PG001/groups.jsonl').read_text())
    assert group['warnings_preserved'] == page['entries'][0]['flags']


def test_isolated_header_is_candidate_but_body_number_is_not(tmp_path):
    d = tmp_path/'root/PG001/text'; d.mkdir(parents=True)
    (d/'x-1.txt').write_text('<cabecalho>507</cabecalho><texto_principal>999</texto_principal>')
    out = tmp_path/'out'; out.mkdir()
    by, _, _ = scan_headers(tmp_path/'root', 'PG001', out)
    assert 507 in by and 999 not in by
    assert by[507][1]['candidate_basis'] == 'header_without_sequence'


def test_profile_fallback_preserves_local_owner_and_source_span():
    raw = 'Alpha I, 10, seq. Beta II, 20.'
    entry = {'entry_type':'material','entry_raw':raw,'char_start':100,'flags':['row_segmentation_candidate']}
    refs = profile_references(entry, {'notation':'explicit_tome_list'})
    assert [r['number'] for r in refs] == [10,20]
    assert refs[-1]['owner_raw'] == 'Beta'
    for ref in refs:
        a,b = ref['source_span']
        assert raw[a-100:b-100] == ref['ref_raw']


def test_scoped_missing_end_keeps_all_start_alternatives(monkeypatch):
    import tools.indexing.global_target_survey as module
    monkeypatch.setattr(module, 'locate_number', lambda *a, **k: {'status':'ambiguous_markers','files':[]})
    monkeypatch.setattr(module, 'marker_options', lambda *a: [
        {'file':'a','physical':1}, {'file':'b','physical':2}])
    result = scoped_location({'number':10,'range_end':20,'series_key':'I'},
                             {'series':{'I':{'method':'body'}}})
    assert [f['file'] for f in result['files']] == ['a','b']
    assert result['extent_status'] == 'range_start_only'

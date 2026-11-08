import hashlib
import json
import sqlite3

import pytest

from tools.indexing.editorial_relation_recovery import adjacency, parse_relations, recover
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.candidate_search import search


@pytest.mark.parametrize('raw', ['XV, 104', '125 n. 1', '0', '12-3', '125,',
                              '125. Alius 12', 'ibid. Leo 17', '1, 075'])
def test_no_mining_numbers_from_unsupported_notation(raw):
    assert parse_relations(raw) is None


def test_qualifiers_and_ranges_keep_extent_and_literal():
    refs = parse_relations('125 et seqq., ibidem, 130–135.')
    assert [r['kind'] for r in refs] == ['number','ibid','number']
    assert refs[0]['qualifier'] == 'et seqq.'
    assert refs[2]['range_end'] == 135


@pytest.mark.parametrize('middle,reason', [
    ('\nAlius 77. Novus, ', 'intervening_locator_or_markup'),
    ('\nB.\nNovus, ', 'possible_section_heading'),
    ('</bloco><bloco tipo="texto_principal">Novus, ', 'block_or_section_boundary'),
    ('\nAlius ibid. Novus, ', 'intervening_locator_or_markup'),
])
def test_intervening_evidence_stops_inheritance(middle,reason):
    raw = '<bloco tipo="texto_principal">Prior, 100.'+middle+'ibid.</bloco>'
    a,b=raw.index('100'),raw.rindex('ibid')
    assert adjacency(raw,{'char_start':a,'char_end':a+3},
                     {'char_start':b,'char_end':b+4},blocks(raw)) == reason


def fixture(tmp_path):
    # Real wording from PL058/590; additional entries exercise failure boundaries.
    raw = ('<bloco tipo="texto_principal">Victor conf. episc ab Assuris, 100.\n'
           'Victor episc. Azurensis, ibid.\nVictor secundus, ibidem.\n'
           'Qualificatus, 125 seqq.\nIntervallum, 130–135.\n'
           'Mixtus, 125, ibid.\nMissing, 999.\nOrphanus, ibid.\n'
           '</bloco><bloco tipo="texto_principal">Boundary, ibid.</bloco>')
    source=tmp_path/'source.txt'; source.write_text(raw)
    target=tmp_path/'target.txt'; target.write_text('destination')
    catalog=tmp_path/'search.sqlite'; survey=tmp_path/'survey'
    folder=survey/'volumes/PL058'; folder.mkdir(parents=True)
    specs=[('Prior','100'),('Azurensis','ibid'),('Secundus','ibidem'),('Qualificatus','125 seqq.'),
           ('Intervallum','130–135'),('Mixtus','125, ibid.'),('Missing','999'),('Orphanus','ibid'),('Boundary','ibid')]
    gs=[]; pos=0
    with sqlite3.connect(catalog) as c:
        c.executescript('''CREATE TABLE items(id TEXT,group_id TEXT,volume TEXT,label TEXT,
            ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume);''')
        for n,(label,ref) in enumerate(specs):
            a=raw.index(ref,pos); pos=a+len(ref)
            g={'id':str(n),'source':str(source),'volume':'PL058','char_start':a,'char_end':pos,
               'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'raw':ref,
               'status':'numeric_screen_only' if n in (0,6) else 'segmentation_or_notation_review'}
            gs.append(g)
            c.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                (str(n),str(n),'PL058',label,ref,str(source),'with_destinations' if n==0 else 'source_only','{}'))
            c.execute('INSERT INTO items_fts VALUES(?,?,?)',(label,ref,'PL058'))
        candidate={'file':str(target),'physical':1,'rank':1,'score':100,'navigable':True,
                   'reasons':['full_label_exact'],'snippet':'DO NOT INHERIT LEXICAL EVIDENCE','certified':False}
        c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',('0',str(target),1,100,1,json.dumps(candidate)))
    (folder/'groups.jsonl').write_text(''.join(json.dumps(g)+'\n' for g in gs))
    (folder/'sources.jsonl').write_text(json.dumps({'source':str(source)})+'\n')
    (folder/'header_markers.jsonl').write_text(''.join(json.dumps({'number':number,'path':str(target),
        'physical':1,'raw':str(number),'char_start':0,'char_end':3})+'\n' for number in (125,130)))
    return catalog,survey,source,target


def test_recovery_integrates_search_preserves_base_and_does_not_copy_lexical_support(tmp_path):
    catalog,survey,source,target=fixture(tmp_path)
    before=hashlib.sha256(catalog.read_bytes()).hexdigest(); ocr=source.read_bytes()
    summary=recover(catalog,survey,tmp_path/'out')
    assert summary['counts']['items_gaining_navigable_candidates']==5
    assert summary['source_only_after']==3
    assert hashlib.sha256(catalog.read_bytes()).hexdigest()==before
    assert source.read_bytes()==ocr
    overlay=tmp_path/'out/relations.sqlite'
    result=search(catalog,'Azurensis',relations_db=overlay)[0]
    assert result['state']=='with_destinations'
    assert result['baseline_state']=='source_only'
    assert result['candidates'][0]['file']==str(target)
    assert result['candidates'][0]['score'] < 100
    assert result['candidates'][0]['snippet']==''
    assert 'no_lexical_support' in result['candidates'][0]['uncertainties']
    assert search(catalog,'Azurensis')[0]['state']=='source_only'
    mixed=search(catalog,'Mixtus',relations_db=overlay)[0]['candidates'][0]
    assert set(mixed['reasons']) == {'explicit_numeric_anchor','ibid_candidate'}
    assert len(mixed['evidence']['supporting_relations']) == 2
    with sqlite3.connect(overlay) as c:
        orphan=json.loads(c.execute("SELECT evidence_json FROM relations WHERE group_id='7'").fetchone()[0])
        assert orphan['relations'][0]['antecedent_group_id']=='6'
        assert orphan['relations'][0]['reason']=='antecedent_without_destination'
        assert not c.execute("SELECT 1 FROM candidates WHERE item_id IN ('7','8')").fetchone()
        interval=json.loads(c.execute("SELECT evidence_json FROM relations WHERE group_id='4'").fetchone()[0])
        assert interval['relations'][0]['extent']=='start_only'


def test_changed_ocr_matching_local_spans_remains_candidate_with_warning(tmp_path):
    catalog,survey,source,_=fixture(tmp_path)
    source.write_text(source.read_text()+'\n')
    recover(catalog,survey,tmp_path/'out')
    r=search(catalog,'Azurensis',relations_db=tmp_path/'out/relations.sqlite')[0]
    assert 'source_changed_local_span_matches_only' in r['candidates'][0]['uncertainties']


def test_changed_reference_span_does_not_inherit(tmp_path):
    catalog,survey,source,_=fixture(tmp_path)
    source.write_text(source.read_text().replace('100.','999.',1))
    recover(catalog,survey,tmp_path/'out')
    assert search(catalog,'Azurensis',relations_db=tmp_path/'out/relations.sqlite')[0]['state']=='source_only'


def test_preserves_multiple_antecedent_destinations_and_chain(tmp_path):
    catalog,survey,_,target=fixture(tmp_path)
    other=tmp_path/'other.txt'; other.write_text('alternative')
    with sqlite3.connect(catalog) as c:
        original=json.loads(c.execute('SELECT candidate_json FROM candidates').fetchone()[0])
        original.update(file=str(other),physical=2)
        c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',('0',str(other),2,90,1,json.dumps(original)))
    recover(catalog,survey,tmp_path/'out')
    r=search(catalog,'Secundus',relations_db=tmp_path/'out/relations.sqlite')[0]
    assert {x['file'] for x in r['candidates']} == {str(target),str(other)}
    assert all(x['evidence']['inheritance_depth']==2 for x in r['candidates'])
    assert all(not x['certified'] for x in r['candidates'])


def test_scoped_qualifier_does_not_fall_back_to_unrelated_header(tmp_path):
    catalog,survey,_,_=fixture(tmp_path)
    path=survey/'volumes/PL058/groups.jsonl'
    groups=[json.loads(line) for line in path.read_text().splitlines()]
    groups[3].update(status='profile_notation_pending',profile='specific-edition.json')
    path.write_text(''.join(json.dumps(g)+'\n' for g in groups))
    recover(catalog,survey,tmp_path/'out')
    r=search(catalog,'Qualificatus',relations_db=tmp_path/'out/relations.sqlite')[0]
    assert not r['candidates']


def test_overlay_rejects_other_catalog(tmp_path):
    catalog,survey,_,_=fixture(tmp_path)
    recover(catalog,survey,tmp_path/'out')
    other=tmp_path/'other.sqlite'; other.write_bytes(catalog.read_bytes())
    with pytest.raises(ValueError,match='different catalog'):
        search(other,'Azurensis',relations_db=tmp_path/'out/relations.sqlite')


def catalog_context_fixture(tmp_path):
    catalog, survey, source, target = fixture(tmp_path)
    path = survey/'volumes/PL058/groups.jsonl'
    groups = [json.loads(line) for line in path.read_text().splitlines()]
    for g in groups:
        g['owner_raw'] = 'Known literal owner'
        g['section_id'] = 'one'
    path.write_text(''.join(json.dumps(g)+'\n' for g in groups))
    return catalog, survey, source, target, path, groups


def test_catalog_clause_added_and_used_as_immediate_antecedent(tmp_path):
    catalog, survey, source, target, path, groups = catalog_context_fixture(tmp_path)
    g = groups[1]
    raw = source.read_text()
    owner = 'Victor episc. Azurensis'
    a = raw.index(owner)
    metadata = {'source_span': [g['char_start'], g['char_end']],
                'source_sha256': g['source_sha256'], 'parent_group_id': '0',
                'entry': {'owner_raw': owner, 'owner_span': [a, a+len(owner)]}}
    with sqlite3.connect(catalog) as db:
        db.execute('UPDATE items SET metadata_json=? WHERE id=?', (json.dumps(metadata), '1'))
    path.write_text(''.join(json.dumps(g)+'\n' for g in groups if g['id'] != '1'))
    recover(catalog, survey, tmp_path/'out', catalog_context=True)
    result = search(catalog, 'Azurensis', relations_db=tmp_path/'out/relations.sqlite')[0]
    assert result['candidates'][0]['file'] == str(target)
    chained = search(catalog, 'Secundus', relations_db=tmp_path/'out/relations.sqlite')[0]
    assert chained['candidates'][0]['evidence']['inheritance_depth'] == 2


@pytest.mark.parametrize('change,reason', [
    ({'owner_raw': ''}, 'unknown_or_changed_owner'),
    ({'section_id': 'two'}, 'editorial_scope_boundary'),
    ({'profile': 'another_namespace'}, 'editorial_scope_boundary'),
])
def test_catalog_context_blocks_unknown_owner_or_changed_scope(tmp_path, change, reason):
    catalog, survey, _, _, path, groups = catalog_context_fixture(tmp_path)
    groups[1].update(change)
    path.write_text(''.join(json.dumps(g)+'\n' for g in groups))
    recover(catalog, survey, tmp_path/'out', catalog_context=True)
    with sqlite3.connect(tmp_path/'out/relations.sqlite') as db:
        evidence = json.loads(db.execute("SELECT evidence_json FROM relations WHERE group_id='1'").fetchone()[0])
        assert evidence['relations'][0]['reason'] == reason
        assert not db.execute("SELECT 1 FROM candidates WHERE item_id IN ('1','2')").fetchone()


def test_catalog_repaired_owner_span_is_rechecked_and_not_replaced_by_legacy_owner(tmp_path):
    catalog, survey, _, _, _, _ = catalog_context_fixture(tmp_path)
    metadata = {'parser_result': {'existing_reference_entry': 0,
                'entries': [{'owner_raw': 'wrong owner', 'owner_span': [0, 11]}]},
                'original_item_metadata': {'query': 'Known literal owner'}}
    with sqlite3.connect(catalog) as db:
        db.execute('UPDATE items SET metadata_json=? WHERE id=?', (json.dumps(metadata), '1'))
    recover(catalog, survey, tmp_path/'out', catalog_context=True)
    with sqlite3.connect(tmp_path/'out/relations.sqlite') as db:
        assert not db.execute("SELECT 1 FROM candidates WHERE item_id IN ('1','2')").fetchone()


def test_catalog_contextualized_antecedent_does_not_use_bare_old_number(tmp_path):
    catalog, survey, _, _, _, _ = catalog_context_fixture(tmp_path)
    with sqlite3.connect(catalog) as db:
        db.execute('UPDATE items SET ref_raw=?,metadata_json=? WHERE id=?',
                   ('II, 100', json.dumps({'reference_display_is_contextualized': True}), '0'))
    recover(catalog, survey, tmp_path/'out', catalog_context=True)
    with sqlite3.connect(tmp_path/'out/relations.sqlite') as db:
        assert not db.execute("SELECT 1 FROM candidates WHERE item_id IN ('1','2')").fetchone()


def test_estimator_overlay_supplies_antecedent_and_preserves_chain(tmp_path):
    catalog,survey,source,target=fixture(tmp_path)
    with sqlite3.connect(catalog) as c:
        c.execute('delete from candidates')
        c.execute("update items set state='source_only' where id='0'")
    overlay=tmp_path/'estimates.sqlite'
    with sqlite3.connect(overlay) as c:
        c.executescript('create table runs(config_json); create table candidates(item_id,file,rank,evidence_json);')
        c.execute('insert into runs values (?)',(json.dumps({'catalog':str(catalog.resolve())}),))
        evidence={'file':str(target),'physical':1,'matches':[{'number':100,'evidence':[{'kind':'sequence_consensus'}]}]}
        c.execute('insert into candidates values (?,?,?,?)',('0',str(target),1,json.dumps(evidence)))
    before=hashlib.sha256(overlay.read_bytes()).hexdigest()
    summary=recover(catalog,survey,tmp_path/'overlay_out',estimator_candidates=overlay,only_ibid=True)
    assert summary['requested_ibid_items']==5
    assert summary['processed_ibid_items']==5
    with sqlite3.connect(tmp_path/'overlay_out/relations.sqlite') as c:
        inherited=json.loads(c.execute("select candidate_json from candidates where item_id='2'").fetchone()[0])
        assert inherited['evidence']['inheritance_depth']==2
        assert inherited['evidence']['estimator_evidence'][0]['number']==100
        assert inherited['snippet']==''
        assert not c.execute("select 1 from candidates where item_id='7'").fetchone()
    assert hashlib.sha256(overlay.read_bytes()).hexdigest()==before


def test_catalog_new_group_keeps_own_section_scope(tmp_path):
    catalog,survey,source,target,path,groups=catalog_context_fixture(tmp_path)
    g=groups[1];raw=source.read_text();owner='Victor episc. Azurensis';a=raw.index(owner)
    meta={'source_span':[g['char_start'],g['char_end']],'source_sha256':g['source_sha256'],
          'parent_group_id':'0','section_id':'different_section',
          'entry':{'owner_raw':owner,'owner_span':[a,a+len(owner)]}}
    with sqlite3.connect(catalog) as c:
        c.execute('update items set metadata_json=? where id=?',(json.dumps(meta),'1'))
    path.write_text(''.join(json.dumps(g)+'\n' for g in groups if g['id']!='1'))
    recover(catalog,survey,tmp_path/'scope_out',catalog_context=True)
    with sqlite3.connect(tmp_path/'scope_out/relations.sqlite') as c:
        evidence=json.loads(c.execute("select evidence_json from relations where group_id='1'").fetchone()[0])
        assert evidence['relations'][0]['reason']=='editorial_scope_boundary'
        assert not c.execute("select 1 from candidates where item_id='1'").fetchone()


@pytest.mark.parametrize('literal,subdivision', [('ibid. C.','C'),('ibid., C, D;','C, D'),('ibid.;',None),('ibid.,',None)])
def test_ibid_subdivisions_and_terminal_punctuation(literal, subdivision):
    tokens=parse_relations(literal)
    assert len(tokens)==1 and tokens[0]['kind']=='ibid'
    assert tokens[0].get('subdivision_raw')==subdivision


@pytest.mark.parametrize('literal', ['ibid. seq.','ibid. et seqq','ibid et seqq.'])
def test_ibid_continuation_is_kept_without_inventing_extent(literal):
    tokens=parse_relations(literal)
    assert len(tokens)==1 and tokens[0]['kind']=='ibid'
    assert tokens[0]['qualifier']


@pytest.mark.parametrize('literal', ['ibid. 760','ibid., A Phoca devictus','ibid., CX','idem','I, ibid.'])
def test_ibid_never_consumes_prose_or_tome_context(literal):
    assert parse_relations(literal) is None

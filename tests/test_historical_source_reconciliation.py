import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.candidate_search import search, search_with_supplements
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.historical_source_candidates import run_versions
from tools.indexing.historical_source_reconciliation import compare_catalog, grouping_key, project_reference, run, reference_key, merge_aligned_groups


def wrap(text):
    return '<bloco tipo="texto_principal">'+text+'</bloco>'


def row(raw, label, ref, ident='base'):
    start = raw.index(ref)
    return {'id': ident, 'group_id': ident, 'volume': 'PG001', 'label': label,
            'ref_raw': ref, 'source': 'source', 'state': 'source_only',
            'metadata_json': json.dumps({'source_sha256': digest(raw.encode()),
                                        'source_span': [start, start+len(ref)]})}


def test_matching_words_need_occurrence_alignment_to_suppress_a_duplicate():
    raw = wrap('Alpha pro hominibus, I, 10.')
    entry = row(raw, 'Alpha pro hominibus', 'I, 10.')
    matches = compare_catalog(entry['label'], entry['ref_raw'], [entry], raw)
    assert matches[0]['kind'] == 'possible_occurrence_match'
    projected = project_reference(raw, raw, [raw.index('I,'),raw.index('I,')+6])
    assert projected
    matches = compare_catalog(entry['label'],entry['ref_raw'],[entry],raw,[projected])
    assert matches[0]['kind'] == 'agreement'
    changed = compare_catalog(entry['label'],'I, 11.',[entry],raw,[projected])
    assert changed[0]['kind'] == 'reference_variant'


def test_different_reference_and_repeated_occurrences_do_not_collapse():
    p = {'page_key': 'PG001:1', 'version_id': 1, 'owner': {'quote': 'Alpha'},
         'reference': {'quote': 'I, 10.', 'span': [100,106]}}
    reading = {'computed_sha256': 'same', 'alignment': {'changes': [
        {'bounded_by_text': True,'alternative_span':[0,300],'current_span':[10,10]}]}}
    multiplicity = {(1,'alpha','i,10'):2}
    one = grouping_key(p,reading,multiplicity)
    p['reference']['span'] = [200,206]
    assert grouping_key(p,reading,multiplicity) != one
    p['reference']['quote'] = '1, 10.'
    assert grouping_key(p,reading,{(1,'alpha','1,10'):1}) != one


def test_reference_punctuation_is_not_lost_in_reconciliation():
    assert reference_key('I, 10–12.') == reference_key('I,10-12')
    assert reference_key('I, 10–12.') != reference_key('I, 10, 12.')
    assert reference_key('I, 1 23.') != reference_key('I,123.')


def test_merge_requires_overlapping_windows_and_distinct_version_occurrences():
    def group(version, window, reference='I, 10.'):
        return {'source':'page','label':'Alpha','reference':reference,'prior_review':None,
                'proposals':[{'version_id':version,'alignment_change':{'bounded_by_text':True,'current_span':window}}]}
    groups = {'a':group(1,[10,40]),'b':group(2,[20,30]),'c':group(3,[100,200]),
              'd':group(1,[20,30]),'e':group(4,[20,30],'I, 11.')}
    merged = merge_aligned_groups(groups)
    assert len(merged) == 4
    assert len(merged['a']['proposals']) == 2
    assert merged['a']['aligned_occurrence_window'] == [20,30]


def fixture(tmp_path):
    source = tmp_path/'source.txt'
    raw = wrap('Ante hanc regionem verba nota. Beta, I, 2. Post hanc regionem verba nota.')
    source.write_text(raw)
    alternative = wrap('Ante hanc regionem verba nota. Æthiopia quid significat, I, 766. Beta, I, 3. Post hanc regionem verba nota.')
    image = tmp_path/'image.png'; image.write_bytes(b'image')
    versions = tmp_path/'versions.sqlite'
    with sqlite3.connect(versions) as con:
        con.executescript('''CREATE TABLE ocr_engine_versions(id,engine,model);
            CREATE TABLE ocr_results(id,text_content,text_hash,image_hash,is_current,engine_version_id,volume_id,page_num);''')
        con.execute('INSERT INTO ocr_engine_versions VALUES(1,"test","test")')
        for ident in (1,2):
            con.execute('INSERT INTO ocr_results VALUES(?,?,?,?,0,1,"PG001",1)',
                        (ident,alternative,digest(alternative.encode()),'image'))
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'pages':[{'volume':'PG001','physical':1,'source':str(source),
        'image':str(image),'source_sha256':digest(raw.encode()),'issues':[
        {'id':'missing','kind':'missing_printed_entries','search_anchors':['Æthiopia']},
        {'id':'number','kind':'missing_reference_qualifier','search_anchors':['Beta']}]}]}))
    recovery = tmp_path/'recovery'
    run_versions(registry,versions,recovery,tmp_path)
    catalog = tmp_path/'catalog.sqlite'
    with sqlite3.connect(catalog) as con:
        con.executescript('''CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json);
            CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume);''')
        item = row(raw,'Beta','I, 2.')
        item['source'] = str(source)
        con.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', tuple(item.values()))
        con.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(1,"beta","I, 2.","PG001")')
    return recovery,catalog,source,versions,alternative


def test_reconciliation_is_readonly_and_search_federates_alternatives(tmp_path):
    recovery,catalog,source,versions,_ = fixture(tmp_path)
    before = [p.read_bytes() for p in (catalog,source,versions)]
    out = tmp_path/'out'
    summary = run(recovery,catalog,None,tmp_path/'survey',out)
    assert [p.read_bytes() for p in (catalog,source,versions)] == before
    assert summary['counts']['supplemental_items'] == 2
    assert summary['counts']['catalog_reference_variant'] == 1
    assert summary['counts']['absent_from_catalog'] == 1
    assert summary['new_destination_links'] == summary['canonical_updates'] == 0
    results = search_with_supplements(catalog,'Æthiopia',supplements=[out/'search.sqlite'])
    assert len(results) == 1
    assert results[0]['label'] == 'Æthiopia quid significat'
    assert not results[0]['candidates']
    beta = search_with_supplements(catalog,'Beta',supplements=[out/'search.sqlite'])
    assert {r['ref_raw'] for r in beta} == {'I, 2.','I, 3.'}
    assert summary['map_inventories']['PG001']['profiles'] == []
    with pytest.raises(ValueError,match='different catalog'):
        search_with_supplements(tmp_path/'different.sqlite','Beta',supplements=[out/'search.sqlite'])


def test_previous_review_is_reused_by_version_span_not_counted_again(tmp_path):
    recovery,catalog,_,_,alternative = fixture(tmp_path)
    a = alternative.index('Æthiopia')
    literal = 'Æthiopia quid significat, I, 766.'
    ref_start = literal.index('I,')
    prior = {'source': str(tmp_path/'source.txt'),'volume':'PG001','physical':1,
             'occurrence_id':'reviewed-entry','quality':{'human_reviewed':False},
             'visual_transcription':{'literal':literal},
             'reference_groups':[{'owner_text_raw':'Æthiopia quid significat','raw':'I, 766.',
                                   'char_start':ref_start,'char_end':len(literal)}],
             'alternative_readings':[{'version_id':i,'snapshot':str(recovery/f'sources/version-{i}.txt'),
                    'snapshot_sha256':digest(alternative.encode()),'char_start':a,'char_end':a+len(literal),
                    'literal':literal} for i in (1,2)]}
    reviewed = tmp_path/'reviewed.jsonl';reviewed.write_text(json.dumps(prior)+'\n')
    out = tmp_path/'out'
    summary = run(recovery,catalog,reviewed,tmp_path/'survey',out)
    assert summary['counts']['previous_review_occurrences'] == 1
    hit, = search(out/'search.sqlite','Æthiopia')
    assert len(hit['metadata']['reconciliation']['proposals']) == 2
    assert hit['metadata']['reference_coordinate_space'] == 'visual_transcription'
    assert hit['metadata']['source_span'] is None


def test_tampered_snapshot_fails_before_writing_supplement(tmp_path):
    recovery,catalog,_,_,_ = fixture(tmp_path)
    (recovery/'sources/version-1.txt').write_text('changed')
    with pytest.raises(ValueError,match='snapshot changed'):
        run(recovery,catalog,None,tmp_path/'survey',tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_duplicate_evidence_annotates_both_existing_items_without_deleting(tmp_path):
    recovery,catalog,source,_,_ = fixture(tmp_path)
    # Independent fixture snapshot: two printed-text occurrences in current OCR.
    current = wrap('nec dare potuerunt, I, 423. nec dare potuerunt, I, 423.')
    alternative = wrap('nec dare potuerunt, I, 423.')
    source.write_text(current)
    page = json.loads((recovery/'pages.jsonl').read_text())
    Path(page['current_snapshot']).write_text(current)
    page['source_sha256'] = digest(current.encode())
    (recovery/'pages.jsonl').write_text(json.dumps(page)+'\n')
    from tools.indexing.historical_source_candidates import recover
    found,_,_ = recover(current,alternative,[{'id':'dup','kind':'duplicated_transcription',
                                             'search_anchors':['nec dare potuerunt']}])
    reading = json.loads((recovery/'readings.jsonl').read_text().splitlines()[0])
    reading['computed_sha256'] = digest(alternative.encode())
    Path(reading['snapshot']).write_text(alternative)
    (recovery/'readings.jsonl').write_text(json.dumps(reading)+'\n')
    proposal = dict(found[0],page_key='PG001:1',version_id=reading['id'],id='dup')
    (recovery/'proposals.jsonl').write_text(json.dumps(proposal)+'\n')
    with sqlite3.connect(catalog) as con:
        con.execute('DELETE FROM items');con.execute('DELETE FROM items_fts')
        for ident in ('one','two'):
            item = row(current,'nec dare potuerunt','I, 423.',ident)
            item['source'] = str(source)
            cursor=con.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',tuple(item.values()))
            con.execute('INSERT INTO items_fts(rowid,label) VALUES(?,?)',(cursor.lastrowid,'nec dare potuerunt'))
    before = catalog.read_bytes()
    out = tmp_path/'out'
    summary = run(recovery,catalog,None,tmp_path/'survey',out)
    assert summary['counts']['duplication_groups'] == 1
    assert summary['counts']['catalog_items_with_duplication_warning'] == 2
    matches = search_with_supplements(catalog,'nec dare potuerunt',supplements=[out/'search.sqlite'])
    assert {m['id'] for m in matches} == {'one','two'}
    assert all(m['metadata']['supplement_annotations'][0]['automatically_removed'] is False for m in matches)
    assert catalog.read_bytes() == before

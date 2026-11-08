import hashlib
import json
import sqlite3

from tools.indexing.candidate_search import search
from tools.indexing.epistle_catalog_recovery import incipit_evidence, ranked_openings, recover


def anchor(path,series='register',book=15,epistle=104,suffix=None,opening='Gravem dilecti filii'):
    path.write_text('OCR destination')
    return {'file':str(path),'physical':314,'series':series,'book':book,'epistle':epistle,
            'suffix':suffix,'opening_text':opening,'uncertainties':[],'spans':[]}


def test_incipit_uses_word_boundaries_and_short_phrases_are_weak():
    assert incipit_evidence('Gravem dilecti.','Gravem dilecti filii')['method']=='normalized'
    assert incipit_evidence('Gravem dilecti.','Gravem dilecti filii')['short_or_formulaic']
    assert incipit_evidence('Cum','Cumque')['method']=='not_found'
    assert incipit_evidence('Cum dilectus filius noster','Cum dilectns filius noster')['method']=='fuzzy'


def fixture(tmp_path):
    source=tmp_path/'index.txt'; source.write_text('Gravem dilecti. XV, 104.')
    checksum=hashlib.sha256(source.read_bytes()).hexdigest()
    db=tmp_path/'catalog.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('''CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json);
            CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume);''')
        for ident,label,ref in [('a','Gravem dilecti','XV, 104.'),('b','Alia epistola','Suppl. 104.'),
                                 ('c','Alia iterata','XV, 104 bis.')]:
            c.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',(ident,ident,'PL217',label,ref,
                str(source),'source_only',json.dumps({'source_sha256':checksum,'source_span':[15,23]})))
            c.execute('INSERT INTO items_fts VALUES(?,?,?)',(label,ref,'PL217'))
    return db,source


def test_recovery_preserves_namespaces_suffix_and_base_and_ranks_incipit(tmp_path):
    db,source=fixture(tmp_path)
    a=anchor(tmp_path/'correct.txt')
    b=anchor(tmp_path/'other.txt',opening='Dilectus filius nobis')
    before=hashlib.sha256(db.read_bytes()).hexdigest(); before_ocr=source.read_bytes()
    s=recover(db,tmp_path,tmp_path/'out',anchors=[a,b])
    assert s['new_candidate_items']==1
    assert s['counts']['items_with_multiple_candidates']==1
    assert s['source_only_combined']==2
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
    assert source.read_bytes()==before_ocr
    r=search(db,'Gravem',relations_db=tmp_path/'out/relations.sqlite')[0]
    assert r['state']=='with_destinations'
    assert r['candidates'][0]['file']==a['file']
    assert len(r['candidates'])==2
    assert not r['candidates'][0]['certified']
    assert search(db,'iterata',relations_db=tmp_path/'out/relations.sqlite')[0]['state']=='source_only'


def test_multiple_overlays_combine_without_double_counting(tmp_path):
    db,_=fixture(tmp_path)
    a=anchor(tmp_path/'a.txt')
    b=anchor(tmp_path/'b.txt',series='supplement',book=None)
    first=tmp_path/'first'; second=tmp_path/'second'
    recover(db,tmp_path,first,anchors=[a])
    s=recover(db,tmp_path,second,anchors=[a,b],previous_relations=[first/'relations.sqlite'])
    assert s['previous_overlay_items']==1
    assert s['new_items_beyond_previous_overlays']==1
    assert s['with_destinations_combined']==2
    result=search(db,'Gravem',relations_db=[first/'relations.sqlite',second/'relations.sqlite'])[0]
    assert len(result['candidates'])==1
    assert len(result['candidates'][0]['additional_support'])==1
    assert search(db,'epistola',relations_db=[first/'relations.sqlite',second/'relations.sqlite'])[0]['state']=='with_destinations'


def test_missing_incipit_keeps_number_candidate_and_marks_uncertainty(tmp_path):
    a=anchor(tmp_path/'target.txt',opening='Dilectus filius')
    refs=[{'series':'register','book':15,'epistle':104,'suffix':None}]
    ranked,_=ranked_openings(refs,'Gravem dilecti',{('register',15,104,None):[a]},tmp_path)
    assert ranked[0]['navigable']
    assert 'no_incipit_support' in ranked[0]['uncertainties']


def test_live_candidate_wins_over_higher_scoring_stale_overlay(tmp_path):
    db,_=fixture(tmp_path)
    a=anchor(tmp_path/'a.txt')
    first=tmp_path/'first'; second=tmp_path/'second'
    recover(db,tmp_path,first,anchors=[a])
    recover(db,tmp_path,second,anchors=[a])
    with sqlite3.connect(first/'relations.sqlite') as c:
        record=json.loads(c.execute('SELECT candidate_json FROM candidates').fetchone()[0])
        record.update(navigable=False,score=1000)
        c.execute('UPDATE candidates SET navigable=0,score=1000,candidate_json=?',(json.dumps(record),))
    r=search(db,'Gravem',relations_db=[first/'relations.sqlite',second/'relations.sqlite'])[0]
    assert r['state']=='with_destinations'
    assert r['candidates'][0]['navigable']
    assert not r['candidates'][0]['additional_support'][0]['navigable']

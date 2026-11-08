import json
import sqlite3

import pytest

from scripts.rebuild_editorial_index_pages import rebuild, export_page_overlay


def test_raw_rebuild_is_repeatable_and_recomputes_offsets_after_ocr_edit(tmp_path):
    root=tmp_path/'raw'
    source=root/'PL001/text/index-1.txt'
    source.parent.mkdir(parents=True)
    body='<texto_principal>Abel, 56. Figura Christi, ibidem.</texto_principal>'
    source.write_text(body)
    manifest=tmp_path/'selection.json'
    manifest.write_text(json.dumps({'version':'editorial-page-rebuild-v1','pages':[
        {'volume':'PL001','source':'PL001/text/index-1.txt',
         'scope':'principal_blocks','section_id':'s'}]}))
    a=rebuild(manifest,root,tmp_path/'a',1)
    b=rebuild(manifest,root,tmp_path/'b',2)
    assert a['rows']==b['rows']==2
    assert a['logical_sha256']==b['logical_sha256']
    assert not a['previous_database_required']
    # A repair changes source coordinates; scopes must come from the new XML.
    source.write_text('<cabecalho>INDEX</cabecalho>\n'+body)
    c=rebuild(manifest,root,tmp_path/'c',1)
    assert c['rows']==2
    assert c['logical_sha256']!=a['logical_sha256']
    with sqlite3.connect(tmp_path/'c/extraction/search.sqlite') as db:
        for ref,meta in db.execute('SELECT ref_raw,metadata_json FROM items'):
            span=json.loads(meta)['reference_span']
            assert source.read_text()[slice(*span)]==ref


def test_page_overlay_keeps_evidence_and_rejects_other_catalog(tmp_path):
    estimates=tmp_path/'estimates.sqlite'
    catalog=tmp_path/'base.sqlite'
    page=tmp_path/'page.txt'
    page.write_text('Abel')
    evidence={'file':str(page),'rank':1,'matches':[{'score':2,'number':56}],
              'source_file_itself':False}
    with sqlite3.connect(estimates) as db:
        db.execute('CREATE TABLE runs(config_json TEXT)')
        db.execute('INSERT INTO runs VALUES(?)',(json.dumps({'catalog':str(catalog)}),))
        db.execute('CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,evidence_json TEXT)')
        db.execute('INSERT INTO candidates VALUES(?,?,?,?)',('i',str(page),1,json.dumps(evidence)))
    before=estimates.read_bytes()
    overlay=tmp_path/'overlay.sqlite'
    export_page_overlay(catalog,estimates,overlay)
    with sqlite3.connect(overlay) as db:
        row=db.execute('SELECT navigable,candidate_json FROM candidates').fetchone()
        candidate=json.loads(row[1])
        assert row[0]==1
        assert candidate['estimator_evidence']==evidence
        assert candidate['certified'] is False
    assert estimates.read_bytes()==before
    with pytest.raises(ValueError,match='another catalog'):
        export_page_overlay(tmp_path/'other.sqlite',estimates,tmp_path/'bad.sqlite')

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
from tools.indexing.entry_parser_experiment import digest,source_span
from tools.indexing.structured_index_experiment import SELECTION_VERSION,consolidate
from tools.indexing.subdivision_clause_recovery import candidates,account_range,run


def test_lists_ranges_and_bare_subdivisions_remain_literal():
    text='Nerva imp., 10 a, 335 a, 468 b. Jovianus succedit, 484 a, b. Moritur, 581 a.'
    proposed,rejected=candidates(text,0,len(text))
    assert [p['reference_groups'][0]['raw'] for p in proposed]==['10 a, 335 a, 468 b.','484 a, b.','581 a.']
    assert not rejected
    assert all(p['quality']['lemma_context']=='unresolved' for p in proposed)
    t='Alpha, 580 a — 581 b.'
    assert candidates(t,0,len(t))[0][0]['reference_groups'][0]['raw']=='580 a — 581 b.'


def test_printed_roman_namespace_stays_with_reference_not_clause_owner():
    text='CATAPHRYGÆ, II, 72 a. Jupiter Pluvius, I, 296 a.'
    p,_=candidates(text,0,len(text))
    assert [x['reference_groups'][0]['raw'] for x in p]==['II, 72 a.','I, 296 a.']
    assert [x['reference_groups'][0]['owner_text_raw'] for x in p]==['CATAPHRYGÆ','Jupiter Pluvius']


def test_spans_greek_and_hyphenation_preserved():
    t='prefix\nΝάσος, 143 b.\nAnthemius bap-\n tizatur, 79 a. suffix'
    p,_=candidates(t,7,len(t))
    assert len(p)==2
    assert p[1]['reference_groups'][0]['owner_text_raw']=='Anthemius bap-\n tizatur'
    for x in p:
        assert t[x['char_start']:x['char_end']]==x['entry_raw']
        g=x['reference_groups'][0];s=g['owner_span']
        assert t[s['char_start']:s['char_end']]==g['owner_text_raw']


@pytest.mark.parametrize('text',[
    'Alpha, 17 a Phoca devictus, 18 a.',
    'Joannes, cod. 95. Adv. eum Basilius, 107 a.',
    'Ibid., 17 a.',
    'Alpha vide Beta, 17 a.',
    'Chalcedonense concilium, v. Concilium.\nChalcedonensium domus, 38 b.',
    'De Chosroë cogitat, ib. Abramum legat, 2 a.',
    'Lacrimarum divina indulgentia, I,\nLarriz luxuria captatrix, II, 96 a.',
    'Continuatio 12, 17 a.',
    'fragmentum, 17 a.',
    'Alpha, 17 a, 18.',
    'Alpha, 17 a, not. 5.',
    'Alpha\n\nBeta, 17 a.',
    'ORDO RERUM, 17 a.',
    'Alpha, 17 A.',
    'Alpha, 12345 a.',
])
def test_negative_contexts_are_not_claimed(text):
    assert not candidates(text,0,len(text))[0]


def test_unsafe_owner_does_not_contaminate_next_safe_clause_and_all_text_accounted():
    text='Codex 95. Commentarius, 107 a. Josephus, 52 b. tail'
    p,r=candidates(text,0,len(text))
    assert len(p)==1 and p[0]['reference_groups'][0]['owner_text_raw']=='Josephus'
    a=account_range(text,0,len(text),p)
    assert a['claimed_characters']+a['remaining_characters']==len(text)
    parts=sorted(p+a['remaining'],key=lambda x:x['char_start'])
    assert ''.join(x.get('entry_raw',x.get('raw')) for x in parts)==text
    with pytest.raises(ValueError,match='Overlapping'):
        account_range(text,0,len(text),p+p)


def fixture(tmp_path):
    source=tmp_path/'teste/PG104/text/page-1.txt';source.parent.mkdir(parents=True)
    raw='<bloco tipo="texto_principal">Alpha, 17 a. Beta, 18 b. Gamma, 19 a.</bloco>'
    source.write_text(raw);a=raw.index('Alpha');b=raw.index('</')
    page={'source':str(source),'physical_page':1,'sha256':digest(raw.encode()),'entries':[],
          'residuals':[{'raw':raw[a:b],**source_span(raw,a,b),'reason':'unsupported'}],
          'campaign_accounting':{'section_stats':[{'span':[a,b],'kind':'lexical_index','section_id':'s'}]}}
    artifact=tmp_path/'page.json';artifact.write_text(json.dumps(page))
    selected=tmp_path/'selection.json';selected.write_text(json.dumps({'version':SELECTION_VERSION,'sources':[
        {'source':str(source),'physical':1,'sha256':page['sha256'],'artifact':str(artifact)}]}))
    consolidate(selected,tmp_path/'baseline',tmp_path)
    return source,tmp_path/'baseline/index.sqlite'


def test_end_to_end_readonly_staging_and_source_changes_deferred(tmp_path):
    source,db=fixture(tmp_path)
    before=db.read_bytes(),source.read_bytes()
    result=run(db,tmp_path/'out')
    assert result['proposed_clauses']==3
    assert result['page_statuses']=={'evaluated':1}
    assert before==(db.read_bytes(),source.read_bytes())
    with sqlite3.connect(tmp_path/'out/clauses.sqlite') as c:
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    source.write_text('new OCR pending upstream')
    result=run(db,tmp_path/'changed')
    assert result['proposed_clauses']==0
    assert result['page_statuses']=={'source_changed_deferred':1}


@pytest.mark.parametrize('kind', ['editorial_closure', 'thematic_index'])
def test_signature_and_region_are_required(tmp_path, kind):
    source,db=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        page=json.loads(c.execute('SELECT page_json FROM sources').fetchone()[0])
        page['campaign_accounting']['section_stats'][0]['kind']=kind
        c.execute('UPDATE sources SET page_json=?',(json.dumps(page),))
    assert run(db,tmp_path/'boundary')['proposed_clauses']==0

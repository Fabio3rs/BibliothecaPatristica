from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import pytest

from tools.indexing.entry_parser_literal_rows import attributed_reference_groups, reference_groups
from tools.indexing.entry_parser_experiment import source_span, digest
from tools.indexing.index_extraction_recovery import residual_cause, remainder, account_source, run
from tools.indexing.structured_index_experiment import SELECTION_VERSION, readonly


def groups(text):
    return attributed_reference_groups(text,0,len(text),'analytic-tome-rows-v1')


@pytest.mark.parametrize('text,owners,refs',[
    ('Basilius, III, 815; ejus erga Deum dilectio. 805; os pietatis, II, 250, A;',
     ['Basilius','ejus erga Deum dilectio','os pietatis'],['III, 815;','805;','II, 250, A;']),
    ('Eunomii, II, 130; ejus vita; 265 et seqq.; an apologiam instituerit, 271, C.',
     ['Eunomii','ejus vita','an apologiam instituerit'],['II, 130;','265 et seqq.;','271, C.']),
    ('Fatum, II, 130; fatum series immutabilis pro certa stellarum conjunctione; 151, 152;',
     ['Fatum','fatum series immutabilis pro certa stellarum conjunctione'],['II, 130;','151, 152;']),
    ('Oculi, 1058; et cur sicut columbæ, ibid.', ['Oculi','et cur sicut columbæ'],['1058;','ibid.']),
])
def test_clause_repairs_keep_exact_owners_and_reference_lists(text,owners,refs):
    result=groups(text)
    assert [g['owner_text_raw'] for g in result]==owners
    assert [g['raw'] for g in result]==refs
    for g in result:
        assert text[g['char_start']:g['char_end']]==g['raw']
        assert text[g['owner_span']['char_start']:g['owner_span']['char_end']]==g['owner_text_raw']
        assert g['inheritance_status']=='not_inferred'


def test_ibid_dependency_is_not_a_resolved_reference():
    a,b=groups('Bellum, I, 745 et seqq. Belli apparatus, ibid.')
    assert b['raw']=='ibid.'
    assert b['context_dependency']['previous_reference_span']['char_start']==a['char_start']
    assert b['context_dependency']['status']=='requires_context_review'


@pytest.mark.parametrize('text',[
    'Eleemosynæ commendatio. III, 459 et seqq.',
    'Exploratores, quinam mystice. I, 411, C, D.',
    'Stultum ambulare in tenebris quid. I, 686, C.',
])
def test_rejected_period_start_does_not_hide_existing_comma_locator(text):
    assert len(groups(text))==1


def test_internal_chapter_number_not_promoted_and_first_period_not_opened():
    assert not reference_groups('Liber cap. 3.','analytic-tome-rows-v1')
    gs=groups('Alpha, I, 4. Liber cap. 3 explicatur, 10.')
    assert [g['raw'] for g in gs]==['I, 4.','10.']


def test_residual_classes_and_subtraction_account_for_all_material():
    assert residual_cause({'raw':'Numerus Romanus tomum indicat.','reason':'open'})=='editorial_title_or_numbering_notice'
    assert residual_cause({'raw':'154.','reason':'unowned'})=='unowned_number_or_page_marker'
    assert residual_cause({'raw':'Virga ejus my-','reason':'open_fragment_at_region_or_column_end'})=='column_or_page_continuation'
    assert [p['raw'] for p in remainder('abcdefghi',0,9,[(2,4),(6,8)])]==['ab','ef','i']
    raw='<bloco tipo="texto_principal">Alpha, 4. Rest</bloco>'
    a=raw.index('Alpha');b=a+9
    record={'raw':raw,'page':{'source':'fixture','sha256':digest(raw.encode()),
         'entries':[{'char_start':a,'char_end':b}], 'residuals':[]}}
    result=account_source(record)
    assert sum(result['categories'].values())==len(raw)
    assert result['categories']['unparsed_source_text']==4
    assert ''.join(raw[r['start']:r['end']] for r in result['runs'])==raw


def fixture(tmp_path):
    paths=[];sources=[];parts=[]
    for n,text in enumerate(['Pulchritudo laudatur','et mystice explanatur, I, 1054.'],1):
        path=tmp_path/f'teste/PG046/text/file-{n}.txt';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        image=tmp_path/f'{n}.png';image.write_bytes(b'fixture')
        entries=[]
        if n==2:
            entries=[{'id':'right','source':str(path),'physical_page':n,'entry_raw':text,
                      **source_span(text,0,len(text)),'reference_groups':groups(text),
                      'unit_kind':'row_candidate','quality':{},'flags':[],'context_candidate_id':None}]
        page={'source':str(path),'sha256':digest(text.encode()),'physical_page':n,
              'profile':'analytic-tome-rows-v1','entries':entries,
              'residuals':[] if entries else [{'raw':text,**source_span(text,0,len(text)),'reason':'open'}]}
        artifact=tmp_path/f'page{n}.json';artifact.write_text(json.dumps(page))
        sources.append({'source':str(path),'sha256':page['sha256'],'physical':n,'artifact':str(artifact)})
        parts.append({'source':str(path),'source_sha256':page['sha256'],'raw':text,
                      **source_span(text,0,len(text)),'image':str(image),'image_sha256':digest(b'fixture')})
        paths.append(path)
    manifest=tmp_path/'selection.json';manifest.write_text(json.dumps({'version':SELECTION_VERSION,'sources':sources}))
    reviews=tmp_path/'reviews.json';review={'decision':'assemble_layout_only','fragments':parts}
    reviews.write_text(json.dumps({'version':'logical-continuity-review-v1','reviews':[review]}))
    return manifest,reviews,review,paths


def test_multi_file_assembly_keeps_sources_and_does_not_duplicate_reference(tmp_path):
    manifest,reviews,_,paths=fixture(tmp_path)
    original=[p.read_bytes() for p in paths]
    result=run(manifest,tmp_path/'out',reviews,tmp_path)
    assert result['logical_entries']==1 and result['effective_reference_groups']==1
    assert result['residual_records_fully_used_by_continuity']==1
    con=readonly(tmp_path/'out/structured/index.sqlite')
    try:
        assert con.execute('SELECT count(*) FROM reference_groups').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM reference_claims').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM logical_fragments').fetchone()[0]==2
        r=con.execute('SELECT * FROM effective_reference_groups').fetchone()
        assert r['owner_text']=='Pulchritudo laudatur et mystice explanatur'
        assert r['literal']=='I, 1054.'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    finally:con.close()
    assert original==[p.read_bytes() for p in paths]


@pytest.mark.parametrize('bad',['duplicate','image','source','decision'])
def test_bad_assembly_cannot_claim_references(tmp_path,bad):
    manifest,reviews,review,_=fixture(tmp_path)
    rs=[review]
    if bad=='duplicate':rs.append(deepcopy(review))
    if bad=='image':review['fragments'][0]['image_sha256']='stale'
    if bad=='source':review['fragments'][0]['source_sha256']='stale'
    if bad=='decision':review['decision']='candidate'
    reviews.write_text(json.dumps({'version':'logical-continuity-review-v1','reviews':rs}))
    with pytest.raises(ValueError):run(manifest,tmp_path/'out',reviews,tmp_path)
    con=sqlite3.connect(tmp_path/'out/structured/index.sqlite')
    try:assert con.execute('SELECT count(*) FROM logical_entries').fetchone()[0]==0
    finally:con.close()

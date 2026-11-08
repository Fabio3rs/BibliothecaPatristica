from tools.indexing.source_recovery_evidence import compare_anchor, find_anchor
from tools.indexing.entry_parser_experiment import digest
import json
import pytest
from tools.indexing.source_recovery_evidence import run
from tools.indexing.source_quality_issues import VERSION


def test_normalized_ligature_match_retains_exact_source_offsets():
    raw='Prologus. Æthiopia quid significat, I, 766.'
    [hit]=find_anchor(raw,'Aethiopia')
    assert raw[slice(*hit['span'])]==hit['quote']=='Æthiopia'
    assert digest(hit['quote'].encode())==hit['text_sha256']
    assert raw[slice(*hit['context_span'])]==hit['context']


def test_missing_anchor_is_evidence_not_automatic_repair():
    r=compare_anchor('Æternum. Ævum.','Æthiopia quid significat, I, 766.','Aethiopia','missing_printed_entries')
    assert r['status']=='missing_anchor_present_in_alternative'
    assert not r['complete_entry_recovered'] and not r['automatic_repair_allowed']


def test_repetition_count_does_not_delete_either_occurrence():
    r=compare_anchor('nec dare potuerunt; nec dare potuerunt','nec dare potuerunt','nec dare potuerunt','duplicated_transcription')
    assert r['status']=='fewer_anchor_occurrences_in_alternative'
    assert len(r['current_matches'])==2


def test_absent_or_shared_anchor_is_not_missing_text_evidence():
    assert compare_anchor('Alpha','Beta','Gamma','missing_printed_entries')['status']=='anchor_not_found_in_alternative'
    assert compare_anchor('Alpha','Alpha','Alpha','missing_printed_entries')['status']=='context_available_for_review'


def test_numbers_are_not_normalized_and_headers_do_not_supply_anchor():
    assert not find_anchor('I, 766.','I, 768')
    raw='<bloco tipo="cabecalho">Æthiopia</bloco><bloco tipo="texto_principal">Ævum</bloco>'
    assert not find_anchor(raw,'Æthiopia')


@pytest.mark.parametrize('tamper',[None,'image','snapshot'])
def test_staging_checks_actual_snapshot_and_image_hash(tmp_path,tamper):
    page={'volume':'PL101','physical':728,'issues':[{'id':'missing','kind':'missing_printed_entries',
          'description':'Entry absent','visual_location':'left column','reviewer':'assistant',
          'recovery_status':'pending','search_anchors':['Æthiopia']}]}
    for field,text in [('source','Æternum. Ævum.'),('image','image'),('review_report','visual review')]:
        p=tmp_path/field;p.write_text(text)
        page[field]=str(p);page[field+'_sha256']=digest(p.read_bytes())
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'version':VERSION,'pages':[page]}))
    alternative=tmp_path/'alternative.txt';alternative.write_text('Æthiopia quid significat, I, 766.')
    h=digest(alternative.read_bytes())
    version={'id':1,'comparison_status':'compared','matches_current_file':False,
             'snapshot':str(alternative),'computed_text_sha256':h,'text_hash':h,
             'image_hash':page['image_sha256'],'engine':'fixture','model':None}
    if tamper=='image':version['image_hash']='wrong'
    if tamper=='snapshot':alternative.write_text('modified')
    audit=tmp_path/'audit';audit.mkdir()
    (audit/'inventory.json').write_text(json.dumps([{**page,'versions':[version]}]))
    result=run(registry,[audit],tmp_path/'out')
    assert result['automatic_repairs']==0
    if tamper:
        assert result['exclusions'] and result['comparisons']==0
    else:
        assert result['issues_with_missing_anchor_evidence']==['missing']
        assert result['comparisons']==1

from tools.indexing.residual_recovery_campaign import evaluate_page, fill_unprocessed
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.index_conservation_audit import coverage


def fixture(tmp_path,text,kind='thematic_index'):
    raw='<bloco tipo="texto_principal">'+text+'</bloco>'
    p=tmp_path/'page.txt';p.write_text(raw)
    meta={'source':str(p),'volume':'PG999','physical':1,'source_sha256':digest(raw.encode()),'fill_missing':True}
    scopes=[{'span':[0,len(raw)],'section_id':'s','kind':kind,'opening_status':'unknown_first_observed'}]
    baseline={**meta,'scopes':scopes,**coverage(raw,scopes,[])}
    return meta,baseline,None


def test_unprocessed_page_is_actually_parsed_with_literal_groups(tmp_path):
    task=fixture(tmp_path,'Alpha verbum, 12.\n\nBeta verbum, 24.')
    r=evaluate_page(task)
    assert r['execution_before']=='not_processed'
    assert r['execution_after']=='processed_with_candidates'
    assert r['fill_missing']['attempted']
    assert len(r['additions'])==2
    assert [x['reference_groups'][0]['raw'] for x in r['additions']]==['12','24']
    assert r['after']['residual']==0


def test_no_candidates_is_distinct_from_no_execution(tmp_path):
    r=evaluate_page(fixture(tmp_path,'Textus nullo numero explicitus.'))
    assert r['fill_missing']['attempted']
    assert r['execution_after']=='processed_without_candidates'
    assert r['after']['residual']>0


def test_unsupported_region_has_explicit_abstention(tmp_path):
    r=evaluate_page(fixture(tmp_path,'Alpha verbum, 12.','work_catalog'))
    assert not r['fill_missing']['attempted']
    assert r['execution_after']=='abstained_profile_unknown'
    assert r['fill_missing']['reason']=='unsupported_region_kind'


def test_old_results_are_not_replaced_by_fallback(tmp_path):
    meta,base,_=fixture(tmp_path,'Alpha verbum, 12.')
    r=evaluate_page((meta,base,{'sha256':meta['source_sha256'],'entries':[],'profile':'older'}))
    assert not r['fill_missing']['attempted']
    assert not r['additions']


def test_boundary_is_not_parsed_as_owned_index(tmp_path):
    r=evaluate_page(fixture(tmp_path,'Alpha verbum, 12.\n\nORDO RERUM\n\nBeta verbum, 24.'))
    assert len(r['additions'])==1
    assert r['additions'][0]['entry_raw']=='Alpha verbum, 12.'
    assert r['after']['residual']>0


def test_warning_and_unconsumed_qualifiers_remain_residual(tmp_path):
    r=evaluate_page(fixture(tmp_path,'Alpha anno 300, 12.\n\nBeta verbum, 24. sqq.\n\nGamma verbum, 33. D'))
    assert not r['additions']
    assert r['fill_missing']['raw_candidates']==3
    assert len(r['fill_missing']['rejected'])==3


def test_no_cross_scope_or_duplicate_claim(tmp_path):
    meta,base,_=fixture(tmp_path,'Alpha verbum, 12.\n\nBeta verbum, 24.')
    raw=open(meta['source']).read()
    base['scopes'][0]['span']=[0,raw.index('Beta')+3]
    rows,_=fill_unprocessed(raw,meta,base['scopes'],base['runs'],[])
    assert len(rows)==1
    repeated,_=fill_unprocessed(raw,meta,base['scopes'],base['runs'],rows)
    assert not repeated

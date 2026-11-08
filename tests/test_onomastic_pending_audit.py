from tools.indexing.onomastic_pending_audit import classify_scope, reason, source_checks, reviewed_scope_conflict, matched_scope


def test_mixed_index_not_counted_as_pure_names():
    assert classify_scope({},[dict(index_type='subject_and_name_index',evidence_priority=80)])[0] == 'mixed_names_and_other_material'


def test_page_only_assignment_is_possible_not_strong():
    assert classify_scope({},[dict(index_type='author_index',evidence_priority=20)])[0] == 'onomastic_possible'
    assert classify_scope({},[])[0] == 'unclassified'


def test_conflicting_top_types_preserve_uncertainty():
    assert classify_scope({},[dict(index_type=t,evidence_priority=80) for t in ['author_index','lexical_index']])[0] == 'onomastic_possible'


def test_reviewed_section_overrides_weaker_metadata():
    meta=dict(region_kind='index_unspecified',reviewed_source_section={'kind':'onomastic_index'})
    assert classify_scope(meta,[])[0]=='onomastic_assigned'


def test_reference_spans_and_relocation(tmp_path):
    p=tmp_path/'source.txt';p.write_text('Aaron 17, 3. Moses 18, 5.')
    item={'source':str(p),'ref_raw':'17, 3.'}
    assert source_checks(item,{'source_span':[6,12]})[0]==['literal_reference_at_span']
    assert source_checks(item,{'source_span':[1,2]})[0]==['reference_relocatable']
    assert source_checks({**item,'ref_raw':'999'}, {})[0]==['reference_not_literal_in_current_source']


def test_operational_reasons_do_not_claim_ocr_failure():
    assert reason({'ref_raw':'vide Aaron'}, {}, [])=='cross_reference_join'
    assert reason({'ref_raw':'ibid. 17'}, {}, [])=='editorial_inheritance'
    assert reason({'ref_raw':'17, 3'}, {'page_line_tokens':[{}]}, [])=='parsed_locator_without_destination'
    assert reason({'ref_raw':'17'}, {}, [])=='numeric_reference_without_current_diagnostic'
    assert reason({'ref_raw':'17'}, {}, ['reference_not_literal_in_current_source'])=='source_or_span_reconciliation'


def test_local_contents_title_flags_wrong_inherited_names():
    item={'source':'source','volume':'PO003'}
    metadata={'namespace_evidence':{'raw':'TABLE DES MATIÈRES'}}
    assert reviewed_scope_conflict(item,metadata,'TABLE DES MATIÈRES\nI. Chapitre',[])['reason']=='local_non_onomastic_title'
    assert reviewed_scope_conflict(item,metadata,'Other text',[]) is None


def test_reviewed_scope_preserves_mixed_page_boundary(tmp_path):
    p=tmp_path/'scan-297.txt';p.write_text('Previous section\nTABLE DES NOMS PROPRES\nAaron 1')
    rule=dict(volume='PO015',start_physical=297,end_physical=299,
              opening_source=str(p),opening_literal='TABLE DES NOMS PROPRES',actual_kind='onomastic_index')
    item={'volume':'PO015','source':str(p)}
    assert matched_scope(item,{'source_span':[0,8]},[rule]) is None
    assert matched_scope(item,{'source_span':[40,47]},[rule])==rule
    assert matched_scope({**item,'source':str(tmp_path/'scan-300.txt')},{},[rule]) is None

import json
from pathlib import Path
from types import SimpleNamespace

from tools.indexing.bounded_index_audit import (
    expected_range, numeric_alternative, query_variants, reading_key, relocate_row, run, select_rows,
)


def test_missing_number_yields_bounded_files_not_asserted_marker():
    pages = {p:dict(file=f'/ocr/{p}.txt') for p in range(1,9)}
    profile = dict(body_pages=[1,8], max_marker_files=4,max_window_pages=4)
    result = expected_range(12,pages,{(10,2):{},(14,5):{}},profile)
    assert result['pages'] == [2,3,4,5]
    assert result['basis'] == 'bracketed_numbering_range'
    assert expected_range(1,pages,{(10,2):{},(14,5):{}},profile)['pages'] == []
    assert expected_range(12,pages,{(10,2):{},(14,8):{}},profile)['pages'] == []
    assert expected_range(12,pages,{(10,5):{},(14,2):{}},profile)['pages'] == []


def test_ambiguous_ranges_do_not_expand_to_whole_work():
    pages = {p:dict(file=f'/ocr/{p}.txt') for p in range(1,30)}
    profile = dict(body_pages=[1,29],max_marker_files=4,max_window_pages=4)
    supported = {(10,1):{},(10,20):{},(14,3):{},(14,23):{}}
    assert expected_range(12,pages,supported,profile)['pages'] == []


def test_query_cleanup_proposal_requires_adjacent_list_punctuation():
    raw = 'ἀρτιφαὴς ἀνήρ 104, 174'
    owner = raw[:raw.index(',')]
    row = dict(owner_raw=owner,owner_span=[0,len(owner)],reference_span=[len(owner)+2,len(raw)])
    variants,flags = query_variants(row,raw)
    assert variants[1]['text'] == 'ἀρτιφαὴς ἀνήρ'
    assert variants[1]['possible_reference_numbers'] == ['104']
    assert not variants[1]['canonical_repair']
    assert 'possible_reference_list_swallowed_by_owner' in flags
    assert len(query_variants(row,raw.replace(',',';'))[0]) == 2
    assert len(query_variants(row,raw.replace(',','.'))[0]) == 1


def test_selection_is_stable_and_keeps_targeted_cases_separate():
    rows = [dict(item_id=str(i),profile='p',candidates=[] if i<3 else ['file']) for i in range(10)]
    a = select_rows(rows,1,711,0,['9'])
    assert a == select_rows(list(reversed(rows)),1,711,0,['9'])
    assert sum(c=='unlinked_input' for r,c in a) == 3
    assert len({r['item_id'] for r,c in a}) == len(a)


def test_numeric_history_keeps_flank_punctuation_without_losing_reading():
    check=dict(field='reference',status='numeric_reading_disagreement',version_id=1,
               alternative_quote=', 240. \n',page_identity='volume_page_registration')
    assert numeric_alternative(check)['numbers']==[240]
    check['alternative_quote']=',31, 75. '
    assert numeric_alternative(check)['numbers']==[31,75]
    for quote in ['cap. 31','31-75','31 A',', . ']:
        check['alternative_quote']=quote
        assert numeric_alternative(check) is None


def test_prefix_edit_relocates_owner_and_adjacent_reference_together():
    row=dict(owner_raw='Aaron',owner_span=[0,5],reference_raw='12',reference_span=[7,9])
    updated=relocate_row(row,'New header. Aaron, 12. Elsewhere 12.')
    assert updated['owner_span']==[12,17]
    assert updated['reference_span']==[19,21]
    assert updated['original_reference_span']==[7,9]
    assert updated['source_span_relocated']
    # A changed reference must not be paired with another occurrence of 12.
    assert relocate_row(row,'New header. Aaron, 13. Elsewhere 12.')==row


def test_partial_ranking_prefers_compact_supported_expression():
    def reading(width,score):
        return dict(query_variant=dict(kind='current_owner'),document_kind='current_ocr',
                    lexical=dict(method='partial',coverage=1,matches=[dict(start=0,end=width,score=score)]))
    assert reading_key(reading(20,1)) < reading_key(reading(200,1))
    assert reading_key(reading(20,1)) < reading_key(reading(20,0.8))


def test_reviewed_gap_examples_keep_continuation_pages():
    for number,lower,upper,start,end,expected,previous in [
        (82,81,84,417,419,{418,419},1),(116,115,117,430,431,{431},1),
        (126,125,127,434,435,{435},1),(174,173,178,453,455,{454,455},1),
        (176,173,178,453,455,{454,455},1),(181,180,182,302,304,{303,304},0),
        (190,189,191,312,315,{313,314},0)]:
        pages={p:dict(file=f'/ocr/{p}.txt') for p in range(start-1,end+1)}
        profile=dict(body_pages=[start-1,end],max_marker_files=4,max_window_pages=4,previous_page_context=previous)
        result=expected_range(number,pages,{(lower,start):{},(upper,end):{}},profile)
        assert expected <= set(result['pages'])
        assert result['basis']=='bracketed_numbering_range'


def test_cli_pipeline_deterministic_source_unchanged(tmp_path):
    text = tmp_path/'teste/PL999/text'
    text.mkdir(parents=True)
    (text/'fixture-001.txt').write_text('<texto_principal>1 initium.</texto_principal>')
    (text/'fixture-002.txt').write_text('<texto_principal>Aaron venit.</texto_principal>')
    (text/'fixture-003.txt').write_text('<texto_principal>3 finis.</texto_principal>')
    source = text/'fixture-004.txt'
    source.write_text('Notice. Aaron 2')
    profile = dict(id='p',volume='PL999',body_pages=[1,3],maximum_number=3,
        notice=dict(source=str(source.relative_to(tmp_path)),quote='Notice.'),
        anchors=[dict(number=n,physical=n,source=str((text/f'fixture-{n:03}.txt').relative_to(tmp_path)),
                      quote=f'{n} '+word) for n,word in [(1,'initium.'),(3,'finis.')]],
        sequence=dict(min_anchors=3,max_number_gap=2,max_file_gap=3),max_marker_files=4,max_window_pages=4)
    plan=tmp_path/'plan.json';plan.write_text(json.dumps({'profiles':[profile]}))
    row=dict(item_id='item',profile='p',source=str(source),owner_raw='Aaron',owner_span=[8,13],
             reference_raw='2',reference_span=[14,15],candidates=[])
    inp=tmp_path/'input.jsonl';inp.write_text(json.dumps(row)+'\n')
    args=SimpleNamespace(input=inp,plan=plan,root=tmp_path,out=tmp_path/'one',versions_db=None,
                         target_history=False,max_versions=3,max_number_gap=8,max_pages=6,
                         controls=0,seed=711,limit=0,include_item=[])
    before=source.read_bytes()
    one=run(args)
    args.out=tmp_path/'two'
    two=run(args)
    assert one['logical_sha256']==two['logical_sha256']
    result=json.loads((tmp_path/'one/results.jsonl').read_text())
    assert result['candidates'][0]['physical']==2
    assert result['candidates'][0]['method']=='exact'
    assert result['status']=='range_exact'
    assert not result['candidates'][0]['certified']
    assert source.read_bytes()==before

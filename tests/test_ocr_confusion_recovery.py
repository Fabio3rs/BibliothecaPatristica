import json
from types import SimpleNamespace

from tools.indexing.ocr_confusion_recovery import (
    build_catalog, classify, join_soft_wraps, match_with_wraps, metrics, propose,
    run, simple_numbers, single_edit,
)


def test_edit_directions_and_no_multi_edit_guess():
    assert single_edit('210', '240') == ('substitution', '1', '4')
    assert single_edit('351', '31') == ('deletion', '5', '')
    assert single_edit('31', '351') == ('insertion', '', '5')
    assert single_edit('βαρύφροτος', 'βαρύφορτος') == ('transposition', 'ρο', 'ορ')
    assert single_edit('123', '456') is None
    assert single_edit('111', '111') is None
    assert simple_numbers(', 31, 75. ') == ['31', '75']
    assert simple_numbers('cap. 31') is None
    assert simple_numbers('31-75') is None


def test_segmentation_is_distinct_from_lexical_disagreement():
    check = dict(field='owner', current_tokens=['homo','104'], alternative_tokens=['homo','174'],
                 current_numbers=['104'], alternative_numbers=['174'])
    assert classify(check) == ['numeric_material_in_owner_possible_segmentation','numeric_substitution']
    assert classify(dict(field='owner',current_tokens=['mortu','orum'], alternative_tokens=['mortuorum'])) == ['token_split_or_fusion']


def reference_row(item, source, old='210', new='240', versions=1):
    return dict(item_id=item, source=source, profile='p', index_version_checks=[dict(
        field='reference', status='numeric_reading_disagreement', current_quote=old,
        alternative_quote=new, current_tokens=[old], alternative_tokens=[new],
        current_numbers=[old], alternative_numbers=[new], version_id=i, page_identity='same_image_bytes',
    ) for i in range(versions)])


def test_duplicate_versions_do_not_multiply_independent_support():
    _, rules = build_catalog([reference_row('a','one',versions=10)])
    row = dict(item_id='target',source='two',profile='p')
    assert rules[0]['supporting_items'] == 1
    assert propose(210,row,rules,740) == []


def test_excludes_entire_target_page_and_other_editorial_profiles():
    _, rules = build_catalog([reference_row('a','one'),reference_row('b','one'),reference_row('c','two')])
    row = dict(item_id='target',source='one',profile='p')
    assert propose(210,row,rules,740) == []
    row['source'] = 'three'
    suggestions = propose(210,row,rules,740)
    assert suggestions[0]['number'] == 240
    assert propose(210,row,rules,226) == []
    row['profile'] = 'other'
    assert propose(210,row,rules,740) == []


def test_no_zero_or_leading_zero_number():
    _, rules = build_catalog([reference_row('a','one','12','02'),reference_row('b','two','12','02')])
    assert propose(12,dict(item_id='c',source='three',profile='p'),rules,740) == []


def test_soft_wrap_match_preserves_raw_spans_and_regular_hyphens():
    raw = '<texto_principal>Mortu-\norum exempla respicere.</texto_principal>'
    evidence = match_with_wraps('Mortuorum exempla respicere',raw)
    assert evidence['method'] == 'normalized'
    assert evidence['normalization'] == 'possible_soft_wrap_join'
    hit = evidence['matches'][0]
    assert raw[hit['start']:hit['end']] == hit['text_raw'] == 'Mortu-\norum exempla respicere'
    assert join_soft_wraps('a-b 12-\n34 <x>')[0] == 'a-b 12-\n34 <x>'
    assert match_with_wraps('Mortu-\norum exempla', 'Mortuorum exempla')['method'] == 'normalized'


def test_metrics_keep_all_gold_and_ignore_unreviewed_links():
    rows = [dict(item_id='a',baseline_files=['/a','/x'],candidates=[dict(file='/b'),dict(file='/a')])]
    labels = [dict(item_id='a',human_reviewed=False,complete_gold=True,gold_files=['/a','/b'])]
    assert metrics(rows,labels)['status'] == 'awaiting_independent_review'
    labels[0]['human_reviewed'] = True
    result = metrics(rows,labels)
    assert result['metrics']['baseline:Recall@5'] == .5
    assert result['metrics']['expanded:Recall@5'] == 1
    assert result['metrics']['expanded:Precision@5'] == .4


def test_experiment_is_reproducible_and_keeps_baseline(tmp_path, monkeypatch):
    import tools.indexing.ocr_confusion_recovery as module
    source = tmp_path/'page-003.txt'
    source.write_text('Mortuorum exempla 2')
    page = tmp_path/'page-002.txt'
    page.write_text('Mortu-\norum exempla')
    reading = dict(query_variant=dict(kind='current_owner',text='Mortuorum exempla'),
                   document_kind='current_ocr',lexical=module.match_text('Mortuorum exempla',page.read_text()))
    row = dict(item_id='a',profile='p',cohort='unlinked_input',source=str(source),
        owner_raw='Mortuorum exempla',owner_span=[0,17],reference_raw='2',reference_span=[18,19],
        flags=[],index_version_checks=[],status='range_absent', candidates=[dict(
            file=str(page),physical=2,sha256=module.digest(page),method=reading['lexical']['method'],best_reading=reading)])
    inp = tmp_path/'input.jsonl'
    inp.write_text(json.dumps(row)+'\n')
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps(dict(profiles=[dict(id='p',volume='PL999',maximum_number=10)])))
    monkeypatch.setattr(module,'load_series',lambda p,r: ({2:dict(file=str(page),raw=page.read_text(),sha256=module.digest(page))},{},{}))
    args=SimpleNamespace(input=inp,out=tmp_path/'one',plan=plan,root=tmp_path,versions_db=None,
                         max_versions=5,min_support=2,max_pages=6,max_alternatives=3,labels=None,seed=711)
    before = source.read_bytes(),page.read_bytes(),inp.read_bytes()
    first = run(args)
    args.out=tmp_path/'two'
    second = run(args)
    assert first['logical_sha256'] == second['logical_sha256']
    assert before == (source.read_bytes(),page.read_bytes(),inp.read_bytes())
    assert first['counts']['unlinked_input:new_lexical'] == 1
    result=json.loads((args.out/'results.jsonl').read_text())
    assert result['candidates'][0]['file'] == str(page)
    assert result['candidates'][0]['best_reading']['lexical']['normalization'] == 'possible_soft_wrap_join'

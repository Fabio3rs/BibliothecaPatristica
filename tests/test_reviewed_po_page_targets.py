"""Regression tests for bounded PO pagination, not semantic certification."""
from pathlib import Path
import json
import pytest

from tools.indexing.reviewed_po_page_targets import (
    build_map, build_series, header_markers, locate, page_candidates,
    reference_requests, routes,
)


def entry(section, reference, namespace='page', **kwargs):
    return {'section_id':section, 'reference_raw':reference, 'reference_span':[50,50+len(reference)],
            'source':'fixture.txt', 'reference_namespace':namespace, **kwargs}


def numbers(e):
    return [x['number'] for x in reference_requests(e)]


def test_biblical_verses_are_never_page_requests():
    e=entry('PO007:martyrs-scripture','58',owner_raw='Genèse xii, 3')
    assert numbers(e)==[58]
    assert reference_requests(entry('PO007:martyrs-scripture','Voir Aaron','cross_reference'))==[]


def test_page_line_tokens_keep_only_page():
    raw='435,12. — A 224'
    e=entry('PO002:martyrs-greek-names',raw,'page_line',page_line_tokens=[{
        'page_raw':'435','line_raw':'12','raw':'435,12','span':[50,56]}])
    assert numbers(e)==[435]


def test_line_tail_is_not_flattened_into_page_range():
    assert numbers(entry('PO011:luc-greek-index','169, 12-22','page_line'))==[169]
    for sid,ref,ns,first in [('po008-plerophories-greek','169, 12-22','page_line_or_page_list',169),
                             ('po008-plerophories-subjects','163, 12-16','page',163)]:
        rs=reference_requests(entry(sid,ref,ns))
        assert rs[0]['number']==first
        assert all(r['interpretation']=='alternative_page_list_reading' and r['score_cap']==.56 for r in rs[1:])


def test_lost_typography_repeated_pairs_keep_page_candidates_only():
    e=entry('po008-plerophories-syriac-words','40, 2 71, 4 100, 8 102, 12','page_line_or_page_list')
    assert [r['number'] for r in reference_requests(e) if not r.get('score_cap')]==[40,71,100,102]


def test_abbreviated_page_ranges_and_note_numbers():
    assert numbers(entry('PO011:ammonas-names-subjects','431-2.','page_line'))==[431,432]
    e=entry('PO011:ammonas-names-subjects','393 n. 1, 409 n. 1, 419 n. 3.','page_line')
    assert numbers(e)==[393,409,419]
    e['page_line_tokens']=[{'page_raw':'1','line_raw':'409','raw':'1, 409','span':[57,63]}]
    assert numbers(e)==[393,409,419]
    assert set(numbers(entry('po008-plerophories-subjects','23,116,151')))=={23,116,151}
    assert numbers(entry('PO011:ammonas-names-subjects','452, p. 395.','page_line'))==[395]


def test_long_manuscript_page_ranges_and_subscript_lines():
    e=entry('po008-plerophories-manuscripts','5, 11 à 156')
    assert numbers(e)==[5,*range(11,157)]
    assert numbers(entry('po008-plerophories-manuscripts','168₁₂₋₁₃170'))==[168,170]


def test_shelfmark_and_book_canon_namespaces_not_claimed():
    assert routes(entry('po008-apostles-canons-subjects','I. 40, 50','book_canon_page'))==[]
    assert numbers(entry('po008-plerophories-greek','IV,378-379','qualified_page'))==[]


def test_header_namespaces_ignore_body_numbers_and_apparatus():
    raw='<bloco tipo="cabecalho">194 VIE DE LUC [50]</bloco><bloco tipo="texto_principal">777</bloco>'
    assert [x['number'] for x in header_markers(raw,'bracket')]==[50]
    assert [x['number'] for x in header_markers(raw,'plain')]==[194]
    for ns in ['bracket','plain']:
        for marker in header_markers(raw,ns):assert raw[slice(*marker['span'])]==marker['raw']


def series(tmp_path,headers,namespace='bracket'):
    grouped={}
    for physical,header in headers.items():
        path=tmp_path/f'x-{physical}.txt'
        path.write_text(f'<bloco tipo="cabecalho">{header}</bloco><bloco tipo="texto_principal">Corpus</bloco>')
        grouped[physical]=[path]
    return build_series({'first':min(headers),'last':max(headers),'min':1,'max':20,'namespace':namespace},grouped)


def test_interpolation_requires_agreeing_local_anchors(tmp_path):
    s=series(tmp_path,{10:'[5]',11:'[6]',12:'TITLE',13:'[8]',14:'[9]'})
    c=page_candidates(s,7)
    assert len(c)==1 and c[0]['physical']==12
    assert c[0]['method']=='interpolated_between_agreeing_headers'


def test_discontinuous_numbering_not_bridged_across_gap(tmp_path):
    s=series(tmp_path,{10:'[5]',11:'[6]',12:'[15]',13:'[16]'})
    c=page_candidates(s,10)
    assert c==[]


def test_namespace_recovery_needs_a_bracket_anchor(tmp_path):
    s=series(tmp_path,{10:'[5] 100',11:'101 CHAPTER 6',12:'[7] 102'})
    assert page_candidates(s,6)[0]['physical']==11
    assert page_candidates(s,6)[0]['uncertainties']==['brackets_lost_in_ocr']
    s=series(tmp_path,{10:'100 TITLE 5',11:'101 TITLE 6',12:'102 TITLE 7'})
    assert not s['anchors']


def test_short_continuation_does_not_link_blank_leaf(tmp_path):
    s=series(tmp_path,{10:'TITLE',11:'[6]',12:'[7]'})
    assert page_candidates(s,5)[0]['physical']==10
    s['pages']['10']['has_body']=False
    assert page_candidates(s,5)==[]


def test_reviewed_current_corpus_routes_and_evidence():
    root=Path(__file__).resolve().parents[1]
    m=build_map(root)
    cases=[('PO007:martyrs-scripture','58','PO007',68),
           ('PO011:luc-greek-index','111','PO011',261),
           ('PO011:isaac-names','49','PO011',355),
           ('PO002:martyrs-greek-names','435','PO002',445),
           ('PO013:sargis-french-names','149','PO013',59),
           ('PO013:sargis-french-names','18','PO003',576),
           ('PO011:ammonas-syriac-words','25','PO010',591)]
    for sid,ref,vol,physical in cases:
        c=locate(entry(sid,ref),m)
        assert any(x['physical']==physical and f'/{vol}/' in x['file'] for x in c), (sid,ref,c)
        for candidate in c:
            for proof in candidate['evidence']:
                for anchor in proof['target_header_evidence']:
                    assert Path(anchor['source']).read_text()[slice(*anchor['span'])]==anchor['raw']
    assert json.loads(json.dumps(m))['version']==m['version']


def test_page_line_interval_never_creates_a_range_of_lines_as_pages():
    for raw,expected in [('167, 8 à 167, 27. Deux',[167]),
                         ('163,27 à 164,14; meurt en450,164,13',[163,164]),
                         ('163, 27–164, 14',[163,164])]:
        e=entry('po008-plerophories-subjects',raw)
        requests=reference_requests(e)
        assert numbers(e)==expected
        assert all(r['interpretation']=='explicit_page_line_interval' for r in requests)
        assert all(raw[slice(r['span'][0]-50,r['span'][1]-50)]==r['raw'] for r in requests)


def test_plain_manuscript_interval_remains_a_page_range():
    assert numbers(entry('po008-plerophories-manuscripts','5, 11 à 156'))==[5,*range(11,157)]

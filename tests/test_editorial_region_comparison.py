import hashlib
import json

from tools.indexing.editorial_region_comparison import compare, load_sections, run
from tools.indexing.parser_region_audit import assignment, orphan_page


def section(sid, end, kind='thematic_index'):
    return {'section_id': sid, 'volume': 'PG009', 'kind': kind,
            'spans': [{'source': '/source.txt', 'source_sha256': 'abc', 'physical': 1, 'span': [0, end]}]}


def test_comparison_preserves_removed_and_new_regions_without_quality_claim(tmp_path):
    before, after, out = [tmp_path/p for p in ['before', 'after', 'out']]
    for folder in [before, after]:
        folder.mkdir()
    (before/'sections.jsonl').write_text('\n'.join(json.dumps(s) for s in [section('a', 80), section('b', 20)]))
    (after/'sections.jsonl').write_text('\n'.join(json.dumps(s) for s in [section('a', 40), section('c', 20)]))
    result = run(before, after, out)
    assert result['changes'] == {'less_assigned_text': 1, 'removed_opening': 1, 'new_opening': 1}
    assert result['quality_measured'] is False
    changes = [json.loads(s) for s in (out/'changes.jsonl').read_text().splitlines()]
    assert all(c['quality_verdict'] is None for c in changes)


def test_equal_spans_reclassified_are_not_unchanged(tmp_path):
    a,b = tmp_path/'a.jsonl',tmp_path/'b.jsonl'
    a.write_text(json.dumps(section('a', 20)))
    b.write_text(json.dumps(section('a', 20, 'work_catalog')))
    assert compare(load_sections(a), load_sections(b))[0]['change'] == 'reclassified'


def entry(raw, start=0, end=None):
    end = len(raw) if end is None else end
    return {'char_start': start, 'char_end': end,
            'text_sha256': hashlib.sha256(raw[start:end].encode()).hexdigest()}


def region(start, end, sid='a', kind='thematic_index'):
    return {'span': [start,end], 'section_id': sid, 'kind': kind}


def test_alignment_allows_only_whitespace_between_same_section_spans():
    raw = 'Aaron,\n  12.'
    status,_ = assignment(raw, entry(raw), [region(0,6), region(9,12)])
    assert status == 'inside_index_candidate'
    raw = 'Aaron,XX 12.'
    status,_ = assignment(raw, entry(raw), [region(0,6), region(9,12)])
    assert status == 'partially_covered'


def test_alignment_preserves_cross_boundary_and_general_material():
    raw = 'Aaron, 12.'
    assert assignment(raw, entry(raw), [region(0,6),region(6,len(raw),'b')])[0] == 'crosses_candidate_sections'
    assert assignment(raw, entry(raw), [region(0,len(raw),kind='work_catalog')])[0] == 'inside_general_or_other_material'
    assert assignment(raw, entry(raw), [region(0,len(raw),sid=None)])[0] == 'outside_candidate_regions'


def test_alignment_rejects_changed_literals():
    raw = 'Aaron, 12.'
    old = entry('Aaron, 13.')
    assert assignment(raw, old, [region(0,len(raw))])[0] == 'invalid_entry_span'


def test_orphan_index_header_is_queue_not_invented_section():
    page = {'source':'a', 'source_sha256':'abc', 'volume':'PO006', 'physical':712,
            'signals':[{'role':'running_header','kind':'onomastic_index','quote':'TABLE DES NOMS PROPRES'}]}
    result = orphan_page(page, [{'id':'x'}])
    assert result['candidate_count'] == 1 and result['section_opening_known'] is False
    assert result['verified'] is False and 'section_id' not in result
    page['signals'][0]['kind'] = 'contents_catalog'
    assert orphan_page(page, [{'id':'x'}]) is None

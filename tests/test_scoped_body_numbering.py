import json
import sqlite3

import pytest

from tools.indexing.scoped_body_numbering import (
    anchor_allows, body_occurrences, load_series, locate, reference_numbers, run,
)


def test_numbers_in_headers_notes_and_xml_attributes_are_not_body_markers():
    raw = ('<cabecalho>308</cabecalho><nota>309</nota>'
           '<texto_principal pagina="999">310 CAPUT IX.</texto_principal>')
    found = body_occurrences(raw, 1000)
    assert set(found) == {310}
    start, end = found[310][0]['span']
    assert raw[start:end] == '310'


def test_reference_requires_local_ownership_but_not_whole_file_hash():
    meta = dict(reference_span=[6,9], reference_raw='308', owner_span=[0,5], owner_raw='Aaron')
    assert reference_numbers({}, meta, 'Aaron 308 unrelated correction') == ([308], None)
    assert reference_numbers({}, meta, 'Aaron 309')[1] == 'reference_span_changed'
    assert reference_numbers({}, meta, 'Moyse 308')[1] == 'owner_span_changed_or_missing'
    for literal in ['ibid. 308', 'II, 308', '308-310', '308 A', '308. 10']:
        meta.update(reference_span=[6,6+len(literal)], reference_raw=literal)
        assert reference_numbers({}, meta, 'Aaron '+literal)[1] == 'notation_requires_other_grammar'
    meta.update(reference_span=[6,14], reference_raw='308, 310')
    assert reference_numbers({}, meta, 'Aaron 308, 310')[0] == [308,310]


def test_visual_anchors_reject_repeated_verse_sequence_on_wrong_pages():
    anchors = [{'number':14,'physical':393},{'number':64,'physical':412}]
    assert anchor_allows(14,393,anchors)
    assert not anchor_allows(14,410,anchors)
    assert not anchor_allows(12,410,anchors)
    assert not anchor_allows(65,400,anchors)
    assert anchor_allows(30,400,anchors)


def test_page_candidates_preserve_alternatives_and_missing_numbers():
    pages = {p:dict(file=f'/ocr/{p}.txt',sha256='hash',occurrences={10:[{'span':[2,4]}]})
             for p in range(1,8)}
    profile = dict(body_pages=[1,7], max_marker_files=4, max_window_pages=4)
    candidates, missing = locate([10,99],pages,{(10,2):{},(10,3):{},(11,4):{}},profile)
    assert {c['physical'] for c in candidates} == {2,3,4}
    assert missing == {'99':'no_supported_body_marker'}
    assert all(c['localization_level']=='ocr_page_file' and not c['passage_verified'] for c in candidates)
    assert not locate([99],pages,{},profile)[0]
    assert not locate([10],pages,{(10,p):{} for p in range(1,6)},profile)[0]


def fixture_plan(tmp_path):
    text = tmp_path/'teste'/'PL999'/'text'
    text.mkdir(parents=True)
    for physical in range(1,6):
        (text/f'fixture-{physical:03}.txt').write_text(
            f'<texto_principal>{physical} body</texto_principal>')
    index = text/'fixture-006.txt'
    index.write_text('Notice. Aaron 3')
    profile = dict(id='fixture',volume='PL999',section_ids=['chosen'],body_pages=[1,5],
        maximum_number=5,notice=dict(source=str(index.relative_to(tmp_path)),quote='Notice.'),
        anchors=[dict(number=3,physical=3,source=str((text/'fixture-003.txt').relative_to(tmp_path)),
                      quote='3 body',source_sha256='old_hash')],
        sequence=dict(min_anchors=3,max_number_gap=1,max_file_gap=1),
        max_marker_files=4,max_window_pages=4)
    return profile,index


def test_anchor_accepts_unrelated_source_edit_and_rejects_changed_quote(tmp_path):
    profile,index = fixture_plan(tmp_path)
    file = tmp_path/profile['anchors'][0]['source']
    file.write_text(file.read_text()+'<nota>New unrelated note</nota>')
    assert (3,3) in load_series(profile,tmp_path)[1]
    file.write_text(file.read_text().replace('3 body','4 body'))
    with pytest.raises(ValueError, match='Anchor quote'):
        load_series(profile,tmp_path)


def test_overlay_replays_deterministically_and_respects_mixed_page_section(tmp_path):
    profile,index = fixture_plan(tmp_path)
    plan = tmp_path/'plan.json'
    plan.write_text(json.dumps({'profiles':[profile]}))
    catalog = tmp_path/'catalog.sqlite'
    db = sqlite3.connect(catalog)
    db.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
    for section in ['chosen','other_section_same_page']:
        meta = dict(section_id=section,reference_raw='3',reference_span=[14,15],
                    owner_raw='Aaron',owner_span=[8,13])
        db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                   (section,section,'PL999','Aaron','3',str(index),'source_only',json.dumps(meta)))
    db.commit()
    db.close()
    before = catalog.read_bytes()
    one = run(catalog,plan,tmp_path/'one',tmp_path)
    two = run(catalog,plan,tmp_path/'two',tmp_path)
    assert one['logical_sha256'] == two['logical_sha256']
    assert one['profiles'][0]['selected_items'] == 1
    assert one['profiles'][0]['newly_linkable_items'] == 1
    assert catalog.read_bytes() == before
    assert (tmp_path/'one'/'results.jsonl').read_bytes() == (tmp_path/'two'/'results.jsonl').read_bytes()


def test_greek_namespace_excludes_latin_notes_and_numbers():
    raw = ('<texto_principal>120 Verbum Latinum longum.</texto_principal>'
           '<texto_principal>120 Θέρμη γάρ τις ἐγγίνεται τῇ καρδίᾳ.</texto_principal>'
           '<nota>121 Ἑλληνικά</nota>')
    found = body_occurrences(raw, 234, 'greek_body')
    assert len(found[120]) == 1
    assert 'Θέρμη' in found[120][0]['quote']
    assert 121 not in found


def test_fascicle_headers_exclude_general_pagination_and_marginal_lines():
    raw = ('<cabecalho>522 HISTOIRE [34</cabecalho>'
           '<nota_marginal>5 10 15</nota_marginal>'
           '<nota_marginal>[35]</nota_marginal>'
           '<texto_principal>36 body</texto_principal>')
    assert set(body_occurrences(raw, 179, 'fascicle_header')) == {34,35}


def test_two_sided_gap_preserves_internal_range_but_interpolates_page_sequence():
    from tools.indexing.scoped_body_numbering import bounded_gap
    supported = {(10,100):{}, (14,104):{}}
    profile = dict(max_window_pages=9, mode='greek_body')
    assert bounded_gap(12,supported,profile)['pages'] == [100,101,102,103,104]
    profile['mode'] = 'fascicle_header'
    assert bounded_gap(12,supported,profile)['pages'] == [102]
    assert bounded_gap(9,supported,profile) is None
    assert bounded_gap(12,{(10,100):{},(14,105):{}},profile) is None
    assert bounded_gap(12,{(10,100):{},(10,101):{},(14,104):{}},profile) is None


def test_subscript_is_detail_not_another_page():
    literal = '18₆; 19₈'
    meta = dict(reference_span=[6,6+len(literal)],reference_raw=literal,
                owner_span=[0,5],owner_raw='Aaron')
    assert reference_numbers({},meta,'Aaron '+literal,'page_subscript')[0] == [18,19]
    assert not meta['_parsed_locators']['residuals']


def test_interpolated_gap_does_not_claim_an_observed_marker():
    pages = {p:dict(file=f'/ocr/{p}.txt',sha256='hash',occurrences={n:[{'span':[0,2]}]})
             for p,n in [(100,10),(101,11),(102,12)]}
    profile = dict(mode='fascicle_header',gap_recovery=True,body_pages=[100,102],
                   max_marker_files=4,max_window_pages=9)
    candidates,missing = locate([11],pages,{(10,100):{},(12,102):{}},profile)
    assert not missing and len(candidates) == 1
    assert candidates[0]['physical'] == 101
    assert candidates[0]['evidence'][0]['marker_file'] is None
    assert len(candidates[0]['evidence'][0]['bounding_markers']) == 2


def test_page_list_preserves_every_page_in_small_printed_ranges():
    from tools.indexing.scoped_body_numbering import page_list
    parsed = page_list('156, 157, 173-176; 85 à 90')
    assert [x['page'] for x in parsed['locators']] == [156,157,173,174,175,176,85,86,87,88,89,90]
    assert not parsed['residuals']
    assert page_list('109₁₁')['residuals']
    assert page_list('12-9')['residuals']

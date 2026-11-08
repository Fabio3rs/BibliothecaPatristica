import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.literal_target_links import VERSION, build_map, interpret_group, locate, rank, run


def test_complete_lists_preserve_occurrences_and_do_not_inherit():
    result=interpret_group('17 a, 17 a, b.',40)
    assert [r['number'] for r in result]==[17,17,None]
    assert result[0]['char_start']==40 and result[1]['char_start']==46
    assert interpret_group('580 a — 581 b.',0) is None
    assert interpret_group('II, 17 a.',0) is None
    assert interpret_group('17 a,',0) is None
    assert interpret_group('ibid.',0) is None
    assert interpret_group('17 a Phoca',0) is None


def map_fixture(tmp_path):
    root=tmp_path/'teste';directory=root/'PG001/text';directory.mkdir(parents=True)
    proofs=[]
    for i in range(1,5):
        raw=f'<bloco tipo="cabecalho" script="latino">TITLE {i} 90a</bloco>\n'+f'<bloco tipo="texto_principal" script="latino">Work {i}a text {i}b text.</bloco>\n'+f'<bloco tipo="nota" script="latino">90b note</bloco>'
        (directory/f'page-{i}.txt').write_text(raw)
    for role,i in [('index_notice',1),('target_work_title',2),('target_continuation_title',3)]:
        path=directory/f'page-{i}.txt'
        proofs.append({'role':role,'path':str(path.relative_to(root)),'sha256':digest(path.read_bytes()),'quote':f'TITLE {i}'})
    profile={'version':VERSION,'notation':'explicit_number_ab_list','evidence':proofs,
             'index_scope':{'volume':'PG009','first':10,'last':10},
             'targets':[{'volume':'PG001','first':1,'last':4}],'max_files':4}
    profile_path=tmp_path/'profile.json';profile_path.write_text(json.dumps(profile))
    return root,profile,profile_path


def test_map_ignores_header_and_notes_and_keeps_source_offsets(tmp_path):
    root,profile,_=map_fixture(tmp_path)
    pages,markers,inventory,skipped=build_map(profile,root)
    assert len(pages)==4 and len(markers)==8
    assert all(m['number']!=90 and m['sequence_support'] for m in markers)
    for m in markers:
        assert Path(m['path']).read_text()[m['char_start']:m['char_end']]==m['raw']


def test_windows_keep_all_files_and_do_not_pick_ambiguous_marker(tmp_path):
    root,profile,_=map_fixture(tmp_path)
    pages,markers,_,_=build_map(profile,root)
    by={m['rank']:[m] for m in markers}
    assert locate({'number':1,'letter_raw':'a'},pages,by,4)['status']=='unique_file_window_candidate'
    result=locate({'number':1,'letter_raw':'b'},pages,by,4)
    assert [p['physical'] for p in result['files']]==[1,2]
    by[rank(1,'b')]*=2
    assert locate({'number':1,'letter_raw':'a'},pages,by,4)['extent_status']=='ambiguous_end'
    repeated=locate({'number':1,'letter_raw':'b'},pages,by,4)
    assert repeated['status']=='marker_file_candidate'
    assert repeated['extent_status']=='ambiguous_start_position_same_file'
    assert [p['physical'] for p in repeated['files']]==[1]
    assert locate({'number':None,'letter_raw':'b'},pages,by,4)['status']=='context_required'
    last=locate({'number':4,'letter_raw':'b'},pages,by,4)
    assert last['status']=='marker_file_candidate'
    assert last['extent_status']=='missing_supported_end'
    assert [p['physical'] for p in last['files']]==[4]
    assert locate({'number':2,'letter_raw':'b'},pages,by,1)['extent_status']=='window_exceeds_limit'
    by[rank(1,'a')].append({**by[rank(1,'a')][0], 'ordinal':2, 'id':'other'})
    ambiguous=locate({'number':1,'letter_raw':'a'},pages,by,4)
    assert ambiguous['status']=='ambiguous_markers' and not ambiguous['files']
    assert not locate({'number':9,'letter_raw':'a'},pages,by,4)['files']


def test_missing_or_multiple_physical_files_block_mapping(tmp_path):
    root,profile,_=map_fixture(tmp_path)
    (root/'PG001/text/other-1.txt').write_text('alternate')
    with pytest.raises(ValueError,match='ambiguous physical'):
        build_map(profile,root)


def test_reversed_markers_are_not_links():
    by={2:[{'id':'a','ordinal':1,'char_end':20}],3:[{'id':'b','ordinal':0,'char_start':5}]}
    assert locate({'number':1,'letter_raw':'a'},[],by,4)['status']=='reversed_markers'


def test_end_to_end_separate_wal_and_stale_sources_fail(tmp_path):
    root,profile,profile_path=map_fixture(tmp_path)
    path=root/'PG009/text/page-10.txt';path.parent.mkdir(parents=True)
    raw='Alpha, 1 a, b.';path.write_text(raw)
    proposal={'id':'p1','source':str(path.resolve()),'source_sha256':digest(raw.encode()),
              'volume':'PG009','physical':10,'section_id':'s','entry_raw':raw,**source_span(raw,0,len(raw)),
              'reference_groups':[{'raw':'1 a, b.',**source_span(raw,7,len(raw)),
                'owner_text_raw':'Alpha','owner_span':source_span(raw,0,5)}]}
    proposals=tmp_path/'proposals.jsonl';proposals.write_text(json.dumps(proposal)+'\n')
    before={str(p):p.read_bytes() for p in root.rglob('*.txt')}
    result=run(proposals,profile_path,root,tmp_path/'out')
    assert result['statuses']=={'unique_file_window_candidate':1,'context_required':1}
    assert result['cross_volume_candidate_occurrences']==1
    assert before=={str(p):p.read_bytes() for p in root.rglob('*.txt')}
    with sqlite3.connect(tmp_path/'out/links.sqlite') as con:
        assert con.execute('pragma journal_mode').fetchone()[0]=='wal'
        assert not con.execute('pragma foreign_key_check').fetchall()
        assert con.execute('select count(*) from candidates').fetchone()[0]==1
    path.write_text('changed OCR')
    with pytest.raises(ValueError,match='Reference OCR changed'):
        run(proposals,profile_path,root,tmp_path/'stale')
    assert not (tmp_path/'stale').exists()


def test_changed_editorial_declaration_blocks_route(tmp_path):
    root,_,profile_path=map_fixture(tmp_path)
    (root/'PG001/text/page-1.txt').write_text('new')
    with pytest.raises(ValueError,match='Editorial evidence changed'):
        run(tmp_path/'irrelevant.jsonl',profile_path,root,tmp_path/'out')

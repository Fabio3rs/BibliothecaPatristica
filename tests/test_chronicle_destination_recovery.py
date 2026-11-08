import hashlib
import json
from pathlib import Path
import sqlite3

from tools.indexing.chronicle_destination_recovery import (
    build_map, candidate_years, interpret_reference, page_rows, rank_candidates, recover,
)


def block(text, kind='texto_principal'):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def test_abraham_axis_and_exact_offsets_exclude_apparatus():
    raw = block('HEBR. ASSYR.\n16 6 37 16\n17 7 38 17\n18 8 39 18')+block('1000 1001 1002','aparato_critico')
    rows=page_rows(raw,133)
    assert [r['year'] for r in rows]==[16,17,18]
    assert all(raw[s:e]==r['raw'] for r in rows for s,e in [r['span']])


def test_olympic_column_is_not_abraham_year():
    raw=block('OLYMP. ABRAHAMUS\n154 1852 15 11 348\n1853 16 1 349\n1854 17 2 350')
    assert [r['year'] for r in page_rows(raw,213)]==[1852,1853,1854]


def test_late_header_and_judaean_column():
    raw=block('Long prose. '*40+'\nOLYMP. CHRISTUS JUD. ROM.\n48 5 7\n49 4 8\n207 50 5 9')
    assert [r['year'] for r in page_rows(raw,230)]==[48,49,50]


def test_christus_and_olympic_fields():
    raw=block('OLYMP. CHRISTUS ROM.\n226 2\n227 3\n252 228 4')
    assert [r['year'] for r in page_rows(raw,245)]==[226,227,228]


def test_embedded_pairs_and_isolated_numbers_not_year_axis():
    assert page_rows(block('anno 25 26 narrat.\nAnno 27 28 memorat.\n1000'),180)==[]


def test_offsets_are_candidates_with_boundary_alternative():
    assert candidate_years(1852)==[('abraham',1852,'literal_abraham_year')]
    assert candidate_years(2241)==[('christus',226,'legend_offset_2015'),('christus',227,'adjacent_year_edition_alternative')]
    assert {x[:2] for x in candidate_years(2015)}=={('abraham',2015),('christus',1)}
    assert interpret_reference('ibid.')['numbers']==[]
    assert interpret_reference('2241 seq.')['residual']=='2241 seq.'
    assert interpret_reference('2241-2243')['numbers']==[]


def page(path):
    return {'file':str(path),'physical':245,'series':'christus','year_min':226,'year_max':228,
            'rows':[{'year':226,'span':[0,3],'raw':'226'},{'year':228,'span':[4,7],'raw':'228'}],
            'source_sha256':'example','text':'Ulpianus jurisconsultus assessor Alexandri',
            'sequence_conflicts':[]}


def test_missing_literal_year_keeps_approximation_and_alternatives(tmp_path):
    path=tmp_path/'target.txt';path.write_text('226 228')
    candidates=rank_candidates(2242,'Ulpianus jurisconsultus',[page(path)])
    assert len(candidates)==1
    assert len(candidates[0]['evidence']['supports'])==2
    assert 'year_between_observed_rows_not_literal' in candidates[0]['uncertainties']
    assert not candidates[0]['certified']
    assert candidates[0]['navigable']
    assert not rank_candidates(2416,'Stilico consul',[page(path)])


def test_readonly_catalog_overlay_compatible_and_missing_legend_null(tmp_path):
    db=tmp_path/'catalog.sqlite';target=tmp_path/'target.txt';target.write_text('226 228')
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
        c.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',('i','g','PL027','Ulpianus jurisconsultus','2241','source.txt','source_only','{}'))
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    summary=recover(db,tmp_path,tmp_path/'out',pages=[page(target)])
    assert summary['counts']['with_candidates']==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
    with sqlite3.connect(tmp_path/'out/relations.sqlite') as c:
        e=json.loads(c.execute('SELECT evidence_json FROM relations').fetchone()[0])
        assert e['item_id']=='i' and e['group_id']=='g' and e['legend'] is None
        assert c.execute('SELECT navigable FROM candidates').fetchone()[0]==1
        assert json.loads(c.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])['catalog']==str(db)


def test_neighbor_bounded_numeric_blocks_without_bbox(tmp_path):
    directory=tmp_path/'teste/PL027/text';directory.mkdir(parents=True)
    (directory/'a-241.txt').write_text(block('OLYMP. CHRISTUS ROM.\n178 2\n179 3'))
    (directory/'a-242.txt').write_text(block('182\n183')+block('241')+block('2\n3'))
    (directory/'a-243.txt').write_text(block('OLYMP. CHRISTUS ROM.\n195 2\n196 3'))
    mapped={p['physical']:p for p in build_map(tmp_path)}
    assert [r['year'] for r in mapped[242]['rows']]==[182,183]
    assert len(mapped[242]['bounds_evidence'])==2


def test_real_ocr_spans_and_reviewed_targets():
    root=Path(__file__).resolve().parents[1]
    for physical,wanted in [(213,1852),(245,226),(230,48)]:
        path=next((root/'teste/PL027/text').glob(f'*-{physical:03}.txt'))
        raw=path.read_text();rows=page_rows(raw,physical)
        assert wanted in {r['year'] for r in rows}
        assert all(raw[r['span'][0]:r['span'][1]]==r['raw'] for r in rows)
    # No numeric correction of the systematic 4xxx instead of 1xxx readings.
    path=next((root/'teste/PL027/text').glob('*-175.txt'))
    assert page_rows(path.read_text(),175)==[]


def test_olympiads_never_replace_christ_year_in_missing_regnal_rows():
    raw=block('OLYMP. CHRISTUS ROM.\n278 333\n279 337\n338 28\n339 29')
    years={r['year'] for r in page_rows(raw,253)}
    assert 333 in years and 337 in years
    assert not years & {278,279}


def test_real_christ_tables_keep_year_namespace(tmp_path):
    root=Path(__file__).resolve().parents[1]
    directory=tmp_path/'teste/PL027/text';directory.mkdir(parents=True)
    for physical in range(250,256):
        source=next((root/'teste/PL027/text').glob(f'*-{physical}.txt'))
        (directory/source.name).write_text(source.read_text())
    pages={p['physical']:p for p in build_map(tmp_path)}
    assert (pages[253]['year_min'],pages[253]['year_max'])==(328,339)
    assert (pages[254]['year_min'],pages[254]['year_max'])==(340,352)
    assert 278 not in {r['year'] for r in pages[253]['rows']}
    assert not rank_candidates(2293,'example',[pages[253],pages[254]])


def test_plausible_numeric_notes_cannot_be_chronology(tmp_path):
    raw=block('OLYMP. CHRISTUS ROM.')+block('226\n227','aparato_critico')+block('228\n229','nota')
    assert page_rows(raw,245)==[]
    directory=tmp_path/'teste/PL027/text';directory.mkdir(parents=True)
    (directory/'a-241.txt').write_text(block('OLYMP. CHRISTUS ROM.\n178 2\n179 3'))
    (directory/'a-242.txt').write_text(block('182\n183','aparato_critico')+block('184\n185','nota'))
    (directory/'a-243.txt').write_text(block('OLYMP. CHRISTUS ROM.\n195 2\n196 3'))
    assert 242 not in {p['physical'] for p in build_map(tmp_path)}


def test_three_consecutive_numeric_apparatus_years_need_neighbor_evidence(tmp_path):
    directory=tmp_path/'teste/PL027/text';directory.mkdir(parents=True)
    (directory/'a-255.txt').write_text(block('OLYMP. CHRISTUS ROM.\n360 1\n361 2'))
    raw=block('362 21','aparato_critico')+block('363 22','aparato_critico')+block('364 23','aparato_critico')
    (directory/'a-256.txt').write_text(raw)
    assert page_rows(raw,256)==[]
    assert 256 not in {p['physical'] for p in build_map(tmp_path)}
    (directory/'a-257.txt').write_text(block('OLYMP. CHRISTUS ROM.\n365 3\n366 4'))
    pages={p['physical']:p for p in build_map(tmp_path)}
    assert {r['year'] for r in pages[256]['rows']}=={362,363,364}
    candidates=rank_candidates(2377,'example',[pages[256]])
    assert 'xml_block_type_conflict' in candidates[0]['uncertainties']

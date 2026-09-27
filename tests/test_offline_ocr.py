"""Small synthetic pages test provenance, noisy OCR, geometry and offline review."""
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_ocr.diff import edit_details, ocr_distance, sequence_diff, weighted_gap
from offline_ocr.layout import align, normalize_words, parse_llm, parse_tsv
from offline_ocr.review import RevisionConflict, corrected_xml, latest_revision, refine, save_revision
from offline_ocr.store import connect, get_comparison, ingest, read_version

HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'


def tsv(words):
    return HEADER + '1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t\n' + ''.join(
        f'5\t1\t{block}\t1\t{line}\t{i+1}\t{x}\t{y}\t{max(15,len(text)*7)}\t20\t80\t{text}\n'
        for i, (text, x, y, block, line) in enumerate(words))


def xml(text, box='0,0,480,900'):
    return f'<pagina><bloco tipo="texto_principal" script="latino" bbox="{box}">{text}</bloco><notas>não transcrito</notas></pagina>'


@pytest.fixture
def page(tmp_path):
    image = tmp_path / 'page.png'
    Image.new('RGB', (1000, 1000), 'white').save(image)
    text = tmp_path / 'page.txt'
    text.write_text(xml('Verbum caro factum.'))
    source = tmp_path / 'page.tsv'
    source.write_text(tsv([('Verbun', 20, 40, 1, 1), ('caro', 100, 40, 1, 1), ('factum.', 150, 40, 1, 1)]))
    return {'image_path': image, 'text_path': text, 'tsv_path': source}


def test_lossless_spans_entities_and_nested_tags():
    raw = xml('cré-\n dit &amp; <i>λόγος</i>')
    llm = parse_llm(raw)
    tokens = normalize_words(llm['words'], 'llm')
    assert [t['text'] for t in tokens] == ['crédit', '&', 'λόγος']
    assert tokens[0]['transform'] == 'linebreak_hyphen_candidate'
    assert [raw[a:b] for p in tokens[0]['pieces'] for a,b in p['source_spans']] == ['cré-', 'dit']
    assert [raw[a:b] for p in tokens[1]['pieces'] for a,b in p['source_spans']] == ['&amp;']
    assert 'não transcrito' not in ' '.join(t['text'] for t in tokens)
    flow = ' '.join(t['text'] for t in tokens)
    for t in tokens:
        assert flow[slice(*t['flow_span'])] == t['text']


def test_all_scripts_and_block_types_are_kept():
    llm = parse_llm('<pagina><bloco tipo="nota_marginal" script="grego">λόγος</bloco><bloco tipo="aparato_critico" script="siriaco">ܐܒ</bloco></pagina>')
    assert [b['script'] for b in llm['blocks']] == ['grego', 'siriaco']
    assert len(llm['words']) == 2


def test_no_join_across_blocks_or_blank_lines_or_inline_hyphens():
    raw = '<pagina><bloco>ver-</bloco><bloco>bum α-β inter-\n\n alia</bloco></pagina>'
    assert [t['text'] for t in normalize_words(parse_llm(raw)['words'], 'llm')] == ['ver-', 'bum', 'α-β', 'inter-', 'alia']


def test_tess_hyphen_retains_two_boxes():
    tess = parse_tsv(tsv([('crede-', 200, 30, 1, 1), ('ret', 20, 60, 1, 2)]), 1000, 1000)
    tokens = normalize_words(tess['words'], 'tesseract')
    assert tokens[0]['text'] == 'crederet'
    assert len(tokens[0]['pieces']) == 2
    assert tokens[0]['pieces'][0]['bbox'] != tokens[0]['pieces'][1]['bbox']
    assert len(normalize_words(tess['words'], 'tesseract', False)) == 2


def test_no_tess_join_between_columns():
    tess = parse_tsv(tsv([('crede-', 400, 30, 1, 1), ('ret', 550, 60, 2, 1)]), 1000, 1000)
    assert len(normalize_words(tess['words'], 'tesseract')) == 2


def test_invalid_tsv_and_transformed_coordinates_rejected():
    with pytest.raises(ValueError):
        parse_tsv('bad\n', 1000, 1000)
    with pytest.raises(ValueError, match='Dimensões'):
        parse_tsv(tsv([]), 1200, 1200)
    with pytest.raises(ValueError, match='coordenadas'):
        parse_tsv(tsv([('fora', 999, 3, 1, 1)]), 1000, 1000)


@pytest.mark.parametrize('a,b', [('præceptum','praeceptum'), ('ſcriptura','scriptura'), ('λόγος','λογος'), ('verbum','verbrn'), ('forma','forrna'), ('verbum','vebrum')])
def test_ocr_similarity_handles_invalid_words(a,b):
    assert ocr_distance(a,b) < .5
    assert sequence_diff([a],[b])[0][-1] == 'replace'


def test_insertion_does_not_shift_all_following_words():
    changes=sequence_diff(['Verbum','caro','factum','est'],['Verbun','autem','caro','factum','est'])
    assert [c[-1] for c in changes] == ['replace','tesseract_only','equal','equal','equal']


@pytest.mark.parametrize('a,b', [(['inprincipio'],['in','principio']), (['in','principio'],['inprincipio'])])
def test_split_merge(a,b):
    assert sequence_diff(a,b) == [(0,len(a),0,len(b),'split_merge')]


def test_large_unanchored_region_does_not_exhaust_memory():
    assert weighted_gap(['x']*501,['y']*501)[0][-1] == 'unresolved'


def test_character_diff_is_explicit():
    assert edit_details('verbum','verbun') == [{'op':'replace','llm_span':[5,6],'tesseract_span':[5,6]}]


def test_imperfect_llm_box_uses_nearby_and_text_anchors():
    llm=parse_llm(xml('Verbum scriptura sanctum', '100,100,450,400'))
    tess=parse_tsv(tsv([('Verbum', 80, 80, 1, 1), ('scriptura', 160, 80, 1, 1), ('sanctum', 250, 80, 1, 1)]),1000,1000)
    result=align(llm,tess)
    assert result['stats'] == {'equal':3}
    assert result['region_evidence']['b0']['anchor_bbox'][1] == 80
    assert result['region_evidence']['b0']['edge_delta'][1] == -20


def test_reordered_columns_use_text_and_geometry():
    llm=parse_llm('<pagina><bloco bbox="500,0,1000,900">dextera veritas</bloco><bloco bbox="0,0,480,900">sinistra littera</bloco></pagina>')
    tess=parse_tsv(tsv([('sinistra',20,40,1,1),('littera',120,40,1,1),('dextera',600,40,2,1),('veritas',700,40,2,1)]),1000,1000)
    result=align(llm,tess)
    assert result['stats'] == {'equal':4}
    assert sorted(x for link in result['links'] for x in link['tesseract_ids']) == sorted(t['id'] for t in result['tesseract_tokens'])


def test_overlapping_repeated_words_are_marked_uncertain():
    llm=parse_llm('<pagina><bloco bbox="0,0,600,600">Deus</bloco><bloco bbox="0,0,600,600">Deus</bloco></pagina>')
    tess=parse_tsv(tsv([('Deus',20,40,1,1)]),1000,1000)
    result=align(llm,tess)
    assert any(link['uncertain'] for link in result['links'] if link['tesseract_ids'])
    assert len(result['tesseract_tokens'][0]['region_candidates']) == 2


def test_blank_and_malformed_inputs_preserved():
    assert parse_llm('<pagina estado="vazio"/>')['words'] == []
    malformed = '<pagina><bloco bbox="bad">abc & xyz'
    result=parse_llm(malformed)
    assert result['raw']==malformed
    assert result['warnings']
    assert result['words']


def test_idempotence_and_independent_tess_cache(page,tmp_path):
    con=connect(tmp_path/'test.db')
    one=ingest(con,**page)
    two=ingest(con,**page)
    assert one['key']==two['key'] and two['reused'] and two['tesseract_cached']
    page['text_path'].write_text(xml('Verbum novo caro factum.'))
    three=ingest(con,**page)
    assert three['key']!=one['key'] and three['tesseract_cached']
    four=ingest(con,**page,tolerance=40)
    assert four['key']!=three['key'] and four['tesseract_cached']
    assert con.execute("SELECT count(*) FROM artifacts WHERE kind='tesseract'").fetchone()[0]==1


def test_parallel_dedup(page,tmp_path):
    path=tmp_path/'test.db'
    connect(path).close()
    def run(_):
        con=connect(path)
        try:return ingest(con,**page)['key']
        finally:con.close()
    with ThreadPoolExecutor(max_workers=3) as executor:
        keys=list(executor.map(run,range(6)))
    con=connect(path)
    assert len(set(keys))==1
    assert con.execute('SELECT count(*) FROM comparisons').fetchone()[0]==1


def test_review_escaping_history_idempotence_and_conflict(page,tmp_path):
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    result=get_comparison(con,key)
    rev=save_revision(con,key,0,{'ln0':'A & B < C'},{'b0':[10,20,490,910]},'conferido')
    assert save_revision(con,key,rev['id'],{'ln0':'A & B < C'},{'b0':[10,20,490,910]},'conferido')['id']==rev['id']
    assert 'A &amp; B &lt; C' in corrected_xml(result,rev)
    assert 'bbox="10,20,490,910"' in corrected_xml(result,rev)
    assert result['llm']['raw']==page['text_path'].read_text()
    with pytest.raises(RevisionConflict):save_revision(con,key,0,{'ln1':'x'},{})
    restore=save_revision(con,key,rev['id'],{'ln0':None},{'b0':None})
    assert corrected_xml(result,restore)==result['llm']['raw']
    assert con.execute('SELECT count(*) FROM revisions').fetchone()[0]==2


def test_hyphen_correction_preserves_source_ranges(page,tmp_path):
    page['text_path'].write_text(xml('ver-\nbum caro'))
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    result=get_comparison(con,key)
    rev=save_revision(con,key,0,{'ln0':'verbo'},{})
    assert '>verbo\n caro</bloco>' in corrected_xml(result,rev)


def test_refine_reuses_tess_and_links_parent(page,tmp_path):
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    save_revision(con,key,0,{'ln0':'Verbun'},{})
    new=refine(con,key)
    assert refine(con,key)==new
    old,result=get_comparison(con,key),get_comparison(con,new)
    assert old['tess_key']==result['tess_key']
    assert result['llm']['provenance']['parent_comparison']==key
    assert result['alignment']['stats']=={'equal':3}
    assert latest_revision(con,key)['words']=={'ln0':'Verbun'}


def test_invalid_review_rejected(page,tmp_path):
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    with pytest.raises(ValueError):save_revision(con,key,0,{'nope':'x'},{})
    with pytest.raises(ValueError):save_revision(con,key,0,{}, {'b0':[2,3,1,5]})
    with pytest.raises(ValueError):save_revision(con,key,0,{}, {'b0':[0,0,float('nan'),5]})


def test_import_version_is_read_only_and_checks_image_hash(page,tmp_path):
    import ocr_versions_db as vdb
    path=tmp_path/'production.db';con=vdb.open_versions_db(path)
    engine=vdb.get_or_create_engine_version(con,engine='llm',model='test',prompt_key='test',system_prompt='test')
    result=vdb.record_ocr_result(con,volume_id='PG013',page_num=1,image_path=page['image_path'],engine_version_id=engine,text_content=page['text_path'].read_text())
    image_hash=vdb.sha256_file(page['image_path'])
    raw,meta=read_version(path,result,image_hash)
    assert raw==page['text_path'].read_text() and meta['result']['id']==result
    with pytest.raises(ValueError,match='imagem'):read_version(path,result,'bad')
    assert con.execute('SELECT is_current FROM ocr_results').fetchone()[0]==1


def test_optional_raster_export_is_clone(page,tmp_path):
    from offline_ocr.raster import render
    import io
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    before=page['image_path'].read_bytes()
    clone=render(get_comparison(con,key),latest_revision(con,key),['blocks','differences'])
    assert clone.startswith(b'\x89PNG')
    assert Image.open(io.BytesIO(clone)).height>1000
    assert page['image_path'].read_bytes()==before


def test_indices_are_used(page,tmp_path):
    con=connect(tmp_path/'test.db');ingest(con,**page)
    plan=con.execute('EXPLAIN QUERY PLAN SELECT * FROM comparisons WHERE image_hash=?',('x',)).fetchone()
    assert 'comparisons_image' in plan[3]
    plan=con.execute('EXPLAIN QUERY PLAN SELECT * FROM revisions WHERE comparison_key=? ORDER BY id DESC LIMIT 1',('x',)).fetchone()
    assert 'revisions_comparison' in plan[3]


def test_short_header_not_stolen_by_body_anchor_envelope():
    llm=parse_llm('<pagina><bloco tipo="cabecalho" bbox="0,0,1000,60">1857 IN LUCAM HOMILIA</bloco><bloco bbox="0,60,480,900">unusquisque consideret sanctum</bloco></pagina>')
    tess=parse_tsv(tsv([('1857',60,50,1,1),('IN',350,50,1,1),('LUCAM',400,50,1,1),('HOMILIA',500,50,1,1),('unusquisque',60,75,2,1),('consideret',150,75,2,1),('sanctum',250,75,2,1)]),1000,1000)
    result=align(llm,tess)
    header=next(link for link in result['links'] if link['llm_ids']==['ln0'])
    assert header['status']=='equal' and header['region']=='b0'


def test_distant_unique_word_is_not_confirmed_by_itself():
    result=align(parse_llm(xml('Verbum','0,0,200,200')),parse_tsv(tsv([('Verbum',800,800,1,1)]),1000,1000))
    assert result['links'][0]['uncertain']


def test_pagination_and_literal_prefix(page,tmp_path):
    from offline_ocr.store import list_comparisons
    con=connect(tmp_path/'test.db')
    first=ingest(con,**page)['key']
    second=ingest(con,**page,tolerance=40)['key']
    rows=list_comparisons(con,limit=1)
    next_rows=list_comparisons(con,after=rows[0]['key'],limit=1)
    assert {rows[0]['key'],next_rows[0]['key']}=={first,second}
    assert not list_comparisons(con,prefix='%')
    assert len(list_comparisons(con,prefix='pag'))==2
    with pytest.raises(ValueError):list_comparisons(con,limit=10000)


def test_refine_rejects_stale_revision(page,tmp_path):
    con=connect(tmp_path/'test.db');key=ingest(con,**page)['key']
    save_revision(con,key,0,{'ln0':'Verbo'},{})
    with pytest.raises(RevisionConflict):refine(con,key,expected_revision=0)


def test_decomposed_diacritic_hyphen_preserves_source():
    llm=parse_llm(xml('α\u0313-\nβ'))
    token=normalize_words(llm['words'],'llm')[0]
    assert token['text']=='α\u0313β'
    assert len(token['pieces'])==2
    assert token['key']=='ἀβ'


def test_recovered_entity_without_semicolon_does_not_swallow_next_character():
    raw=xml('A &amp B')
    parsed=parse_llm(raw)
    token=next(t for t in parsed['words'] if t['text']=='&')
    assert [raw[a:b] for a,b in token['source_spans']]==['&amp']


def test_duplicate_tsv_ids_rejected():
    source=tsv([('abc',10,10,1,1)])
    with pytest.raises(ValueError,match='duplicado'):
        parse_tsv(source+source.splitlines()[-1]+'\n',1000,1000)

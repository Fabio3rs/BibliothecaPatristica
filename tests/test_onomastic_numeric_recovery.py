import json

from tools.indexing.onomastic_numeric_recovery import locate, page_line, read_pages, recover


def page(n, anchors=(), numbers=(), body=True):
    return dict(file=f'/p/{n}.txt', physical=n, anchors=list(anchors),
        numbers=[dict(number=x,file=f'/p/{n}.txt',span=[0,1],raw=str(x)) for x in numbers],
        sha256='fixture',body=body)


def test_page_lines_not_pages():
    assert page_line('98, 11, 12.')==98
    assert page_line('74. 10')==74
    assert page_line('79 10')==79
    assert page_line('cf. Πτολεμαῖος 9ᵃ') is None
    assert page_line('33-39') is None


def test_missing_brackets_with_two_anchors():
    pages={1:page(1,[10],[10]),2:page(2,[],[11]),3:page(3,[12],[12])}
    c=locate(11,pages)[0]
    assert c['physical']==2 and c['score']==.86
    assert not c['evidence']['inferred']


def test_suppressed_page_inferred():
    pages={1:page(1,[10],[10]),2:page(2),3:page(3,[12],[12])}
    c=locate(11,pages)[0]
    assert c['evidence']['inferred'] and c['score']==.78


def test_conflicting_restart_rejected():
    pages={1:page(1,[10]),2:page(2,[1]),3:page(3,[12])}
    assert not locate(11,pages)


def test_one_anchor_not_enough():
    assert not locate(11,{1:page(1,[10]),2:page(2)})


def test_edge_bound_and_body():
    pages={1:page(1,[10]),2:page(2,[11]),3:page(3),4:page(4)}
    assert locate(12,pages)[0]['score']==.65
    assert not locate(13,pages)
    pages[3]['body']=False
    assert not locate(12,pages)


def test_ambiguity_retained():
    pages={1:page(1,[10]),2:page(2),3:page(3,[12]),20:page(20,[10]),21:page(21),22:page(22,[12])}
    assert [c['physical'] for c in locate(11,pages)]==[2,21]


def test_read_spans_and_source_change(tmp_path):
    directory=tmp_path/'PO015'/'text';directory.mkdir(parents=True)
    for n in [1,2,3]:
        (directory/f'x-{n}.txt').write_text(f'<cabecalho>[{n+9}]</cabecalho><texto_principal>Some body with enough alphabetic text</texto_principal>')
    pages=read_pages(tmp_path,dict(volume='PO015',target_physical_scope=[1,3]))
    for p in pages.values():
        for evidence in p['numbers']:
            assert (directory/f'x-{p["physical"]}.txt').read_text()[slice(*evidence['span'])]==evidence['raw']
    source=tmp_path/'index.txt';source.write_text('Name 11, 2')
    row=dict(id='a',group_id='b',volume='PO015',label='Name',ref_raw='11, 2',source=str(source),metadata_json=json.dumps(dict(source_span=[5,10],reference_interpretation=dict(notation_status='interpreted_page_line'))))
    assert recover(row,{},pages)['candidates']
    source.write_text('Name 12, 2')
    assert recover(row,{},pages)['reason']=='source_reference_changed_or_missing_span'

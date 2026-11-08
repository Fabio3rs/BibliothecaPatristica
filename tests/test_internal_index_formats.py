from tools.indexing.internal_index_formats import parse_internal_page,greek_number
from tools.indexing.internal_index_targets import scan_targets,locate
from tools.indexing.unrepresented_region_recovery import valid_entry


def parse(raw,family,context=None):
    return parse_internal_page(raw,family=family,source='ocr',volume='V',physical=1,
        scopes=[{'section_id':'s','span':[0,len(raw)]}],section_context=context)


def test_question_prefix_keeps_two_propositions_and_multiple_numbers():
    raw='<bloco tipo="texto_principal">(Lib. II, interrog. 15, 16.) Baptismus Patris:\nBaptismus Jesu.\n(I 82.) Calix vini:</bloco>'
    entries=parse(raw,'book_question')['entries']
    assert len(entries)==2
    assert entries[0]['internal_binding']=={'book':2,'numbers':[15,16]}
    assert entries[0]['owner_raw']=='Baptismus Patris:\nBaptismus Jesu.'
    assert all(valid_entry(raw,e) for e in entries)
    assert 'possible_continuation' in entries[1]['flags']


def test_marginal_greek_and_inline_latin_share_binding_not_spans():
    raw='''<bloco tipo="texto_principal" script="grego">Στοιχεῖον ἄλφα.</bloco>
<bloco tipo="nota_marginal" script="grego">Ϛ'.</bloco>
<bloco tipo="texto_principal" script="grego">Περὶ ἀγγέλων.</bloco>
<bloco tipo="texto_principal" script="latino">Littera alpha.</bloco>
<bloco tipo="texto_principal" script="latino">VI. De angelis.</bloco>'''
    entries=parse(raw,'letter_title')['entries']
    assert len(entries)==2 and all(valid_entry(raw,e) for e in entries)
    assert [e['internal_binding']['numbers'] for e in entries]==[[6],[6]]
    assert {e['internal_binding']['letter']['key'] for e in entries}=={'α'}


def test_marginal_association_cannot_jump_unrelated_markup():
    raw='''<bloco tipo="nota_marginal" script="grego">Α'.</bloco>
<nota>Outro contexto</nota><bloco tipo="texto_principal" script="grego">Περὶ Θεοῦ.</bloco>'''
    assert not parse(raw,'letter_title')['entries']
    assert greek_number('ΙΑ')==11 and greek_number('ΑΙ') is None


def test_book_boundary_and_alias_number_never_mix():
    pages=[{'source':'target','physical':30,'raw':'''<bloco tipo="texto_principal">LIBER PRIMUS
INTERROGATIO II (Alias 45).
Question one. Compare lib. II. in another work.
INTERROGATIO III.
Another question.
LIBER SECUNDUS
INTERROGATIO II.
Question two.</bloco>'''}]
    mapping=scan_targets(pages,'book_question')
    assert set(mapping)=={(1,2),(1,3),(2,2)}
    meta={'grammar':'book_question','reference_namespace':'book_question','internal_binding':{'book':2,'numbers':[2,45]},'language':'lat'}
    candidates,missing=locate({'label':'Question two'},meta,mapping)
    assert missing==[45] and candidates[0]['file']=='target'
    assert candidates[0]['evidence']['anchors'][0]['context']['key']==2
    assert not candidates[0]['certified']


def test_target_titles_require_explicit_letter_and_heading_syntax():
    raw='''<bloco tipo="texto_principal" script="latino">Tit. II. — No letter yet.
LITTERA A.
Tit. II. — Quod Deus vitari nequeat.
Tit. II, 10. Scripture citation.</bloco>'''
    mapping=scan_targets([{'source':'target','physical':10,'raw':raw}],'letter_title')
    assert list(mapping)==[('α',2)] and len(mapping[('α',2)])==1


def test_question_headings_with_footnote_marker_merged_title_and_apparatus():
    raw='''<bloco tipo="texto_principal">LIBER SECUNDUS
b INTERROGATIO LXXI (Alias 70).
EX ACTIBUS APOSTOLORUM. INTERROGATIO LXXXIII. Cum Jesus.</bloco>
<bloco tipo="aparato_critico">INTERROGATIO VI.
Quomodo Matthæus...</bloco>'''
    mapping=scan_targets([{'source':'target','physical':10,'raw':raw}],'book_question')
    assert set(mapping)=={(2,71),(2,83),(2,6)}
    meta={'grammar':'book_question','reference_namespace':'book_question','internal_binding':{'book':2,'numbers':[6]},'language':'lat'}
    candidates,missing=locate({'label':'Matthæus'},meta,mapping)
    assert not missing
    assert 'division_heading_in_ocr_apparatus_block' in candidates[0]['uncertainties']


def test_new_letter_clears_parallel_context_and_regression_is_auditable():
    raw='''<bloco tipo="texto_principal" script="grego">ΣΤΟΙΧΕΙΟΝ Α.
Τίτλ. Α'. — First.</bloco>
<bloco tipo="texto_principal" script="latino">LITTERA B.
Tit. I. — Second letter.</bloco>
<bloco tipo="texto_principal" script="grego">Τίτλ. Β'. — No Greek context.</bloco>
<bloco tipo="texto_principal" script="latino">LITTERA A.
Tit. III. — Possibly delta misread as A.</bloco>'''
    diagnostics=[]
    mapping=scan_targets([{'source':'target','physical':10,'raw':raw}],'letter_title',diagnostics)
    assert set(mapping)=={('α',1),('β',1)}
    assert diagnostics[0]['reason']=='letter_sequence_regression'
    a,b=diagnostics[0]['span']
    assert raw[a:b]==diagnostics[0]['raw']


def test_repeated_key_retains_alternatives_with_warning():
    pages=[{'source':f'page{n}','physical':n,'raw':'<bloco tipo="texto_principal">LITTERA A.\nTit. I. — Topic.</bloco>'} for n in (1,2)]
    mapping=scan_targets(pages,'letter_title')
    meta={'grammar':'letter_title','reference_namespace':'letter_title','internal_binding':{'letter':{'key':'α'},'numbers':[1]},'language':'lat'}
    candidates,_=locate({'label':'Topic'},meta,mapping)
    assert len(candidates)==2
    assert all('same_internal_key_in_multiple_files' in c['uncertainties'] for c in candidates)


def test_index_letter_regression_preserves_label_without_false_alpha_binding():
    raw='''<bloco tipo="texto_principal" script="latino">Littera K.
I. First kappa entry.
Littera A.
I. Disputed letter entry.</bloco>'''
    result=parse(raw,'letter_title')
    first,second=result['entries']
    assert first['internal_binding']['letter']['key']=='κ'
    assert second['internal_binding']['letter']['key'] is None
    assert second['internal_binding']['letter']['observed_key']=='α'
    assert 'letter_sequence_regression' in second['flags']
    assert all(valid_entry(raw,e) for e in result['entries'])


def test_printed_page_prefix_and_transmissions_do_not_merge_entries():
    raw='''<bloco tipo="texto_principal" script="latino">Littera A.
V. De servis.
285 VI. De discretione.
— Sequuntur transmissiones titulorum.
De justitia. Habetur in littera χ, titulo I.</bloco>'''
    result=parse(raw,'letter_title')
    assert [e['internal_binding']['numbers'] for e in result['entries']]==[[5],[6]]
    assert [e['owner_raw'] for e in result['entries']]==['De servis.','De discretione.']
    assert any('De justitia' in r['raw'] for r in result['residuals'])


def test_region_runner_dispatches_family_and_carries_letter_context(tmp_path):
    from tools.indexing.unrepresented_region_recovery import recover_volume
    sources=[]
    for n,text in enumerate(('Littera B.\nI. De bono.','II. De benignitate.'),1):
        path=tmp_path/f'index-{n:03}.txt'
        raw=f'<bloco tipo="texto_principal" script="latino">{text}</bloco>'
        path.write_text(raw)
        sources.append({'source':str(path),'physical':n,'span':[0,len(raw)]})
    section={'section_id':'s','volume':'PG999','kind':'thematic_index',
             'internal_index_family':'letter_title','spans':sources}
    result=recover_volume('PG999',[section])
    assert len(result['entries'])==2
    assert all(e['internal_binding']['letter']['key']=='β' for e in result['entries'])
    assert result['entries'][1]['internal_binding']['letter']['source']==str(tmp_path/'index-001.txt')


def test_cli_replays_explicit_plan_for_already_represented_volume(tmp_path,monkeypatch):
    import json
    import sqlite3
    from scripts.recover_internal_index_sections import main
    root=tmp_path/'teste'
    text=root/'PL998'/'text';text.mkdir(parents=True)
    (text/'part-001.txt').write_text('<bloco tipo="texto_principal">(I, 2.) A statement.</bloco>')
    (text/'part-002.txt').write_text('<bloco tipo="texto_principal">LIBER PRIMUS\nINTERROGATIO II.\nA statement.</bloco>')
    catalog=tmp_path/'catalog.sqlite'
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE items(volume TEXT)')
        db.execute("INSERT INTO items VALUES('PL998')")
    original=catalog.read_bytes()
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps({'sections':[{'id':'test','volume':'PL998','family':'book_question',
                                            'index_pages':[1,1],'body_pages':[2,2]}]}))
    for n in (1,2):
        out=tmp_path/f'run{n}'
        monkeypatch.setattr('sys.argv',['recover','--plan',str(plan),'--catalog',str(catalog),
                                       '--root',str(root),'--out',str(out),'--workers','1'])
        main()
        summary=json.loads((out/'localization.json').read_text())
        assert summary['counts']=={'with_destinations':1,'candidate_links':1}
        assert len((out/'body_manifest.jsonl').read_text().splitlines())==1
    assert catalog.read_bytes()==original

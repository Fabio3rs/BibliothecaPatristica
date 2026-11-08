import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from tools.indexing.internal_index_formats import parse_internal_page
from tools.indexing.internal_index_targets import scan_targets, locate
from tools.indexing.unrepresented_region_recovery import valid_entry


def parse(raw, family):
    return parse_internal_page(raw, family=family, source='source', volume='V', physical=1,
                              scopes=[{'section_id':'s','span':[0,len(raw)]}])


def test_bilingual_titles_brackets_and_unnumbered_appendix():
    raw='''<bloco tipo="texto_principal">[Τίτλος ΙΘ'. — Κατὰ Ἀγνοητῶν.]
Τίτλος ΚϚ'. — Κατὰ Μασσαλιανῶν.
Παράτιτλος. — Φωτίου πατριάρχου.</bloco>
<bloco tipo="texto_principal">Titulus XIX. — Contra Agnoetas.</bloco>'''
    entries=parse(raw,'title_number')['entries']
    assert [e['internal_binding']['numbers'] for e in entries]==[[19],[26],[],[19]]
    assert all(valid_entry(raw,e) for e in entries)
    assert 'Φωτίου' not in entries[1]['owner_raw']


def test_headings_exclude_running_headers_prose_and_other_divisions():
    raw='''<bloco tipo="cabecalho">493 DE VITA IN CHRISTO LIB. I. 494</bloco>
<bloco tipo="texto_principal">LIBER SEPTIMUS.
Qualis fit initiatus.
Compare Titulus I. in another work.
TITULUS II.
A title.
CAPUT III.</bloco>'''
    pages=[{'source':'target','physical':5,'raw':raw}]
    assert set(scan_targets(pages,'book_number'))=={('book_number',7)}
    assert set(scan_targets(pages,'title_number'))=={('title_number',2)}


def test_manuscript_folio_side_missing_folio_and_incipit():
    raw='''<bloco tipo="texto_principal">1. Init. sine alio titulo. Ἡ εἰς τὴν τελώνην παραβολή.
2. Fol. 3 verso. Κυριακὴ τοῦ ἀσώτου. Init. Ἀγαθὸς μὲν ὁ Θεὸς πρὸς πάντας ἐστὶ καὶ φιλάνθρωπος.
3. Sabbato S. Lazari. Initium: Τοῦ Κυρίου ἡμῶν.
4. Fol. 6. Incomplete without marker.</bloco>'''
    entries=parse(raw,'manuscript_homily')['entries']
    assert len(entries)==3 and all(valid_entry(raw,e) for e in entries)
    assert entries[0]['reference_raw'] is None
    assert entries[0]['incipit_raw'].startswith('Ἡ εἰς')
    assert entries[1]['internal_binding']['folio']==3
    assert entries[1]['internal_binding']['side']=='verso'
    assert entries[2]['internal_binding']['folio'] is None
    assert entries[1]['internal_binding']['numbers']==[]
    assert locate({'label':'test'},entries[1],{('manuscript_folio',3):[{}]})==([],[])


def test_incipit_matches_opening_not_any_mention_and_keeps_uncertainty():
    raw='<bloco tipo="texto_principal">53. Fol. 146. Κυριακῇ. Init. Εἶπεν ὁ Κύριος, Οὐδεὶς ἀναβέβηκεν εἰς τὸν οὐρανὸν εἰ μὴ ὁ ἐκ τοῦ.</bloco>'
    e=parse(raw,'manuscript_homily')['entries'][0]
    t='Εἰπεν ὁ Κύριος· Οὐδεὶς ἀναβέβηκεν εἰς τὸν οὐρανὸν εἰ μὴ ὁ ἐκ τοῦ οὐρανοῦ.'
    pages=[{'source':str(i),'physical':i,'raw':f'<bloco tipo="texto_principal">{prefix}{t}</bloco>'}
           for i,prefix in [(1,''),(2,'A quotation follows: ')]]
    candidates, _=locate({'label':'x'},e,scan_targets(pages,'manuscript_homily'))
    assert len(candidates)==1 and candidates[0]['file']=='1'
    assert not candidates[0]['certified']


def test_real_index_counts_and_literal_spans():
    for volume,physical,family,start,end,count in [
        ('PG130',22,'title_number','ΠΙΝΑΞ',None,36),
        ('PG130',23,'title_number',None,'EUTHYMII MONACHI',22),
        ('PG150',251,'book_number','INDEX LIBRORUM.','ΝΙΚΟΛΑΟΥ ΤΟΥ ΚΑΒΑΣΙΛΑ',14),
        ('PG150',130,'manuscript_homily',None,'Postea sequitur Lectionarium',55)]:
        raw=next(Path('teste',volume,'text').glob(f'*-{physical:03}.txt')).read_text()
        result=parse_internal_page(raw,family=family,source='source',volume=volume,physical=physical,
            scopes=[{'section_id':'s','span':[raw.index(start) if start else 0,raw.index(end) if end else len(raw)]}])
        assert len(result['entries'])==count
        assert all(valid_entry(raw,e) for e in result['entries'])


def test_runner_two_works_in_one_volume_do_not_share_number_map(tmp_path):
    root=tmp_path/'corpus';text=root/'V'/'text';text.mkdir(parents=True)
    for physical,body in [(1,'Titulus I. — Alpha.'),(2,'TITULUS I.\nAlpha.'),
                          (3,'Titulus I. — Beta.'),(4,'TITULUS I.\nBeta.')]:
        (text/f'file-{physical:03}.txt').write_text(f'<bloco tipo="texto_principal">{body}</bloco>')
    catalog=tmp_path/'catalog.sqlite'
    with sqlite3.connect(catalog) as db:db.execute('CREATE TABLE items(volume TEXT)')
    plan=tmp_path/'plan.json';plan.write_text(json.dumps({'sections':[
        {'id':s,'volume':'V','family':'title_number','index_pages':[i,i],'body_pages':[b,b]}
        for s,i,b in [('a',1,2),('b',3,4)]]}))
    out=tmp_path/'out'
    subprocess.run([sys.executable,'scripts/recover_internal_index_sections.py','--plan',str(plan),
        '--catalog',str(catalog),'--root',str(root),'--out',str(out),'--workers','1'],check=True,capture_output=True)
    with sqlite3.connect(out/'extraction/search.sqlite') as db:
        rows=db.execute('SELECT i.label,c.file FROM items i JOIN candidates c ON c.item_id=i.id ORDER BY i.label').fetchall()
    assert [(label,Path(file).name) for label,file in rows]==[('Alpha.','file-002.txt'),('Beta.','file-004.txt')]


def test_encoded_line_breaks_emphasis_and_stigma_preserve_evidence_offsets():
    raw='''<bloco tipo="texto_principal">Previous text.&amp;#10;&amp;#10;TITULUS VII.&amp;#10;Topic.</bloco>
<bloco tipo="texto_principal">[b]ΤΙΤΛΟΣ ΙΕ´.[/b]\nTopic.</bloco>
<bloco tipo="texto_principal">ΤΙΤΛΟΣ ςʹ.\nTopic.</bloco>'''
    mapping=scan_targets([{'source':'target','physical':1,'raw':raw}],'title_number')
    assert set(mapping)=={('title_number',7),('title_number',15),('title_number',6)}
    for anchors in mapping.values():
        for anchor in anchors:
            a,b=anchor['marker']['span']
            assert anchor['marker']['raw']==raw[a:b]


def test_parallel_ordinal_conflict_is_retained_and_flagged():
    raw='''<bloco tipo="texto_principal" script="latino">TITULUS X.\nAdversus.</bloco>
<bloco tipo="texto_principal" script="grego">ΤΙΤΛΟΣ Γʹ.\nΚατὰ.</bloco>'''
    diagnostics=[]
    mapping=scan_targets([{'source':'target','physical':1,'raw':raw}],'title_number',diagnostics)
    assert set(mapping)=={('title_number',10),('title_number',3)}
    assert len(diagnostics)==2
    e=parse('<bloco tipo="texto_principal">Titulus III. — De Deo.</bloco>','title_number')['entries'][0]
    candidates,_=locate({'label':e['owner_raw']},e,mapping)
    assert 'parallel_heading_ordinals_disagree' in candidates[0]['uncertainties']

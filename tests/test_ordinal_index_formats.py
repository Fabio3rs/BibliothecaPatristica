from tools.indexing.internal_index_formats import parse_internal_page
from tools.indexing.ordinal_index_formats import reconcile_bounded_ordinals
from tools.indexing.internal_index_targets import scan_targets,locate,add_bounded_section_candidates
from tools.indexing.unrepresented_region_recovery import valid_entry


def parse(raw,family='rule_number',page=1):
    return parse_internal_page(raw,family=family,source=f'page{page}',volume='PG999',physical=page,
        scopes=[{'section_id':'work','span':[0,len(raw)]}])


def test_arabic_and_greek_ordinals_not_running_page_number():
    raw='''<bloco tipo="cabecalho">695 S. BASILII MAGNI 693</bloco>
<bloco tipo="cabecalho" script="latino">77 De virginibus.\n78 De militibus.</bloco>
<bloco tipo="texto_principal" script="grego">αʹ Περὶ μετανοίας.\nβ᾽ Περὶ ἀγάπης.</bloco>'''
    entries=parse(raw)['entries']
    assert [e['internal_binding']['numbers'] for e in entries]==[[77],[78],[1],[2]]
    assert all(valid_entry(raw,e) for e in entries)


def test_column_soft_wrap_retains_both_literal_fragments():
    raw='''<bloco tipo="texto_principal">III. Auctor fideli-</bloco>
<bloco tipo="texto_principal">ter scripsit.\nIV. Nascitur.</bloco>'''
    entries=parse(raw,'section_number')['entries']
    assert len(entries)==2
    assert entries[0]['search_label']=='Auctor fideliter scripsit.'
    assert len(entries[0]['owner_fragments'])==2 and valid_entry(raw,entries[0])
    entries[0]['owner_fragments'][1]['raw']='different'
    assert not valid_entry(raw,entries[0])


def test_no_continuation_join_without_soft_wrap():
    raw='<bloco tipo="texto_principal">III. Auctor.</bloco><bloco tipo="texto_principal">Other text.</bloco>'
    result=parse(raw,'section_number')
    assert len(result['entries'][0]['owner_fragments'])==1
    assert any(r['raw']=='Other text.' for r in result['residuals'])


def test_soft_wrap_cannot_jump_intervening_text():
    raw='<bloco tipo="texto_principal">III. Auctor fideli-</bloco><nota>Other material</nota><bloco tipo="texto_principal">ter scripsit.</bloco>'
    result=parse(raw,'section_number')
    assert len(result['entries'][0]['owner_fragments'])==1
    assert any(r['raw']=='ter scripsit.' for r in result['residuals'])


def sequence(right=5):
    raws=['<bloco tipo="texto_principal" script="latino">1 De primo.</bloco>',
          '<bloco tipo="texto_principal" script="latino">De secundo.\nQuod tertium.\nDe quarto.</bloco>',
          f'<bloco tipo="texto_principal" script="latino">{right} De quinto.</bloco>']
    return [e for n,raw in enumerate(raws,1) for e in parse(raw,page=n)['entries']]


def test_missing_numbers_recover_from_both_bounds_without_fabricated_raw():
    entries=sequence()
    events=reconcile_bounded_ordinals(entries)
    assert len(events)==3
    assert [e['internal_binding']['numbers'] for e in entries]==[[1],[2],[3],[4],[5]]
    assert all(e['reference_raw'] is None for e in entries[1:4])
    assert all(e['internal_binding']['inferred'] for e in entries[1:4])


def test_row_count_or_language_mismatch_does_not_infer_ordinals():
    entries=sequence(6)
    assert reconcile_bounded_ordinals(entries)==[]
    assert entries[1]['internal_binding']['numbers']==[]
    entries=sequence()
    entries[-1]['language']='grc'
    assert reconcile_bounded_ordinals(entries)==[]


def test_literal_ordinal_conflict_retains_alternative():
    raw='<bloco tipo="texto_principal">76 De filiis.\n76 De virginibus.\n78 De militibus.</bloco>'
    entries=parse(raw)['entries']
    reconcile_bounded_ordinals(entries)
    assert entries[1]['internal_binding']['numbers']==[76]
    assert entries[1]['internal_binding']['alternative_numbers']==[77]
    assert entries[1]['reference_raw']=='76'


def test_rule_targets_exclude_subchapter_and_narrative_targets_exclude_caput():
    raw='''<bloco tipo="texto_principal">234 REGULA I.\nCaput II.\nΟΡΟΣ Γʹ.\nIV. Narration.</bloco>
<bloco tipo="nota">CIII.</bloco><bloco tipo="nota_marginal">C</bloco>'''
    page={'source':'target','physical':1,'raw':raw}
    assert set(scan_targets([page],'rule_number'))=={('rule_number',1),('rule_number',3)}
    assert set(scan_targets([page],'section_number'))=={('section_number',4),('section_number',103)}


def test_bounded_missing_section_window_includes_intervening_files():
    pages=[{'source':f'body{n}','physical':n,'raw':f'<bloco tipo="texto_principal">{text}</bloco>'}
           for n,text in [(1,'II. Start.'),(2,'Continues.'),(3,'V. End.')]]
    targets=scan_targets(pages,'section_number')
    add_bounded_section_candidates(targets,pages)
    meta={'grammar':'section_number','internal_binding':{'division':'section_number','numbers':[3]},'language':'lat'}
    candidates,missing=locate({'label':'Third'},meta,targets)
    assert not missing and [c['physical'] for c in candidates]==[1,2,3]
    assert all(c['tier']=='bounded_internal_sequence' and not c['certified'] for c in candidates)
    assert candidates[1]['evidence']['anchors'][0]['marker'] is None
    assert [b['number'] for b in candidates[1]['evidence']['anchors'][0]['bounds']]==[2,5]


def test_long_reversed_or_missing_file_window_does_not_expand():
    for positions in [(1,5),(3,1),(1,3)]:
        pages=[{'source':str(n),'physical':n,'raw':f'<bloco tipo="texto_principal">{num}. Text.</bloco>'}
               for n,num in zip(positions,['II','IV'])]
        targets=scan_targets(pages,'section_number')
        add_bounded_section_candidates(targets,pages)
        assert ('section_number',3) not in targets


def test_real_mixed_page_preserves_last_summaries_without_entering_work():
    from pathlib import Path
    raw=Path('teste/PG031/text/PG031-352.txt').read_text()
    end=raw.index('ΑΡΧΗ ΤΩΝ ΗΘΙΚΩΝ.')
    entries=parse_internal_page(raw,family='rule_number',source='ocr',volume='PG031',physical=352,
        scopes=[{'section_id':'s','span':[0,end]}])['entries']
    assert len(entries)==8
    assert [e['internal_binding']['numbers'][0] for e in entries]==[77,78,79,80,76,78,79,80]
    assert all(valid_entry(raw,e) for e in entries)


def test_shared_roman_summary_keeps_one_owner_and_both_ordinals():
    raw='<bloco tipo="texto_principal">IX. X. Item quod emptores et mediatores damnet.\nXI. De tertio statuto.</bloco>'
    entries=parse(raw,'section_number')['entries']
    assert len(entries)==2
    assert entries[0]['reference_raw']=='IX. X'
    assert entries[0]['internal_binding']['numbers']==[9,10]
    assert entries[0]['owner_raw'].startswith('Item quod')
    assert all(valid_entry(raw,e) for e in entries)
    # Separate rows are not swallowed as a shared ordinal, nor prose as a numeral.
    separate=parse('<bloco tipo="texto_principal">IX. De primo.\nX. De altero.</bloco>','section_number')['entries']
    assert [e['internal_binding']['numbers'] for e in separate]==[[9],[10]]


def test_malformed_roman_preserved_and_only_bounded_sequence_supplies_candidate():
    raw='<bloco tipo="texto_principal">XXII. De primo.\nXXIIII. Quod episcopos damnet.\nXXIV. De obedientia.</bloco>'
    entries=parse(raw,'section_number')['entries']
    assert len(entries)==3
    assert entries[1]['internal_binding']['numbers']==[]
    assert entries[1]['reference_raw']=='XXIIII' and valid_entry(raw,entries[1])
    assert 'ordinal_unparseable_in_ocr' in entries[1]['flags']
    assert len(reconcile_bounded_ordinals(entries))==1
    assert entries[1]['internal_binding']['numbers']==[23]
    assert entries[1]['internal_binding']['inferred']
    assert entries[1]['reference_raw']=='XXIIII'
    unbounded=parse('<bloco tipo="texto_principal">XXIIII. Quod episcopos damnet.</bloco>','section_number')['entries']
    assert reconcile_bounded_ordinals(unbounded)==[]
    assert unbounded[0]['internal_binding']['numbers']==[]


def test_dash_section_opening_is_detected_but_inline_citations_are_not():
    raw='<bloco tipo="texto_principal">- XX. — Fortassis autem aliquis dicit.\nVide cap. XXI. et XXII.\nCaput XXIII.</bloco>'
    targets=scan_targets([{'source':'target','physical':394,'raw':raw}],'section_number')
    assert set(targets)=={('section_number',20)}
    marker=targets[('section_number',20)][0]['marker']
    assert raw[slice(*marker['span'])]==marker['raw']


def test_elided_greek_preposition_is_not_recovered_as_malformed_ordinal():
    assert parse('<bloco tipo="texto_principal" script="grego">δι\' ἀγνωμοσύνην μὴ καταδεχομένων.</bloco>')['entries']==[]


def test_real_pl148_table_all_ordinals_and_bounded_body_openings():
    import json
    from pathlib import Path
    from scripts.recover_internal_index_sections import select_pages
    plan=json.loads(Path('docs/fixtures/pl148_chapter_sections.json').read_text())['sections'][0]
    pages=select_pages(Path('teste'),'PL148',*plan['index_pages'])
    entries=[]
    for i,p in enumerate(pages):
        raw=p['raw']
        lo=raw.index(plan['index_start_literal']) if i==0 else 0
        hi=raw.index(plan['index_end_before']) if i==len(pages)-1 else len(raw)
        found=parse_internal_page(raw,family='section_number',source=p['source'],volume='PL148',physical=p['physical'],
            scopes=[{'section_id':plan['id'],'span':[lo,hi]}])['entries']
        assert all(valid_entry(raw,e) for e in found)
        entries.extend(found)
    reconcile_bounded_ordinals(entries)
    assert len(entries)==23
    assert [n for e in entries for n in e['internal_binding']['numbers']]==list(range(1,25))
    body=select_pages(Path('teste'),'PL148',*plan['body_pages'])
    body[0]['start']=body[0]['raw'].index(plan['body_start_literal'])
    body[-1]['end']=body[-1]['raw'].index(plan['body_end_before'])
    targets=scan_targets(body,'section_number')
    assert set(targets)=={('section_number',n) for n in range(1,25)}
    assert {a['physical'] for a in targets[('section_number',20)]}=={394}
    assert {a['physical'] for n in [9,10] for a in targets[('section_number',n)]}=={385}
    for e in entries:
        candidates,missing=locate({'label':e['owner_raw']},e,targets)
        assert not missing and len(candidates)==1
        assert not candidates[0]['certified']


def test_standalone_rebuild_from_raw_exports_final_candidates_deterministically(tmp_path):
    import csv
    import json
    import subprocess
    import sys
    root=tmp_path/'raw'
    source=root/'PL999'/'text'
    source.mkdir(parents=True)
    (source/'page-001.txt').write_text('<bloco tipo="texto_principal">IX. X. Shared summary.</bloco>')
    (source/'page-002.txt').write_text('<bloco tipo="texto_principal">IX. First opening.</bloco>')
    (source/'page-003.txt').write_text('<bloco tipo="texto_principal">X. Second opening.</bloco>')
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps({'sections':[{'id':'shared','volume':'PL999','family':'section_number',
        'index_pages':[1,1],'body_pages':[2,3]}]}))
    snapshots=[]
    for attempt in range(2):
        out=tmp_path/f'out{attempt}'
        subprocess.run([sys.executable,'scripts/recover_internal_index_sections.py','--plan',str(plan),
            '--root',str(root),'--out',str(out),'--workers','1'],check=True,capture_output=True,text=True)
        exported=out/'entries.jsonl'
        rows=[json.loads(line) for line in exported.read_text().splitlines()]
        assert len(rows)==1
        assert rows[0]['metadata']['internal_binding']['numbers']==[9,10]
        assert {c['physical'] for c in rows[0]['candidates']}=={2,3}
        assert rows[0]['state']=='with_destinations'
        with (out/'entries.csv').open() as handle:
            csv_rows=list(csv.DictReader(handle))
        assert len(json.loads(csv_rows[0]['candidate_files']))==2
        assert json.loads((out/'extraction/summary.json').read_text())['catalog'] is None
        snapshots.append(exported.read_bytes())
    assert snapshots[0]==snapshots[1]

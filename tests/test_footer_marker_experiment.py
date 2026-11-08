import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.footer_marker_experiment import (
    citation_spans, classify, make_notes_db, refresh_citations, refresh_markers,
    run_volume, search_notes, visible_map,
)


def markers(raw):
    import re
    return [{'char_start': m.start(), 'char_end': m.end(), 'raw': m[0], 'number': int(m[0])}
            for m in re.finditer(r'\d+', raw)]


def test_mapping_entities_nested_tags_and_unicode():
    raw = 'e\u0301 &amp; <sup x="99">14</sup> Eph. IV, 16.\r\n'
    text, positions = visible_map(raw)
    assert text == 'é & 14 Eph. IV, 16.\n'
    a, b = positions[text.index('1')][0], positions[text.index('1')+1][1]
    assert raw[a:b] == '14'
    assert '99' not in text


def test_citation_alignment_is_local_and_abstains_on_duplicate_blocks():
    body = 'e\u0301 &amp; <sup>14</sup> Eph. IV, 16.'
    raw = f'<pagina><rodape>{body}</rodape><cabecalho>16</cabecalho></pagina>'
    text, _ = visible_map(body)
    a = text.index('Eph.')
    group = {'group_id': 1, 'block_index': 0, 'source_start': a, 'source_end': len(text)-1,
             'citation_raw': 'Eph. IV, 16', 'evidence_json': {'note_call': {'raw': '14'}}}
    spans = citation_spans(raw, [group])
    classified = [(m['number'], classify(m, raw, blocks(raw), spans)['role']) for m in markers(raw)]
    assert classified == [(14, 'note_call'), (16, 'scripture_locator'), (16, 'isolated_numeric_candidate')]
    duplicate = f'<pagina><rodape>{body}</rodape><rodape>{body}</rodape></pagina>'
    assert citation_spans(duplicate, [group]) == []
    assert citation_spans(raw, [{**group, 'citation_raw': 'wrong'}]) == []


def test_numeric_footer_preserved_and_bracket_namespace_separate():
    raw = '<rodape>455</rodape><rodape>456</rodape><cabecalho>218 [18]</cabecalho>'
    decisions = [classify(m, raw, blocks(raw), []) for m in markers(raw)]
    assert all(d['eligible'] for d in decisions)
    assert [d['channel'] for d in decisions] == ['plain', 'plain', 'plain', 'bracketed']


def test_repeated_notes_do_not_support_pagination():
    from collections import defaultdict
    nodes = defaultdict(set)
    raw = '<rodape>'+'\n'.join(f'({i}) Annotationes historiae.' for i in range(1, 31))+'</rodape>'
    for page in range(1, 5):
        for marker in markers(raw):
            decision = classify(marker, raw, blocks(raw), [])
            assert decision['role'] == 'note_call_pattern'
            if decision['eligible']:
                nodes[marker['number']].add(page)
    assert supported_sequence_nodes(nodes) == {}


def test_apparatus_and_uncertain_prose_are_not_pagination():
    raw = '<rodape>VARIANTES LECTIONES. 30 veniret 1. proveniret 7.</rodape>'
    assert all(classify(m, raw, blocks(raw), [])['role'] == 'apparatus_number' for m in markers(raw))
    raw = '<rodape>Confer opus aliud p. 382.</rodape>'
    assert classify(markers(raw)[0], raw, blocks(raw), [])['role'] == 'note_content_unclassified'


def test_watermark_does_not_turn_isolated_pagination_into_note():
    raw = '<rodape>55\nDigitized by Google</rodape><rodape>69 70 Digitized by Google</rodape>'
    decisions = [classify(m, raw, blocks(raw), []) for m in markers(raw)]
    assert all(d['eligible'] and d['watermark_masked'] for d in decisions)
    raw = '<rodape>55 Annotationes. Digitized by Google</rodape>'
    assert not classify(markers(raw)[0], raw, blocks(raw), [])['eligible']


def test_chronology_is_not_pagination_and_open_bracket_does_not_swallow_right_column():
    raw = '<cabecalho>953 [A. C. 1344] HISTORIA [IMP. J. PALAEOLOG. 4 954</cabecalho>'
    decisions = [classify(m, raw, blocks(raw), []) for m in markers(raw)]
    assert [d['role'] for d in decisions] == ['header_candidate', 'header_chronology',
                                             'header_chronology', 'header_candidate']
    assert decisions[0]['channel'] == decisions[-1]['channel'] == 'plain'
    raw = '<cabecalho>953 HISTORIA [unknown 954</cabecalho>'
    assert classify(markers(raw)[-1], raw, blocks(raw), [])['basis'] == 'consecutive_outer_header_pair'
    raw = '<cabecalho>953 HISTORIA [unknown 956</cabecalho>'
    assert not classify(markers(raw)[-1], raw, blocks(raw), [])['eligible']


def test_refresh_is_mechanical_and_uses_current_offsets(tmp_path):
    path = tmp_path/'PG001-001.txt'
    raw = '<pagina><cabecalho>312</cabecalho><rodape>14 Eph. IV, 16.</rodape></pagina>'
    path.write_text(raw)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    fresh = refresh_markers(raw, {'path': str(path), 'physical': 1}, checksum)
    groups = refresh_citations(str(path), 'PG001', checksum)
    spans = citation_spans(raw, groups)
    assert [classify(m, raw, blocks(raw), spans)['role'] for m in fresh] == [
        'isolated_numeric_candidate', 'note_call', 'scripture_locator']
    assert all(m['sha256'] == checksum and raw[m['char_start']:m['char_end']] == m['raw'] for m in fresh)
    import pytest
    with pytest.raises(ValueError, match='changed during refresh'):
        refresh_citations(str(path), 'PG001', 'stale')


def test_real_reviewed_footer_controls():
    path = Path('data/index_entry_parser/roi_investigation_001/footer/evidence.json')
    if not path.exists():
        import pytest
        pytest.skip('Local corpus fixture unavailable')
    for evidence in json.loads(path.read_text()):
        raw = Path(evidence['ocr_path']).read_text()
        if hashlib.sha256(raw.encode()).hexdigest() != evidence['ocr_sha256']:
            import pytest
            pytest.skip('Reviewed source changed; re-audit required')
        spans = citation_spans(raw, evidence['citation_groups'])
        for marker in evidence['supported_footer_markers']:
            decision = classify(marker, raw, blocks(raw), spans)
            if evidence['volume'] == 'PG004':
                assert decision['eligible']
            else:
                assert not decision['eligible'], (evidence['volume'], marker, decision)


def test_search_deduplicates_files_and_never_resolves(tmp_path):
    with make_notes_db(tmp_path/'notes.sqlite') as db:
        db.row_factory = sqlite3.Row
        for path, text in [('index', 'Rachel mater'), ('one', 'Rachel mater'), ('one', 'Rachel'), ('two', 'mater Rachel')]:
            c = db.execute('INSERT INTO notes VALUES(NULL,?,?,?,?,?,?,?,?,?)', ('PG001', path, 'hash', 1, 0, len(text), 'rodape', text, text))
            db.execute('INSERT INTO note_search(rowid,text) VALUES(?,?)', (c.lastrowid, text))
        results = search_notes(db, 'Rachel mater', {'index'}, 20)
        assert {r['path'] for r in results} == {'one', 'two'}
        assert len(results) == 2 and all(not r['verified'] for r in results)
        assert search_notes(db, '1234 ibid.', set(), 20) == []


def test_run_is_readonly_preserves_header_and_stale_sources_are_flagged(tmp_path):
    survey = tmp_path/'survey'/'volumes'/'PG001'
    survey.mkdir(parents=True)
    inventory, all_markers = [], []
    source = tmp_path/'index.txt'
    source.write_text('Rachel 999.')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    inventory.append({'path': str(source), 'sha256': sha(source), 'physical': 100})
    for page in range(1, 10):
        path = tmp_path/f'{page}.txt'
        path.write_text(f'<cabecalho>{page}</cabecalho><rodape>(999) Rachel mater.</rodape>')
        inventory.append({'path': str(path), 'sha256': sha(path), 'physical': page})
        all_markers.extend({**m, 'id': f'{page}:{m["char_start"]}', 'path': str(path),
            'sha256': sha(path), 'physical': page, 'volume': 'PG001', 'sequence_support': {'legacy': True}}
            for m in markers(path.read_text()))
    group = {'id': 'g', 'source': str(source), 'owner_raw': 'Rachel mater'}
    occurrences = [{'id': 'o', 'group_id': 'g', 'source': str(source), 'source_sha256': sha(source),
                    'tier': 'unmapped_namespace_screen', 'number': 999, 'files': []},
                   {'id': 'o2', 'group_id': 'g', 'source': str(source), 'source_sha256': sha(source),
                    'tier': 'unmapped_namespace_screen', 'number': 4, 'files': [{'path': str(tmp_path/'4.txt')}]},
                   {'id': 'stale', 'group_id': 's', 'source': str(source), 'source_sha256': 'old',
                    'tier': 'unmapped_namespace_screen', 'number': 5, 'files': []}]
    for name, data in [('inventory', inventory), ('header_markers', all_markers), ('groups', [group]), ('occurrences', occurrences)]:
        (survey/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in data))
    citations = tmp_path/'citations.sqlite'
    with sqlite3.connect(citations) as db:
        db.executescript('CREATE TABLE citation_files(file_id INTEGER,volume_id TEXT,file_path TEXT,file_sha256 TEXT);'
                         'CREATE TABLE citation_groups(group_id INTEGER,file_id INTEGER);')
    before = citations.read_bytes()
    out = tmp_path/'out'
    out.mkdir()
    config = {'survey': str(tmp_path/'survey'), 'citation_db': str(citations), 'out': str(out),
              'query_limit': 100, 'seed': 73, 'top_k': 20}
    result = run_volume((config, 'PG001'))
    assert citations.read_bytes() == before
    assert result['counts']['note_queries'] == 1
    assert result['counts']['note_queries_with_matches'] == 1
    assert result['counts']['stale_index_occurrences'] == 1
    diff = [json.loads(line) for line in (out/'PG001/candidate_diff.jsonl').read_text().splitlines()]
    assert diff[1]['proposed'] == [str(tmp_path/'4.txt')]
    with sqlite3.connect(out/'PG001/notes.sqlite') as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    # Correct a target after the frozen survey: refresh witnesses, never old index spans.
    changed = tmp_path/'4.txt'
    changed.write_text('<pagina><cabecalho>404</cabecalho><rodape>Rachel correcta.</rodape></pagina>')
    refreshed_out = tmp_path/'refreshed'
    refreshed_out.mkdir()
    fresh = run_volume(({**config, 'out': str(refreshed_out), 'refresh_sources': True}, 'PG001'))
    assert fresh['counts']['ocr_files_refreshed'] == 1
    with sqlite3.connect(refreshed_out/'PG001/notes.sqlite') as db:
        row = db.execute('SELECT sha256,visible_text FROM notes WHERE path=?', (str(changed),)).fetchone()
        assert row == (sha(changed), 'Rachel correcta.')
    assert citations.read_bytes() == before
    source.write_text('Rachel 998.')
    stale_out = tmp_path/'stale_index'
    stale_out.mkdir()
    stale = run_volume(({**config, 'out': str(stale_out), 'refresh_sources': True}, 'PG001'))
    assert stale['counts'].get('note_queries', 0) == 0
    assert stale['counts']['stale_index_occurrences'] == 3

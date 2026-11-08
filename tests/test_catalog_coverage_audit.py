import hashlib
import json
import sqlite3

from tools.indexing.catalog_coverage_audit import account, page_signals, scan_volume, union, report_gaps


def test_union_does_not_count_overlapping_extractions_twice():
    assert union([[1, 5], [3, 8], [1, 5], [10, 12]]) == [[1, 8], [10, 12]]


def test_accounting_distinguishes_numeric_references_from_owner_and_notes():
    raw = '<texto_principal>Aaron 12. Isaac 30.</texto_principal><nota>note 99</nota>'
    a = raw.index('Aaron'); b = raw.index(' Isaac'); r = raw.index('12')
    entry = {'span': [a, b], 'ref_span': [r, r+2], 'ref_raw': '12',
             'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    counts, issues, residue = account(raw, [entry, entry])
    assert not issues
    assert counts['body_numbers_outside_reference_spans'] == 1
    assert counts['other_blocks_numbers_outside_reference_spans'] == 1
    assert counts['body_registered_span_chars'] == len('Aaron12.')
    assert counts['body_chars'] == counts['body_registered_span_chars'] + counts['body_residual_chars']
    assert residue


def test_changed_hash_does_not_block_current_reference_or_discovery():
    raw = '<texto_principal>Aaron 12.</texto_principal>'
    a = raw.index('Aaron'); r = raw.index('12')
    entry = {'span': [a, r+3], 'ref_span': [r, r+2], 'ref_raw': '12', 'sha256': 'old'}
    counts, issues, _ = account(raw, [entry])
    assert counts['body_numbers_outside_reference_spans'] == 0
    assert issues['envelope_not_revalidated'] == 1
    assert counts['body_registered_span_chars'] == 0


def test_running_header_retained_but_prose_not_promoted_to_index():
    raw = '<cabecalho>INDEX NOMINUM</cabecalho><texto_principal>Aaron 12.</texto_principal>'
    headings, _ = page_signals(raw)
    assert any(h['role'] == 'running_header' and h['eligible'] for h in headings)
    hs, weak = page_signals('<texto_principal>See the index for more information.</texto_principal>')
    assert not any(h['eligible'] for h in hs)
    assert not weak


def test_represented_volume_still_scans_unrepresented_section_and_neighbors(tmp_path):
    folder = tmp_path / 'PL999/text'; folder.mkdir(parents=True)
    one = folder / 'one-001.txt'; one.write_text('<texto_principal>Old entry 1.</texto_principal>')
    two = folder / 'one-002.txt'; two.write_text('<texto_principal>Continuation.</texto_principal>')
    three = folder / 'one-003.txt'
    three.write_text('<cabecalho>INDEX NOMINUM</cabecalho><texto_principal>Aaron 12.\nIsaac 30.</texto_principal>')
    catalog = {str(one): [{'span': [17, 29], 'ref_span': None, 'ref_raw': '', 'sha256': 'old'}]}
    result = scan_volume(('PL999', str(tmp_path), catalog, {}, {str(two): [{'status': 'processed_without_entries'}]}, 2))
    pages = {p['physical']: p for p in result['pages']}
    assert pages[3]['coverage_state'] == 'no_execution_evidence_in_supplied_campaigns'
    assert pages[3]['probe']['valid_literal_proposals'] == 2
    assert pages[2]['coverage_state'] == 'attempt_recorded_without_catalog_occurrences'
    assert pages[1]['catalog_items'] == 1
    assert three.read_text().startswith('<cabecalho>')


def test_notes_are_visible_even_when_parser_excludes_them(tmp_path):
    folder = tmp_path / 'PO999/text'; folder.mkdir(parents=True)
    path = folder / 'x-001.txt'
    raw = '<cabecalho>TABLE DES NOMS PROPRES</cabecalho><nota>Aaron 12.</nota>'
    path.write_text(raw)
    result = scan_volume(('PO999', str(tmp_path), {}, {}, {}, 1))
    page = result['pages'][0]
    assert page['accounting']['other_blocks_numeric_occurrences'] == 1
    assert page['probe']['valid_literal_proposals'] == 0
    assert page['residual_examples'][0]['kind'] == 'nota'


def test_extra_history_distinguishes_attempt_from_missing_selection_and_replays(tmp_path):
    folder = tmp_path / 'PL999/text'; folder.mkdir(parents=True)
    source = folder / 'p-001.txt'
    source.write_text('<cabecalho>INDEX NOMINUM</cabecalho><texto_principal>Aaron I, 12.</texto_principal>')
    page = scan_volume(('PL999', str(tmp_path), {}, {}, {}, 0))['pages'][0]
    out = tmp_path / 'out'; out.mkdir()
    with sqlite3.connect(out / 'audit.sqlite') as db:
        db.execute('CREATE TABLE pages(volume TEXT,physical INTEGER,source TEXT,evidence_json TEXT)')
        db.execute('INSERT INTO pages VALUES(?,?,?,?)', ('PL999', 1, str(source), json.dumps(page)))
    (out / 'volumes.json').write_text(json.dumps([{'volume': 'PL999', 'represented': True}]))
    history = tmp_path / 'history.sqlite'
    with sqlite3.connect(history) as db:
        db.execute('CREATE TABLE run_pages(source TEXT)')
        db.execute('INSERT INTO run_pages VALUES(?)', (str(source),))
    result = report_gaps(out, history)
    assert result['counts']['known_attempt_without_catalog_rows'] == 1
    assert result['syntax_signature_pages_nonexclusive']['roman_then_arabic'] == 1
    saved = (out / 'gaps.jsonl').read_bytes()
    assert report_gaps(out, history) == result
    assert (out / 'gaps.jsonl').read_bytes() == saved


def test_independent_title_probe_finds_glossary_outside_detector():
    headings, weak = page_signals('<cabecalho>GLOSSARIUM GRÆCUM.</cabecalho><texto_principal>Alpha 20.</texto_principal>')
    assert not headings
    assert weak[0]['raw'] == 'GLOSSARIUM GRÆCUM.'

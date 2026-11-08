import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools.indexing.entry_parser_bulk import inventory, open_experiment, save, sample_run, fingerprint, work
from tools.indexing.entry_parser_experiment import parse_page, compare_rows

ROOT = Path(__file__).resolve().parents[1]


def fixture_db(tmp_path):
    root = tmp_path / 'teste'
    text = root / 'PG069/text'
    text.mkdir(parents=True)
    for n in [740, 741, 744]:
        (text / f'page-{n}.txt').write_text('<bloco tipo="texto_principal">Rachel formosa\ntypus Ecclesiæ, 123. Residuum</bloco>')
    db = tmp_path / 'source.sqlite'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE alphabetical_sections(section_key,volume_id,file_start,file_end,index_ocr_file_start,index_ocr_file_end,page_start,page_end)')
    c.execute('INSERT INTO alphabetical_sections VALUES(?,?,?,?,?,?,?,?)',
              ('s', 'PG069', str(text/'page-740.txt'), str(text/'page-741.txt'), None, None, 9000, 9001))
    c.execute('CREATE TABLE alphabetical_entries(entry_key,entry_order,entry_raw,lemma_raw,context_raw,heading_letter,index_entry_source_ocr_file,section_key)')
    for i, raw in enumerate(['Rachel formosa', 'typus Ecclesiæ, 123.']):
        c.execute('INSERT INTO alphabetical_entries VALUES(?,?,?,?,?,?,?,?)',
                  (str(i), i, raw, raw, None, 'R', str(text/'page-744.txt'), 's'))
    c.commit()
    c.close()
    return db, root


def test_inventory_unions_origins_outside_ranges_and_ignores_editorial_pages(tmp_path):
    db, root = fixture_db(tmp_path)
    before = db.read_bytes()
    tasks, issues = inventory(db, root)
    assert [t['physical'] for t in tasks] == [740, 741, 744]
    assert not issues
    assert len(tasks[-1]['old_rows']) == 2
    assert db.read_bytes() == before
    assert inventory(db, root, ['PL010'])[0] == []


def test_inventory_does_not_guess_between_duplicate_physical_files(tmp_path):
    db, root = fixture_db(tmp_path)
    (root / 'PG069/text/other-741.txt').write_text('other')
    tasks, issues = inventory(db, root)
    assert [t['physical'] for t in tasks] == [740, 744]
    assert issues[0]['kind'] == 'missing_or_ambiguous_range_source'


def test_protect_source_alias_and_unrelated_database(tmp_path):
    db, root = fixture_db(tmp_path)
    alias = tmp_path / 'alias.sqlite'
    alias.symlink_to(db)
    before = db.read_bytes()
    with pytest.raises(ValueError):
        open_experiment(alias, db)
    with pytest.raises(ValueError):
        open_experiment(db, tmp_path / 'different-source.sqlite')
    assert db.read_bytes() == before


def test_generic_does_not_inherit_pg069_context_or_section():
    p = parse_page('<bloco tipo="texto_principal">Pax, 770, 9. Data Racheli, 137.</bloco>',
                   volume='PG110', profile='generic-literal-experimental')
    assert all(e['context_candidate_id'] is None and e['section_candidate'] is None for e in p['entries'])
    assert all('unvalidated_profile' in e['flags'] for e in p['entries'])


def test_running_header_numbers_do_not_hide_ordo_boundary():
    p = parse_page('<bloco tipo="cabecalho">1481 ORDO RERUM 1482</bloco>'
                   '<bloco tipo="texto_principal">Liber, 123.</bloco>',
                   volume='PG001', profile='generic-literal-experimental')
    assert not p['entries']
    assert p['residuals'][0]['reason'] == 'outside_owned_boundary'


def test_worker_errors_are_auditable_and_empty_pages_can_be_sampled(tmp_path):
    db, root = fixture_db(tmp_path)
    task = inventory(db, root)[0][0]
    task.update(raw='<pagina/>', fingerprint='empty')
    result = work(task)
    assert result['status'] == 'no_candidates'
    con = open_experiment(tmp_path / 'experiment.sqlite', db)
    con.execute('INSERT INTO runs VALUES(?,?,?,?)', ('r', '', '{}', 'running'))
    con.commit()
    save(con, 'r', task, result)
    sample_run(con, 'r', tmp_path/'sample')
    assert json.loads((tmp_path/'sample/sample.json').read_text())['items'][0]['kind'] == 'page'
    con.close()
    task['profile'] = 'nonexistent'
    assert work(task)['status'] == 'error'


def test_literal_join_cannot_cross_sections():
    p = parse_page('<bloco tipo="texto_principal">Rachel formosa typus Ecclesiæ, 123.</bloco>')
    rows = [{'section_key': 'a', 'entry_raw': 'Rachel formosa', 'heading_letter': 'R'},
            {'section_key': 'b', 'entry_raw': 'typus Ecclesiæ, 123.', 'heading_letter': 'R'}]
    assert compare_rows(p, rows)[0]['status'] == 'no_literal_alignment'


def test_persistent_results_sampling_and_rewrite_referenced_task(tmp_path):
    db, root = fixture_db(tmp_path)
    task = inventory(db, root)[0][-1]
    task['raw'] = Path(task['source']).read_text()
    task['fingerprint'] = fingerprint(task)
    result = work(task)
    con = open_experiment(tmp_path / 'experiment.sqlite', db)
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    for rid in ['first', 'second']:
        con.execute('INSERT INTO runs VALUES(?,?,?,?)', (rid, '', '{}', 'running'))
        con.commit()
        save(con, rid, task, result)
    for name in ['sample1', 'sample2']:
        sample_run(con, 'first', tmp_path/name, seed=42)
    a = json.loads((tmp_path/'sample1/sample.json').read_text())
    b = json.loads((tmp_path/'sample2/sample.json').read_text())
    assert a == b
    assert {i['kind'] for i in a['items']} == {'candidate', 'residual'}
    con.close()


def test_parallel_cli_resume_and_source_unchanged(tmp_path):
    db, root = fixture_db(tmp_path)
    before = db.read_bytes()
    exp = tmp_path / 'experiment.sqlite'
    base = [sys.executable, str(ROOT/'scripts/survey_index_entry_parser.py'), '--all-volumes',
            '--root', str(root), '--source-db', str(db), '--experiment-db', str(exp), '--workers', '2']
    for name, extra in [('first', []), ('second', ['--resume'])]:
        subprocess.run(base + extra + ['--out', str(tmp_path/name)], check=True, capture_output=True, text=True)
    s = json.loads((tmp_path/'second/summary.json').read_text())
    assert s['cache_hits'] == 3 and s['totals']['pages'] == 3
    assert db.read_bytes() == before
    # Source changes invalidate only their corresponding cached task.
    (root/'PG069/text/page-740.txt').write_text('<bloco tipo="texto_principal">Pax, 160.</bloco>')
    subprocess.run(base + ['--resume', '--out', str(tmp_path/'third')], check=True, capture_output=True, text=True)
    assert json.loads((tmp_path/'third/summary.json').read_text())['cache_hits'] == 2

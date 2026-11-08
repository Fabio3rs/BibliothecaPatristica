import sqlite3

from scripts.report_global_index_progress import compare_surveys


def survey(tmp_path, name, status='pending', target='page1', extra=False):
    directory = tmp_path / name
    directory.mkdir()
    with sqlite3.connect(directory / 'survey.sqlite') as con:
        con.executescript('''
            CREATE TABLE groups(id TEXT PRIMARY KEY,status TEXT,evidence_json TEXT);
            CREATE TABLE occurrences(id TEXT PRIMARY KEY,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(occurrence_id TEXT,path TEXT,physical INTEGER);
        ''')
        con.execute('INSERT INTO groups VALUES(?,?,?)', ('g', 'pending', '{}'))
        con.execute('INSERT INTO occurrences VALUES(?,?,?)', ('o', status, '{"number": 1}'))
        con.execute('INSERT INTO candidates VALUES(?,?,?)', ('o', target, 1))
        if extra:
            con.execute('INSERT INTO occurrences VALUES(?,?,?)', ('o2', 'pending', '{}'))
    return directory


def test_identical_surveys_have_no_gain_or_mutation(tmp_path):
    before, after = survey(tmp_path, 'before'), survey(tmp_path, 'after')
    contents = [(p / 'survey.sqlite').read_bytes() for p in (before, after)]
    result = compare_surveys(before, after)
    assert all(v == 0 for v in result['groups'].values())
    assert all(v == 0 for v in result['occurrences'].values())
    assert result['candidate_edges_added'] == result['candidate_edges_removed'] == 0
    assert contents == [(p / 'survey.sqlite').read_bytes() for p in (before, after)]


def test_equal_candidate_counts_do_not_hide_replaced_destination(tmp_path):
    before = survey(tmp_path, 'before')
    after = survey(tmp_path, 'after', status='candidate', target='page2', extra=True)
    result = compare_surveys(before, after)
    assert result['occurrences']['added'] == 1
    assert result['occurrences']['status_changed'] == 1
    assert result['candidate_edges_added'] == result['candidate_edges_removed'] == 1

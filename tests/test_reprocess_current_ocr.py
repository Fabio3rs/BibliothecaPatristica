from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import reprocess_current_ocr as runner


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "source.db"
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
            CREATE TABLE ocr_engine_versions (id INTEGER PRIMARY KEY, engine TEXT, model TEXT);
            CREATE TABLE ocr_results (id INTEGER PRIMARY KEY, volume_id TEXT, page_num INTEGER,
                image_hash TEXT, text_hash TEXT, engine_version_id INTEGER, is_current INTEGER);
            INSERT INTO ocr_engine_versions VALUES (1,'openai','gpt-5-mini'),
                (2,'ollama','target'),(3,'openai','gpt-5-mini-new');
        """)
        rows = [(1,"PG001",4,1,1), (2,"PG001",5,1,0), (3,"PG001",5,2,1),
                (4,"PG001",15,1,1), (5,"PL002",2,1,1), (6,"PO001",4,1,1),
                (7,"PG002",1,3,1), (8,"PG003",1,1,1), (9,"PG003",1,2,1)]
        con.executemany("INSERT INTO ocr_results VALUES (?,?,?,'image','text',?,?)", rows)
    pdf_dir = tmp_path / "pdfs with spaces"
    pdf_dir.mkdir()
    for volume in ("PG001", "PL002"):
        (pdf_dir / f"{volume}.pdf").touch()
    args = runner.parse_args(["--db", str(db), "--pdf-dir", str(pdf_dir),
                              "--out", str(tmp_path / "corpus"), "--work-dir", str(tmp_path / "work"),
                              "--llm-model", "target", "--jobs", "2", "--procs", "3"])
    directory = tmp_path / "plan"
    directory.mkdir()
    (directory / "volumes").mkdir()
    (directory / "logs").mkdir()
    return args, directory


def test_selection_exact_current_model_collections_and_read_only(setup):
    args, directory = setup
    jobs, summary = runner.prepare(args, directory)
    assert summary["collections"] == {"PG": 2, "PL": 1}
    assert summary["blocked"] == {"ambiguous_current": 1}
    assert summary["max_page_workers"] == 6
    assert summary["journal_mode"] == "wal"
    assert len(jobs) == 2
    job = json.loads(jobs[0].read_text())
    assert [r["page_num"] for r in job["pages"]] == [4, 15]
    assert job["argv"][job["argv"].index("--pages") + 1] == "4,15"
    assert job["argv"][-1] == str(args.pdf_dir / "PG001.pdf")
    assert "--force" in job["argv"] and "--verify-fix" in job["argv"]
    with closing(runner.connect_read_only(args.db)) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM ocr_results")
        assert con.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 9


def test_filters_limit_and_missing_pdf(setup):
    args, directory = setup
    args.collections = ["PL"]
    args.volumes = ["PL002"]
    args.limit = 1
    (args.pdf_dir / "PL002.pdf").unlink()
    jobs, summary = runner.prepare(args, directory)
    assert not jobs
    assert summary["selected_pages"] == 1
    assert summary["blocked"] == {"missing_pdf": 1}


def test_changed_current_is_not_overwritten(setup):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    source = json.loads(jobs[0].read_text())["pages"]
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE ocr_results SET is_current=0 WHERE id=1")
        con.execute("INSERT INTO ocr_results VALUES(10,'PG001',4,'image','new',2,1)")
    retained, skipped = runner.still_selected(source, runner.volume_current(args.db, "PG001"))
    assert [r["page_num"] for r in retained] == [15]
    assert skipped == [4]
    with closing(runner.connect_read_only(args.db)) as con:
        assert [r["page_num"] for r in runner.current_rows(con, "gpt-5-mini", ["PG"], ["PG001"])] == [15]


def test_completion_requires_target_version_and_exported_file(setup):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    source = json.loads(jobs[0].read_text())["pages"]
    assert all(r["status"] == "unconfirmed" for r in runner.confirm_outputs(args, "PG001", source))
    text_dir = args.out / "PG001/text"
    text_dir.mkdir(parents=True)
    path = text_dir / "PG001-004.txt"
    path.write_bytes(b"corrected")
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE ocr_results SET is_current=0 WHERE id=1")
        con.execute("INSERT INTO ocr_results VALUES (10,'PG001',4,'image',?,2,1)",
                    (hashlib.sha256(b"corrected").hexdigest(),))
    results = runner.confirm_outputs(args, "PG001", source)
    assert [r["status"] for r in results] == ["recorded", "unconfirmed"]
    path.write_bytes(b"different")
    assert runner.confirm_outputs(args, "PG001", source)[0]["status"] == "unconfirmed"


def test_dry_run_never_spawns_process(setup, monkeypatch):
    args, _ = setup
    launch = Mock(side_effect=AssertionError("unexpected inference"))
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    assert runner.main(["--db", str(args.db), "--pdf-dir", str(args.pdf_dir),
                        "--work-dir", str(args.work_dir)]) == 0
    launch.assert_not_called()


def test_parallel_volumes_are_independent_process_commands(setup, monkeypatch):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    barrier = threading.Barrier(2)
    seen = []

    def run(args, path, stop):
        seen.append(path.stem)
        barrier.wait(timeout=5)
        return {"volume": path.stem, "status": "completed"}

    monkeypatch.setattr(runner, "run_volume", run)
    assert runner.execute(args, jobs, directory, threading.Event()) == 0
    assert set(seen) == {"PG001", "PL002"}
    results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
    assert len(results) == 2


def test_worker_does_not_trust_zero_exit_code(setup, monkeypatch, tmp_path):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    process = Mock(returncode=0)
    process.poll.return_value = 0
    launch = Mock(return_value=process)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    result = runner.run_volume(args, jobs[0], threading.Event())
    assert result["status"] == "incomplete"
    assert result["pages"] == {"unconfirmed": 2}
    argv = launch.call_args.args[0]
    assert argv[argv.index("--pages") + 1] == "4,15"
    assert launch.call_args.kwargs["start_new_session"] is True
    assert "shell" not in launch.call_args.kwargs


def test_stopped_scheduler_does_not_launch_more_volumes(setup, monkeypatch):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    launch = Mock(side_effect=AssertionError("should not launch"))
    monkeypatch.setattr(runner, "run_volume", launch)
    stop = threading.Event()
    stop.set()
    assert runner.execute(args, jobs, directory, stop) == 130
    launch.assert_not_called()


def test_alternate_db_cannot_accidentally_execute_against_production(setup):
    args, _ = setup
    with pytest.raises(SystemExit):
        runner.parse_args(["--db", str(args.db), "--execute"])


@pytest.mark.parametrize("option", ["--jobs", "--procs", "--limit"])
def test_nonpositive_configuration_rejected(option):
    with pytest.raises(SystemExit):
        runner.parse_args([option, "0"])


def test_real_split_volume_names_are_accepted():
    args = runner.parse_args(["--volumes", "PG007.01", "PL115-1", "PL116-1"])
    assert args.volumes == ["PG007.01", "PL115-1", "PL116-1"]


def test_offline_subprocesses_record_two_volumes_in_wal(setup, monkeypatch, tmp_path):
    args, directory = setup
    jobs, _ = runner.prepare(args, directory)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    stub = tmp_path / "offline_worker.py"
    stub.write_text('''import hashlib, json, sqlite3, sys
from pathlib import Path
db, out, volume, records = sys.argv[1:]
text_dir = Path(out) / volume / "text"
text_dir.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(db, timeout=10) as con:
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    for row in json.loads(records):
        text = f'<pagina estado="com_texto"><bloco>{volume}:{row["page_num"]}</bloco></pagina>'
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE ocr_results SET is_current=0 WHERE id=?", (row["id"],))
        con.execute("INSERT INTO ocr_results VALUES(NULL,?,?,?, ?,2,1)",
                    (volume, row["page_num"], row["image_hash"], hashlib.sha256(text.encode()).hexdigest()))
        con.commit()
        (text_dir / f'{volume}-{row["page_num"]:03d}.txt').write_text(text)
print("offline fixture only")
''')
    monkeypatch.setattr(runner, "command", lambda args, volume, rows:
                        [sys.executable, str(stub), str(args.db), str(args.out), volume, json.dumps(rows)])
    assert runner.execute(args, jobs, directory, threading.Event()) == 0
    results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
    assert len(results) == 2
    assert all(row["status"] == "completed" for row in results)
    assert sum(row["pages"]["recorded"] for row in results) == 3
    with closing(runner.connect_read_only(args.db)) as con:
        assert con.execute("SELECT is_current FROM ocr_results WHERE id=3").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM ocr_results WHERE id IN (1,4,5)").fetchone()[0] == 3

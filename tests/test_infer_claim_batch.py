from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            image_path TEXT,
            bbox TEXT,
            line_image BLOB,
            tesseract_text TEXT,
            qwen_text TEXT,
            agreement_score REAL,
            status TEXT,
            updated_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            created_at TEXT,
            updated_at TEXT,
            run_id TEXT
        )
        """
    )
    rows = [
        (1, "img1.png", "{}", b"a", "t1", 0.9, "pending"),
        (2, "img2.png", "{}", b"b", "t2", 0.1, "pending"),
        (3, "img3.png", "{}", b"c", "t3", 0.2, "pending"),
        (4, "img4.png", "{}", b"d", "t4", 0.05, "pending"),
        (5, "img5.png", "{}", b"e", "t5", 0.3, "inferred"),
    ]
    con.executemany(
        "INSERT INTO lines(id, image_path, bbox, line_image, tesseract_text, agreement_score, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    versions = [
        (1, 2, "openai", "gpt-5-mini", None, None, "run-a"),
        (2, 3, "openai", "gpt-4.1-mini", None, None, "run-b"),
        (3, 4, "ollama", "qwen3.5:9b", None, None, "run-c"),
    ]
    con.executemany(
        "INSERT INTO line_versions(id, line_id, provider, model, created_at, updated_at, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        versions,
    )
    con.commit()
    con.close()


def _pending_statuses(path: Path) -> dict[int, str]:
    con = sqlite3.connect(path)
    rows = con.execute("SELECT id, status FROM lines ORDER BY id").fetchall()
    con.close()
    return {row[0]: row[1] for row in rows}


def test_claim_batch_without_preference_keeps_score_order(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(str(db_path), batch_size=3)

    assert [row[0] for row in batch] == [4, 2, 3]
    assert _pending_statuses(db_path) == {
        1: "pending",
        2: "processing",
        3: "processing",
        4: "processing",
        5: "inferred",
    }


def test_claim_batch_skips_bbox_that_cannot_reach_model_minimum(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE lines SET bbox = ?, qwen_text = ? WHERE id = 4",
        ('{"x": 10, "y": 20, "w": 4, "h": 16}', "texto preservado"),
    )
    con.commit()
    con.close()

    batch = infer.claim_batch(str(db_path), batch_size=1, min_image_side=11)

    assert [row[0] for row in batch] == [2]
    assert _pending_statuses(db_path)[4] == "skipped"
    con = sqlite3.connect(db_path)
    qwen_text = con.execute("SELECT qwen_text FROM lines WHERE id = 4").fetchone()[0]
    con.close()
    assert qwen_text == "texto preservado"


def test_claim_batch_accepts_bbox_reaching_minimum_with_padding(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE lines SET bbox = ? WHERE id = 4",
        ('{"x": 10, "y": 20, "w": 7, "h": 7}',),
    )
    con.commit()
    con.close()

    batch = infer.claim_batch(str(db_path), batch_size=1, min_image_side=11)

    assert [row[0] for row in batch] == [4]
    assert _pending_statuses(db_path)[4] == "processing"


def test_claim_batch_prefers_rows_missing_exact_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(
        str(db_path),
        batch_size=4,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini@3"),
    )

    assert [row[0] for row in batch] == [4, 3, 5, 1]


def test_claim_batch_treats_other_model_as_missing_for_target_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    batch = infer.claim_batch(
        str(db_path),
        batch_size=2,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
    )

    assert [row[0] for row in batch] == [4, 3]


def test_claim_batch_preserves_inferred_status_when_bbox_is_too_small(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE lines SET bbox = ? WHERE id = 5",
        ('{"x": 10, "y": 20, "w": 4, "h": 16}',),
    )
    con.commit()
    con.close()

    batch = infer.claim_batch(
        str(db_path),
        batch_size=5,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
        min_image_side=11,
    )

    assert 5 not in [row[0] for row in batch]
    assert _pending_statuses(db_path)[5] == "inferred"


def test_count_claimable_excludes_too_small_inferred_for_preferred_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE lines SET bbox = ? WHERE id = 5",
        ('{"x": 10, "y": 20, "w": 4, "h": 16}',),
    )
    con.commit()
    con.close()

    count = infer.count_claimable_lines(
        str(db_path),
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
        min_image_side=11,
    )

    assert count == 4
    assert _pending_statuses(db_path)[5] == "inferred"


def test_claim_batch_does_not_reclaim_processing_rows_with_backend_preference(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute("UPDATE lines SET status = 'processing' WHERE id = 5")
    con.commit()
    con.close()

    batch = infer.claim_batch(
        str(db_path),
        batch_size=4,
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
    )

    assert 5 not in [row[0] for row in batch]


def test_parse_backend_identity_ignores_weight(tmp_path):
    backend = infer.parse_backend_identity("openai:gpt-5-mini@7")

    assert backend.provider == "openai"
    assert backend.model == "gpt-5-mini"
    assert backend.weight == 1.0


def test_count_claimable_lines_includes_inferred_missing_preferred_backend(tmp_path):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    count = infer.count_claimable_lines(
        str(db_path),
        prefer_missing_backend=infer.parse_backend_identity("openai:gpt-5-mini"),
    )

    assert count == 5


def test_search_trigger_ignores_status_only_updates(tmp_path):
    db_path = tmp_path / "ocr.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            page_id TEXT,
            volume TEXT,
            reviewed_text TEXT,
            qwen_text TEXT,
            tesseract_text TEXT,
            status TEXT,
            updated_at TEXT
        );
        CREATE VIRTUAL TABLE lines_fts USING fts5(
            line_id UNINDEXED,
            page_id,
            volume,
            search_text
        );
        INSERT INTO lines
            (id, page_id, volume, reviewed_text, qwen_text, tesseract_text, status)
        VALUES (1, 'p1', 'PL001', '', 'original', '', 'pending');
        INSERT INTO lines_fts(line_id, page_id, volume, search_text)
        VALUES (1, 'p1', 'PL001', 'sentinel');
        """
    )
    con.commit()
    con.close()

    infer.ensure_search_update_trigger(str(db_path))

    con = sqlite3.connect(db_path)
    con.execute("UPDATE lines SET status='processing' WHERE id=1")
    after_status = con.execute(
        "SELECT search_text FROM lines_fts WHERE line_id=1"
    ).fetchone()[0]
    con.execute("UPDATE lines SET qwen_text='changed' WHERE id=1")
    after_text = con.execute(
        "SELECT search_text FROM lines_fts WHERE line_id=1"
    ).fetchone()[0]
    con.close()

    assert after_status == "sentinel"
    assert after_text == "changed"


def test_run_worker_pass_still_runs_when_only_inferred_rows_are_missing_backend(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute("UPDATE lines SET status = 'inferred' WHERE status = 'pending'")
    con.commit()
    con.close()

    captured: list[dict] = []

    def fake_worker(worker_args: dict) -> None:
        captured.append(worker_args)

    monkeypatch.setattr(infer, "worker", fake_worker)

    args = infer.argparse.Namespace(
        db=str(db_path),
        jobs=1,
        limit=None,
        base_url="http://localhost:11434",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key=None,
        backend=["openai:gpt-5-mini"],
        model="gpt-5-mini",
        delay=0.0,
        batch_size=10,
        prefer_missing_backend="openai:gpt-5-mini",
    )

    infer._run_worker_pass(args=args, run_id="run-test", pass_label="pendentes", limit=None)

    assert len(captured) == 1
    assert captured[0]["prefer_missing_backend"] == infer.parse_backend_identity(
        "openai:gpt-5-mini"
    )


def test_main_runs_initial_pass_when_only_missing_backend_rows_exist(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "ocr.db"
    _make_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute("UPDATE lines SET status = 'inferred' WHERE status = 'pending'")
    con.commit()
    con.close()

    captured_calls: list[tuple[str, str | None]] = []

    def fake_run_worker_pass(*, args, run_id, pass_label, limit):
        captured_calls.append((pass_label, args.prefer_missing_backend))

    monkeypatch.setattr(infer, "_run_worker_pass", fake_run_worker_pass)
    monkeypatch.setattr(infer, "ensure_runs_column", lambda db: None)
    monkeypatch.setattr(infer, "ensure_score_llm_column", lambda db: None)
    monkeypatch.setattr(infer, "ensure_versions_table", lambda db: None)
    monkeypatch.setattr(infer, "ensure_search_update_trigger", lambda db: None)
    monkeypatch.setattr(
        infer.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            db=str(db_path),
            rerun_tesseract=False,
            rerun_tesseract_empty=False,
            recalc_agreement_score=False,
            tesseract_lang="lat",
            tessdata_dir=None,
            limit=None,
            delay=0.0,
            dry_run=False,
            tesseract_reference="auto",
            jobs=1,
            run_id="run-test",
            prefer_missing_backend="openai:gpt-5-mini",
            backend=["openai:gpt-5-mini"],
            model="gpt-5-mini",
            batch_size=10,
            base_url="http://localhost:11434",
            openai_base_url="https://api.openai.com/v1",
            openai_api_key=None,
            reprocess_below=0.0,
        ),
    )

    infer.main()

    assert captured_calls == [("pendentes", "openai:gpt-5-mini")]

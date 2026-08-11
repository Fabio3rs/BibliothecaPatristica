import json
import random
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sample


def make_line(
    *,
    page_id: str = "PG001-001",
    image_path: str,
    bbox: dict | None = None,
    text: str = "linea",
) -> dict:
    return {
        "page_id": page_id,
        "volume": page_id.split("-", 1)[0],
        "image_path": image_path,
        "line_index": 0,
        "bbox": bbox or {"x": 10, "y": 20, "w": 90, "h": 30},
        "line_image": b"png",
        "tesseract_text": text,
        "detected_lang": "Latin",
    }


def test_bbox_virtual_columns_are_indexed_and_exact_duplicates_are_rejected(tmp_path):
    db_path = tmp_path / "ocr.db"
    image_path = tmp_path / "page.png"
    conn = sample.open_db(str(db_path))
    session_id = sample.create_session(conn, "teste", 0, [])

    sample.insert_line(conn, session_id, make_line(image_path=str(image_path)))
    conn.commit()

    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(lines)")}
    assert {"bbox_x", "bbox_y", "bbox_w", "bbox_h"} <= columns
    assert tuple(
        conn.execute(
            "SELECT bbox_x, bbox_y, bbox_w, bbox_h FROM lines"
        ).fetchone()
    ) == (10, 20, 90, 30)

    conn.execute(
        "UPDATE lines SET bbox = ? WHERE id = 1",
        (json.dumps({"x": 12, "y": 22, "w": 88, "h": 28}),),
    )
    assert tuple(
        conn.execute(
            "SELECT bbox_x, bbox_y, bbox_w, bbox_h FROM lines"
        ).fetchone()
    ) == (12, 22, 88, 28)

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(lines)")}
    assert "idx_lines_page_bbox_lookup" in indexes
    assert "idx_lines_image_bbox_lookup" in indexes
    assert "idx_lines_created_at" in indexes
    assert "uq_lines_page_bbox_exact" in indexes
    query_plan = " ".join(
        str(value)
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM lines WHERE page_id=? AND bbox_x<?",
            ("PG001-001", 100),
        )
        for value in row
    )
    assert "USING INDEX" in query_plan or "USING COVERING INDEX" in query_plan

    with pytest.raises(sqlite3.IntegrityError):
        sample.insert_line(
            conn,
            session_id,
            make_line(
                image_path=str(tmp_path / "alias.png"),
                bbox={"x": 12, "y": 22, "w": 88, "h": 28},
            ),
        )
    conn.close()


def test_bbox_iou_and_material_conflict_threshold(tmp_path):
    first = {"x": 0, "y": 0, "w": 90, "h": 20}
    at_threshold = {"x": 10, "y": 0, "w": 90, "h": 20}
    assert sample.bbox_iou(first, at_threshold) == pytest.approx(0.8)

    db_path = tmp_path / "ocr.db"
    image_path = tmp_path / "page.png"
    conn = sample.open_db(str(db_path))
    session_id = sample.create_session(conn, "teste", 0, [])
    sample.insert_line(
        conn,
        session_id,
        make_line(image_path=str(image_path), bbox=first),
    )
    conn.commit()

    assert sample.find_bbox_conflict(
        conn, "PG001-001", str(tmp_path / "alias.png"), at_threshold
    ) == 1
    assert sample.find_bbox_conflict(
        conn, "PG999-001", str(image_path), at_threshold
    ) == 1
    assert sample.find_bbox_conflict(
        conn,
        "PG001-001",
        str(image_path),
        {"x": 200, "y": 0, "w": 90, "h": 20},
    ) is None
    conn.close()


def test_nonconflicting_page_insert_is_atomic_and_normalizes_nfc(tmp_path):
    db_path = tmp_path / "ocr.db"
    image_path = tmp_path / "page.png"
    conn = sample.open_db(str(db_path))
    session_id = sample.create_session(conn, "teste", 0, [])
    sample.insert_line(
        conn,
        session_id,
        make_line(
            image_path=str(image_path),
            bbox={"x": 0, "y": 0, "w": 90, "h": 20},
        ),
    )
    conn.commit()

    decomposed = "λόγος Cafe\u0301"
    result = {
        "page_id": "PG001-001",
        "volume": "PG001",
        "image_path": str(image_path),
        "detected_lang": "Greek",
        "rows": [
            {
                "line_index": 1,
                "bbox": {"x": 10, "y": 0, "w": 90, "h": 20},
                "line_image": b"conflict",
                "tesseract_text": "duplicada",
            },
            {
                "line_index": 2,
                "bbox": {"x": 0, "y": 100, "w": 90, "h": 20},
                "line_image": b"new",
                "tesseract_text": decomposed,
            },
        ],
    }

    assert sample.insert_nonconflicting_page(conn, session_id, result) == (1, 1)
    stored = conn.execute(
        "SELECT tesseract_text FROM lines WHERE line_index = 2"
    ).fetchone()[0]
    assert stored == unicodedata.normalize("NFC", decomposed)
    assert unicodedata.is_normalized("NFC", stored)
    conn.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('<bloco script="misto">λόγος verbum</bloco>', "mixed"),
        ('<bloco script="grego">λόγος</bloco><bloco script="latino">verbum</bloco>', "greek"),
        ("λόγος verbum", "mixed"),
        ("μόνος", "greek"),
        ("verbum", "other"),
    ],
)
def test_classify_ocr_text(text, expected):
    assert sample.classify_ocr_text(text) == expected


def test_priority_counts_use_50_40_10_and_redistribute_shortages():
    assert sample.priority_targets(10) == {"mixed": 5, "greek": 4, "other": 1}
    assert sample.priority_targets(1) == {"mixed": 1, "greek": 0, "other": 0}
    assert sample.allocate_priority_counts(
        10, {"mixed": 2, "greek": 20, "other": 20}
    ) == {"mixed": 2, "greek": 7, "other": 1}


def test_prioritized_selection_is_reproducible_and_excludes_existing_material(tmp_path):
    pairs = []
    classes = ["mixed"] * 6 + ["greek"] * 5 + ["other"] * 2
    for index, script_class in enumerate(classes):
        image = tmp_path / f"PG001-{index:03d}.png"
        text = tmp_path / f"source-{index:03d}.txt"
        content = {
            "mixed": '<bloco script="misto">λόγος verbum</bloco>',
            "greek": '<bloco script="grego">λόγος</bloco>',
            "other": '<bloco script="latino">verbum</bloco>',
        }[script_class]
        text.write_text(content, encoding="utf-8")
        pairs.append((image, text, f"PG001-{index:03d}"))

    kwargs = {
        "pairs": pairs,
        "per_volume": 10,
        "existing_page_ids": {"PG001-000"},
        "existing_image_paths": set(),
    }
    selected_a, counts_a, existing_a = sample.select_prioritized_pages(
        rng=random.Random(42), **kwargs
    )
    selected_b, counts_b, existing_b = sample.select_prioritized_pages(
        rng=random.Random(42), **kwargs
    )

    assert [pair[2] for pair in selected_a] == [pair[2] for pair in selected_b]
    assert counts_a == counts_b == {"mixed": 5, "greek": 4, "other": 1}
    assert existing_a == existing_b == 1
    assert "PG001-000" not in {pair[2] for pair in selected_a}


def test_bbox_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "ocr.db"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(sample.DDL)
    legacy.execute(
        """
        INSERT INTO lines(page_id, volume, image_path, line_index, bbox)
        VALUES ('PG001-001', 'PG001', '/tmp/page.png', 0, ?)
        """,
        (json.dumps({"x": 4, "y": 5, "w": 6, "h": 7}),),
    )
    legacy.commit()
    legacy.close()

    conn = sample.open_db(str(db_path))
    columns = [row[1] for row in conn.execute("PRAGMA table_xinfo(lines)")]
    assert columns.count("bbox_x") == 1
    assert columns.count("bbox_y") == 1
    assert columns.count("bbox_w") == 1
    assert columns.count("bbox_h") == 1
    assert tuple(
        conn.execute(
            "SELECT bbox_x, bbox_y, bbox_w, bbox_h FROM lines"
        ).fetchone()
    ) == (4, 5, 6, 7)
    conn.close()

    sample.open_db(str(db_path)).close()


def test_unexpected_integrity_error_is_raised_and_reported(tmp_path):
    conn = sample.open_db(str(tmp_path / "ocr.db"))
    report = sample.create_ingestion_report(
        db_path=str(tmp_path / "ocr.db"),
        root=str(tmp_path),
        mode="append",
        per_volume=1,
    )
    result = {
        "page_id": "PG001-001",
        "volume": "PG001",
        "image_path": str(tmp_path / "page.png"),
        "detected_lang": "Latin",
        "rows": [
            {
                "line_index": 0,
                "bbox": {"x": 0, "y": 0, "w": 20, "h": 20},
                "line_image": b"png",
                "tesseract_text": "linea",
            }
        ],
    }

    with pytest.raises(sqlite3.IntegrityError):
        sample.insert_nonconflicting_page(conn, 999999, result, report=report)

    assert conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0] == 0
    assert report["issues"][0]["code"] == "unexpected_integrity_error"
    assert report["issues"][0]["sqlite_errorname"] == "SQLITE_CONSTRAINT_FOREIGNKEY"
    conn.close()


def test_interrupted_ingestion_removes_empty_session_and_records_issue(
    tmp_path, monkeypatch
):
    volume = tmp_path / "teste" / "PG001"
    (volume / "images").mkdir(parents=True)
    (volume / "text").mkdir()
    (volume / "images" / "PG001-001.png").write_bytes(b"not-opened")
    (volume / "text" / "source-001.txt").write_text(
        '<bloco script="misto">λόγος verbum</bloco>', encoding="utf-8"
    )
    conn = sample.open_db(str(tmp_path / "ocr.db"))
    report = sample.create_ingestion_report(
        db_path=str(tmp_path / "ocr.db"),
        root=str(volume.parent),
        mode="append",
        per_volume=1,
    )

    class BrokenPool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("worker startup failed")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sample.mp, "Pool", BrokenPool)
    with pytest.raises(RuntimeError, match="worker startup failed"):
        sample.sample_and_ingest(
            volume.parent,
            conn,
            1,
            "teste",
            jobs=1,
            report=report,
        )

    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert report["session_id"] is None
    assert report["status"] == "failed"
    assert "ingestion_interrupted" in {
        issue["code"] for issue in report["issues"]
    }
    conn.close()


def test_interrupted_session_preserves_partial_counts(tmp_path):
    conn = sample.open_db(str(tmp_path / "ocr.db"))
    session_id = sample.create_session(conn, "teste", 0, [])
    sample.insert_line(
        conn,
        session_id,
        make_line(image_path=str(tmp_path / "page.png")),
    )
    conn.commit()

    count, volumes = sample.finalize_interrupted_session(conn, session_id)

    assert count == 1
    assert volumes == ["PG001"]
    row = conn.execute(
        "SELECT sample_size, volumes FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert row["sample_size"] == 1
    assert json.loads(row["volumes"]) == ["PG001"]
    conn.close()


def test_no_volumes_is_a_reported_failure(tmp_path):
    conn = sample.open_db(str(tmp_path / "ocr.db"))
    report = sample.create_ingestion_report(
        db_path=str(tmp_path / "ocr.db"),
        root=str(tmp_path),
        mode="append",
        per_volume=1,
    )

    with pytest.raises(sample.IngestionError, match="Nenhum volume"):
        sample.sample_and_ingest(tmp_path, conn, 1, "teste", report=report)

    assert report["issues"][0]["code"] == "no_volumes"
    conn.close()


def test_cli_failure_exits_nonzero_and_writes_json_report(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sample.py",
            "--root",
            str(tmp_path),
            "--db",
            str(tmp_path / "ocr.db"),
            "--append",
            "--report",
            str(report_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        sample.main()

    assert exc_info.value.code == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["finished_at"]
    assert "no_volumes" in {issue["code"] for issue in payload["issues"]}


def test_resample_dry_run_is_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sample.py",
            "--root",
            str(tmp_path),
            "--db",
            str(tmp_path / "ocr.db"),
            "--resample",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        sample.main()

    assert exc_info.value.code == 2
    assert "--resample não pode ser combinado" in capsys.readouterr().err

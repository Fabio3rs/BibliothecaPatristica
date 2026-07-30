from __future__ import annotations

import os
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as vdb
from scripts import sync_current_ocr_xml as syncer


VALID_XML = '<pagina estado="com_texto"><bloco tipo="texto_principal">Novo</bloco></pagina>'


@pytest.fixture()
def versions_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ocr_versions.db"
    con = vdb.open_versions_db(db_path)
    vdb.get_or_create_engine_version(
        con,
        engine="test",
        model="fixture",
        prompt_key="test",
        system_prompt="",
    )
    con.close()
    return db_path


def insert_result(
    db_path: Path,
    *,
    volume: str,
    page: int,
    content: str = VALID_XML,
    created_at: str = "2024-02-01 00:00:00",
    is_current: int = 1,
) -> int:
    con = sqlite3.connect(db_path)
    engine_id = con.execute("SELECT id FROM ocr_engine_versions LIMIT 1").fetchone()[0]
    marker = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM ocr_results").fetchone()[0]
    cur = con.execute(
        """
        INSERT INTO ocr_results (
            volume_id, page_num, image_path, image_hash, engine_version_id,
            text_content, text_hash, is_current, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            volume,
            page,
            f"teste/{volume}/images/{volume}-{page:03d}.png",
            f"image-{marker}",
            engine_id,
            content,
            vdb.sha256_str(content),
            is_current,
            created_at,
            created_at,
        ),
    )
    con.commit()
    result_id = cur.lastrowid
    con.close()
    return result_id


def make_text_file(
    base_dir: Path,
    *,
    volume: str,
    page: int,
    content: str = "antigo",
    prefix: str = "uuid",
    mtime: float | None = None,
) -> Path:
    path = base_dir / volume / "text" / f"{prefix}-{page:03d}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def utc_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_dry_run_then_apply_writes_exact_content_and_preserves_mode(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    content = '<?xml version="1.0"?>\n' + VALID_XML + "\n"
    insert_result(versions_db, volume="PG001", page=7, content=content)
    target = make_text_file(
        base_dir,
        volume="PG001",
        page=7,
        mtime=utc_timestamp("2024-01-01 00:00:00"),
    )
    target.chmod(0o640)

    dry_stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=False,
    )
    assert dry_stats.would_update == 1
    assert dry_stats.updated == 0
    assert target.read_text(encoding="utf-8") == "antigo"

    apply_stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert apply_stats.updated == 1
    assert target.read_text(encoding="utf-8") == content
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.parametrize(
    ("file_date", "expected_not_newer"),
    [
        ("2024-02-01 00:00:00", 1),
        ("2024-03-01 00:00:00", 1),
    ],
)
def test_equal_or_newer_file_is_not_replaced(
    versions_db: Path,
    tmp_path: Path,
    file_date: str,
    expected_not_newer: int,
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(versions_db, volume="PL001", page=12)
    target = make_text_file(
        base_dir,
        volume="PL001",
        page=12,
        mtime=utc_timestamp(file_date),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert stats.not_newer == expected_not_newer
    assert stats.updated == 0
    assert target.read_text(encoding="utf-8") == "antigo"


@pytest.mark.parametrize(
    "content",
    [
        "texto comum",
        "<pagina><bloco>sem fechamento</pagina>",
        "<documento><bloco>XML válido, raiz errada</bloco></documento>",
    ],
)
def test_rejects_content_that_is_not_valid_pagina_xml(
    versions_db: Path, tmp_path: Path, content: str
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(versions_db, volume="PO001", page=3, content=content)
    target = make_text_file(
        base_dir,
        volume="PO001",
        page=3,
        mtime=utc_timestamp("2024-01-01 00:00:00"),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert stats.invalid_xml == 1
    assert stats.no_valid_xml == 1
    assert stats.updated == 0
    assert target.read_text(encoding="utf-8") == "antigo"


def test_invalid_current_uses_most_recent_valid_xml_from_history(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    older_valid = "<pagina><bloco>válido antigo</bloco></pagina>"
    latest_valid = "<pagina><bloco>válido mais recente</bloco></pagina>"
    insert_result(
        versions_db,
        volume="PL020",
        page=10,
        content=older_valid,
        created_at="2024-01-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PL020",
        page=10,
        content=latest_valid,
        created_at="2024-02-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PL020",
        page=10,
        content="<pagina>histórico inválido",
        created_at="2024-03-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PL020",
        page=10,
        content="atual sem XML",
        created_at="2024-04-01 00:00:00",
    )
    target = make_text_file(
        base_dir,
        volume="PL020",
        page=10,
        mtime=utc_timestamp("2023-12-01 00:00:00"),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert stats.invalid_xml == 1
    assert stats.fallback_selected == 1
    assert stats.no_valid_xml == 0
    assert stats.updated == 1
    assert target.read_text(encoding="utf-8") == latest_valid


def test_since_also_limits_valid_xml_fallback(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(
        versions_db,
        volume="PO010",
        page=4,
        content=VALID_XML,
        created_at="2024-01-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PO010",
        page=4,
        content="atual inválida",
        created_at="2024-02-01 00:00:00",
    )
    target = make_text_file(
        base_dir,
        volume="PO010",
        page=4,
        mtime=utc_timestamp("2023-01-01 00:00:00"),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        since=syncer.parse_datetime("2024-02-01"),
        apply=True,
    )
    assert stats.no_valid_xml == 1
    assert stats.fallback_selected == 0
    assert stats.updated == 0
    assert target.read_text(encoding="utf-8") == "antigo"


def test_ignore_file_mtime_allows_older_fallback_to_replace_newer_file(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    fallback = "<pagina><bloco>fallback</bloco></pagina>"
    insert_result(
        versions_db,
        volume="PG010",
        page=8,
        content=fallback,
        created_at="2024-01-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PG010",
        page=8,
        content="atual inválida",
        created_at="2024-03-01 00:00:00",
    )
    target = make_text_file(
        base_dir,
        volume="PG010",
        page=8,
        mtime=utc_timestamp("2024-02-01 00:00:00"),
    )

    regular = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert regular.fallback_selected == 1
    assert regular.not_newer == 1
    assert regular.updated == 0
    assert target.read_text(encoding="utf-8") == "antigo"

    ignored = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
        ignore_file_mtime=True,
    )
    assert ignored.fallback_selected == 1
    assert ignored.not_newer == 0
    assert ignored.updated == 1
    assert target.read_text(encoding="utf-8") == fallback


def test_since_is_inclusive_and_volume_filter_is_applied(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(
        versions_db,
        volume="PG001",
        page=1,
        created_at="2024-01-31 23:59:59",
    )
    insert_result(
        versions_db,
        volume="PG001",
        page=2,
        created_at="2024-02-01 00:00:00",
    )
    insert_result(
        versions_db,
        volume="PG002",
        page=1,
        created_at="2024-03-01 00:00:00",
    )
    for volume, page in (("PG001", 1), ("PG001", 2), ("PG002", 1)):
        make_text_file(
            base_dir,
            volume=volume,
            page=page,
            mtime=utc_timestamp("2024-01-01 00:00:00"),
        )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        since=syncer.parse_datetime("2024-02-01"),
        volumes=["PG001"],
    )
    assert stats.selected == 1
    assert stats.would_update == 1


def test_historical_row_is_ignored_and_latest_duplicate_current_wins(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(
        versions_db,
        volume="PG053",
        page=316,
        content="<pagina><bloco>histórica</bloco></pagina>",
        created_at="2024-04-01 00:00:00",
        is_current=0,
    )
    insert_result(
        versions_db,
        volume="PG053",
        page=316,
        content="<pagina><bloco>atual antiga</bloco></pagina>",
        created_at="2024-03-01 00:00:00",
    )
    latest = "<pagina><bloco>atual mais recente</bloco></pagina>"
    insert_result(
        versions_db,
        volume="PG053",
        page=316,
        content=latest,
        created_at="2024-05-01 00:00:00",
    )
    target = make_text_file(
        base_dir,
        volume="PG053",
        page=316,
        mtime=utc_timestamp("2024-01-01 00:00:00"),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
        apply=True,
    )
    assert stats.selected == 1
    assert stats.duplicate_current == 1
    assert stats.updated == 1
    assert target.read_text(encoding="utf-8") == latest


def test_missing_and_ambiguous_targets_are_reported(
    versions_db: Path, tmp_path: Path
) -> None:
    base_dir = tmp_path / "teste"
    insert_result(versions_db, volume="PL010", page=1)
    insert_result(versions_db, volume="PL010", page=2)
    make_text_file(
        base_dir,
        volume="PL010",
        page=2,
        prefix="first",
        mtime=utc_timestamp("2024-01-01 00:00:00"),
    )
    make_text_file(
        base_dir,
        volume="PL010",
        page=2,
        prefix="second",
        mtime=utc_timestamp("2024-01-01 00:00:00"),
    )

    stats = syncer.sync_current_xml(
        db_path=versions_db,
        base_dir=base_dir,
    )
    assert stats.missing_file == 1
    assert stats.ambiguous_file == 1
    assert stats.would_update == 0


def test_parse_datetime_normalizes_offset_to_utc() -> None:
    parsed = syncer.parse_datetime("2024-02-01T03:00:00-03:00")
    assert parsed == datetime(2024, 2, 1, 6, 0, tzinfo=timezone.utc)

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as vdb
from scripts.normalize_duplicate_ocr_pages import apply_plan, build_plan


def insert_result(
    con: sqlite3.Connection,
    *,
    volume: str,
    page: int,
    text: str,
    current: bool,
    created_at: str,
) -> int:
    engine_id = con.execute("SELECT id FROM ocr_engine_versions LIMIT 1").fetchone()[0]
    cur = con.execute(
        """
        INSERT INTO ocr_results (
            volume_id, page_num, image_path, image_hash, engine_version_id,
            text_content, text_hash, is_current, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            volume,
            page,
            f"teste/{volume}/images/{volume}-{page:03d}.png",
            f"image-{page}",
            engine_id,
            text,
            vdb.sha256_str(text),
            int(current),
            created_at,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def make_fixture(
    tmp_path: Path,
    *,
    include_canonical_history: bool = True,
) -> tuple[Path, Path, Path, int]:
    base_dir = tmp_path / "teste"
    text_dir = base_dir / "PG013" / "text"
    text_dir.mkdir(parents=True)
    legacy = text_dir / "bd07f101-534d-403a-b3ff-5b6ded5abf67-0001.txt"
    canonical = text_dir / "PG013-001.txt"
    legacy.write_text("legado", encoding="utf-8")
    canonical.write_text("<pagina>novo</pagina>", encoding="utf-8")

    db_path = tmp_path / "ocr_versions.db"
    con = vdb.open_versions_db(db_path)
    vdb.get_or_create_engine_version(
        con, engine="test", model="fixture", prompt_key="test", system_prompt=""
    )
    if include_canonical_history:
        insert_result(
            con,
            volume="PG013",
            page=1,
            text=canonical.read_text(encoding="utf-8"),
            current=False,
            created_at="2024-02-01 00:00:00",
        )
    old_id = insert_result(
        con,
        volume="PG013",
        page=1,
        text=legacy.read_text(encoding="utf-8"),
        current=True,
        created_at="2024-01-01 00:00:00",
    )
    con.commit()
    con.close()
    return base_dir, db_path, legacy, old_id


def test_build_plan_is_read_only_and_selects_canonical(tmp_path: Path) -> None:
    base_dir, db_path, legacy, old_id = make_fixture(tmp_path)

    plan = build_plan(base_dir=base_dir, db_path=db_path, volumes=["PG013"])

    assert len(plan) == 1
    assert plan[0].page_num == 1
    assert plan[0].current_result_id == old_id
    assert plan[0].changes_current is True
    assert Path(plan[0].kept_file).name == "PG013-001.txt"
    assert legacy.exists()


def test_apply_quarantines_legacy_and_promotes_canonical(tmp_path: Path) -> None:
    base_dir, db_path, legacy, old_id = make_fixture(tmp_path)
    plan = build_plan(base_dir=base_dir, db_path=db_path, volumes=["PG013"])

    manifest = apply_plan(
        plan=plan,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )

    assert manifest.is_file()
    assert not legacy.exists()
    assert (manifest.parent / "PG013" / legacy.name).read_text(encoding="utf-8") == "legado"
    con = sqlite3.connect(db_path)
    current = con.execute(
        "SELECT id, text_content FROM ocr_results WHERE is_current = 1"
    ).fetchone()
    con.close()
    assert current == (plan[0].kept_result_id, "<pagina>novo</pagina>")
    assert current[0] != old_id


def test_missing_canonical_history_requires_explicit_option(tmp_path: Path) -> None:
    base_dir, db_path, _, _ = make_fixture(
        tmp_path,
        include_canonical_history=False,
    )

    with pytest.raises(RuntimeError, match="register-missing-canonical"):
        build_plan(base_dir=base_dir, db_path=db_path, volumes=["PG013"])


def test_apply_registers_missing_canonical_as_auditable_current(
    tmp_path: Path,
) -> None:
    base_dir, db_path, legacy, old_id = make_fixture(
        tmp_path,
        include_canonical_history=False,
    )
    plan = build_plan(
        base_dir=base_dir,
        db_path=db_path,
        volumes=["PG013"],
        register_missing_canonical=True,
    )

    assert plan[0].kept_result_id is None
    assert plan[0].registers_canonical is True
    manifest = apply_plan(
        plan=plan,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )

    assert not legacy.exists()
    con = sqlite3.connect(db_path)
    current = con.execute(
        """
        SELECT r.id, r.text_content, r.reprocess_reason, e.engine
        FROM ocr_results r
        JOIN ocr_engine_versions e ON e.id = r.engine_version_id
        WHERE r.is_current = 1
        """
    ).fetchone()
    con.close()
    assert current[0] != old_id
    assert current[1:] == (
        "<pagina>novo</pagina>",
        "canonical_filesystem_reconciliation",
        "filesystem_reconciliation",
    )
    assert '"registered_results"' in manifest.read_text(encoding="utf-8")

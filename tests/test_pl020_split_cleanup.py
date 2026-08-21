from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import cleanup_pl020_split_derivatives as db_cleanup
from scripts import quarantine_pl020_embedded_pl021 as file_cleanup


def connect(path: Path, schema: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(schema)
    return con


def test_file_move_is_resumable_and_keeps_lower_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_cleanup, "FIRST_PAGE", 2)
    monkeypatch.setattr(file_cleanup, "LAST_PAGE", 4)
    monkeypatch.setattr(file_cleanup, "EXPECTED_FILES", {"images": 3, "text": 4})
    monkeypatch.setattr(
        file_cleanup, "EXPECTED_UNIQUE_PAGES", {"images": 3, "text": 3}
    )
    source = tmp_path / "teste" / "PL020"
    quarantine = tmp_path / "quarantine"
    for kind in ("images", "text"):
        (source / kind).mkdir(parents=True)
    for page in range(1, 5):
        (source / "images" / f"PL020-{page:03d}.png").write_bytes(str(page).encode())
        (source / "text" / f"PL020-{page:03d}.txt").write_text(str(page))
    (source / "text" / "uuid-002.txt").write_text("shadow")

    rows, before = file_cleanup.build_inventory(source, quarantine)
    file_cleanup.assert_expected_inventory(before)
    assert before["images"]["pending"] == 3
    assert before["text"]["pending"] == 4

    moved = file_cleanup.move_pending(rows)
    assert len(moved) == 7
    assert (source / "images" / "PL020-001.png").is_file()
    assert (source / "text" / "PL020-001.txt").is_file()

    rows_after, after = file_cleanup.build_inventory(source, quarantine)
    file_cleanup.assert_expected_inventory(after)
    assert after["images"]["moved"] == 3
    assert after["text"]["moved"] == 4
    assert file_cleanup.move_pending(rows_after) == []


def test_file_inventory_rejects_source_destination_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_cleanup, "FIRST_PAGE", 2)
    monkeypatch.setattr(file_cleanup, "LAST_PAGE", 2)
    monkeypatch.setattr(file_cleanup, "EXPECTED_FILES", {"images": 1, "text": 1})
    monkeypatch.setattr(
        file_cleanup, "EXPECTED_UNIQUE_PAGES", {"images": 1, "text": 1}
    )
    source = tmp_path / "source"
    quarantine = tmp_path / "quarantine"
    for root in (source, quarantine):
        for kind in ("images", "text"):
            (root / kind).mkdir(parents=True)
            (root / kind / f"PL020-002.{ 'png' if kind == 'images' else 'txt'}").write_text("x")
    _, summary = file_cleanup.build_inventory(source, quarantine)
    with pytest.raises(RuntimeError, match="coexistem"):
        file_cleanup.assert_expected_inventory(summary)


def test_resumo_cleanup_keeps_pages_1_to_611_and_run(tmp_path: Path) -> None:
    path = tmp_path / "patristica_resumos.db"
    page_tables = "\n".join(
        f"CREATE TABLE {table}(documento TEXT, pagina_num INTEGER);"
        for table in db_cleanup.PAGE_SUMMARY_TABLES
        if table not in {"resumo_review_queue"}
    )
    schema = f"""
        CREATE TABLE resumo_runs(id INTEGER PRIMARY KEY, documento TEXT);
        CREATE TABLE resumo_generations(
            id INTEGER PRIMARY KEY,
            run_id INTEGER REFERENCES resumo_runs(id) ON DELETE CASCADE,
            documento TEXT,
            pagina_num INTEGER,
            previous_generation_id INTEGER REFERENCES resumo_generations(id)
        );
        CREATE TABLE resumo_translations(
            id INTEGER PRIMARY KEY,
            generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE
        );
        CREATE TABLE resumo_generation_clusters(
            generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE,
            documento TEXT,
            pagina_num INTEGER
        );
        CREATE TABLE resumo_review_queue(
            generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE,
            documento TEXT,
            pagina_num INTEGER
        );
        {page_tables}
    """
    with connect(path, schema) as con:
        con.execute("INSERT INTO resumo_runs VALUES(1, 'PL020')")
        con.executemany(
            "INSERT INTO resumo_generations(id,run_id,documento,pagina_num) VALUES(?,?,?,?)",
            ((1, 1, "PL020", 611), (2, 1, "PL020", 612)),
        )
        con.execute("UPDATE resumo_generations SET previous_generation_id=id")
        con.execute("INSERT INTO resumo_translations VALUES(1, 2)")
        con.execute("INSERT INTO resumo_generation_clusters VALUES(2,'PL020',612)")
        con.executemany(
            "INSERT INTO resumo_review_queue VALUES(?,?,?)",
            ((1, "PL020", 611), (2, "PL020", 612)),
        )
        for table in db_cleanup.PAGE_SUMMARY_TABLES:
            if table == "resumo_review_queue":
                continue
            con.executemany(
                f"INSERT INTO {table}(documento,pagina_num) VALUES(?,?)",
                (("PL020", 611), ("PL020", 612)),
            )

    changed, after = db_cleanup.apply_one(path, path.name)
    assert changed["resumo_generations"] == 1
    assert all(
        value == 0
        for key, value in after.items()
        if key != "resumo_runs_preserved"
    )
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT pagina_num FROM resumo_generations"
        ).fetchall() == [(611,)]
        assert con.execute("SELECT COUNT(*) FROM resumo_runs").fetchone()[0] == 1
        for table in db_cleanup.PAGE_SUMMARY_TABLES:
            assert con.execute(
                f"SELECT pagina_num FROM {table} ORDER BY pagina_num"
            ).fetchall() == [(611,)]


def test_keyword_cleanup_removes_only_newly_orphaned_keywords(tmp_path: Path) -> None:
    path = tmp_path / "patristica_keywords.db"
    schema = """
        CREATE TABLE keywords(
            id INTEGER PRIMARY KEY,
            hdbscan_group_id INTEGER
        );
        CREATE TABLE keyword_occurrence(
            id INTEGER PRIMARY KEY,
            keyword_id INTEGER REFERENCES keywords(id),
            documento TEXT,
            pagina_num INTEGER
        );
        CREATE TABLE keyword_alias(id INTEGER PRIMARY KEY, keyword_id INTEGER REFERENCES keywords(id));
        CREATE TABLE keyword_category(id INTEGER PRIMARY KEY, keyword_id INTEGER REFERENCES keywords(id));
        CREATE TABLE keyword_embedding(id INTEGER PRIMARY KEY, keyword_id INTEGER REFERENCES keywords(id));
        CREATE TABLE keyword_clusters(keyword_id INTEGER PRIMARY KEY, cluster_id INTEGER);
        CREATE TABLE keyword_cluster_meta(cluster_id INTEGER PRIMARY KEY);
        CREATE TABLE cluster_canonical_names(group_id INTEGER);
    """
    with connect(path, schema) as con:
        con.executemany("INSERT INTO keywords VALUES(?,?)", ((1, 10), (2, 20)))
        con.executemany(
            "INSERT INTO keyword_occurrence VALUES(?,?,?,?)",
            (
                (1, 1, "PL020", 612),
                (2, 2, "PL020", 612),
                (3, 2, "PL021", 5),
            ),
        )
        for table in ("keyword_alias", "keyword_category", "keyword_embedding"):
            con.executemany(
                f"INSERT INTO {table}(id,keyword_id) VALUES(?,?)", ((1, 1), (2, 2))
            )
        con.executemany("INSERT INTO keyword_clusters VALUES(?,?)", ((1, 100), (2, 200)))
        con.executemany("INSERT INTO keyword_cluster_meta VALUES(?)", ((100,), (200,)))
        con.executemany("INSERT INTO cluster_canonical_names VALUES(?)", ((10,), (20,)))

    changed, after = db_cleanup.apply_one(path, path.name)
    assert changed["keyword_occurrence"] == 2
    assert changed["keywords_orphaned"] == 1
    assert set(after.values()) == {0}
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT id FROM keywords").fetchall() == [(2,)]
        assert con.execute(
            "SELECT documento,pagina_num FROM keyword_occurrence"
        ).fetchall() == [("PL021", 5)]
        assert con.execute("SELECT cluster_id FROM keyword_cluster_meta").fetchall() == [
            (200,)
        ]


def test_scripture_cleanup_uses_exact_page_suffix_and_cascades(tmp_path: Path) -> None:
    path = tmp_path / "scripture_citations.db"
    schema = """
        CREATE TABLE citation_volumes(
            volume_id TEXT PRIMARY KEY, scan_status TEXT, last_run_id INTEGER
        );
        CREATE TABLE citation_files(
            file_id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES citation_volumes(volume_id) ON DELETE CASCADE,
            file_path TEXT
        );
        CREATE TABLE citation_file_pages(
            file_id INTEGER REFERENCES citation_files(file_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_groups(
            group_id INTEGER PRIMARY KEY,
            file_id INTEGER REFERENCES citation_files(file_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_occurrences(
            occurrence_key TEXT PRIMARY KEY,
            group_id INTEGER REFERENCES citation_groups(group_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_index_seeds(seed_key TEXT PRIMARY KEY, volume_id TEXT);
        CREATE TABLE citation_seed_links(
            seed_key TEXT REFERENCES citation_index_seeds(seed_key) ON DELETE CASCADE,
            occurrence_key TEXT REFERENCES citation_occurrences(occurrence_key) ON DELETE CASCADE
        );
        CREATE TABLE citation_book_aliases(id INTEGER PRIMARY KEY, volume_id TEXT);
        CREATE TABLE citation_format_profiles(id INTEGER PRIMARY KEY, volume_id TEXT);
    """
    with connect(path, schema) as con:
        con.execute("INSERT INTO citation_volumes VALUES('PL020','complete',9)")
        con.executemany(
            "INSERT INTO citation_files VALUES(?,?,?)",
            (
                (1, "PL020", "/repo/teste/PL020/text/PL020-611.txt"),
                (2, "PL020", "/repo/teste/PL020/text/uuid-0612.txt"),
            ),
        )
        con.executemany("INSERT INTO citation_file_pages VALUES(?)", ((1,), (2,)))
        con.executemany("INSERT INTO citation_groups VALUES(?,?)", ((1, 1), (2, 2)))
        con.executemany(
            "INSERT INTO citation_occurrences VALUES(?,?)", (("a", 1), ("b", 2))
        )

    _, after = db_cleanup.apply_one(path, path.name)
    assert after["citation_files"] == 0
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT file_id FROM citation_files").fetchall() == [(1,)]
        assert con.execute("SELECT group_id FROM citation_groups").fetchall() == [(1,)]
        assert con.execute(
            "SELECT scan_status,last_run_id FROM citation_volumes"
        ).fetchall() == [("partial", None)]


def test_patristic_indices_cleanup_rebuilds_only_pl020(tmp_path: Path) -> None:
    path = tmp_path / "patristic_indices.db"
    schema = """
        CREATE TABLE volumes(volume_id TEXT PRIMARY KEY);
        CREATE TABLE runs(id INTEGER PRIMARY KEY, volume_id TEXT);
        CREATE TABLE works(
            work_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE index_sections(
            section_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE index_entries(
            id INTEGER PRIMARY KEY,
            section_key TEXT REFERENCES index_sections(section_key) ON DELETE CASCADE
        );
    """
    with connect(path, schema) as con:
        for index, volume in enumerate(("PL020", "PL021"), start=1):
            con.execute("INSERT INTO volumes VALUES(?)", (volume,))
            con.execute("INSERT INTO runs VALUES(?,?)", (index, volume))
            con.execute("INSERT INTO works VALUES(?,?)", (f"{volume}:w", volume))
            con.execute(
                "INSERT INTO index_sections VALUES(?,?)", (f"{volume}:s", volume)
            )
            con.execute(
                "INSERT INTO index_entries VALUES(?,?)", (index, f"{volume}:s")
            )

    _, after = db_cleanup.apply_one(path, path.name)
    assert set(after.values()) == {0}
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT volume_id FROM volumes").fetchall() == [("PL021",)]
        assert con.execute("SELECT volume_id FROM runs").fetchall() == [("PL021",)]
        assert con.execute("SELECT COUNT(*) FROM index_entries").fetchone()[0] == 1


def test_alphabetical_analysis_cleanup_removes_cascades_and_fts(tmp_path: Path) -> None:
    path = tmp_path / "alphabetical_analysis.db"
    schema = """
        CREATE TABLE analysis_volumes(volume_id TEXT PRIMARY KEY);
        CREATE TABLE analysis_stage_runs(run_id INTEGER PRIMARY KEY, volume_id TEXT);
        CREATE TABLE analysis_stage_artifacts(
            id INTEGER PRIMARY KEY,
            run_id INTEGER REFERENCES analysis_stage_runs(run_id) ON DELETE CASCADE,
            volume_id TEXT
        );
        CREATE TABLE analysis_volume_stages(
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_discovered_pages(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_discovered_segments(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_sections(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_entries(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_occurrences(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_scripture_refs(
            id INTEGER PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE VIRTUAL TABLE analysis_entry_fts USING fts5(volume_id, text);
    """
    with connect(path, schema) as con:
        con.execute("INSERT INTO analysis_volumes VALUES('PL020')")
        con.execute("INSERT INTO analysis_stage_runs VALUES(1,'PL020')")
        con.execute("INSERT INTO analysis_stage_artifacts VALUES(1,1,'PL020')")
        for table in (
            "analysis_volume_stages",
            "analysis_discovered_pages",
            "analysis_discovered_segments",
            "analysis_sections",
            "analysis_entries",
            "analysis_occurrences",
            "analysis_scripture_refs",
        ):
            con.execute(f"INSERT INTO {table}(volume_id) VALUES('PL020')")
        con.execute("INSERT INTO analysis_entry_fts VALUES('PL020','entry')")

    _, after = db_cleanup.apply_one(path, path.name)
    assert set(after.values()) == {0}


def test_summary_scripture_cleanup_preserves_earlier_page(tmp_path: Path) -> None:
    path = tmp_path / "summary_scripture_citations_v3.db"
    schema = """
        CREATE TABLE pages(id INTEGER PRIMARY KEY, volume_id TEXT, physical_page INTEGER);
        CREATE TABLE scripture_mentions(
            id INTEGER PRIMARY KEY,
            page_id INTEGER REFERENCES pages(id) ON DELETE CASCADE
        );
        CREATE TABLE rejected_mentions(
            id INTEGER PRIMARY KEY,
            page_id INTEGER REFERENCES pages(id) ON DELETE CASCADE
        );
    """
    with connect(path, schema) as con:
        con.executemany(
            "INSERT INTO pages VALUES(?,?,?)",
            ((1, "PL020", 611), (2, "PL020", 612), (3, "PL021", 612)),
        )
        con.executemany("INSERT INTO scripture_mentions VALUES(?,?)", ((1, 1), (2, 2)))
        con.execute("INSERT INTO rejected_mentions VALUES(1,2)")

    _, after = db_cleanup.apply_one(path, path.name)
    assert set(after.values()) == {0}
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT volume_id,physical_page FROM pages ORDER BY id"
        ).fetchall() == [("PL020", 611), ("PL021", 612)]
        assert con.execute("SELECT page_id FROM scripture_mentions").fetchall() == [(1,)]


def test_ocr_databases_are_not_in_cleanup_scope() -> None:
    assert not set(db_cleanup.DATABASE_ORDER).intersection(
        {"ocr_versions.db", "ocr_eval.db", "tesseract.db"}
    )
    assert {"ocr_versions.db", "ocr_eval.db", "tesseract.db"}.issubset(
        db_cleanup.PRESERVED_DATABASES
    )

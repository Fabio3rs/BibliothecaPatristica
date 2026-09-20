from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import cleanup_defective_volume_databases as db_cleanup
from scripts import quarantine_defective_volume_files as file_cleanup


TARGETS = db_cleanup.AUTHORIZED_VOLUME_IDS


def _connect(path: Path, schema: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(schema)
    return connection


@pytest.mark.parametrize(
    ("database_name", "schema", "insert_sql", "selected_label"),
    [
        (
            "ocr_eval.db",
            "CREATE TABLE evaluations(id INTEGER PRIMARY KEY, volume_id TEXT);",
            "INSERT INTO evaluations(volume_id) VALUES (?)",
            "evaluations",
        ),
        (
            "ocr_versions.db",
            "CREATE TABLE ocr_results(id INTEGER PRIMARY KEY, volume_id TEXT);",
            "INSERT INTO ocr_results(volume_id) VALUES (?)",
            "ocr_results",
        ),
        (
            "patristica_keywords.db",
            "CREATE TABLE keyword_occurrence(id INTEGER PRIMARY KEY, documento TEXT);",
            "INSERT INTO keyword_occurrence(documento) VALUES (?)",
            "keyword_occurrence",
        ),
    ],
)
def test_simple_database_cleanup_preserves_unselected_volume(
    tmp_path: Path,
    database_name: str,
    schema: str,
    insert_sql: str,
    selected_label: str,
) -> None:
    path = tmp_path / database_name
    with _connect(path, schema) as connection:
        connection.execute(insert_sql, ("PG024",))
        connection.execute(insert_sql, ("PL020",))

    queries = db_cleanup._database_queries(database_name, TARGETS)
    with db_cleanup._connect_readonly(path) as connection:
        assert db_cleanup._count_queries(connection, queries)[selected_label] == 1

    changed, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert changed[selected_label] == 1
    assert remaining[selected_label] == 0
    assert violations == []
    column = "documento" if database_name == "patristica_keywords.db" else "volume_id"
    table = selected_label
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT {column} FROM {table}").fetchall() == [
            ("PL020",)
        ]


def test_tesseract_cleanup_uses_old_image_path(tmp_path: Path) -> None:
    path = tmp_path / "tesseract.db"
    with _connect(
        path,
        "CREATE TABLE tesseract_cache(image_hash TEXT, imgpath TEXT, lang TEXT);",
    ) as connection:
        connection.executemany(
            "INSERT INTO tesseract_cache VALUES (?, ?, ?)",
            (
                ("old", "/repo/teste/PG024/images/PG024-001.png", "migne"),
                ("kept", "/repo/teste/PL020/images/PL020-001.png", "migne"),
            ),
        )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    changed, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert changed == {"tesseract_cache": 1}
    assert remaining == {"tesseract_cache": 0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT image_hash FROM tesseract_cache"
        ).fetchall() == [("kept",)]


def test_resumo_cleanup_respects_self_chain_and_shared_rows(tmp_path: Path) -> None:
    path = tmp_path / "patristica_resumos.db"
    schema = """
        CREATE TABLE resumo_runs(id INTEGER PRIMARY KEY, documento TEXT);
        CREATE TABLE resumo_generations(
            id INTEGER PRIMARY KEY,
            run_id INTEGER REFERENCES resumo_runs(id) ON DELETE CASCADE,
            documento TEXT,
            previous_generation_id INTEGER REFERENCES resumo_generations(id)
        );
        CREATE TABLE resumo_translations(
            id INTEGER PRIMARY KEY,
            generation_id INTEGER REFERENCES resumo_generations(id) ON DELETE CASCADE
        );
        CREATE TABLE resumo_generation_clusters(documento TEXT, generation_id INTEGER);
        CREATE TABLE resumo_review_queue(documento TEXT, generation_id INTEGER);
        CREATE TABLE resumo_context_anchors(documento TEXT);
        CREATE TABLE resumo_pagina_embedding(documento TEXT);
        CREATE TABLE resumo_pagina_embedding_reduced(documento TEXT);
        CREATE TABLE resumo_pagina_clusters(documento TEXT);
        CREATE TABLE resumo_global_embedding(documento TEXT);
        CREATE TABLE resumo_global_embedding_reduced(documento TEXT);
        CREATE TABLE resumo_global_clusters(documento TEXT);
        CREATE TABLE resumos(documento TEXT);
    """
    with _connect(path, schema) as connection:
        connection.executemany(
            "INSERT INTO resumo_runs(id, documento) VALUES (?, ?)",
            ((1, "PG024"), (2, "PL020")),
        )
        connection.execute(
            "INSERT INTO resumo_generations VALUES (10, 1, 'PG024', NULL)"
        )
        connection.execute(
            "INSERT INTO resumo_generations VALUES (11, 1, 'PG024', 10)"
        )
        connection.execute(
            "INSERT INTO resumo_generations VALUES (20, 2, 'PL020', NULL)"
        )
        connection.execute("INSERT INTO resumo_translations VALUES (1, 11)")
        for table in (
            "resumo_context_anchors",
            "resumo_pagina_embedding",
            "resumo_pagina_embedding_reduced",
            "resumo_pagina_clusters",
            "resumo_global_embedding",
            "resumo_global_embedding_reduced",
            "resumo_global_clusters",
            "resumos",
        ):
            connection.executemany(
                f"INSERT INTO {table}(documento) VALUES (?)",
                (("PG024",), ("PL020",)),
            )
        connection.executemany(
            "INSERT INTO resumo_generation_clusters VALUES (?, ?)",
            (("PG024", 11), ("PL020", 20)),
        )
        connection.executemany(
            "INSERT INTO resumo_review_queue VALUES (?, ?)",
            (("PG024", 11), ("PL020", 20)),
        )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    _, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert set(remaining.values()) == {0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT documento FROM resumo_generations"
        ).fetchall() == [("PL020",)]
        assert connection.execute("SELECT documento FROM resumos").fetchall() == [
            ("PL020",)
        ]


def test_patristic_index_cleanup_uses_foreign_key_cascades(tmp_path: Path) -> None:
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
            volume_id TEXT REFERENCES volumes(volume_id) ON DELETE CASCADE,
            work_key TEXT REFERENCES works(work_key) ON DELETE SET NULL
        );
        CREATE TABLE index_entries(
            id INTEGER PRIMARY KEY,
            section_key TEXT REFERENCES index_sections(section_key) ON DELETE CASCADE
        );
    """
    with _connect(path, schema) as connection:
        for volume_id in ("PG024", "PL020"):
            connection.execute("INSERT INTO volumes VALUES (?)", (volume_id,))
            connection.execute("INSERT INTO runs(volume_id) VALUES (?)", (volume_id,))
            connection.execute(
                "INSERT INTO works VALUES (?, ?)", (f"{volume_id}:w", volume_id)
            )
            connection.execute(
                "INSERT INTO index_sections VALUES (?, ?, ?)",
                (f"{volume_id}:s", volume_id, f"{volume_id}:w"),
            )
            connection.execute(
                "INSERT INTO index_entries(section_key) VALUES (?)",
                (f"{volume_id}:s",),
            )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    _, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert set(remaining.values()) == {0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT volume_id FROM volumes").fetchall() == [
            ("PL020",)
        ]
        assert connection.execute("SELECT COUNT(*) FROM index_entries").fetchone()[0] == 1


def test_alphabetical_index_cleanup_uses_foreign_key_cascades(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alphabetical_indices.db"
    schema = """
        CREATE TABLE alphabetical_volumes(volume_id TEXT PRIMARY KEY);
        CREATE TABLE alphabetical_runs(
            run_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_sections(
            section_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_source_spans(
            span_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_boundaries(
            boundary_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_volume_quality(
            volume_id TEXT PRIMARY KEY REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_nodes(
            node_key TEXT PRIMARY KEY,
            section_key TEXT REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_entries(
            entry_key TEXT PRIMARY KEY,
            section_key TEXT REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_refs(
            ref_id INTEGER PRIMARY KEY,
            entry_key TEXT REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE
        );
        CREATE TABLE alphabetical_scripture_refs(
            ref_id INTEGER PRIMARY KEY,
            entry_key TEXT REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE
        );
    """
    with _connect(path, schema) as connection:
        for index, volume_id in enumerate(("PG116", "PL020"), start=1):
            section = f"{volume_id}:s"
            entry = f"{volume_id}:e"
            connection.execute("INSERT INTO alphabetical_volumes VALUES (?)", (volume_id,))
            connection.execute(
                "INSERT INTO alphabetical_runs VALUES (?, ?)",
                (f"{volume_id}:run", volume_id),
            )
            connection.execute(
                "INSERT INTO alphabetical_sections VALUES (?, ?)", (section, volume_id)
            )
            connection.execute(
                "INSERT INTO alphabetical_source_spans VALUES (?, ?)",
                (f"{volume_id}:span", volume_id),
            )
            connection.execute(
                "INSERT INTO alphabetical_boundaries VALUES (?, ?)",
                (f"{volume_id}:boundary", volume_id),
            )
            connection.execute(
                "INSERT INTO alphabetical_volume_quality VALUES (?)", (volume_id,)
            )
            connection.execute(
                "INSERT INTO alphabetical_nodes VALUES (?, ?)",
                (f"{volume_id}:node", section),
            )
            connection.execute(
                "INSERT INTO alphabetical_entries VALUES (?, ?)", (entry, section)
            )
            connection.execute(
                "INSERT INTO alphabetical_refs VALUES (?, ?)", (index, entry)
            )
            connection.execute(
                "INSERT INTO alphabetical_scripture_refs VALUES (?, ?)",
                (index, entry),
            )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    _, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert set(remaining.values()) == {0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT volume_id FROM alphabetical_volumes"
        ).fetchall() == [("PL020",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM alphabetical_refs"
        ).fetchone()[0] == 1


def test_alphabetical_analysis_cleanup_releases_stage_run_reference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alphabetical_analysis.db"
    schema = """
        CREATE TABLE analysis_volumes(volume_id TEXT PRIMARY KEY);
        CREATE TABLE analysis_stage_runs(run_id TEXT PRIMARY KEY, volume_id TEXT);
        CREATE TABLE analysis_volume_stages(
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE,
            last_run_id TEXT REFERENCES analysis_stage_runs(run_id)
        );
        CREATE TABLE analysis_stage_artifacts(
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES analysis_stage_runs(run_id) ON DELETE CASCADE,
            volume_id TEXT
        );
        CREATE TABLE analysis_discovered_pages(
            page_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_discovered_segments(
            segment_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_sections(
            section_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_entries(
            entry_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_occurrences(
            occurrence_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE analysis_scripture_refs(
            ref_key TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES analysis_volumes(volume_id) ON DELETE CASCADE
        );
    """
    with _connect(path, schema) as connection:
        for volume_id in ("PG024", "PL020"):
            run_id = f"{volume_id}:run"
            connection.execute("INSERT INTO analysis_volumes VALUES (?)", (volume_id,))
            connection.execute(
                "INSERT INTO analysis_stage_runs VALUES (?, ?)", (run_id, volume_id)
            )
            connection.execute(
                "INSERT INTO analysis_volume_stages VALUES (?, ?)",
                (volume_id, run_id),
            )
            connection.execute(
                "INSERT INTO analysis_stage_artifacts VALUES (?, ?, ?)",
                (f"{volume_id}:artifact", run_id, volume_id),
            )
            for table, key in (
                ("analysis_discovered_pages", "page"),
                ("analysis_discovered_segments", "segment"),
                ("analysis_sections", "section"),
                ("analysis_entries", "entry"),
                ("analysis_occurrences", "occurrence"),
                ("analysis_scripture_refs", "ref"),
            ):
                connection.execute(
                    f"INSERT INTO {table} VALUES (?, ?)",
                    (f"{volume_id}:{key}", volume_id),
                )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    _, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert set(remaining.values()) == {0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT volume_id FROM analysis_volumes"
        ).fetchall() == [("PL020",)]
        assert connection.execute(
            "SELECT volume_id FROM analysis_stage_runs"
        ).fetchall() == [("PL020",)]


def test_scripture_cleanup_removes_direct_and_cascaded_rows(tmp_path: Path) -> None:
    path = tmp_path / "scripture_citations.db"
    schema = """
        CREATE TABLE citation_volumes(volume_id TEXT PRIMARY KEY);
        CREATE TABLE citation_files(
            file_id TEXT PRIMARY KEY,
            volume_id TEXT REFERENCES citation_volumes(volume_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_file_pages(
            page_id TEXT PRIMARY KEY,
            file_id TEXT REFERENCES citation_files(file_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_groups(
            group_id TEXT PRIMARY KEY,
            file_id TEXT REFERENCES citation_files(file_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_occurrences(
            occurrence_key TEXT PRIMARY KEY,
            group_id TEXT REFERENCES citation_groups(group_id) ON DELETE CASCADE
        );
        CREATE TABLE citation_book_aliases(alias_id TEXT PRIMARY KEY, volume_id TEXT);
        CREATE TABLE citation_format_profiles(profile_id TEXT PRIMARY KEY, volume_id TEXT);
        CREATE TABLE citation_index_seeds(seed_key TEXT PRIMARY KEY, volume_id TEXT);
        CREATE TABLE citation_seed_links(
            seed_key TEXT REFERENCES citation_index_seeds(seed_key) ON DELETE CASCADE,
            occurrence_key TEXT REFERENCES citation_occurrences(occurrence_key) ON DELETE CASCADE
        );
    """
    with _connect(path, schema) as connection:
        for volume_id in ("PG084", "PL020"):
            file_id = f"{volume_id}:file"
            group_id = f"{volume_id}:group"
            occurrence = f"{volume_id}:occurrence"
            seed = f"{volume_id}:seed"
            connection.execute("INSERT INTO citation_volumes VALUES (?)", (volume_id,))
            connection.execute("INSERT INTO citation_files VALUES (?, ?)", (file_id, volume_id))
            connection.execute(
                "INSERT INTO citation_file_pages VALUES (?, ?)",
                (f"{volume_id}:page", file_id),
            )
            connection.execute("INSERT INTO citation_groups VALUES (?, ?)", (group_id, file_id))
            connection.execute(
                "INSERT INTO citation_occurrences VALUES (?, ?)",
                (occurrence, group_id),
            )
            connection.execute(
                "INSERT INTO citation_book_aliases VALUES (?, ?)",
                (f"{volume_id}:alias", volume_id),
            )
            connection.execute(
                "INSERT INTO citation_format_profiles VALUES (?, ?)",
                (f"{volume_id}:profile", volume_id),
            )
            connection.execute("INSERT INTO citation_index_seeds VALUES (?, ?)", (seed, volume_id))
            connection.execute(
                "INSERT INTO citation_seed_links VALUES (?, ?)",
                (seed, occurrence),
            )

    queries = db_cleanup._database_queries(path.name, TARGETS)
    _, remaining, violations = db_cleanup._apply_one(path, queries, TARGETS)

    assert set(remaining.values()) == {0}
    assert violations == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT volume_id FROM citation_volumes"
        ).fetchall() == [("PL020",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM citation_seed_links"
        ).fetchone()[0] == 1


def test_count_guard_accepts_old_or_zero_and_rejects_partial() -> None:
    manifest = {
        "expected_database_rows": {"ocr_versions.db": {"ocr_results": 10}}
    }
    assert db_cleanup._validate_expected_counts(
        "ocr_versions.db",
        {"ocr_results": 10},
        manifest,
        allow_count_drift=False,
    ) == []
    assert db_cleanup._validate_expected_counts(
        "ocr_versions.db",
        {"ocr_results": 0},
        manifest,
        allow_count_drift=False,
    ) == []
    with pytest.raises(RuntimeError, match="Count guard rejected"):
        db_cleanup._validate_expected_counts(
            "ocr_versions.db",
            {"ocr_results": 5},
            manifest,
            allow_count_drift=False,
        )


def test_file_candidates_exclude_neighbors_and_pagefind_by_default(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "teste" / "PG024" / "images").mkdir(parents=True)
    (root / "teste" / "PG024" / "text").mkdir(parents=True)
    (root / "teste" / "PG024" / "images" / "PG024-001.png").write_bytes(b"png")
    (root / "teste" / "PG024" / "text" / "page-001.txt").write_text("old")
    (root / "teste" / "PG023").mkdir(parents=True)
    (root / "data" / "index_payloads").mkdir(parents=True)
    (root / "data" / "index_payloads" / "PG024_indices.json").write_text("{}")
    (root / "data" / "index_payloads" / "PG023_indices.json").write_text("{}")
    (root / "web" / "public" / "pagefind").mkdir(parents=True)

    candidates = file_cleanup._collect_candidates(
        root, TARGETS, file_cleanup.DEFAULT_PHASES
    )
    relative = {str(path.relative_to(root)) for path in candidates}

    assert "teste/PG024" in relative
    assert "data/index_payloads/PG024_indices.json" in relative
    assert all("PG023" not in item for item in relative)
    assert "web/public/pagefind" not in relative


def test_quarantine_move_preserves_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / "teste" / "PG024"
    source.mkdir(parents=True)
    (source / "old.txt").write_text("old")
    quarantine = tmp_path / "quarantine"

    moves = file_cleanup._move_to_quarantine(root, quarantine, [source])

    assert not source.exists()
    assert (quarantine / "teste" / "PG024" / "old.txt").read_text() == "old"
    assert moves == [
        {
            "source": str(source),
            "destination": str(quarantine / "teste" / "PG024"),
        }
    ]


def test_ocr_guard_rejects_reprocessed_page_count() -> None:
    manifest = {"old_ocr_pages": {volume_id: 1 for volume_id in TARGETS}}
    counts = {volume_id: None for volume_id in TARGETS}
    counts["PG024"] = {"images": 2, "texts": 2}
    with pytest.raises(RuntimeError, match="OCR page-count guard rejected"):
        file_cleanup._validate_ocr_guard(
            counts, manifest, allow_count_drift=False
        )

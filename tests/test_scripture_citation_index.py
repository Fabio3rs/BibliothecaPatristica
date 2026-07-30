from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.scripture_citation_index import (
    ScanTask,
    build_citation_database,
    citation_logical_text,
    enrich_locator_items_from_citation_db,
    scan_file_task,
    search_citations,
)


def _write_sample_volume(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "teste" / "PO999" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PO999-001.txt"
    body.write_text(
        """<pagina tipo="conteudo">
<cabecalho>[109]</cabecalho>
<bloco tipo="aparato_critico" script="latino" bbox="0,0,900,1000">
Gen. 1,4,5,7; I Rois, II, 8; I Cor. II, 4;
I Corin-
thios 3, 18-
20. Ex Psalmo CXII. v. 7. caritate
</bloco>
</pagina>""",
        encoding="utf-8",
    )
    index_file = source_root / "PO999-002.txt"
    index_file.write_text(
        """<pagina tipo="indice">
<bloco tipo="texto_principal">I Rois, II, 8 ..... 109</bloco>
</pagina>""",
        encoding="utf-8",
    )
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    payload = {
        "sections": [
            {
                "section_key": "PO999:index",
                "file_start": str(index_file),
                "file_end": str(index_file),
            }
        ],
        "entries": [
            {"entry_key": "PO999:index:e1", "section_key": "PO999:index"}
        ],
        "refs": [{"entry_key": "PO999:index:e1", "page_ref_int": 109}],
        "scripture_refs": [
            {
                "entry_key": "PO999:index:e1",
                "ref_order": 1,
                "ref_raw": "I Rois, II, 8",
                "book_raw": "I Rois",
                "book_norm": "1 Samuel",
                "chapter_start": 2,
                "verse_start": 8,
            }
        ],
    }
    (payload_dir / "PO999_alphabetical_indices.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return source_root, payload_dir


def _semantic_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            """SELECT f.source_relpath, g.block_index, g.source_start,
                      g.detector_rule, o.item_order, o.book_key,
                      o.chapter_start, o.verse_start, o.chapter_end,
                      o.verse_end, o.normalization_status
               FROM citation_occurrences o
               JOIN citation_groups g ON g.group_id = o.group_id
               JOIN citation_files f ON f.file_id = g.file_id
               ORDER BY f.source_relpath, g.block_index, g.source_start,
                        g.detector_rule, o.item_order"""
        ).fetchall()


def test_logical_text_joins_words_but_preserves_numeric_ranges() -> None:
    source = "I Corin-\nthios 3, 18-\n20"

    logical = citation_logical_text(source)

    assert logical.text == "i corinthios 3, 18-20"
    assert source[slice(*logical.source_span(0, len(logical.text)))] == source


def test_scanner_handles_lists_historical_names_and_roman_boundaries(
    tmp_path: Path,
) -> None:
    source_root, _ = _write_sample_volume(tmp_path)
    path = source_root / "PO999-001.txt"

    result = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    occurrences = [
        occurrence
        for group in result["groups"]
        for occurrence in group["occurrences"]
    ]
    normalized = {item["ref_norm"] for item in occurrences}

    assert {"Gênesis 1,4", "Gênesis 1,5", "Gênesis 1,7"} <= normalized
    assert "1 Samuel 2,8" in normalized
    assert "1 Coríntios 2,4" in normalized
    assert "1 Coríntios 3,18-20" in normalized
    assert "Salmos 112,7" in normalized
    assert not any(
        item["book_key"] == "1 corintios"
        and item["chapter_start"] == 1
        and item["verse_start"] is None
        for item in occurrences
    )
    assert result["pages"][0]["editorial_page"] == 109

    dotless_path = source_root / "PO999-003.txt"
    dotless_path.write_text("I Cor. ıı, 4", encoding="utf-8")
    dotless = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(dotless_path),
            physical_index=2,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    assert dotless["groups"][0]["occurrences"][0]["ref_norm"] == "1 Coríntios 2,4"

    stopwords_path = source_root / "PO999-004.txt"
    stopwords_path.write_text(
        "ex 10, 2; si 4, 5; ne 2, 1; est 3, 4",
        encoding="utf-8",
    )
    stopwords = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(stopwords_path),
            physical_index=3,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    assert stopwords["groups"] == []


def test_database_is_incremental_searchable_and_excludes_index_sources(
    tmp_path: Path,
) -> None:
    source_root, payload_dir = _write_sample_volume(tmp_path)
    db_path = tmp_path / "citations.db"
    volumes = [("PO999", "PO", source_root)]

    first = build_citation_database(
        db_path=db_path,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )
    second = build_citation_database(
        db_path=db_path,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )

    assert first["scanned_file_count"] == 2
    assert first["seed_link_count"] >= 1
    assert second["scanned_file_count"] == 0
    assert second["skipped_file_count"] == 2
    editorial = search_citations(
        db_path=db_path,
        volume_id="PO999",
        editorial_page=109,
        book="1 Samuel",
    )
    assert len(editorial) == 1
    assert editorial[0]["ref_norm"] == "1 Samuel 2,8"
    assert editorial[0]["linked_to_index"] == 1
    assert editorial[0]["source_relpath"] == "PO999-001.txt"
    physical = search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=1,
        text_query="caritate",
    )
    assert physical
    assert {item["source_relpath"] for item in physical} == {"PO999-001.txt"}
    with_index = search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=2,
        include_index=True,
    )
    assert with_index
    assert search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=2,
    ) == []
    enriched, artifact = enrich_locator_items_from_citation_db(
        [
            {
                "locator_key": "PO999:index:e1::ref:000001",
                "section_file_start": str(source_root / "PO999-002.txt"),
                "section_file_end": str(source_root / "PO999-002.txt"),
                "cited_pages": [109],
                "candidates": [],
                "scripture_ref": {
                    "book_key": "1 samuel",
                    "chapter_start": 2,
                    "verse_start": 8,
                },
            }
        ],
        db_path=db_path,
        source_root=source_root,
    )
    assert artifact["coverage_status"] == "complete"
    assert enriched[0]["candidates"][0]["file"].endswith("PO999-001.txt")
    assert enriched[0]["candidates"][0]["candidate_role"] == (
        "persistent_scripture_evidence"
    )
    body_file = source_root / "PO999-001.txt"
    body_file.write_text(
        body_file.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    _, stale_artifact = enrich_locator_items_from_citation_db(
        [],
        db_path=db_path,
        source_root=source_root,
    )
    assert stale_artifact["coverage_status"] == "stale"
    assert stale_artifact["stale_file_count"] == 1


def test_one_and_two_workers_produce_identical_semantic_rows(
    tmp_path: Path,
) -> None:
    source_root, payload_dir = _write_sample_volume(tmp_path)
    volumes = [("PO999", "PO", source_root)]
    serial_db = tmp_path / "serial.db"
    parallel_db = tmp_path / "parallel.db"

    build_citation_database(
        db_path=serial_db,
        volumes=volumes,
        workers=1,
        payload_dir=payload_dir,
    )
    build_citation_database(
        db_path=parallel_db,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )

    assert _semantic_rows(serial_db) == _semantic_rows(parallel_db)

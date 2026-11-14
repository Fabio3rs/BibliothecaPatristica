"""Unit tests for the scripture citation index connector."""
import json
from pathlib import Path
import sqlite3
import tempfile
import zlib

import pytest

from tools.indexing.apply_scripture_citation_links import (
    CorpusScriptureOccurrenceLocator,
    PL142PsalmCommentaryLocator,
    PO015FascicleScriptureLocator,
    PO019LogiaScriptureLocator,
    norm_book_text,
    resolve_scripture_citations,
)


def create_mock_scripture_db(db_path: Path):
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE citation_files (
            file_id INTEGER PRIMARY KEY,
            volume_id TEXT,
            file_path TEXT,
            is_index_source INTEGER
        );
        CREATE TABLE citation_groups (
            group_id INTEGER PRIMARY KEY,
            file_id INTEGER
        );
        CREATE TABLE citation_occurrences (
            occurrence_id INTEGER PRIMARY KEY,
            group_id INTEGER,
            book_key TEXT,
            chapter_start INTEGER,
            verse_start INTEGER
        );
        -- PL142 files: Psalm 1 in file 029, Psalm 14 in file 047
        INSERT INTO citation_files VALUES (1, 'PL142', '/path/PL142/text/vol-029.txt', 0);
        INSERT INTO citation_files VALUES (2, 'PL142', '/path/PL142/text/vol-047.txt', 0);
        INSERT INTO citation_groups VALUES (10, 1);
        INSERT INTO citation_groups VALUES (20, 2);
        INSERT INTO citation_occurrences VALUES (100, 10, 'salmos', 1, 1);
        INSERT INTO citation_occurrences VALUES (200, 20, 'salmos', 14, 2);

        -- PO012 occurrences: Genesis 9 in file 800
        INSERT INTO citation_files VALUES (3, 'PO012', '/path/PO012/text/vol-800.txt', 0);
        INSERT INTO citation_groups VALUES (30, 3);
        INSERT INTO citation_occurrences VALUES (300, 30, 'genesis', 9, 1);
        """
    )
    con.commit()
    con.close()


def test_norm_book_text():
    assert norm_book_text("Psaumes (Vulgate)") == "Psaumes"
    assert norm_book_text("Genèse") == "Genese"
    assert norm_book_text("Ézéchiel (LXX)") == "Ezechiel"


def test_pl142_locator():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "scripture_citations.db"
        create_mock_scripture_db(db_path)

        loc = PL142PsalmCommentaryLocator(db_path)
        # Hit for psalm 14
        hit = loc.locate("Apostoli montes dicuntur, psal. xiv, vers", "7")
        assert hit is not None
        assert "vol-047.txt" in hit[0]
        assert hit[1] == 9.8
        assert hit[2] == "scripture_psalm_commentary"

        # Hit for ibid
        hit_ibid = loc.locate("principes", "ibid")
        assert hit_ibid is not None
        assert "vol-047.txt" in hit_ibid[0]


def test_po015_fascicle_locator():
    with tempfile.TemporaryDirectory() as tmpdir:
        vol_dir = Path(tmpdir) / "PO015" / "text"
        vol_dir.mkdir(parents=True, exist_ok=True)
        # Page 32 -> File 178 + 32 = 210
        (vol_dir / "vol-210.txt").write_text("Text of page 32")

        loc = PO015FascicleScriptureLocator(vol_dir)
        hit = loc.locate("GEN., I, 1", "32, 4")
        assert hit is not None
        assert "vol-210.txt" in hit[0]
        assert hit[3]["fascicle_page"] == 32


def test_po019_logia_locator():
    with tempfile.TemporaryDirectory() as tmpdir:
        root_dir = Path(tmpdir)
        po13_dir = root_dir / "PO013" / "text"
        po19_dir = root_dir / "PO019" / "text"
        po13_dir.mkdir(parents=True, exist_ok=True)
        po19_dir.mkdir(parents=True, exist_ok=True)

        # Logion 1 in PO013
        (po13_dir / "vol-100.txt").write_text("1° — TS., 10. Dixit Jesus...")
        # Logion 140 in PO019
        (po19_dir / "vol-574.txt").write_text("140. — TS., 135. وفى الانجيل مكتوب...")

        loc = PO019LogiaScriptureLocator(root_dir)
        hit1 = loc.locate("Matth. V 19", "1")
        assert hit1 is not None
        assert "vol-100.txt" in hit1[0]
        assert hit1[3]["target_volume"] == "PO013"
        assert hit1[3]["logion_number"] == 1

        hit140 = loc.locate("Prov. VI 14", "140")
        assert hit140 is not None
        assert "vol-574.txt" in hit140[0]
        assert hit140[3]["target_volume"] == "PO019"
        assert hit140[3]["logion_number"] == 140


def test_corpus_scripture_occurrence_locator():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "scripture_citations.db"
        create_mock_scripture_db(db_path)

        loc = CorpusScriptureOccurrenceLocator(db_path)
        hit = loc.locate("PO012", "Genèse IX 1-6", "22")
        assert hit is not None
        assert "vol-800.txt" in hit[0]
        assert hit[3]["book_key"] == "genesis"
        assert hit[3]["chapter_start"] == 9
        loc.close()


def test_resolve_scripture_citations_workflow():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cat_db = tmp_path / "search.sqlite"
        scrip_db = tmp_path / "scripture_citations.db"
        create_mock_scripture_db(scrip_db)

        # Setup catalog tables
        con = sqlite3.connect(str(cat_db))
        con.executescript(
            """
            CREATE TABLE items (id TEXT PRIMARY KEY, volume TEXT, label TEXT, ref_raw TEXT, source TEXT, state TEXT);
            CREATE TABLE candidate_storage (item_id TEXT, file TEXT, rank INTEGER, score REAL, navigable INTEGER, candidate_json BLOB, PRIMARY KEY (item_id, file));
            CREATE TABLE residual_embedding_queue (item_id TEXT PRIMARY KEY, status TEXT, candidate_files_json TEXT);

            INSERT INTO items VALUES ('item_pl142', 'PL142', 'psal. xiv, vers', '2', 'src.txt', 'source_only');
            INSERT INTO residual_embedding_queue VALUES ('item_pl142', 'pending', '[]');

            INSERT INTO items VALUES ('item_po012', 'PO012', 'Genèse IX 1', '10', 'src.txt', 'source_only');
            INSERT INTO residual_embedding_queue VALUES ('item_po012', 'pending', '[]');
            """
        )
        con.commit()
        con.close()

        # Summary json
        summary_path = tmp_path / "catalog_summary.json"
        with open(summary_path, "w") as f:
            json.dump({"total_items": 2, "items_with_destinations": 0}, f)

        # Dry run
        res_dry = resolve_scripture_citations(
            catalog_db_path=cat_db,
            scripture_db_path=scrip_db,
            target_volumes=["PL142", "PO012"],
            apply=False,
        )
        assert res_dry["status"] == "success"
        assert res_dry["dry_run"] is True
        assert res_dry["total_promoted"] == 2

        # Apply
        res_apply = resolve_scripture_citations(
            catalog_db_path=cat_db,
            scripture_db_path=scrip_db,
            target_volumes=["PL142", "PO012"],
            apply=True,
        )
        assert res_apply["status"] == "success"
        assert res_apply["dry_run"] is False
        assert res_apply["total_promoted"] == 2

        # Check catalog
        con = sqlite3.connect(str(cat_db))
        cur = con.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'item_pl142'")
        assert cur.fetchone()[0] == "with_destinations"
        cur.execute("SELECT status FROM residual_embedding_queue WHERE item_id = 'item_pl142'")
        assert cur.fetchone()[0] == "confirmed"

        cur.execute("SELECT file, score, candidate_json FROM candidate_storage WHERE item_id = 'item_pl142'")
        row = cur.fetchone()
        assert "vol-047.txt" in row[0]
        data = json.loads(zlib.decompress(row[2]).decode("utf-8"))
        assert data["method"] == "scripture_psalm_commentary"
        assert data["psalm"] == 14
        con.close()

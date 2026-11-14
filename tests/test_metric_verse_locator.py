"""Unit tests for sequence-consensus poetic verse and section locator."""
import json
from pathlib import Path
import sqlite3
import tempfile
import zlib

import pytest

from tools.indexing.metric_verse_and_section_locator import (
    PoeticVolumeVerseEstimator,
    resolve_poetic_and_section_volumes,
)


def create_mock_poetic_volume_dir(vol_dir: Path):
    vol_dir.mkdir(parents=True, exist_ok=True)
    # File 014: lines 10 - 50
    f14 = vol_dir / "vol-014.txt"
    f14.write_text("10 Ἄλφα\n20 Βῆτα\n50 Γάμμα\n", encoding="utf-8")

    # File 015: gap (no numbers recognized by OCR)
    f15 = vol_dir / "vol-015.txt"
    f15.write_text("Text without numbers\n", encoding="utf-8")

    # File 016: lines 100 - 150
    f16 = vol_dir / "vol-016.txt"
    f16.write_text("100 Δέλτα\n125 Ἔψιλον\n150 Ζῆτα\n", encoding="utf-8")


def test_poetic_estimator_exact_and_interpolated():
    with tempfile.TemporaryDirectory() as tmpdir:
        vol_dir = Path(tmpdir) / "PG143" / "text"
        create_mock_poetic_volume_dir(vol_dir)

        estimator = PoeticVolumeVerseEstimator(
            volume_id="PG143",
            volume_dir=vol_dir,
            min_file_seq=14,
            max_file_seq=16,
            max_verse_bound=500,
        )

        # 1. Exact hit on file 14 (verse 25)
        hits14 = estimator.locate_verse(25)
        assert len(hits14) > 0
        assert hits14[0][0].name == "vol-014.txt"

        # 2. Interpolated hit on file 15 (verse 75)
        hits15 = estimator.locate_verse(75)
        assert len(hits15) > 0
        assert hits15[0][0].name == "vol-015.txt"

        # 3. Exact hit on file 16 (verse 120)
        hits16 = estimator.locate_verse(120)
        assert len(hits16) > 0
        assert hits16[0][0].name == "vol-016.txt"


def test_resolve_poetic_volume_workflow():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        vol_dir = tmp_path / "teste" / "PG143" / "text"
        create_mock_poetic_volume_dir(vol_dir)

        db_path = tmp_path / "search.sqlite"
        summary_path = tmp_path / "catalog_summary.json"
        with open(summary_path, "w") as f:
            json.dump({"total_items": 3, "items_with_destinations": 1}, f)

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("CREATE TABLE items (id TEXT PRIMARY KEY, volume TEXT, label TEXT, ref_raw TEXT, state TEXT)")
        cur.execute("CREATE TABLE candidate_storage (item_id TEXT, file TEXT, rank INTEGER, score REAL, navigable INTEGER, candidate_json BLOB, PRIMARY KEY (item_id, file))")
        cur.execute("CREATE TABLE residual_embedding_queue (item_id TEXT PRIMARY KEY, status TEXT, candidate_files_json TEXT)")

        # Target item in PG143 citing verse 75
        cur.execute("INSERT INTO items VALUES ('item_v75', 'PG143', 'Studites', '75', 'source_only')")
        cur.execute("INSERT INTO residual_embedding_queue VALUES ('item_v75', 'pending', '[]')")
        conn.commit()
        conn.close()

        # Dry run
        res_dry = resolve_poetic_and_section_volumes(
            catalog_db_path=db_path,
            source_root=tmp_path / "teste",
            target_volumes=["PG143"],
            apply=False,
        )
        assert res_dry["status"] == "success"
        assert res_dry["dry_run"] is True
        assert res_dry["total_promoted"] == 1

        # Apply
        res_apply = resolve_poetic_and_section_volumes(
            catalog_db_path=db_path,
            source_root=tmp_path / "teste",
            target_volumes=["PG143"],
            apply=True,
        )
        assert res_apply["status"] == "success"
        assert res_apply["dry_run"] is False
        assert res_apply["total_promoted"] == 1

        # Verify DB updated
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT state FROM items WHERE id = 'item_v75'")
        assert cur.fetchone()[0] == "with_destinations"

        cur.execute("SELECT file, score FROM candidate_storage WHERE item_id = 'item_v75'")
        row = cur.fetchone()
        assert "vol-015.txt" in row[0]
        assert row[1] > 8.0
        conn.close()


def test_nonnus_multi_mode_estimator():
    with tempfile.TemporaryDirectory() as tmpdir:
        vol_dir = Path(tmpdir) / "PG043" / "text"
        vol_dir.mkdir(parents=True, exist_ok=True)

        # Prolegomena / Praefatio file 167 with [xi]
        f167 = vol_dir / "vol-167.txt"
        f167.write_text("Præfatio [xi] Andreæ Cæsareensis\n", encoding="utf-8")

        # Nonnus file 454
        f454 = vol_dir / "vol-454.txt"
        f454.write_text("ἀγρύπνοισιν ἀνίαις Cap. XVI\n", encoding="utf-8")

        # Column 858 file
        f442 = vol_dir / "vol-442.txt"
        f442.write_text("PARAPHRASIS 858\n", encoding="utf-8")

        pmap = {858: str(f442)}

        from tools.indexing.metric_verse_and_section_locator import NonnusPanopolitanusEstimator
        est = NonnusPanopolitanusEstimator(vol_dir, pmap)

        # 1. Prolegomena Roman page 'xi'
        hit_roman = est.locate("Andreæ Cæsareensis", "xi")
        assert hit_roman is not None
        assert "vol-167.txt" in hit_roman[0].name
        assert hit_roman[2] == "praefatio_roman_page"

        # 2. Nonnus edition page 174 with lexical hit
        hit_nonnus = est.locate("ἀγρύπνοισιν ἀνίαις", "174")
        assert hit_nonnus is not None
        assert "vol-454.txt" in hit_nonnus[0].name
        assert hit_nonnus[2] == "nonnus_edition_page_lexical"

        # 3. Direct Migne column
        hit_col = est.locate("Paraphrasis", "858")
        assert hit_col is not None
        assert "vol-442.txt" in hit_col[0].name
        assert hit_col[2] == "migne_column_direct"


def test_eugyppius_thesaurus_estimator():
    with tempfile.TemporaryDirectory() as tmpdir:
        vol_dir = Path(tmpdir) / "PL062" / "text"
        vol_dir.mkdir(parents=True, exist_ok=True)

        f304 = vol_dir / "vol-304.txt"
        f304.write_text("THESAURUS. CAP. XVIII. Zizania\n", encoding="utf-8")

        f306 = vol_dir / "vol-306.txt"
        f306.write_text("THESAURUS. CAP. XIX. Visus a sacramento\n", encoding="utf-8")

        from tools.indexing.metric_verse_and_section_locator import EugyppiusThesaurusEstimator
        est = EugyppiusThesaurusEstimator(vol_dir)

        # Cap. 18 hit
        hit18 = est.locate("Zizania exteriora", "18")
        assert hit18 is not None
        assert "vol-304.txt" in hit18[0].name
        assert hit18[2] == "eugyppius_thesaurus_chapter"

        # Cap. 19 hit
        hit19 = est.locate("Visus a sacramento", "19")
        assert hit19 is not None
        assert "vol-306.txt" in hit19[0].name
        assert hit19[2] == "eugyppius_thesaurus_chapter"


def test_po006_fascicle_estimator():
    with tempfile.TemporaryDirectory() as tmpdir:
        vol_dir = Path(tmpdir) / "PO006" / "text"
        vol_dir.mkdir(parents=True, exist_ok=True)

        f688 = vol_dir / "vol-688.txt"
        f688.write_text("VIE D'ALEXANDRE L'ACÉMÈTE. [38]\nΑἰγύπτιοι\n", encoding="utf-8")

        from tools.indexing.metric_verse_and_section_locator import PO006FascicleEstimator
        est = PO006FascicleEstimator(vol_dir)

        # Subscript ref '38₇'
        hit = est.locate("Αἰγύπτιοι", "38₇")
        assert hit is not None
        assert "vol-688.txt" in hit[0].name
        assert hit[2] == "po_fascicle_page"


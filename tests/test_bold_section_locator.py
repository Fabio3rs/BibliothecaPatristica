"""Tests for bold_section_intervolume_locator."""

from pathlib import Path
import tempfile
import pytest

from tools.indexing.bold_section_intervolume_locator import (
    PG086AnalyticalLocator,
    VitaePatrumLocator,
)


def test_vitae_patrum_locator_mock():
    with tempfile.TemporaryDirectory() as tmpdir:
        root_dir = Path(tmpdir)
        pl73_dir = root_dir / "PL073" / "text"
        pl74_dir = root_dir / "PL074" / "text"
        pl73_dir.mkdir(parents=True, exist_ok=True)
        pl74_dir.mkdir(parents=True, exist_ok=True)

        # File in PL073 with bold section 259
        (pl73_dir / "vol73-240.txt").write_text("259 Quod autem ex me...")
        # File in PL074 with bold section 1002
        (pl74_dir / "vol74-050.txt").write_text("1002 Deinde cum orasset...")

        loc = VitaePatrumLocator(root_dir)

        # Section 259 -> PL073
        hit259 = loc.locate("Inflavit spiraculum", "259")
        assert hit259 is not None
        assert "vol73-240.txt" in hit259[0]
        assert hit259[3]["target_volume"] == "PL073"
        assert hit259[3]["section_number"] == 259

        # Section 1002 -> PL074
        hit1002 = loc.locate("Agathon", "1002")
        assert hit1002 is not None
        assert "vol74-050.txt" in hit1002[0]
        assert hit1002[3]["target_volume"] == "PL074"
        assert hit1002[3]["section_number"] == 1002


def test_pg086_analytical_locator_mock():
    with tempfile.TemporaryDirectory() as tmpdir:
        root_dir = Path(tmpdir)
        pg86_1_dir = root_dir / "PG086.01" / "text"
        pg86_2_dir = root_dir / "PG086.02" / "text"
        pg86_1_dir.mkdir(parents=True, exist_ok=True)
        pg86_2_dir.mkdir(parents=True, exist_ok=True)

        # Timothy section 395 in PG086.01
        (pg86_1_dir / "d5d0-025.txt").write_text("395 Aetius haereticus...")
        # Column 1215 in PG086.01
        (pg86_1_dir / "vol861-100.txt").write_text(
            "<bloco tipo=\"cabecalho\">1215 SANCTI CYRILLI 1216</bloco> Text"
        )
        # Evagrius section 400 in PG086.02
        (pg86_2_dir / "vol862-479.txt").write_text("400 Vitiges Gothorum dux...")

        loc = PG086AnalyticalLocator(root_dir)

        # Index I (file 816) -> Timothy in PG086.01
        hit395 = loc.locate("fca-816.txt", "Aetius", "395")
        assert hit395 is not None
        assert "d5d0-025.txt" in hit395[0]
        assert hit395[3]["target_volume"] == "PG086.01"
        assert hit395[3]["section_number"] == 395

        # Index III (file 818) -> Migne col in PG086.01
        hit1215 = loc.locate("fca-818.txt", "Alexander CPolitanus", "1215")
        assert hit1215 is not None
        assert "vol861-100.txt" in hit1215[0]
        assert hit1215[3]["target_volume"] == "PG086.01"
        assert hit1215[3]["column"] == 1215

        # Index IV (file 823) -> Evagrius in PG086.02
        hit400 = loc.locate("fca-823.txt", "Vitiges", "400")
        assert hit400 is not None
        assert "vol862-479.txt" in hit400[0]
        assert hit400[3]["target_volume"] == "PG086.02"
        assert hit400[3]["section_number"] == 400

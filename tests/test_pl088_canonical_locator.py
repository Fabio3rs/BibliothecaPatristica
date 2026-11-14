"""Tests for pl088_canonical_collections_locator."""

from pathlib import Path
import tempfile
import pytest

from tools.indexing.pl088_canonical_collections_locator import (
    PL088CanonicalLocator,
)


def test_pl088_canonical_locator_mock():
    with tempfile.TemporaryDirectory() as tmpdir:
        root_dir = Path(tmpdir)
        pl88_dir = root_dir / "PL088" / "text"
        pl88_dir.mkdir(parents=True, exist_ok=True)

        # Ferrandus canon 199 in file 418
        (pl88_dir / "vol88-418.txt").write_text("199. Baptizandi symbolum fidei discant...")
        # Cresconius canon 78 in file 440
        (pl88_dir / "vol88-440.txt").write_text("78. Abrenuntiantes qui ad saeculum revertuntur...")

        loc = PL088CanonicalLocator(root_dir)

        # Ferrandus entry: '199 —'
        hit_ferr = loc.locate("Baptizandi symbolum", "199 —")
        assert hit_ferr is not None
        assert "vol88-418.txt" in hit_ferr[0]
        assert hit_ferr[3]["work"] == "ferrandus_breviatio"
        assert hit_ferr[3]["canon_number"] == 199

        # Cresconius entry: '— 78'
        hit_cresc = loc.locate("Abrenuntiantes", "— 78")
        assert hit_cresc is not None
        assert "vol88-440.txt" in hit_cresc[0]
        assert hit_cresc[3]["work"] == "cresconius_breviarium"
        assert hit_cresc[3]["canon_number"] == 78

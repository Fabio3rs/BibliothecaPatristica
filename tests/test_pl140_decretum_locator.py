"""Tests for pl140_burchard_decretum_locator."""

from pathlib import Path
import tempfile
import pytest

from tools.indexing.pl140_burchard_decretum_locator import (
    PL140BurchardLocator,
)


def test_pl140_burchard_locator_mock():
    with tempfile.TemporaryDirectory() as tmpdir:
        root_dir = Path(tmpdir)
        pl140_dir = root_dir / "PL140" / "text"
        pl140_dir.mkdir(parents=True, exist_ok=True)

        # Liber 3 capitula file 337
        (pl140_dir / "vol140-337.txt").write_text("CAPITULA LIBRI TERTII: I. Quid sit Ecclesia...")
        # Liber 3 body file 341
        (pl140_dir / "vol140-341.txt").write_text("CAP. I. -- Quid sit Ecclesia. CAP. II. -- De primitiva...")

        loc = PL140BurchardLocator(root_dir)

        # Chapter I in Liber 3
        hit1 = loc.locate(str(pl140_dir / "vol140-337.txt"), "Quid sit Ecclesia", "I")
        assert hit1 is not None
        assert "vol140-341.txt" in hit1[0]
        assert hit1[3]["liber"] == 3
        assert hit1[3]["caput"] == 1

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.index_translation.setup_cltk import model_present, reuse_stanza_cache


def test_reuse_stanza_cache_copies_model_and_resources(tmp_path: Path) -> None:
    source = tmp_path / "modern-cache"
    target = tmp_path / "legacy-cache"
    tokenizer = source / "grc" / "tokenize"
    tokenizer.mkdir(parents=True)
    (tokenizer / "perseus.pt").write_bytes(b"model")
    (source / "resources.json").write_text("{}", encoding="utf-8")

    assert reuse_stanza_cache(
        source_root=source,
        target_root=target,
        stanza_code="grc",
    )
    assert model_present(target, "grc")
    assert (target / "resources.json").read_text(encoding="utf-8") == "{}"


def test_reuse_stanza_cache_requires_a_tokenizer_model(tmp_path: Path) -> None:
    source = tmp_path / "modern-cache"
    target = tmp_path / "legacy-cache"
    source.mkdir()

    assert not reuse_stanza_cache(
        source_root=source,
        target_root=target,
        stanza_code="grc",
    )
    assert not target.exists()

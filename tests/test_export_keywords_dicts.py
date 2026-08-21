from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "export_keywords_dicts.py"
SPEC = importlib.util.spec_from_file_location("export_keywords_dicts", MODULE_PATH)
assert SPEC and SPEC.loader
export_keywords = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = export_keywords
SPEC.loader.exec_module(export_keywords)

KeywordRow = export_keywords.KeywordRow
build_catalog_items = export_keywords.build_catalog_items


def keyword(
    keyword_id: int,
    label: str,
    count: int,
    group_id: int | None,
    *,
    canonical: str = "",
    scripture: bool = False,
) -> KeywordRow:
    return KeywordRow(
        id=keyword_id,
        label=label,
        label_norm=label.casefold(),
        count=count,
        group_id=group_id,
        is_noise=0,
        status="clustered",
        nome_canonico=canonical.casefold(),
        nome_canonico_original=canonical,
        is_scripture_citation=scripture,
    )


def test_catalog_keeps_only_useful_canonical_and_scripture_ids() -> None:
    rows = [
        keyword(1, "Graça", 3, 7, canonical="Graça divina"),
        keyword(2, "Graça divina", 5, 7, canonical="Graça divina"),
        keyword(3, "Termo isolado", 1, None),
        keyword(4, "João 3:16", 2, -1, scripture=True),
    ]
    items, canonical_ids, scripture_ids = build_catalog_items(
        rows, group_count={7: 8}, min_count=1
    )

    assert len(items) == 2
    assert {item["label"] for item in items} == {"Graça divina", "João 3:16"}
    assert all(isinstance(item["id"], str) and item["id"].startswith("k:") for item in items)
    assert canonical_ids == {7: next(item["id"] for item in items if not item["iscit"])}
    assert scripture_ids == {4: next(item["id"] for item in items if item["iscit"])}

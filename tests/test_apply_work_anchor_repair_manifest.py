from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.apply_work_anchor_repair_manifest import apply_repairs_to_payload


def _payload(source_root: Path) -> dict:
    return {
        "volume": {"volume_id": "PG001", "source_root": str(source_root)},
        "works": [
            {
                "work_key": "PG001:work:one",
                "start_page": 99,
                "start_file": None,
                "title_translations": {"pt": "Um", "en": "One"},
                "raw_json": {"work_anchor_rerun": {"status": "unresolved"}},
            }
        ],
    }


def test_apply_resolved_repair_preserves_unrelated_fields(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    target = source_root / "page.txt"
    target.write_text("OPUS", encoding="utf-8")
    payload = _payload(source_root)

    changes = apply_repairs_to_payload(
        payload,
        [
            {
                "volume_id": "PG001",
                "work_key": "PG001:work:one",
                "status": "resolved",
                "start_page": 101,
                "start_file": str(target),
                "image_files_opened": ["image.png"],
                "confidence": "high",
                "evidence": "Title and incipit confirmed.",
            }
        ],
    )

    work = payload["works"][0]
    assert changes[0]["before"] == {"start_page": 99, "start_file": None}
    assert work["start_page"] == 101
    assert work["start_file"] == str(target.resolve())
    assert work["title_translations"] == {"pt": "Um", "en": "One"}
    assert "work_anchor_rerun" not in work["raw_json"]
    assert work["raw_json"]["manual_anchor_review"]["status"] == "resolved"


def test_apply_unlocated_repair_clears_false_anchor(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    payload = _payload(source_root)

    apply_repairs_to_payload(
        payload,
        [
            {
                "volume_id": "PG001",
                "work_key": "PG001:work:one",
                "status": "resolved_unlocated",
                "start_page": None,
                "start_file": None,
                "evidence": "External remission without local body.",
            }
        ],
    )

    work = payload["works"][0]
    assert work["start_page"] is None
    assert work["start_file"] is None
    assert work["raw_json"]["manual_anchor_review"]["status"] == (
        "resolved_unlocated"
    )


def test_apply_rejects_file_outside_volume(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    outside = tmp_path / "PG002" / "text" / "page.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("OPUS", encoding="utf-8")

    with pytest.raises(ValueError, match="outside source_root"):
        apply_repairs_to_payload(
            _payload(source_root),
            [
                {
                    "volume_id": "PG001",
                    "work_key": "PG001:work:one",
                    "status": "resolved",
                    "start_page": 101,
                    "start_file": str(outside),
                }
            ],
        )

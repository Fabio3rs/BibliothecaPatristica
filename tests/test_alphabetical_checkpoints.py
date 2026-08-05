from __future__ import annotations

import json
from pathlib import Path

from patristica_pipeline.alphabetical_checkpoints import (
    read_checkpointed_json,
    update_source_snapshot,
    write_checkpointed_json,
)


def test_source_snapshot_reuses_hashes_and_detects_ocr_change(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    first = source_root / "page-001.txt"
    second = source_root / "page-002.txt"
    first.write_text("alpha\n", encoding="utf-8")
    second.write_text("beta\n", encoding="utf-8")
    snapshot_file = tmp_path / "snapshot.json"

    initial = update_source_snapshot(snapshot_file, source_root)
    reused = update_source_snapshot(snapshot_file, source_root)

    assert initial["source_snapshot_fingerprint"] == reused[
        "source_snapshot_fingerprint"
    ]
    assert reused["reused_hash_count"] == 2
    assert reused["hashed_file_count"] == 0

    first.write_text("changed alpha\n", encoding="utf-8")
    changed = update_source_snapshot(snapshot_file, source_root)

    assert changed["source_snapshot_fingerprint"] != initial[
        "source_snapshot_fingerprint"
    ]
    assert changed["hashed_file_count"] == 1
    assert changed["reused_hash_count"] == 1


def test_checkpoint_reuse_verifies_output_content(tmp_path: Path) -> None:
    artifact = tmp_path / "mechanical.json"
    write_checkpointed_json(
        artifact,
        {"value": 1},
        stage="mechanical",
        input_fingerprint="input-a",
    )

    assert read_checkpointed_json(
        artifact,
        stage="mechanical",
        input_fingerprint="input-a",
    ) == {"value": 1}
    assert (
        read_checkpointed_json(
            artifact,
            stage="mechanical",
            input_fingerprint="input-b",
        )
        is None
    )

    artifact.write_text(json.dumps({"value": 2}), encoding="utf-8")
    assert (
        read_checkpointed_json(
            artifact,
            stage="mechanical",
            input_fingerprint="input-a",
        )
        is None
    )

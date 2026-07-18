from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.run_alphabetical_index_extraction import (
    inspect_output_checkpoint,
    resolve_path,
    resolve_previous_payload_path,
)


def test_resolve_previous_payload_path_keeps_missing_explicit_dir_candidate(tmp_path: Path) -> None:
    previous_dir = tmp_path / "previous"
    default_payload = tmp_path / "out" / "PG001_alphabetical_indices.json"
    args = SimpleNamespace(previous_result_json=None, previous_result_dir=previous_dir)

    resolved_path, source = resolve_previous_payload_path(
        args=args,
        volume_id="PG001",
        default_payload_file=default_payload,
    )

    assert resolved_path == previous_dir / "PG001_alphabetical_indices.json"
    assert source == "explicit_dir_missing"


def test_resolve_path_returns_absolute_path() -> None:
    resolved = resolve_path(Path("data"))
    assert resolved is not None
    assert resolved.is_absolute()


def test_inspect_output_checkpoint_accepts_valid_minimal_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "PG001_alphabetical_indices.json"
    payload = {
        "schema_version": 1,
        "generated_at": "2026-07-17T00:00:00Z",
        "volume": {
            "volume_id": "PG001",
            "collection": "PG",
            "source_root": "/tmp/pg001/text",
            "volume_label": "PG001",
        },
        "sections": [],
        "nodes": [],
        "entries": [],
        "refs": [],
        "scripture_refs": [],
        "coverage": {},
        "notes": [],
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    status, error = inspect_output_checkpoint(payload_path)

    assert status == "done"
    assert error is None

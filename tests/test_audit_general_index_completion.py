from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_general_index_completion import audit_payload


EMPTY_DB_STATS = {
    "entry_counts": {},
    "run_counts": {},
    "exact_duplicate_counts": {},
}


def write_payload(path: Path, *, with_entry_key: bool = True) -> None:
    entry = {
        "entry_raw": "OPUS. 15",
        "page_ref_raw": "15",
        "target_file": "/repo/teste/PG001/text/page-009.txt",
    }
    if with_entry_key:
        entry["entry_key"] = "PG001:index:1:entry:1"
    path.write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PG001"},
                "sections": [{"section_key": "PG001:index:1", "entries": [entry]}],
            }
        ),
        encoding="utf-8",
    )


def test_stale_evidence_is_refreshed_instead_of_triggering_rerun(tmp_path: Path) -> None:
    payload_path = tmp_path / "PG001_indices.json"
    audit_dir = tmp_path / "audits"
    audit_dir.mkdir()
    write_payload(payload_path, with_entry_key=False)
    (audit_dir / "PG001_indices_evidence.json").write_text(
        json.dumps(
            {
                "pipeline_kind": "general",
                "payload_entry_count": 2,
                "sampled_entry_count": 2,
                "verified_ratio": 0,
                "unjustified_empty_list_section_count": 1,
                "segmentation_suspect_count": 1,
            }
        ),
        encoding="utf-8",
    )

    report = audit_payload(payload_path, audit_dir, EMPTY_DB_STATS, 0.9)

    assert report["evidence_stale"] is True
    assert report["requires_extraction_rerun"] is False
    assert report["payload_upgrade_reasons"] == ["missing_stable_entry_keys"]
    assert report["target_coverage"] == 1.0


def test_current_low_quality_evidence_triggers_rerun(tmp_path: Path) -> None:
    payload_path = tmp_path / "PG001_indices.json"
    audit_dir = tmp_path / "audits"
    audit_dir.mkdir()
    write_payload(payload_path)
    (audit_dir / "PG001_indices_evidence.json").write_text(
        json.dumps(
            {
                "pipeline_kind": "general",
                "payload_entry_count": 1,
                "sampled_entry_count": 1,
                "verified_ratio": 0,
                "unjustified_empty_list_section_count": 1,
                "segmentation_suspect_count": 1,
            }
        ),
        encoding="utf-8",
    )

    report = audit_payload(payload_path, audit_dir, EMPTY_DB_STATS, 0.9)

    assert report["evidence_stale"] is False
    assert report["extraction_rerun_reasons"] == [
        "unjustified_empty_list_sections",
        "segmentation_suspects",
        "low_ocr_evidence_ratio",
    ]

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_index_page_boundary_sample import audit_reference


def _write_page(path: Path, body: str) -> None:
    path.write_text(
        f'<pagina estado="com_texto"><bloco tipo="texto_principal">{body}</bloco></pagina>',
        encoding="utf-8",
    )


def test_manual_boundary_audit_reports_misses_and_unsafe_joins(tmp_path: Path) -> None:
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    _write_page(left, "AARON interpreta-")
    _write_page(right, "tione, 15")
    reference = {
        "seed": "test",
        "boundaries": [
            {
                "volume_id": "TEST",
                "physical_left_file": "left.txt",
                "physical_right_file": "right.txt",
                "is_same_logical_entry": True,
                "reason": "split word",
            }
        ],
    }

    report = audit_reference(reference, tmp_path)

    assert report["status"] == "ok"
    assert report["matched_count"] == 1
    assert report["missed_continuation_count"] == 0
    assert report["unsafe_join_count"] == 0

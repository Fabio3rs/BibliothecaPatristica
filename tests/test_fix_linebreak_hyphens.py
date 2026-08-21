from __future__ import annotations

from scripts.pipeline_index_extraction.fix_linebreak_hyphens import clean_payload


def test_clean_payload_handles_general_nested_entries_without_touching_raw_json() -> None:
    payload = {
        "sections": [
            {
                "heading_raw": "ORDO RERUM",
                "entries": [
                    {
                        "entry_raw": "persona Chri-\nsti",
                        "target_raw": "persona Chri-\nsti",
                        "raw_json": {"source_lines": ["persona Chri-", "sti"]},
                    }
                ],
            }
        ]
    }

    clean_payload(payload)

    entry = payload["sections"][0]["entries"][0]
    assert entry["entry_raw"] == "persona Christi"
    assert entry["target_raw"] == "persona Christi"
    assert entry["raw_json"]["source_lines"] == ["persona Chri-", "sti"]

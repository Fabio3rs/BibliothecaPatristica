from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.build_alphabetical_prompt import (
    FILTERED_PAGES_SNIPPET_BYTES,
    build_work_instructions,
    summarize_intermediate_dir,
    summarize_json_block,
    summarize_text_block,
)


def test_build_work_instructions_mentions_neighbor_checks_and_regex_search() -> None:
    text = build_work_instructions(Path("/tmp/po025/text"))

    assert "inspect several OCR files before and after" in text
    assert "rg -n -S" in text
    assert "450_3-5" in text
    assert "coverage.locator_status=partial" in text
    assert "intermediate_dir" in text
    assert "pipeline_index_extraction" in text


def test_summarize_intermediate_dir_lists_existing_files(tmp_path: Path) -> None:
    intermediate_dir = tmp_path / "PL018"
    intermediate_dir.mkdir()
    (intermediate_dir / "entries.json").write_text("[]\n", encoding="utf-8")
    (intermediate_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    text = summarize_intermediate_dir(intermediate_dir)

    assert "WORKSPACE PATHS" in text
    assert "entries.json" in text
    assert "manifest.json" in text


def test_summarize_json_block_truncates_large_payload() -> None:
    value = {"items": ["x" * 5000 for _ in range(50)]}
    text = summarize_json_block(value, max_bytes=FILTERED_PAGES_SNIPPET_BYTES // 4)
    assert "... [TRUNCATED]" in text


def test_summarize_text_block_truncates_large_text() -> None:
    text = summarize_text_block("abcde" * 10000, max_bytes=500)
    assert "... [TRUNCATED]" in text

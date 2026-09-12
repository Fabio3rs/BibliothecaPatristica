"""Launch the fresh-context semantic extraction phase shared by both index runners.

The main runners use this small wrapper instead of duplicating subprocess construction. Each
workplan chunk is executed by `run_index_extraction_chunks.py`, while JSON fragments on disk carry
state between processes and the final Codex invocation only assembles/validates those fragments.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_index_chunk_agents(
    *,
    workplan_file: Path,
    codex_bin: str,
    model: str | None,
    log_dir: Path,
    workers: int = 1,
    verbose: bool = False,
    reuse_complete_chunks: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_index_extraction_chunks.py"),
        "--workplan",
        str(workplan_file),
        "--codex-bin",
        codex_bin,
        "--log-dir",
        str(log_dir),
        "--workers",
        str(max(1, workers)),
    ]
    if reuse_complete_chunks:
        command.append("--skip-complete")
    if model:
        command.extend(["--model", model])
    if verbose:
        command.append("--verbose")
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=not verbose,
        check=False,
    )
    if result.returncode != 0:
        output = (
            f"STDOUT:\n{result.stdout or ''}\nSTDERR:\n{result.stderr or ''}"
            if not verbose
            else f"See chunk logs in {log_dir}"
        )
        raise SystemExit(
            f"chunked Codex extraction failed for {workplan_file}\n"
            f"{output}"
        )
    return result

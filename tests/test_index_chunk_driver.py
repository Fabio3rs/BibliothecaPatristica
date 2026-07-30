from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patristica_pipeline.index_chunk_driver as driver


def test_chunk_driver_uses_ephemeral_chunk_runner_and_persistent_log_dir(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"status":"ok"}\n', stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    workplan = tmp_path / "workplan.json"
    log_dir = tmp_path / "logs"

    driver.run_index_chunk_agents(
        workplan_file=workplan,
        codex_bin="codex-test",
        model="model-test",
        log_dir=log_dir,
        workers=3,
    )

    command = captured["command"]
    assert "run_index_extraction_chunks.py" in str(command[1])
    assert command[command.index("--workplan") + 1] == str(workplan)
    assert command[command.index("--codex-bin") + 1] == "codex-test"
    assert command[command.index("--log-dir") + 1] == str(log_dir)
    assert command[command.index("--workers") + 1] == "3"
    assert "--skip-complete" in command
    assert captured["kwargs"]["capture_output"] is True


def test_verbose_chunk_driver_propagates_verbose_and_inherits_terminal_streams(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    driver.run_index_chunk_agents(
        workplan_file=tmp_path / "workplan.json",
        codex_bin="codex-test",
        model=None,
        log_dir=tmp_path / "logs",
        verbose=True,
    )

    assert "--verbose" in captured["command"]
    assert captured["kwargs"]["capture_output"] is False

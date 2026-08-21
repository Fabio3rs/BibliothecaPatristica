from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "resumo_parallel.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_forwards_optional_worker_arguments(tmp_path: Path) -> None:
    volume = tmp_path / "PG TEST"
    volume.mkdir()

    result = run_script(
        "--pipeline",
        "legacy",
        "--pattern",
        str(volume),
        "--jobs",
        "10",
        "--facsimile",
        "--fill-gaps-overlap",
        "7",
        "--page",
        "5",
        "--api-key-env",
        "CUSTOM_KEY",
        "--verbose",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "--facsimile" in result.stdout
    assert "--fill-gaps --fill-gaps-overlap 7" in result.stdout
    assert "--page 5" in result.stdout
    assert "--api-key-env CUSTOM_KEY" in result.stdout
    assert "--verbose" in result.stdout
    assert "PG\\ TEST" in result.stdout
    assert "(total: 1 comandos, 10 em paralelo)" in result.stdout


def test_rejects_invalid_parallelism_before_scanning_corpus() -> None:
    result = run_script("--jobs", "0", "--dry-run")

    assert result.returncode == 1
    assert "--jobs deve ser um inteiro maior que zero" in result.stderr


def test_help_does_not_leak_script_implementation() -> None:
    result = run_script("--help")

    assert result.returncode == 0
    assert "--facsimile" in result.stdout
    assert "set -euo pipefail" not in result.stdout


def test_v2_dry_run_forwards_chain_and_facsimile_arguments(tmp_path: Path) -> None:
    volume = tmp_path / "PG001"
    volume.mkdir()

    result = run_script(
        "--pattern",
        str(volume),
        "--facsimile-threshold",
        "4",
        "--lookahead-pages",
        "2",
        "--force-replace-from",
        "175",
        "--force-replace-through",
        "next-work",
        "--fail-fast",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "--pipeline v2" in result.stdout
    assert "--facsimile-threshold 4" in result.stdout
    assert "--lookahead-pages 2" in result.stdout
    assert "--force-replace-from 175 --force-replace-through next-work" in result.stdout
    assert "Falha do lote:     parar de agendar" in result.stdout
    assert "Janela de contexto: automática por pipeline/modelo" in result.stdout
    assert "--num-ctx" not in result.stdout


def test_rejects_legacy_gap_mode_in_v2() -> None:
    result = run_script("--fill-gaps", "--dry-run")

    assert result.returncode == 1
    assert "--fill-gaps pertence ao pipeline legacy" in result.stderr


def test_rejects_isolated_page_in_v2() -> None:
    result = run_script("--page", "175", "--dry-run")

    assert result.returncode == 1
    assert "--page isolada não preserva a cadeia v2" in result.stderr


def test_rejects_force_replace_end_without_start() -> None:
    result = run_script("--force-replace-through", "end", "--dry-run")

    assert result.returncode == 1
    assert "--force-replace-through requer --force-replace-from" in result.stderr

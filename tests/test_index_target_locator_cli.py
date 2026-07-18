from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_index_target_locator_cli_reads_json_and_writes_result(tmp_path: Path) -> None:
    text_root = tmp_path / "PGZ" / "text"
    text_root.mkdir(parents=True)
    (text_root / "page-001.txt").write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">543 INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )
    request = {
        "volume_id": "PGZ",
        "source_root": str(text_root),
        "entries": [{"entry_id": "e1", "query_names": ["Dionysius"], "page_hint_ints": [543]}],
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    cmd = [
        sys.executable,
        "scripts/index_target_locator.py",
        "--input",
        str(request_path),
        "--pretty",
    ]
    completed = subprocess.run(
        cmd,
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["volume_id"] == "PGZ"
    assert payload["entries"][0]["best_candidate"]["file"].endswith("page-001.txt")

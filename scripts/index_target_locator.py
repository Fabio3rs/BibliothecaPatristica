#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.index_target_locator import load_request_json, resolve_index_targets


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Resolve candidatos de target_file para entradas de índice a partir "
            "de nomes e páginas editoriais plausíveis."
        )
    )
    ap.add_argument("--input", type=Path, required=True, help="Arquivo JSON de entrada.")
    ap.add_argument("--output", type=Path, help="Arquivo JSON de saída. Se omitido, escreve em stdout.")
    ap.add_argument("--pretty", action="store_true", help="Formata JSON com indentação.")
    args = ap.parse_args()

    request = load_request_json(args.input)
    result = resolve_index_targets(request)
    payload = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None)

    if args.output:
        args.output.write_text(payload + ("\n" if not payload.endswith("\n") else ""), encoding="utf-8")
        return
    print(payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Gera web/public/dict/all.json agregando os dicionários existentes.
Uso: python tools/build_dict_bundle.py --public web/public --out web/public/dict/all.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", default="web/public", help="Diretório public (default: web/public)")
    parser.add_argument("--out", default=None, help="Arquivo de saída (default: <public>/dict/all.json)")
    args = parser.parse_args()

    public_dir = Path(args.public)
    dict_dir = public_dir / "dict"
    out_path = Path(args.out) if args.out else dict_dir / "all.json"

    sources = {
        "keywords": dict_dir / "keywords.json",
        "keywords_lookup": dict_dir / "keywords_lookup.json",
        "keywords_manifest": dict_dir / "keywords_manifest.json",
        "keywords_top": dict_dir / "keywords_top.json",
        "keyword_groups": dict_dir / "keyword_groups.json",
        "entities": dict_dir / "entities.json",
        "authors": dict_dir / "authors.json",
        "themes": dict_dir / "themes.json",
    }

    data = {}
    for key, path in sources.items():
        if not path.exists():
            raise SystemExit(f"[ERRO] Arquivo não encontrado: {path}")
        data[key] = load_json(path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Bundle gerado em {out_path}")


if __name__ == "__main__":
    main()

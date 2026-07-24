#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.editorial_page_estimator import (
    clear_page_override,
    estimate_editorial_pages_json,
    list_page_overrides,
    set_page_override,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Estima páginas editoriais de arquivos OCR PG/PL a partir de "
            "cabeçalhos, rodapés e consistência local entre arquivos vizinhos."
        )
    )
    ap.add_argument("--volume-id", help="Volume, por exemplo PG035 ou PL169.")
    ap.add_argument("--source-root", type=Path, help="Diretório teste/<VOLUME>/text.")
    ap.add_argument("--files", nargs="*", type=Path, help="Subconjunto de arquivos OCR para analisar.")
    ap.add_argument("--collection", help="Coleção opcional, por exemplo PG ou PL.")
    ap.add_argument("--window", type=int, default=2, help="Janela de vizinhança para reforço monotônico.")
    ap.add_argument("--db", type=Path, help="Banco SQLite dedicado ao estimador.")
    ap.add_argument("--no-cache", action="store_true", help="Ignora o cache SQLite de observações por arquivo.")
    ap.add_argument("--set-override", nargs="+", help="Define override manual: <arquivo> <pag1> [pag2].")
    ap.add_argument("--clear-override", type=Path, help="Remove override manual de um arquivo.")
    ap.add_argument("--list-overrides", action="store_true", help="Lista overrides registrados no banco.")
    ap.add_argument("--note", help="Nota opcional para override manual.")
    ap.add_argument("--output", type=Path, help="Arquivo JSON de saída.")
    ap.add_argument("--pretty", action="store_true", help="Formata o JSON com indentação.")
    args = ap.parse_args()

    if args.list_overrides:
        payload = json.dumps(
            list_page_overrides(db_path=args.db, volume_id=args.volume_id),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    elif args.clear_override:
        payload = json.dumps(
            clear_page_override(db_path=args.db, file_path=args.clear_override),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    elif args.set_override:
        if len(args.set_override) < 2:
            raise SystemExit("--set-override exige: <arquivo> <pag1> [pag2]")
        file_path = Path(args.set_override[0])
        pages = [int(item) for item in args.set_override[1:]]
        payload = json.dumps(
            set_page_override(
                db_path=args.db,
                file_path=file_path,
                pages=pages,
                volume_id=args.volume_id,
                note=args.note,
            ),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    else:
        payload = estimate_editorial_pages_json(
            volume_id=args.volume_id,
            source_root=args.source_root,
            files=args.files,
            collection=args.collection,
            window=args.window,
            db_path=args.db,
            use_cache=not args.no_cache,
            pretty=args.pretty,
        )

    if args.output:
        args.output.write_text(payload + ("\n" if not payload.endswith("\n") else ""), encoding="utf-8")
        return
    print(payload)


if __name__ == "__main__":
    main()

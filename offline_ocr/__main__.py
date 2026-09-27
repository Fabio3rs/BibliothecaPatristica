import argparse
import json
from pathlib import Path

from .store import DEFAULT_DB, connect, get_comparison, ingest


def main():
    parser = argparse.ArgumentParser(description="OCR versions com layout — processamento offline isolado")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("ingest", help="Processa uma página explícita, com cache por conteúdo")
    add.add_argument("--image", type=Path, required=True)
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", type=Path)
    source.add_argument("--result-id", type=int)
    add.add_argument("--versions-db", type=Path, default=Path("data/ocr_versions.db"))
    add.add_argument("--lang", default="migne")
    add.add_argument("--psm", type=int, choices=range(3, 14), default=3)
    add.add_argument("--oem", type=int, choices=range(4), default=1)
    add.add_argument("--tessdata-dir", type=Path)
    add.add_argument("--tsv", type=Path, help="TSV existente nas coordenadas da imagem original")
    add.add_argument("--bbox-tolerance", type=float, default=25, help="Margem na escala 0–1000 (padrão: 25)")
    add.add_argument("--keep-hyphens", action="store_true")
    view = commands.add_parser("serve", help="Abre servidor somente em 127.0.0.1")
    view.add_argument("--port", type=int, default=8765)
    export = commands.add_parser("export", help="Exporta evidências e alinhamento completos para JSON")
    export.add_argument("key")
    export.add_argument("--format", choices=("json", "xml", "png"), default="json")
    export.add_argument("--output", type=Path, required=True)
    batch = commands.add_parser("batch", help="Manifesto JSONL explícito, multiprocessamento por página")
    batch.add_argument("manifest", type=Path)
    batch.add_argument("--workers", type=int, default=2)
    commands.add_parser("list")
    args = parser.parse_args()
    con = connect(args.db)
    try:
        if args.command == "ingest":
            result = ingest(con, image_path=args.image, text_path=args.text, result_id=args.result_id,
                            versions_db=args.versions_db, lang=args.lang, psm=args.psm, oem=args.oem,
                            tessdata=args.tessdata_dir, tsv_path=args.tsv, dehyphenate=not args.keep_hyphens, tolerance=args.bbox_tolerance)
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "export":
            result = get_comparison(con, args.key)
            if result is None:
                parser.error("Comparação não encontrada")
            from .review import corrected_xml, latest_revision
            result["revision"] = latest_revision(con, args.key)
            result["history"] = [dict(row) for row in con.execute("SELECT * FROM revisions WHERE comparison_key=? ORDER BY id", (args.key,))]
            sources = [Path(result["image_path"]), args.db]
            if result["llm"]["provenance"].get("text_path"):
                sources.append(Path(result["llm"]["provenance"]["text_path"]))
            if args.output.resolve() in [path.resolve() for path in sources]:
                parser.error("Exportação não pode sobrescrever a fonte ou o banco")
            if args.format == "png":
                from .raster import render
                args.output.write_bytes(render(result, result["revision"]))
            else:
                text = corrected_xml(result, result["revision"]) if args.format == "xml" else json.dumps(result, ensure_ascii=False, indent=2)
                args.output.write_text(text, encoding="utf-8")
        elif args.command == "batch":
            from .batch import run_batch
            failed = False
            for result in run_batch(args.db, args.manifest, args.workers, {}):
                print(json.dumps(result, ensure_ascii=False), flush=True)
                failed = failed or "error" in result
            if failed:
                raise SystemExit(1)
        elif args.command == "list":
            for row in con.execute("SELECT key,label,created_at FROM comparisons ORDER BY created_at DESC"):
                print(json.dumps(dict(row), ensure_ascii=False))
    finally:
        con.close()
    if args.command == "serve":
        from .server import serve
        serve(args.db, args.port)


if __name__ == "__main__":
    main()

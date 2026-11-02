#!/usr/bin/env python3
"""Prepare offline or explicitly run one OpenAI correction using the exact cached OCR."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from offline_ocr import cached_repair as repair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Offline: freeze DB XML, cached Tesseract and prompts")
    prepare.add_argument("--image", type=Path, default=ROOT / "teste/PG001/images/PG001-638.png")
    prepare.add_argument("--volume", default="PG001")
    prepare.add_argument("--page", type=int, default=638)
    prepare.add_argument("--db", type=Path, default=ROOT / "data/ocr_versions.db")
    prepare.add_argument("--cache-db", type=Path, default=ROOT / "data/tesseract.db")
    prepare.add_argument("--version-id", type=int, help="Default: most recent non-Tesseract version of this image")
    prepare.add_argument("--expect-model", default="gpt-5-mini", help="Reject a different source model")
    prepare.add_argument("--lang", default="migne")
    prepare.add_argument("--out", type=Path, required=True, help="New experiment directory")
    run = commands.add_parser("run", help="EXPLICIT INFERENCE: exactly one request, no automatic retries")
    run.add_argument("--experiment", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True, help="New results directory")
    run.add_argument("--model", default="gpt-5.4-mini")
    run.add_argument("--base-url", default="https://api.openai.com/v1")
    run.add_argument("--api-key-env", default="OPENAI_API_KEY")
    run.add_argument("--image-detail", choices=["auto", "low", "high", "original"], default="original")
    run.add_argument("--parameters", type=Path, help="Optional JSON object with provider fields")
    run.add_argument("--reasoning-effort", choices=repair.REASONING_EFFORTS,
                     help="Overrides --parameters; default: medium unless specified in that file")
    run.add_argument("--timeout", type=float, default=600)
    inspect = commands.add_parser("inspect", help="Offline: repeat XML validation and the before/after diff")
    inspect.add_argument("--experiment", type=Path, required=True)
    inspect.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            manifest = repair.prepare(args.image, args.out, db_path=args.db, cache_db_path=args.cache_db,
                                      volume=args.volume, page=args.page, version_id=args.version_id,
                                      expect_model=args.expect_model, lang=args.lang)
            result = {"experiment_dir": str(args.out.resolve()), "source_version": manifest["source_version"],
                      "cache": manifest["cache"], "inference_performed": False}
        elif args.command == "run":
            parameters = json.loads(args.parameters.read_text(encoding="utf-8")) if args.parameters else None
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError("parameters must be a JSON object")
            result = repair.run(args.experiment, args.out, model=args.model, base_url=args.base_url,
                                api_key_env=args.api_key_env, image_detail=args.image_detail,
                                parameters=parameters, reasoning_effort=args.reasoning_effort,
                                timeout=args.timeout)
        else:
            result = repair.inspect_result(args.experiment, args.results)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        inspection = result.get("inspection", result)
        return int(bool(result.get("counts", {}).get("error") or inspection.get("xml_errors") or
                        inspection.get("finish_reason") not in {None, "stop"}))
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

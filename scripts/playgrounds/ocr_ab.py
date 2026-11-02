#!/usr/bin/env python3
"""Compare compact Tesseract hints. Only the explicit `run` command calls a model."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from offline_ocr import ab_experiment as ab
from offline_ocr.migne_hints import FORMATS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Offline: validate TSV and save comparable prompts")
    prepare.add_argument("--image", type=Path, required=True)
    prepare.add_argument("--tsv", type=Path, required=True, help="TSV from this original image, without padding/resizing")
    prepare.add_argument("--out", type=Path, required=True, help="New experiment directory")
    prepare.add_argument("--draft", type=Path, help="Optional previous XML, identical in every arm")
    prepare.add_argument("--formats", nargs="+", choices=FORMATS, default=list(FORMATS))
    run = commands.add_parser("run", help="EXPLICIT INFERENCE: sends every arm to the selected provider")
    run.add_argument("--experiment", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True, help="New results directory")
    run.add_argument("--provider", choices=["ollama", "openai-like"], required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--base-url", required=True, help="Ollama host or OpenAI-compatible API root (including /v1)")
    run.add_argument("--api-key-env", help="Environment variable containing a key; not saved to logs")
    run.add_argument("--parameters", type=Path, help="Provider JSON body fields, shared by all arms")
    run.add_argument("--image-detail", choices=["auto", "low", "high", "original"])
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--order-seed", type=int, default=42, help="Randomises arm order only; not the model seed")
    run.add_argument("--timeout", type=float, default=600)
    evaluate = commands.add_parser("evaluate", help="Offline: validate XML, align outputs and report paired differences")
    evaluate.add_argument("--experiment", type=Path, required=True)
    evaluate.add_argument("--results", type=Path, required=True)
    evaluate.add_argument("--targets", nargs="*", help="Optional single words for explicitly annotated omission checks")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = ab.prepare(args.image, args.tsv, args.out, draft_path=args.draft, formats=args.formats)
            print(json.dumps({k: result[k] for k in ("mode", "layout_stats", "system_characters", "arms")}, ensure_ascii=False, indent=2))
        elif args.command == "run":
            parameters = json.loads(args.parameters.read_text()) if args.parameters else None
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError("parameters must be a JSON object")
            result = ab.run(args.experiment, args.out, provider=args.provider, model=args.model,
                            base_url=args.base_url, repeats=args.repeats, order_seed=args.order_seed,
                            parameters=parameters, api_key_env=args.api_key_env,
                            image_detail=args.image_detail, timeout=args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["counts"].get("error") else 0
        else:
            result = ab.evaluate(args.experiment, args.results, targets=args.targets)
            print((args.results / "evaluation.txt").read_text())
            return 1 if any(r["status"] != "ok" for r in result["rows"]) else 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

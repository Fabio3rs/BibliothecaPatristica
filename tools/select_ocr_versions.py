#!/usr/bin/env python3
"""Manually scan OCR history, inspect proposals, then apply or roll back exports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline_ocr.selection import apply_run, report_run, rollback_run, scan
from offline_ocr.refinement import refine_run


def page_numbers(value):
    result = set()
    try:
        for part in value.split(","):
            if "-" in part:
                low, high = map(int, part.split("-", 1))
                if low < 0 or high < low or high - low > 10000:
                    raise ValueError
                result.update(range(low, high + 1))
            else:
                result.add(int(part))
        if not result or min(result) < 0:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use pages such as 638 or 637-639,650") from None
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("scan", help="Read history and create proposals; never changes corpus files")
    scope = inspect.add_mutually_exclusive_group(required=True)
    scope.add_argument("--volumes", nargs="+")
    scope.add_argument("--all-volumes", action="store_true", help="Explicitly allow database-wide scope")
    inspect.add_argument("--pages", type=page_numbers)
    inspect.add_argument("--limit", type=int, default=25, help="Maximum pages to inspect (default 25)")
    inspect.add_argument("--after", help="Resume after a volume:page cursor from the previous report")
    inspect.add_argument("--db", type=Path, default=Path("data/ocr_versions.db"))
    inspect.add_argument("--base-dir", type=Path, default=Path("teste"))
    inspect.add_argument("--run-dir", type=Path, required=True, help="New directory for immutable proposals and logs")
    inspect.add_argument("--cache-dir", type=Path, default=Path(".ocr-workbench/selection-reference-cache"))
    inspect.add_argument("--lang", default="migne")
    inspect.add_argument("--psm", type=int, default=3)
    inspect.add_argument("--oem", type=int, default=1)
    inspect.add_argument("--tessdata-dir")
    inspect.add_argument("--no-rejects", action="store_true", help="Enable both zero_kelvin_rejection and textord_no_rejects")
    for command in ("report", "apply", "rollback"):
        action = commands.add_parser(command, help="Read report or explicitly apply/restore files; DB stays read-only")
        action.add_argument("--run-dir", type=Path, required=True)
    refine = commands.add_parser("refine", help="Experimental review draft; never exports to corpus or writes DB")
    refine.add_argument("--run-dir", type=Path, required=True)
    refine.add_argument("--page", required=True, help="Page key, for example PG001:638")
    refine.add_argument("--version-id", type=int, required=True, help="Explicit eligible base version")
    refine.add_argument("--out", type=Path, required=True, help="New draft directory outside corpus")
    refine.add_argument("--repair-bboxes", action="store_true", help="Propose bbox-only changes from unique TSV text anchors")
    refine.add_argument("--merge-candidates", action="store_true", help="Locate complementary donor spans; no automatic insertion")
    args = parser.parse_args()
    try:
        if args.command == "scan":
            result = scan(db_path=args.db, base_dir=args.base_dir, run_dir=args.run_dir,
                          volumes=args.volumes, pages=args.pages, limit=args.limit, after=args.after, cache_dir=args.cache_dir,
                          lang=args.lang, psm=args.psm, oem=args.oem, tessdata=args.tessdata_dir,
                          no_rejects=args.no_rejects)
            print(json.dumps({"run_dir": str(args.run_dir), "summary": result["summary"],
                              "policy": result["policy"]["version"], "next_after": result["next_after"]}, ensure_ascii=False))
            if result["summary"].get("error"):
                return 1
        elif args.command == "report":
            print(report_run(args.run_dir), end="")
        elif args.command == "refine":
            result = refine_run(args.run_dir, args.page, args.version_id, args.out,
                                repair_bboxes=args.repair_bboxes, merge_candidates=args.merge_candidates)
            print(json.dumps({"output": str(args.out), "automatic_export": False,
                              "bbox_statuses": [b["status"] for b in result.get("bbox_review", {}).get("blocks", [])],
                              "merge_candidates": len(result.get("merge_review", {}).get("proposals", []))}, ensure_ascii=False))
        else:
            operation = apply_run if args.command == "apply" else rollback_run
            print(json.dumps(operation(args.run_dir), ensure_ascii=False))
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

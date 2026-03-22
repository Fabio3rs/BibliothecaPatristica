#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Dict, List

from patristica_pipeline.common import parse_volume_info
from patristica_pipeline.db import connect_db, init_catalog_schema, init_databases
from patristica_pipeline.ingest import ingest_ocr_to_text_db
from patristica_pipeline.smol_loops import (
    GuidedLoopConfig,
    dumps_loop_result,
    run_guided_volume_loop,
    save_guided_loop_result,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run guided smolagents loop for a target Patristica volume."
    )
    p.add_argument("--provider", choices=["openai", "ollama"], default="openai")
    p.add_argument("--model", default="gpt-5-mini")
    p.add_argument("--base-url", default=None)
    p.add_argument("--volume-id", default="", help="Target volume, e.g. PG001")
    p.add_argument("--all-volumes", action="store_true", help="Run loops across all volumes under --root matching --series.")
    p.add_argument("--root", type=Path, default=Path("teste"), help="Root with PG/PL/PO volume dirs.")
    p.add_argument("--series", default="PG,PL,PO", help="Comma-separated series filter.")
    p.add_argument("--limit", type=int, default=0, help="Max volumes in all-volumes mode (0 = all).")
    p.add_argument("--prepare-base", action="store_true", help="Initialize DB schema and ingest OCR before loops.")
    p.add_argument("--reset-db", action="store_true", help="Reset DBs when using --prepare-base.")
    p.add_argument("--catalog-db", type=Path, default=Path("data/patristica_catalog.db"))
    p.add_argument("--text-db", type=Path, default=Path("data/patristica_text.db"))
    p.add_argument("--min-chars", type=int, default=1200)
    p.add_argument("--max-chars", type=int, default=5000)
    p.add_argument("--hard-max-chars", type=int, default=10000)
    p.add_argument("--objective", default="", help="Automated task objective")
    p.add_argument("--hints", default="", help="Optional hints for the agent")
    p.add_argument("--passes", type=int, default=3, help="Number of loop passes")
    p.add_argument("--continue-on-error", action="store_true", help="Continue remaining volumes when one fails.")
    p.add_argument("--skip-completed", action="store_true", help="Skip volumes whose latest guided run status is 'ok'.")
    p.add_argument("--list-only", action="store_true", help="Only list selected volumes and exit.")
    p.add_argument("--out", type=Path, default=None, help="Optional JSON output file")
    p.add_argument("--out-dir", type=Path, default=Path("out/patristica_loops"), help="Output dir for per-volume JSON in all-volumes mode.")
    p.add_argument("--jsonl-out", type=Path, default=None, help="Optional JSONL file with one run record per volume.")
    return p


def _select_volume_ids(root: Path, series: str, limit: int) -> List[str]:
    if not root.exists():
        return []
    wanted = {s.strip().upper() for s in series.split(",") if s.strip()}
    out: List[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        info = parse_volume_info(child)
        if not info:
            continue
        if wanted and info.series not in wanted:
            continue
        if not (child / "text").exists():
            continue
        out.append(info.volume_id)
    out.sort()
    if limit > 0:
        out = out[:limit]
    return out


def _latest_status_by_volume(catalog_db: Path) -> Dict[str, str]:
    if not catalog_db.exists():
        return {}
    with connect_db(catalog_db) as con:
        init_catalog_schema(con)
        rows = con.execute(
            """
            SELECT r.volume_id, r.status
            FROM agent_loop_runs r
            WHERE r.id IN (
                SELECT MAX(id)
                FROM agent_loop_runs
                GROUP BY volume_id
            )
            """
        ).fetchall()
    return {str(r["volume_id"]): str(r["status"]) for r in rows}


def _volume_listing_payload(root: Path, volume_ids: List[str]) -> Dict[str, object]:
    rows = []
    for volume_id in volume_ids:
        text_dir = root / volume_id / "text"
        txt_files = (
            sum(1 for p in text_dir.glob("*.txt") if p.name != "texto_extraido.txt")
            if text_dir.exists()
            else 0
        )
        rows.append({"volume_id": volume_id, "text_files": txt_files})
    return {"root": str(root), "count": len(rows), "volumes": rows}


def _append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    args = build_parser().parse_args()

    if not args.all_volumes and not args.volume_id.strip():
        raise SystemExit("Use --volume-id or --all-volumes.")
    if not args.list_only and not args.objective.strip():
        raise SystemExit("--objective is required unless --list-only is used.")

    if args.prepare_base:
        init_databases(args.catalog_db, args.text_db, reset=args.reset_db)
        ingest_ocr_to_text_db(
            root=args.root,
            text_db=args.text_db,
            series_filter=[s.strip().upper() for s in args.series.split(",") if s.strip()],
            min_chars=args.min_chars,
            max_chars=args.max_chars,
            hard_max_chars=args.hard_max_chars,
            limit=(None if args.limit <= 0 else args.limit),
        )

    volume_ids = (
        _select_volume_ids(args.root, args.series, args.limit)
        if args.all_volumes
        else [args.volume_id.strip()]
    )
    if args.skip_completed:
        latest = _latest_status_by_volume(args.catalog_db)
        volume_ids = [v for v in volume_ids if latest.get(v) != "ok"]

    if args.list_only:
        print(dumps_loop_result(_volume_listing_payload(args.root, volume_ids)))
        return

    if not volume_ids:
        raise SystemExit("No volumes selected.")

    from patristica_pipeline.smol_runtime import build_patristica_agent

    agent = build_patristica_agent(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
    )

    overall = {
        "provider": args.provider,
        "model": args.model,
        "objective": args.objective,
        "hints": args.hints,
        "passes": max(1, args.passes),
        "volumes_total": len(volume_ids),
        "success": 0,
        "failed": 0,
        "results": [],
    }

    for idx, volume_id in enumerate(volume_ids, start=1):
        print(f"[INFO] {idx}/{len(volume_ids)} volume={volume_id}")
        try:
            cfg = GuidedLoopConfig(
                volume_id=volume_id,
                objective=args.objective,
                hints=args.hints,
                passes=max(1, args.passes),
                text_db=str(args.text_db),
                catalog_db=str(args.catalog_db),
                root=str(args.root),
            )
            result = run_guided_volume_loop(agent, cfg)
            overall["success"] += 1
            overall["results"].append({"volume_id": volume_id, "status": "ok"})
            save_guided_loop_result(
                catalog_db=str(args.catalog_db),
                volume_id=volume_id,
                provider=args.provider,
                model=args.model,
                objective=args.objective,
                hints=args.hints,
                passes=max(1, args.passes),
                status="ok",
                result=result,
            )
            if args.all_volumes:
                args.out_dir.mkdir(parents=True, exist_ok=True)
                per_file = args.out_dir / f"{volume_id}.json"
                per_file.write_text(dumps_loop_result(result), encoding="utf-8")
            else:
                output = dumps_loop_result(result)
                print(output)
                if args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(output, encoding="utf-8")
                    print(f"[OK] wrote {args.out}")
            if args.jsonl_out:
                _append_jsonl(
                    args.jsonl_out,
                    {"volume_id": volume_id, "status": "ok", "result": result},
                )
        except Exception as exc:
            overall["failed"] += 1
            err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
            overall["results"].append({"volume_id": volume_id, "status": "failed", "error": str(exc)})
            save_guided_loop_result(
                catalog_db=str(args.catalog_db),
                volume_id=volume_id,
                provider=args.provider,
                model=args.model,
                objective=args.objective,
                hints=args.hints,
                passes=max(1, args.passes),
                status="failed",
                result=None,
                error_text=err,
            )
            print(f"[ERROR] volume={volume_id} {exc}")
            if args.jsonl_out:
                _append_jsonl(
                    args.jsonl_out,
                    {"volume_id": volume_id, "status": "failed", "error": str(exc)},
                )
            if not args.continue_on_error:
                raise

    summary_text = dumps_loop_result(overall)
    print(summary_text)
    if args.all_volumes:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        summary_file = args.out_dir / "_summary.json"
        summary_file.write_text(summary_text, encoding="utf-8")
        print(f"[OK] wrote {summary_file}")


if __name__ == "__main__":
    main()

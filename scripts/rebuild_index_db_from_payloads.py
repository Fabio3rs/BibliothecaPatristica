#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_index_json.py"
INIT_SCRIPT = PROJECT_ROOT / "scripts" / "init_index_db.py"


def run(cmd: list[str]) -> None:
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    if result.stdout.strip():
        print(result.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild patristic_indices.db from payload JSON files.")
    ap.add_argument("--payload-dir", type=Path, default=PROJECT_ROOT / "data" / "index_payloads")
    ap.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "patristic_indices.rebuilt.db")
    ap.add_argument("--glob", default="*_indices.json", help="Payload glob filter")
    ap.add_argument("--fresh", action="store_true", help="Delete the target DB before rebuilding it")
    args = ap.parse_args()

    payloads = sorted(args.payload_dir.glob(args.glob))
    if not payloads:
        raise SystemExit(f"No payloads found in {args.payload_dir} with glob {args.glob!r}")

    if args.fresh and args.db.exists():
        args.db.unlink()

    run([sys.executable, str(INIT_SCRIPT), "--db", str(args.db)])
    for payload in payloads:
        run(
            [
                sys.executable,
                str(IMPORT_SCRIPT),
                "--db",
                str(args.db),
                "--input",
                str(payload),
                "--replace",
            ]
        )
    print(f"[OK] rebuilt {args.db} from {len(payloads)} payloads")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.indexing.index_operation_lock import index_operation_lock
from tools.indexing.structural_target_validation import validate_applied_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an applied structural-target report against payload JSON and SQLite."
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "patristic_indices.db")
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=PROJECT_ROOT / "data" / ".patristic-index-write.lock",
    )
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    args = parser.parse_args()
    if args.lock_timeout < 0:
        parser.error("--lock-timeout cannot be negative")

    active_transaction = PROJECT_ROOT / "data" / ".structural-target-repair" / "active_transaction.json"
    with index_operation_lock(
        args.lock_file,
        exclusive=False,
        timeout=args.lock_timeout,
        active_transaction_path=active_transaction,
    ):
        result = validate_applied_report(args.report, database_path=args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

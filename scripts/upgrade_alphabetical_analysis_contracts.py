#!/usr/bin/env python3
"""Audit or apply locator-contract v2 upgrades in the analysis database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.alphabetical_analysis_db import (
    DEFAULT_ANALYSIS_DB,
    connect_analysis_db,
    init_analysis_schema,
    upgrade_locator_contracts,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita e padroniza locator_item_json sem alterar decisões de alvo."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_ANALYSIS_DB)
    parser.add_argument("--volume-id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Confirma as atualizações; sem esta opção a transação é revertida.",
    )
    args = parser.parse_args()

    with connect_analysis_db(args.db) as con:
        init_analysis_schema(con)
        counts = upgrade_locator_contracts(
            con,
            volume_id=args.volume_id,
        )
        if args.apply:
            con.commit()
        else:
            con.rollback()
    report = {
        "schema_version": 1,
        "database": str(args.db.resolve()),
        "volume_id": args.volume_id,
        "mode": "apply" if args.apply else "audit",
        **counts,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if counts["invalid_json"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

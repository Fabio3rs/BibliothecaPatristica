#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".codex"
    / "skills"
    / "patristic-index-extractor"
    / "scripts"
    / "init_index_db.py"
)
SCRIPT_DIR = SCRIPT_PATH.parent


if __name__ == "__main__":
    sys.path.insert(0, str(SCRIPT_DIR))
    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

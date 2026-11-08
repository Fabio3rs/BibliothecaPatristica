#!/usr/bin/env python3
"""Re-extract opening-guided index entries and connect their physical destinations."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.opening_target_connection import main

if __name__ == "__main__":
    main()

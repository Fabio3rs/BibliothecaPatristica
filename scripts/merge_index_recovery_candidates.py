#!/usr/bin/env python3
"""Apply a repeatable recovery recipe to the experimental catalog only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.search_recovery_merge import main

if __name__ == "__main__":
    main()

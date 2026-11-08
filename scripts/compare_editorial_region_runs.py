#!/usr/bin/env python3
"""Compare two experimental regional inventories, offline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.editorial_region_comparison import main

if __name__ == '__main__':
    main()

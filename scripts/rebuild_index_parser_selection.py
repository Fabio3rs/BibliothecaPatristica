#!/usr/bin/env python3
"""Rebuild frozen historical selections directly from raw OCR."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.reproducible_selection import main

if __name__ == '__main__':
    main()

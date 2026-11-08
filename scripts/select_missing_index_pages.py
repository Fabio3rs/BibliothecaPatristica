#!/usr/bin/env python3
"""Build an additive experimental supplement after full-section extraction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.missing_page_supplement import main

if __name__ == '__main__':
    main()

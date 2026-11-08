#!/usr/bin/env python3
"""Discover source-scoped editorial locator patterns without changing canonical data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.editorial_pattern_discovery import main

if __name__ == '__main__':
    main()

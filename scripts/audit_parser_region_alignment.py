#!/usr/bin/env python3
"""Audit existing extraction spans against an experimental regional inventory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.parser_region_audit import main

if __name__ == '__main__':
    main()

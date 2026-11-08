#!/usr/bin/env python3
"""Recover PL217 book/epistle destinations without altering source databases."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.epistle_catalog_recovery import main

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Recover experimental orphan index regions using bounded local evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.orphan_index_recovery import main

if __name__ == '__main__':
    main()

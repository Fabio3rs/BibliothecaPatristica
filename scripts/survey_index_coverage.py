#!/usr/bin/env python3
"""Bounded index discovery and mechanical inventory, isolated from the source DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.index_coverage_experiment import main

if __name__ == '__main__':
    main()

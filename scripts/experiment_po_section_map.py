#!/usr/bin/env python3
"""Rebuild scoped PO fascicle maps and literal index links, offline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.po_section_map_experiment import main

if __name__ == '__main__':
    main()

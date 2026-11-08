#!/usr/bin/env python3
"""Rebuild isolated, bounded maps from current OCR without modifying source databases."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.internal_section_map_experiment import main

if __name__ == '__main__':
    main()

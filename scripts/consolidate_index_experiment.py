#!/usr/bin/env python3
"""Consolidate explicitly selected OCR extraction versions into a separate SQLite."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.structured_index_experiment import main

if __name__ == '__main__':
    main()

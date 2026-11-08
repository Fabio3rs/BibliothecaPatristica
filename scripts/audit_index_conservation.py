#!/usr/bin/env python3
"""Audit complete recovered regions against original OCR and existing candidates."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.index_conservation_audit import main

if __name__ == '__main__':
    main()

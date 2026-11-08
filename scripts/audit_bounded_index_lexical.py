#!/usr/bin/env python3
"""Compare index readings with OCR inside expected editorial page ranges."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.bounded_index_audit import main

if __name__ == '__main__':
    main()

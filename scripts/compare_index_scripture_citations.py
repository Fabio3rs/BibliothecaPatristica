#!/usr/bin/env python3
"""Compare existing scripture extractions without modifying either source database."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.scripture_comparison import main

if __name__ == '__main__':
    main()

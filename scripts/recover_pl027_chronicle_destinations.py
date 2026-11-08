#!/usr/bin/env python3
"""Build an experimental chronology overlay, keeping the source database read-only."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.chronicle_destination_recovery import main

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Run the isolated parallel index parser survey."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import main

if __name__ == '__main__':
    main()

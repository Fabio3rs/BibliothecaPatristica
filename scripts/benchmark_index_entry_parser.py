#!/usr/bin/env python3
"""Run the isolated, offline PG069 entry-parser proof of concept."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_experiment import main

if __name__ == "__main__":
    main()

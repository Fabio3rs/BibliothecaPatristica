#!/usr/bin/env python3
"""Search clauses or complete textual groups without modifying the experiment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.structured_index_experiment import search_main

if __name__ == '__main__':
    search_main()

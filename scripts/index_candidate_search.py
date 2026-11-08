#!/usr/bin/env python3
"""Build, search and evaluate the navigable candidate index without canonical writes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.candidate_search import main

if __name__ == '__main__':
    main()

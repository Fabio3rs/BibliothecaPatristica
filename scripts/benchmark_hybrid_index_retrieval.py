#!/usr/bin/env python3
"""Offline hybrid shortlist evaluation; reuses saved semantic rankings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.hybrid_retrieval_experiment import main

if __name__ == "__main__":
    main()

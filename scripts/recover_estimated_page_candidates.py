#!/usr/bin/env python3
"""Deterministic candidate overlay; all input databases are read-only."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.estimator_candidate_recovery import main

if __name__ == "__main__":
    main()

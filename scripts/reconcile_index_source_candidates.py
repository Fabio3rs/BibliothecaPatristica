#!/usr/bin/env python3
"""Reconcile historical OCR fragments into an additive search supplement."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.historical_source_reconciliation import main

if __name__ == '__main__':
    main()

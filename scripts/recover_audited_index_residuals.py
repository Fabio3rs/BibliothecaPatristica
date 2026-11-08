#!/usr/bin/env python3
"""Rank audited residuals and stage conservative literal paragraph recovery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.residual_recovery_campaign import main

if __name__ == '__main__':
    main()

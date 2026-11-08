#!/usr/bin/env python3
"""Offline ibid/qualifier recovery into a searchable overlay."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.editorial_relation_recovery import main

if __name__ == '__main__':
    main()

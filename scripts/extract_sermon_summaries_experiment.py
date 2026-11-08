#!/usr/bin/env python3
"""Extract literal sermon synopses to the separate experiment database."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.sermon_summary_experiment import main

if __name__ == '__main__':
    main()

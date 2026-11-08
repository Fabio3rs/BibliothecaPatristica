#!/usr/bin/env python3
"""Extract a bounded, evidence-preserving title-page catalogue, without inference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.title_page_experiment import main

if __name__ == '__main__':
    main()

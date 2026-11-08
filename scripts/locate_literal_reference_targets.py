#!/usr/bin/env python3
"""Locate existing literal reference candidates using an explicit editorial profile."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.literal_target_links import main
if __name__=='__main__':
    main()

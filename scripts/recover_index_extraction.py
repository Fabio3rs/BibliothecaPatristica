#!/usr/bin/env python3
"""Refine literal clauses and assemble explicitly reviewed continuations offline."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.index_extraction_recovery import main
if __name__=='__main__':
    main()

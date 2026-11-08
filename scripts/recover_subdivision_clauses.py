#!/usr/bin/env python3
"""Recover literal reference clauses and cross-references in a separate experiment."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.subdivision_clause_recovery import main
if __name__=='__main__':
    main()

#!/usr/bin/env python3
"""Extract regional index candidates missing from the search experiment."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.unrepresented_region_recovery import main
if __name__=='__main__':
    main()

#!/usr/bin/env python3
"""Run the isolated global mechanical file-candidate experiment."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.global_target_survey import main

if __name__=='__main__':
    main()

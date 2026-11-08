#!/usr/bin/env python3
"""Read-only comparison of link experiments and preparation of explicit review queues."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.link_next_steps_review import main

if __name__ == '__main__':
    main()

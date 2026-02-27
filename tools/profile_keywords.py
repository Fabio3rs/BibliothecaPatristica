#!/usr/bin/env python3
"""Profile de `extract_citations` usando cProfile.

Usage:
  python tools/profile_keywords.py --input dumptrecho.log --keywords 5000 --cats 2000 --top 30

Gera resumo das funções mais consumidas.
"""
from __future__ import annotations
import argparse
import random
import time
import pstats
import cProfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUMP_PATH = PROJECT_ROOT / "dumptrecho.log"


def load_seed(path: Path, max_lines: int | None = None) -> list[str]:
    items = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            parts = line.strip().split("|")
            if len(parts) >= 2 and parts[1].strip():
                items.append(parts[1].strip())
            if len(parts) >= 3 and parts[2].strip():
                items.append(parts[2].strip())
            if len(parts) >= 4 and parts[3].strip():
                items.append(parts[3].strip())
    # unique preserving order
    seen = set()
    out = []
    for it in items:
        k = it.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def expand(seed: list[str], target: int) -> list[str]:
    out = []
    n = len(seed)
    if n == 0:
        return out
    for i in range(target):
        s = seed[i % n]
        # small variation
        if i % 7 == 0:
            s = s + " " + str((i // 7) % 97)
        out.append(s)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default=str(DUMP_PATH))
    p.add_argument("--keywords", type=int, default=5000)
    p.add_argument("--cats", type=int, default=2000)
    p.add_argument("--top", type=int, default=30)
    args = p.parse_args()

    seed = load_seed(Path(args.input))
    print(f"Seed unique: {len(seed)}")
    kw = expand(seed, args.keywords)
    cats = expand(seed, args.cats)
    print(f"Prepared payload: keywords={len(kw)} cats={len(cats)}")

    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    # import both implementations
    from keywords_serial import extract_citations as extract_citations_orig
    from scripture_ref_normalizer import extract_citations_from_value_cached, extract_citations_from_value_ac

    payload = {"keywords": kw, "categorias": {"obras_citadas": cats}}

    # Warmup
    print("Warmup...")
    _ = extract_citations_orig({"keywords": kw[:100], "categorias": {"obras_citadas": cats[:50]}})

    # 1) Profile original extract_citations (regex-based)
    pr1 = cProfile.Profile()
    print("Profiling original extract_citations()...")
    pr1.enable()
    t0 = time.perf_counter()
    cleaned1, issues1 = extract_citations_orig(payload)
    t1 = time.perf_counter()
    pr1.disable()
    print(f"original extract_citations completed in {t1-t0:.3f}s; issues={issues1}")

    # 2) Profile alternative: call extractor per item using cached wrapper
    # This simulates replacement of the inner calls by AC-based variant
    pr2 = cProfile.Profile()
    print("Profiling AC-based extractor per item...")
    pr2.enable()
    t2 = time.perf_counter()
    # run AC extractor over all category items and keywords
    all_anchors = []
    for idx, raw in enumerate(cats):
        recs = extract_citations_from_value_ac(raw, source_kind="obras_citadas", source_path=f"categorias.obras_citadas[{idx}]", support_mode=False)
        all_anchors.extend(recs)
    all_kw = []
    for idx, raw in enumerate(kw):
        recs = extract_citations_from_value_ac(raw, source_kind="keywords", source_path=f"keywords[{idx}]", support_mode=True)
        all_kw.extend(recs)
    t3 = time.perf_counter()
    pr2.disable()
    print(f"AC-based extraction completed in {t3-t2:.3f}s; anchors={len(all_anchors)} keyword_hits={len(all_kw)}")

    print('\n--- Original profile (top) ---')
    pstats.Stats(pr1).strip_dirs().sort_stats("cumtime").print_stats(args.top)
    print('\n--- AC-based profile (top) ---')
    pstats.Stats(pr2).strip_dirs().sort_stats("cumtime").print_stats(args.top)


if __name__ == '__main__':
    main()

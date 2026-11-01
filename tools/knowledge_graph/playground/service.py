"""Shared CLI/UI application boundary; no inference until execute is called."""
from __future__ import annotations

from pathlib import Path
from .common import ROOT, DEFAULT_DB
from .snapshot_v2 import (benchmark_catalog, demo_snapshot, guard_holdout, list_generations,
                          load_benchmark, load_page)
from .storage import Store


class Playground:
    def __init__(self, *, db=DEFAULT_DB, summaries_db=ROOT / 'data/patristica_resumos.db',
                 indices_db=ROOT / 'data/patristic_indices.db', corpus_root=ROOT / 'teste',
                 benchmark_dir=ROOT / '.work/kg_benchmark_v1', ocr_versions_db=ROOT / 'data/ocr_versions.db'):
        self.summaries_db, self.indices_db = Path(summaries_db), Path(indices_db)
        self.corpus_root, self.benchmark_dir = Path(corpus_root), Path(benchmark_dir)
        self.ocr_versions_db = Path(ocr_versions_db)
        self.store = Store(db, (summaries_db, indices_db, ocr_versions_db))

    def load(self, *, page=None, generation_id=None, run_id=None, benchmark_id=None,
             demo=False, evaluation=False, allow_source_drift=False):
        if demo:
            snapshot = demo_snapshot()
        elif benchmark_id:
            snapshot = load_benchmark(self.benchmark_dir, benchmark_id, self.corpus_root, evaluation)
        else:
            guard_holdout(self.benchmark_dir, page, evaluation)
            snapshot = load_page(self.summaries_db, self.corpus_root, page,
                                 generation_id=generation_id, run_id=run_id, indices_db=self.indices_db,
                                 ocr_versions_db=self.ocr_versions_db, allow_source_drift=allow_source_drift)
        return self.store.case(snapshot)

    def generations(self, page, evaluation=False):
        guard_holdout(self.benchmark_dir, page, evaluation)
        return list_generations(self.summaries_db, page)

    def catalog(self, evaluation=False):
        return benchmark_catalog(self.benchmark_dir, evaluation)

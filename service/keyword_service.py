"""HTTP service exposing KeywordIntegrityChecker via FastAPI.

Endpoints
- POST /check : single term
- POST /batch : list of terms
- GET  /health: resource/caches status
- GET  /cache/stats: cache sizes; optional reset when allowed

CLI
    python -m service.keyword_service --host 0.0.0.0 --port 8000 --workers 4 --threads 8
"""

from __future__ import annotations

import argparse
import os
import pickle
from dataclasses import asdict
from pathlib import Path
import sys
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from keyword_integrity import (
    ValidationEvidence,
    KeywordIntegrityChecker,
    batch_check_keywords,
    get_checker,
)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


DEFAULT_BATCH_WORKERS = min(32, (os.cpu_count() or 1) * 2)
ALLOW_CACHE_RESET = _bool_env("KW_ALLOW_CACHE_RESET", False)
CACHE_PATH = Path(os.getenv("KW_CACHE_PATH", "")) if os.getenv("KW_CACHE_PATH") else None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CheckRequest(BaseModel):
    term: str
    language_hint: Optional[str] = None
    is_canon: bool = False


class BatchRequest(BaseModel):
    terms: List[str]
    language_hint: Optional[str] = None
    is_canon_flags: Optional[List[bool]] = None


class EvidenceResponse(BaseModel):
    raw_term: str
    normalized_term: str
    status: str
    reasons: List[str]
    lemma: Optional[str] = None
    language: str = "unknown"
    confidence: float = 0.0
    script_flags: dict = Field(default_factory=dict)
    matched_vocab: bool = False
    matched_stopword: bool = False
    control_chars_found: bool = False
    tokens: Optional[List[dict]] = None


class CacheStats(BaseModel):
    evidence: int
    lemma: int


class HealthResponse(BaseModel):
    cltk_loaded: bool
    embeddings: List[str]
    lexica: List[str]
    cache_sizes: CacheStats
    missing_resources: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evidence_to_response(ev: ValidationEvidence, include_tokens: bool = False) -> EvidenceResponse:
    tokens_payload = None
    if include_tokens and ev.tokens:
        tokens_payload = [
            {
                "raw": t.raw,
                "normalized": t.normalized,
                "corrected": t.corrected,
                "lemma": t.lemma,
                "language": t.language,
                "status": t.status.value,
                "reasons": t.reasons,
            }
            for t in ev.tokens
        ]

    return EvidenceResponse(
        raw_term=ev.raw_term,
        normalized_term=ev.normalized_term,
        status=ev.status.value,
        reasons=list(ev.reasons),
        lemma=ev.lemma,
        language=ev.language,
        confidence=ev.confidence,
        script_flags=ev.script_flags,
        matched_vocab=ev.matched_vocab,
        matched_stopword=ev.matched_stopword,
        control_chars_found=ev.control_chars_found,
        tokens=tokens_payload,
    )


def _load_cache_from_disk(checker: KeywordIntegrityChecker) -> None:
    if not CACHE_PATH or not CACHE_PATH.exists():
        return
    try:
        with CACHE_PATH.open("rb") as fh:
            data = pickle.load(fh)
        evidence_cache = data.get("evidence", {})
        lemma_cache = data.get("lemma", {})
        if isinstance(evidence_cache, dict):
            checker.evidence_cache.update(evidence_cache)
        if isinstance(lemma_cache, dict):
            checker.lemma_cache.update(lemma_cache)
    except Exception:
        pass


def _dump_cache_to_disk(checker: KeywordIntegrityChecker) -> None:
    if not CACHE_PATH:
        return
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"evidence": checker.evidence_cache, "lemma": checker.lemma_cache}
        with CACHE_PATH.open("wb") as fh:
            pickle.dump(payload, fh)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    checker = get_checker()  # warm-up load
    _load_cache_from_disk(checker)

    app = FastAPI(title="Keyword Integrity Service", version="1.0.0")

    @app.on_event("shutdown")
    async def _shutdown_event():  # pragma: no cover - side effect only
        _dump_cache_to_disk(checker)

    @app.post("/check", response_model=EvidenceResponse)
    async def check_keyword(req: CheckRequest, expand_tokens: bool = Query(False)) -> EvidenceResponse:
        ev = checker.check_keyword_integrity(
            req.term, is_canon_name=req.is_canon, language_hint=req.language_hint
        )
        return _evidence_to_response(ev, include_tokens=expand_tokens)

    @app.post("/batch", response_model=List[EvidenceResponse])
    async def batch_keywords(req: BatchRequest, expand_tokens: bool = Query(False)) -> List[EvidenceResponse]:
        flags = req.is_canon_flags if req.is_canon_flags else None
        workers = int(os.getenv("KW_BATCH_WORKERS", DEFAULT_BATCH_WORKERS))
        evidences = batch_check_keywords(
            req.terms,
            is_canon_flags=flags,
            checker=checker,
            language_hint=req.language_hint,
            num_workers=workers,
            parallel=True,
        )
        return [_evidence_to_response(ev, include_tokens=expand_tokens) for ev in evidences]

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        cltk_loaded = bool(checker.cltk and checker.cltk.available_codes)
        return HealthResponse(
            cltk_loaded=cltk_loaded,
            embeddings=sorted(checker.embedding_models.keys()),
            lexica=sorted(checker.lexicon_sets.keys()),
            cache_sizes=CacheStats(
                evidence=len(checker.evidence_cache), lemma=len(checker.lemma_cache)
            ),
            missing_resources=sorted(checker.missing_resources),
        )

    @app.get("/cache/stats", response_model=CacheStats)
    async def cache_stats(reset: bool = Query(False)) -> CacheStats:
        if reset:
            if not ALLOW_CACHE_RESET:
                raise HTTPException(status_code=403, detail="Cache reset not allowed")
            checker.evidence_cache.clear()
            checker.lemma_cache.clear()
        return CacheStats(evidence=len(checker.evidence_cache), lemma=len(checker.lemma_cache))

    return app


app = create_app()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Keyword Integrity HTTP service")
    parser.add_argument("--host", default=os.getenv("KW_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("KW_PORT", 8000)))
    parser.add_argument("--workers", type=int, default=int(os.getenv("KW_WORKERS", 2)))
    parser.add_argument("--threads", type=int, default=int(os.getenv("KW_THREADS", DEFAULT_BATCH_WORKERS)))
    args = parser.parse_args()

    uvicorn.run(
        "service.keyword_service:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()

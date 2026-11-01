"""Shared provenance, retrieval and sampling primitives. No model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resumo_v2 import normalize_match_text, sha256_text
from tools.corpus_utils import page_number, resolve_page_file
from tools.ocr_xml_utils import parse_ocr_xml_page

VERSION = "kg-benchmark-discovery-v1.0"
DEFAULT_OUT = ROOT / ".work/kg_benchmark_v1"
FIELDS = (
    "id run_id documento pagina_num pagina_file previous_generation_id "
    "previous_chain_hash source_hash output_hash chain_hash schema_version "
    "prompt_version provider model status is_current tainted_by_page "
    "primary_page_kind page_kinds_json segments_json contributors_json "
    "summary_display_pt cumulative_summary source_conflicts_json "
    "static_analysis_json validation_issues_json ocr_result_id facsimile_used"
).split()

PATTERNS = {
    "entities_bibliography": r"\b(autor(?:ia)?|atribui\w*|atribuid\w*|editor\w*|tradutor\w*|manuscrit\w*|cita\w*|concilio\w*|escola\w*|comentar\w*)\b",
    "argumentative_claim": r"\b(refut\w*|obje\w*|argument\w*|defend\w*|nega\w*|contesta\w*|opini\w*|afirma\w*|conclu\w*)\b",
    "historical_event": r"\b(imperador\w*|reinad\w*|concilio\w*|exilio\w*|batalh\w*|elei\w*|morte|sucess\w*|cronolog\w*|seculo\w*)\b|\b(?:ano|em) [1-9][0-9]{2,3}\b",
    "biblical_intertextuality": r"\b(tipolog\w*|profeci\w*|cumprimento|alegori\w*|evangel\w*|testamento|salmo\w*|escritur\w*)\b",
    "philosophical_concept": r"\b(substancia\w*|essencia\w*|ousia|hipostase\w*|logos|silogismo\w*|aristotel\w*|platon\w*|defin\w*|etica|causal\w*|livre arbitrio|genero|especie|logica|medicin\w*|astronom\w*|geometr\w*)\b",
    "entity_resolution": r"\b(gratia|graca|logos|ousia|substantia|sub[s]?tancia|hipostase\w*|essencia|homonim\w*|equivoc\w*|denomina\w*|signific\w*|traduz\w*|gregorio|joao|cirilo|dionisio|eusebio)\b",
    "difficult_negative": r"\b(nao|refut\w*|neg\w*|alegori\w*|hipotet\w*|incert\w*|duvidos\w*|adversario\w*|fragment\w*|ilegivel|corrompid\w*|interromp\w*|lacuna\w*)\b",
}
REGEX = {k: re.compile(v) for k, v in PATTERNS.items()}
TERM_PATTERNS = {
    "grace": r"\b(?:graça|gratia)\b",
    "logos": r"\b(?:logos|λόγος|λόγον|λόγου)\b",
    "substance": r"\b(?:substância|substantia|ousia|οὐσία|essência)\b",
    "hypostasis": r"\b(?:hipóstase|hypostasis|ὑπόστασις)\b",
    "nature": r"\b(?:natureza|natura|φύσις)\b",
    "free_will": r"\b(?:livre.arbítrio|liberum arbitrium)\b",
    "gregory": r"\b(?:Gregório|Gregorius)\b",
    "john": r"\b(?:João|Joannes|Ioannes)\b",
    "cyril": r"\b(?:Cirilo|Cyrillus)\b",
    "dionysius": r"\b(?:Dionísio|Dionysius)\b",
    "eusebius": r"\b(?:Eusébio|Eusebius)\b",
    "plato": r"\b(?:Platão|Plato|Platonis)\b",
    "aristotle": r"\b(?:Aristóteles|Aristoteles|Aristotelis)\b",
    "origen": r"\b(?:Orígenes|Origenes|Origenis)\b",
    "paul": r"\b(?:Paulo|Paulus|Pauli)\b",
    "council_nicaea": r"\b(?:Niceia|Nicéia|Nicæa|Nicaea)\b",
}
TERMS = {k: re.compile(v, re.I) for k, v in TERM_PATTERNS.items()}


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_ocr(path: Path):
    """Preserve line endings because raw OCR offsets are content-addressed."""
    return path.read_bytes().decode("utf-8")


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(stable_json(row) + "\n")


def readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def validate_schema(con):
    columns = {r[1] for r in con.execute("PRAGMA table_info(resumo_generations)")}
    missing = set(FIELDS) - columns
    if missing:
        raise ValueError(f"Missing summary-v2 columns: {sorted(missing)}; no legacy fallback")
    return [dict(r) for r in con.execute("PRAGMA table_info(resumo_generations)")]


def parse_generation(row):
    row = dict(row)
    for name, default in (("segments", []), ("contributors", []), ("static_analysis", {}),
                          ("source_conflicts", []), ("validation_issues", []), ("page_kinds", [])):
        value = json.loads(row[name + "_json"])
        if not isinstance(value, type(default)):
            raise ValueError(f"Invalid {name} in generation {row['id']}")
        row[name] = value
    for segment in row["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("summary"), str):
            raise ValueError(f"Invalid segment in generation {row['id']}")
    return row


def eligibility(scope, run_ids=()):
    base = "status IN ('valid','metadata_pending') AND tainted_by_page IS NULL"
    if scope == "promoted":
        return base + " AND is_current=1", []
    if scope != "shadow" or not run_ids:
        raise ValueError("Shadow scope requires explicit --shadow-run-ids-file; scopes cannot mix")
    return base + " AND is_current=0 AND run_id IN (" + ",".join("?" for _ in run_ids) + ")", list(run_ids)


def classify(row):
    summary = " ".join(s["summary"] for s in row["segments"])
    folded = normalize_match_text(summary)
    hits = {k: sorted(set(r.findall(folded)))[:12] for k, r in REGEX.items()}
    categories = [k for k, v in hits.items() if v]
    a = row["static_analysis"]
    risks = []
    if row["status"] == "metadata_pending":
        risks.append("metadata_pending")
    if row["source_conflicts"] or a.get("index_ambiguous"):
        risks.append("metadata_conflict")
    if len(row["segments"]) > 1:
        risks.append("multiple_segments")
    if len({s.get("work_key") for s in row["segments"] if s.get("work_key")}) > 1:
        risks.append("multiple_works")
    if a.get("is_empty") or row["primary_page_kind"] in {"administrative", "illegible"}:
        risks.append("minimal_or_illegible")
    if a.get("alpha_ratio", 1) < .65 or a.get("single_token_ratio", 0) > .12:
        risks.append("weak_ocr")
    if re.search(r"\b(refut|neg|alegori|hipotet|incert|duvidos|adversario|interromp|lacuna)\w*", folded):
        risks.append("attribution_or_nonliteral")
    if a.get("scripture_candidate_count", 0) and "biblical_intertextuality" not in categories:
        categories.append("biblical_intertextuality")
    if risks and "difficult_negative" not in categories:
        categories.append("difficult_negative")
    return sorted(categories or ["general_control"]), hits, risks


def rank(seed, key):
    return int(digest(f"{seed}:{key}")[:16], 16)


def physical_key(record):
    s = record["source"]
    return s["documento"], s["pagina_num"]


def work_keys(record):
    return {s["work_key"] for s in record["text"]["page_segments"] if s.get("work_key")}


def resolve_source(corpus_root, row):
    """Honor stored filename. A numerical fallback is only accepted by byte hash later."""
    doc, name = row["documento"], row["pagina_file"]
    if Path(doc).name != doc or doc in {".", ".."}:
        return None, "unsafe_document"
    directory = corpus_root / doc / "text"
    if Path(name).name != name:
        return None, "unsafe_filename"
    exact = directory / name
    if page_number(exact) != row["pagina_num"]:
        return None, "physical_number_mismatch"
    if exact.is_file():
        if not exact.resolve().is_relative_to(corpus_root.resolve()):
            return None, "unsafe_symlink"
        return exact, "stored_filename"
    path, ambiguous = resolve_page_file(directory, volume_id=doc,
                                       page_num=row["pagina_num"], suffixes=(".txt",))
    if ambiguous:
        return None, "ambiguous_numeric_fallback"
    if path and not path.resolve().is_relative_to(corpus_root.resolve()):
        return None, "unsafe_symlink"
    return path, "numeric_fallback_requires_historical_hash" if path else "missing"


def excerpt(raw, summary, limit=2000):
    """A retrieval window, never a semantic verification of translated evidence."""
    if len(raw) <= limit:
        return raw, 0, len(raw)
    tokens = set(re.findall(r"[A-Za-zÀ-ÿἀ-῾]{5,}", summary))
    anchors = sorted(m.start() for t in sorted(tokens) for m in list(re.finditer(re.escape(t), raw, re.I))[:3])
    if not anchors:
        anchors = [m.start() for m in re.finditer(
            r"refut|substant|ousia|οὐσί|λόγο|hypostas|gratia|concil|Aristot|Platon|negat|dicitur", raw, re.I)]
    center = max(anchors, key=lambda x: sum(abs(x-y) < limit // 2 for y in anchors)) if anchors else 0
    start = max(0, min(len(raw) - limit, center - limit // 3))
    return raw[start:start + limit], start, start + limit


def summary_quote(summary):
    end = re.search(r"[.!?](?:\s|$)", summary[100:])
    return summary[:100 + end.end()].rstrip() if end else summary


def text_shingles(text, width=5):
    tokens = normalize_match_text(text).split()
    return {" ".join(tokens[i:i+width]) for i in range(len(tokens)-width+1)}


class DuplicateIndex:
    """Bounded-pool inverted shingle index; headers/notes excluded from OCR comparison."""
    def __init__(self, threshold=.85):
        self.threshold = threshold
        self.exact = {}
        self.postings = {}
        self.sets = {}

    def check_add(self, key, text):
        normalized = normalize_match_text(text)
        exact = digest(normalized)
        if normalized and exact in self.exact:
            return self.exact[exact], 1.0
        shingles = text_shingles(text)
        if len(shingles) >= 20:
            overlaps = Counter(other for shingle in shingles for other in self.postings.get(shingle, ()))
            for other, n in overlaps.most_common():
                similarity = n / len(shingles | self.sets[other])
                if similarity >= self.threshold:
                    return other, similarity
        self.exact[exact] = key
        self.sets[key] = shingles
        for shingle in shingles:
            self.postings.setdefault(shingle, []).append(key)
        return None


def common_arguments(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--db", type=Path, default=ROOT / "data/patristica_resumos.db")
    p.add_argument("--corpus-root", type=Path, default=ROOT / "teste")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--seed", type=int, default=20261031)
    return p


def distributions(records):
    result = {}
    for name, values in {
        "split": [r.get("split", "candidate") for r in records],
        "collection": [r["source"]["documento"][:2] for r in records],
        "volume": [r["source"]["documento"] for r in records],
        "quality_stratum": [r["source"]["status"] for r in records],
        "page_kind": [r["source"]["primary_page_kind"] for r in records],
        "difficulty": [r["selection"]["difficulty"] for r in records],
        "category": [c for r in records for c in r["selection"]["categories"]],
        "ocr_support": [r["evidence"]["ocr_support"] for r in records],
    }.items():
        result[name] = dict(sorted(Counter(values).items()))
    result["distinct_works"] = len(set().union(*(work_keys(r) for r in records))) if records else 0
    result["pages_without_work_key"] = sum(not work_keys(r) for r in records)
    return result

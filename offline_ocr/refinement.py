"""Review-only bbox drafts and complementary passages from existing OCR versions.

Never invokes a model or writes to the corpus/database. Bbox drafts change only
attributes, and missing-text suggestions retain exact donor offsets and IDs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
import re
import unicodedata

from .diff_v2 import WORD, compare
from .layout import BlockParser, envelope
from .migne_hints import analyse_tsv
from .quality import content_reference

VERSION = "ocr-refinement-drafts-v1"
BBOX_POLICY = {"ngram": 3, "min_anchor_tokens": 12, "min_coverage": .85,
               "max_other_region_fraction": 0}
TAG = re.compile(r'''<bloco\b(?:[^>"']|"[^"]*"|'[^']*')*>''')
ATTRIBUTE = re.compile(r'''([\w:.-]+)\s*=\s*(["'])(.*?)\2''', re.DOTALL)


def _key(text):
    return unicodedata.normalize("NFC", text).lower()


def _ngrams(tokens, size):
    grams = defaultdict(list)
    for i in range(len(tokens) - size + 1):
        window = tokens[i:i + size]
        if len({t["block_id"] for t in window}) == 1:
            grams[tuple(t["key"] for t in window)].append(i)
    return grams


def bbox_draft(text, layout):
    parser = BlockParser(text)
    parser.feed(text)
    candidate = []
    for block in parser.blocks:
        candidate.extend({"key": _key(m.group()), "block_id": block["id"]}
                         for m in WORD.finditer(block["text"]))
    words = {w["id"]: w for w in layout["words"]}
    reference = []
    for block in layout["blocks"]:
        for word_id in block["word_ids"]:
            word = words[word_id]
            reference.extend({"key": _key(m.group()), "block_id": block["id"],
                              "word_id": word_id, "bbox": word["bbox"]} for m in WORD.finditer(word["text"]))
    size = BBOX_POLICY["ngram"]
    a, b = _ngrams(candidate, size), _ngrams(reference, size)
    matches = defaultdict(set)
    for gram in a.keys() & b.keys():
        if len(a[gram]) == len(b[gram]) == 1:
            for offset in range(size):
                matches[a[gram][0] + offset].add(b[gram][0] + offset)
    # Overlapping anchors must agree, and cannot consume an OCR occurrence twice.
    counts = Counter(next(iter(ids)) for ids in matches.values() if len(ids) == 1)
    links = {i: next(iter(ids)) for i, ids in matches.items()
             if len(ids) == 1 and counts[next(iter(ids))] == 1}
    proposals, patches = [], []
    for block in parser.blocks:
        indices = [i for i, t in enumerate(candidate) if t["block_id"] == block["id"]]
        support = [(i, links[i]) for i in indices if i in links]
        regions = Counter(reference[j]["block_id"] for _, j in support)
        coverage = len(support) / max(1, len(indices))
        item = {"block_id": block["id"], "before_bbox": block["bbox"], "status": "abstain",
                "anchor_tokens": len(support), "candidate_tokens": len(indices),
                "coverage": coverage, "physical_regions": dict(regions), "reasons": []}
        if len(support) < BBOX_POLICY["min_anchor_tokens"] or coverage < BBOX_POLICY["min_coverage"]:
            item["reasons"].append("insufficient_unique_text_anchors")
        if regions:
            dominant, count = regions.most_common(1)[0]
            if (len(support) - count) / len(support) > BBOX_POLICY["max_other_region_fraction"]:
                item["reasons"].append("text_spans_multiple_physical_regions_requires_segmentation")
            reference_order = [j for _, j in support]
            if reference_order != sorted(reference_order):
                item["reasons"].append("non_monotonic_text_anchors")
        if not item["reasons"]:
            selected = [reference[j] for _, j in support if reference[j]["block_id"] == dominant]
            bounds = envelope([t["bbox"] for t in selected])
            bbox = [math.floor(bounds[0]), math.floor(bounds[1]), math.ceil(bounds[2]), math.ceil(bounds[3])]
            item.update(after_bbox=bbox, source_word_ids=sorted({t["word_id"] for t in selected}),
                        status="unchanged" if bbox == block["bbox"] else "proposed",
                        reasons=["unique_text_anchors_within_one_physical_region"])
            if item["status"] == "proposed":
                tag = TAG.match(text, block["raw_start"])
                if tag is None:
                    raise ValueError("block_start_tag_unresolved")
                replacement = ",".join(map(str, bbox))
                old = tag.group()
                attribute = next((a for a in ATTRIBUTE.finditer(old) if a.group(1) == "bbox"), None)
                new = (old[:attribute.start(3)] + replacement + old[attribute.end(3):]
                       if attribute else old[:-1] + f' bbox="{replacement}">')
                patches.append((tag.start(), tag.end(), new))
        proposals.append(item)
    draft = text
    for start, end, new in sorted(patches, reverse=True):
        draft = draft[:start] + new + draft[end:]
    return draft, {"version": VERSION, "policy": BBOX_POLICY, "blocks": proposals,
                   "text_changed": False, "automatic_export": False,
                   "limitations": ["Boxes enclose matched TSV words, not independently verified ink.",
                                   "Sparse text, repeated passages and blocks spanning columns require review.",
                                   "This operation cannot recover a word absent from Tesseract or transcribe missing text."]}


def complementary_passages(base_text, donor_texts, layout):
    """Find donor spans for reference gaps; do not compose or select readings."""
    reference = content_reference(layout)
    base = compare(base_text, reference)
    donors = [(version_id, text, compare(text, reference)) for version_id, text in donor_texts]
    proposals = []
    exact = {"equal", "normalized_equal", "typography_variant"}
    for gap in base["omission_candidates"]:
        if gap["token_count"] < 3:
            continue
        target_ids = set(gap["token_ids"])
        offers = []
        for version_id, text, result in donors:
            links = [l for l in result["links"] if target_ids & set(l["reference_ids"])]
            if not links or any(l["status"] not in exact or set(l["reference_ids"]) - target_ids for l in links):
                continue
            if {i for l in links for i in l["reference_ids"]} != target_ids:
                continue
            ids = {i for l in links for i in l["candidate_ids"]}
            indices = [i for i, t in enumerate(result["candidate_tokens"]) if t["id"] in ids]
            tokens = [result["candidate_tokens"][i] for i in indices]
            if (not indices or indices[-1] - indices[0] + 1 != len(indices) or
                    len({t["block_id"] for t in tokens}) != 1):
                continue
            spans = [s for t in tokens for p in t["pieces"] for s in p["source_spans"]]
            start, end = min(s[0] for s in spans), max(s[1] for s in spans)
            offers.append({"version_id": version_id, "block_id": tokens[0]["block_id"],
                           "source_span": [start, end], "text": text[start:end]})
        if offers:
            proposals.append({"reference_text": gap["text"], "context_before": gap["context_before"],
                              "context_after": gap["context_after"], "donors": offers,
                              "status": "review_required", "automatic_merge": False,
                              "reason": "reference_gap_with_existing_donor_text_not_a_verified_omission"})
    return {"version": VERSION, "proposals": proposals, "automatic_merge": False,
            "limitations": ["Agreement between runs is not independent evidence: models may share the same OCR error.",
                            "No majority vote, substitutions or automatic insertion are performed.",
                            "Offsets refer to original donor XML; verify wording, location and insertion point in the image."]}


def render_review(report):
    lines = [f"Rascunho OCR — {report['page_key']} — versão base {report['base_version_id']}",
             "Nenhuma exportação automática. draft.xml preserva o texto da versão base.",
             "Trechos complementares são sugestões para conferência, sem inserção no rascunho.", ""]
    for block in report.get("bbox_review", {}).get("blocks", []):
        lines.append(f"BBox {block['block_id']}: {block['status']}; {block['before_bbox']} -> {block.get('after_bbox')}")
        lines.append(f"  Âncoras: {block['anchor_tokens']}/{block['candidate_tokens']}; motivos: {', '.join(block['reasons'])}")
    for index, proposal in enumerate(report.get("merge_review", {}).get("proposals", []), 1):
        lines.extend(["", f"Trecho complementar {index}: {proposal['reference_text']}",
                      f"  Contexto: {proposal['context_before']} [...] {proposal['context_after']}"])
        for donor in proposal["donors"]:
            lines.append(f"  Versão {donor['version_id']}, bloco {donor['block_id']}, offsets {donor['source_span']}: {donor['text']}")
    return "\n".join(lines) + "\n"


def refine_run(run_dir, page_key, version_id, output_dir, *, repair_bboxes=False, merge_candidates=False):
    from .selection import (_check_evidence, _history, _history_fingerprint, _load_run,
                            _reference_snapshot, canonical, digest, journal, read_only, POLICY_VERSION)

    run_dir, output_dir = Path(run_dir).resolve(), Path(output_dir).resolve()
    manifest, entries = _load_run(run_dir)
    if manifest["policy"]["version"] != POLICY_VERSION:
        raise ValueError("policy_changed_rescan_required")
    if not repair_bboxes and not merge_candidates:
        raise ValueError("Choose repair_bboxes or merge_candidates")
    entry = next((e for e in entries if e["page_key"] == page_key), None)
    if entry is None or version_id not in {c["id"] for c in entry.get("candidates", [])}:
        raise ValueError("version_not_in_eligible_page_history")
    if output_dir.is_relative_to(Path(manifest["base_dir"]).resolve()):
        raise ValueError("draft_directory_must_be_outside_corpus")
    con = read_only(manifest["db_path"])
    try:
        rows = _history(con, entry["volume_id"], entry["page_num"])
    finally:
        con.close()
    if _history_fingerprint(rows) != entry["history_fingerprint"]:
        raise ValueError("history_changed")
    if digest(Path(entry["image_path"]).read_bytes()) != entry["reference"]["image_hash"]:
        raise ValueError("image_changed")
    for candidate in entry["candidates"]:
        _check_evidence(run_dir, candidate)
    tsv = _reference_snapshot(run_dir, entry)
    layout = analyse_tsv(tsv, entry["reference"]["image_size"])
    texts = {r["id"]: r["text_content"] for r in rows if r["id"] in {c["id"] for c in entry["candidates"]}}
    base = texts[version_id]
    report = {"version": VERSION, "source_run": str(run_dir), "page_key": page_key,
              "base_version_id": version_id, "base_text_sha256": digest(base.encode()),
              "reference_sha256": digest(tsv.encode()), "automatic_export": False,
              "source_versions": [{"id": c["id"], "text_sha256": c["text_hash"], "engine": c["engine"],
                                   "model": c["model"]} for c in entry["candidates"]]}
    draft = base
    if repair_bboxes:
        draft, report["bbox_review"] = bbox_draft(base, layout)
    if merge_candidates:
        report["merge_review"] = complementary_passages(base, [(i, t) for i, t in texts.items() if i != version_id], layout)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "base.xml").write_text(base, encoding="utf-8")
    (output_dir / "draft.xml").write_text(draft, encoding="utf-8")
    report["draft_sha256"] = digest(draft.encode())
    (output_dir / "review.json").write_text(canonical(report) + "\n", encoding="utf-8")
    (output_dir / "review.txt").write_text(render_review(report), encoding="utf-8")
    journal(output_dir, "draft_created", page_key=page_key, base_version_id=version_id,
            draft_sha256=report["draft_sha256"], automatic_export=False)
    return report

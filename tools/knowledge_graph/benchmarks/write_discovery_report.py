#!/usr/bin/env python3
"""Render the discovery report from actual artifacts, without new corpus scans."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.knowledge_graph.benchmarks.common import common_arguments, digest, read_json, read_jsonl, write_json


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows)
    return "\n".join(lines)


def main():
    args = common_arguments(__doc__).parse_args()
    out = args.output_dir
    profile = read_json(out / "corpus_profile.json")
    design = read_json(out / "sample_design.json")
    manifest = read_json(out / "manifest.json")
    validation = read_json(out / "validation_report.json")
    pool = list(read_jsonl(out / "candidate_pool.jsonl"))
    pages = list(read_jsonl(out / "selected_pages.jsonl"))
    pairs = list(read_jsonl(out / "resolution_pairs.jsonl"))
    inspected = [r for r in pages if r["review"]["status"] == "source_inspected_pending_independent_review"]
    changed = [r for r in pool if "ocr_changed_since_generation" in r["selection"]["risk_flags"]]
    d = profile["distributions"]
    auxiliary = profile.get("auxiliary_sources", {})
    lines = ["# Historical Knowledge Graph benchmark discovery v1", "",
             "## 1. Scope and inspected datasets", "",
             f"This is a real-corpus **experimental {profile['scope']} discovery benchmark**, not adjudicated gold. "
             f"The read-only SQLite snapshot contains {profile['eligible_generations']:,} eligible generations in the explicitly selected scope. "
             f"There are **{profile['promoted_eligible']} eligible promoted generations**. No legacy summary or keywords_v1 was used as benchmark text.", "",
             "Inspected implementations: `resumo_v2.py`, the v2 path in `resumo_serial.py`, "
             "`docs/pipeline_resumos_v2.md`, `tools/report_resumos_v2.py`, `tools/corpus_utils.py`, "
             "`tools/ocr_xml_utils.py`, scripture normalization/extraction utilities, and "
             "`docs/pipeline_citacoes_biblicas_deterministicas.md`. Reused the repository’s "
             "filename page-number resolution, XML parser, text normalization, v2 output-hash function, "
             "and read-only `ScanTask`/`scan_file_task` scripture detector. No production pipeline was altered.", "",
             f"Actual database: `{profile['database']}` ({profile['database_size_bytes']:,} bytes). "
             "`PRAGMA table_info(resumo_generations)` was checked before querying. Its primary key is `id`, "
             "exported as `generation_id`; `work_key` resides in segment JSON, not a generation column. "
             "The database also contains legacy and embedding tables, which were not used as evidence.", "",
             "`data/patristic_indices.db` was inspected for `works`, `volumes`, `index_strings`, and "
             f"`index_translations`. Its {auxiliary.get('index_work_rows', 'unrecorded')} work rows carry raw author/title, physical filename anchors, "
             "editorial page ranges and confidence. Selected work metadata is looked up only by an existing "
             "segment work_key. Historical OCR content is checked by `ocr_result_id` in the inspected "
             "`data/ocr_versions.db.ocr_results` schema. Current source files are under `teste/<volume>/text/`.", "",
             "## 2. Eligibility and structural profile", "",
             table(["Status", "Current", "Tainted", "Generations"],
                   [(r["status"], r["is_current"], r["tainted"], r["total"]) for r in profile["all_generation_statuses"]]), "",
             "The default tooling query uses `is_current=1`, `status IN ('valid','metadata_pending')`, "
             "and `tainted_by_page IS NULL`. Because the promoted set is empty, this execution explicitly "
             f"selected {len(profile['selected_shadow_run_ids'])} shadow runs recorded in `shadow_runs.json` "
             "and `shadow_run_inventory.json`. The scopes cannot silently mix. Provisional/tainted generations "
             "were excluded. `metadata_pending` remains visible in every record and in each split.", "",
             table(["Collection", "Eligible generations", "Volumes"],
                   [(c, n, sum(v.startswith(c) for v in d["volumes"])) for c, n in d["collections"].items()]), "",
             "No PO v2 generations were available in the inspected database. The requested approximately "
             "288,000-page total corpus is broader than the available v2 subset; no OCR-wide fallback scan was made.", "",
             table(["Page kind", "Eligible generations"], sorted(d["page_kinds"].items())), "",
             table(["Segments on page", "Generations"], sorted(d["segments_per_page"].items(), key=lambda x:int(x[0]))), "",
             table(["Summary length (characters)", "Generations"], d["summary_length_chars"].items()), "",
             f"Distinct generation work keys: **{len(d['works'])}**. Distinct author strings in the "
             f"generation’s containing-work hints: **{len(d.get('authors_from_generation_index_hints', {}))}**. "
             "These are metadata strings, not resolved historical identities. All work/author/volume counts "
             "are retained in `corpus_profile.json` rather than reduced to famous authors.", ""]
    for name in ("work_key_presence", "metadata_conflicts", "mixed_scripts", "is_xml", "is_empty", "column_count", "alpha_ratio", "single_token_ratio"):
        lines.append(f"- `{name}`: {d.get(name, {})}")
    refs = d["scripture_reference_candidates"]
    ref_pages = sum(n for k, n in refs.items() if k.isdigit() and int(k) > 0)
    lines += ["", f"Stored deterministic scripture candidates occur on **{ref_pages:,}** eligible generations. "
              "The complete density distribution is in the profile; candidates do not establish exegetical links. "
              "Current selected OCR was also rescanned using the existing detector, retaining ambiguity, "
              "block type and printed-page hypotheses separately from physical page numbers.", "",
              "## 3. Reproducible candidate generation and grounding", "",
              f"Fixed seed: **{design['seed']}**. A single SQLite read transaction streams only "
              "needed v2 columns, omitting embeddings, raw model responses and legacy summaries. "
              "It accumulates counters plus bounded hash-priority reservoirs by category × collection × status, "
              "page kind, volume and actual terminology occurrences. Regular expressions retrieve, never verify, "
              "entities, arguments, events, scripture, concepts and difficult cases. No embedding/KNN or LLM calls were needed.", "",
              f"The bounded shortlist contained **{profile['shortlist_size']:,}** generations. "
              f"Round-robin volume/category/status retrieval produced **{len(pool):,}** initial candidates. "
              "Only shortlisted files were opened. The stored filename takes precedence; a numeric fallback "
              "is allowed for selection only if historical OCR proves identical. Path traversal, mismatched "
              "physical suffixes, ambiguous locators, missing/invalid UTF-8 and malformed XML are recorded.", "",
              f"**{len(changed)}** candidates have current OCR different from the generation-linked historical "
              "OCR and were excluded from selection. Their original candidate provenance remains available "
              "for audit. Such differences are not repaired or silently summarized from newer text.", "",
              table(["Retrieval category", "Eligible generations matching heuristic", "Candidate pool", "Selected"],
                    [(c, d["heuristic_categories"].get(c, 0), profile["candidate_distribution"]["category"].get(c, 0), design["distribution"]["category"].get(c, 0))
                     for c in sorted(set(d["heuristic_categories"]) | set(design["distribution"]["category"]))]), "",
              "Counts overlap and reflect retrieval labels, not established historical correctness. "
              f"All {len(pages)} selected pages have exact frozen OCR and generation locators, hashes, full v2 segments "
              f"and raw OCR snapshots. **{len(inspected)}** selected pages received substantive source reading "
              f"for a bounded claim or issue during this discovery; **{len(pages)-len(inspected)}** remain "
              "mechanically prepared candidates awaiting semantic review. This distinction is explicit in "
              "`evidence.inspection`, `evidence.scope` and `review.status`; the uninspected remainder is not "
              "presented as source-verified gold.", "",
              "`source_hash` is the existing composite of prompt, raw OCR, historical OCR ID, index hints, "
              "scripture evidence, active work and previous chain. Not all original inputs are fully persisted, "
              "so it is preserved and checked against the frozen/live generation, not misrepresented as a raw "
              "file digest. Independently checkable hashes cover UTF-8 segment text, segments JSON, raw OCR "
              "bytes, generation snapshots, source code and artifacts. The v2 `output_hash` is recomputed "
              "with its actual NUL-separated formula. `cumulative_summary` is retained only in an explicitly "
              "labelled context object and is never used for candidate retrieval or summary evidence.", "",
              "## 4. Deduplication and sample allocation", "",
              "Exact normalized summary and raw OCR checks, plus five-token shingle Jaccard ≥0.85 "
              "on page-local summaries and parsed OCR body, prevent accidental duplicates. Cumulative "
              "summaries, headers and footers are not used as content similarity signals. Selected physical "
              "pages are unique. Rejections and duplicate source IDs are recorded. This is lexical near-duplicate "
              "detection, not proof against translated or heavily paraphrased transmission parallels.", "",
              f"Discovery rejection counts: `{profile['rejected_counts']}`. Additional exact OCR duplicates "
              f"excluded during selection: `{design.get('exact_ocr_duplicates_excluded', [])}`. "
              "Rejection logs retain duplicate source IDs; accidental duplicates are absent from the selected benchmark.", "",
              table(["Split", "Pages", "Valid", "Metadata pending", "PG", "PL"],
                    [(split, sum(r["split"] == split for r in pages),
                      sum(r["split"] == split and r["source"]["status"] == "valid" for r in pages),
                      sum(r["split"] == split and r["source"]["status"] == "metadata_pending" for r in pages),
                      sum(r["split"] == split and r["source"]["documento"].startswith("PG") for r in pages),
                      sum(r["split"] == split and r["source"]["documento"].startswith("PL") for r in pages))
                     for split in ("calibration", "representative", "difficult", "blind_holdout")]), "",
              f"The selected set spans **{len(design['distribution']['volume'])} volumes** and "
              f"**{design['distribution']['distinct_works']} existing work keys**; "
              f"**{design['distribution']['pages_without_work_key']} pages have no work key**. No keys were invented. "
              "The 24 calibration pages are also reserved for repeated inference to estimate stability; "
              "no inference has been run. `sample_design.json` enumerates their IDs.", "",
              table(["Selected page kind", "Pages"], design["distribution"]["page_kind"].items()), "",
              f"Difficulty labels: `{design['distribution']['difficulty']}`. OCR evidence statuses after "
              f"source reading: `{design['distribution']['ocr_support']}`. A verified evidence field applies "
              "only to the inspected quotation, not every statement on the page, modern historical truth, "
              "or gold annotation. Structural hard flags are provisional and can be revised.", "",
              "The representative split is deliberately diversity-balanced, not a probability-weighted "
              "estimate of the entire corpus. Editorial genres and metadata_pending are oversampled. "
              "Report evaluation by quality stratum and genre, and do not infer corpus-wide accuracy without "
              "a separate probability sample or weighting design.", "",
              f"Blind holdout reserves **{len(design['reserved_holdout_volumes'])} complete volumes**. "
              "No selected work key or catalog author+title family crosses its boundary, and adjacent pages "
              "from its volumes are excluded from development splits. No holdout semantic curation or prompt "
              "tuning was performed. Candidate artifacts contain holdout material, so isolation is procedural, "
              "not encryption; restrict packet access during later development.", "",
              "## 5. Grounded showcase", "",
              "These are actual source readings, with exact Portuguese summary quotations and separate "
              "original OCR quotations. The edition’s claims remain source-attributed and revisable.", ""]
    showcase_ids = {60887, 5031, 160125, 152577, 23270, 60227, 40094, 33478, 9067, 16239, 73004, 13603}
    for r in inspected:
        s, e = r["source"], r["evidence"]
        if s["generation_id"] not in showcase_ids:
            continue
        lines += [f"### {r['benchmark_id']}: {r['review']['showcase_title']}", "",
                  f"Generation **{s['generation_id']}**, run **{s['run_id']}**, **{s['scope']} / {s['status']}**, "
                  f"{s['documento']} physical page **{s['pagina_num']}**, segment **{s['segment_order']}**. "
                  f"File: `{s['ocr_path']}`. Work key: `{s['work_key']}`. "
                  f"Raw OCR evidence offsets: **{e['ocr_start']}–{e['ocr_end']}** (Unicode code points).", "",
                  r["selection"]["reason"], "", "Exact summary evidence:", "", "> " + e["summary_quote"].replace("\n", "\n> "), "",
                  "Original OCR excerpt (full exact span retained in JSONL):", "", "```text", e["ocr_quote"][:650], "```", "", e["notes"], ""]
    lines += ["## 6. Ambiguous, misleading and negative candidates", ""]
    for gid in (89403, 54956, 174892, 167301, 62985):
        r = next((r for r in inspected if r["source"]["generation_id"] == gid), None)
        if r is None:
            continue
        s = r["source"]
        lines += [f"- **{s['documento']} physical p. {s['pagina_num']}, generation {gid}**, `{s['ocr_path']}`: {r['evidence']['notes']}"]
    lines += ["", "A further concrete trap in PL011 physical p.637 is the summary’s 'edito de Maximiano' "
              "versus OCR *Maximianistis*, which concerns a group addressed by the decree; a graph must not "
              "invent an emperor as issuer from that wording. For PG005 p.699 the editor refuses to guarantee "
              "that all fragments reproduce the elders’ exact words. PL033 p.390 ends one letter and begins "
              "another addressed to a different bishop. These are attribution and segmentation tests, not noise "
              "to be flattened into one page-level author.", "",
              "## 7. Resolution pairs and groups", "",
              f"**{len(pairs)}** pairs are retained: **{sum(not p.get('deliberate_same_page_group', False) for p in pairs)}** "
              "cross-page pairs plus four local terminology/direction controls. They refer only to selected pages "
              "and never cross a split. One positive Aristotle pair was rejected after OCR inspection and "
              "remains in `rejected_resolution_pairs.jsonl`. No extra pages are counted in the 240.", "",
              table(["Provisional relationship", "Pairs"], sorted(Counter(p["provisional_relationship"] for p in pairs).items())), "",
              "Examples with explicit provisional evidence: Paul in the Titus material versus Paolo Giovio "
              "(DIFFERENT); Eusebius associated with the Ecclesiastical History versus Eusebius of Barcelona "
              "(DIFFERENT); Origen/Origenis in Hexapla and Jerome contexts (EQUIVALENT); Aristotle as author "
              "of Categories and Rhetoric (EQUIVALENT); singularitas/μονότης in a local gloss (EQUIVALENT); "
              "singularitas/unitas (RELATED, not interchangeable). Boethius’s homo/substantia pair and its "
              "deliberately reversed counterpart test NARROWER/BROADER under his classification of significant terms. "
              "The reversed pair is a declared direction control, not an accidental duplicate.", "",
              "Most automatically retrieved pairs remain UNCERTAIN, which is an abstention rather than a "
              "negative identity label. A city named Nicaea versus a council, a biblical book label versus a "
              "person, and terms inside work titles intentionally expose type/sense mistakes. Cross-work "
              "BROADER/NARROWER judgments and stable cross-school conceptual equivalence remain underrepresented. "
              "The local controls do not fill that gap. Greek/Latin/Portuguese gloss evidence exists, but "
              "there is no exhaustive multilingual term alignment or balanced homonym inventory yet.", "",
              "## 8. Coverage gaps", "",
              "- No promoted v2 data and no PO v2 data are available in this snapshot. PG/PL coverage is a "
              "pipeline subset rather than all volumes in the repository.",
              "- Page kinds are editorial structure, not reliable literary genre labels. Theology and "
              "Christian editorial scholarship dominate. Logic (PL064), rhetoric (PL090), astronomy (PG019), "
              "Trojan epitaphs (PL019), Renaissance intellectual biography (PG161), and historical/epistolary "
              "material broaden coverage but do not constitute a balanced non-theological corpus.",
              "- Historical period is not automatically assigned: a page can combine an ancient author, "
              "a medieval event, an early modern editor and a nineteenth-century imprint. Source examples "
              "include reported 311 martyrdom chronology and Renaissance biography, but period strata need "
              "separate author/event/editor dates and scholarly review.",
              "- Many metadata strings and work boundaries are unresolved, and OCR often mixes languages "
              "or damaged readings. The full OCR and candidate index metadata are retained so reviewers can "
              "reject a bibliographic shortcut.",
              "- No independent expert/model adjudication has occurred. The 29 provisional source readings "
              "are a curated starting point, not semantic validation of all 240 pages. Near-duplicate detection "
              "does not eliminate all cross-language parallels.", "",
              "## 9. Review packets, token volume and validation", "",
              f"Prepared **{len(manifest['packets'])}** split-separated packets. Page batches target 12 examples "
              "with a character-estimated token cap, shrinking for long pages; pair packets bring both full "
              "source pages when the occurrences fall in different page batches. Every packet supplies criteria, "
              "original v2 segments, source metadata, full current-page OCR, context limitations, questions "
              "and a structured output template that allows rejection.", "",
              f"Estimated input volume: **{manifest['estimated_review_input_tokens']:,} tokens** "
              f"(heuristic range **{sum(p['token_range'][0] for p in manifest['packets']):,}–"
              f"{sum(p['token_range'][1] for p in manifest['packets']):,}**). "
              "This uses Unicode characters/3, not a model tokenizer; the range is chars/4 to chars/2. "
              "It includes repeated context in pair packets and excludes generated responses, retries, "
              "reviewer comparison and repeated stability inference. No paid APIs or extraction jobs were run.", "",
              f"Artifact validation checked **{validation['checked_pages']} pages** and "
              f"**{validation['checked_pairs']} pairs** against frozen sources and live read-only generation rows: "
              f"**{len(validation['errors'])} errors**, **{len(validation['warnings'])} warnings**. "
              "This verifies provenance, exact spans, content hashes, eligibility, duplicates and isolation; "
              "it cannot prove semantics. Synthetic pytest fixtures are confined to tests and never enter "
              "the real corpus outputs. Test results are in `test_results.txt`.", "",
              "## 10. Recommended next steps before extraction", "",
              "1. Review the 24 calibration pages and the explicit negative controls; freeze prompts and "
              "schemas before releasing holdout packets. Run no tuning against holdout.",
              "2. Independently review the remaining candidates with OCR and attribution layers visible. "
              "Require span-level evidence and track rejected relations, uncertain identity and missing context. "
              "Then adjudicate disagreements; model agreement alone is never verified gold.",
              "3. Score valid and metadata_pending separately. Preserve source-asserted attribution "
              "versus modern authorship, reported events versus established history, and physical versus "
              "editorial/Migne pagination.",
              "4. Add PO and promoted-v2 strata when genuinely available. Build a probability sample for "
              "corpus-level estimates and a larger expert-grounded terminology set, especially cross-school "
              "and cross-work broader/narrower relations.",
              "5. Before single-/two-pass comparison, fix evaluation units (page, segment, claim, relation), "
              "define correct abstention, and freeze accepted/rejected annotations. Evaluate grounding and "
              "attribution precision separately from entity recall and graph density.",
              "6. Use the 24 reserved pages for repeated runs with recorded model/version/prompt/seed. "
              "Measure node/claim/relation stability and canonicalization consistency after adjudication, "
              "rather than spending the full extraction budget now.", "",
              "## Reproduction and artifact contract", "",
              "See `tools/knowledge_graph/benchmarks/README.md` for exact stage commands. "
              "`discovery_config.json` records discovery paths and options; `sample_design.json` records "
              "selection quotas and holdout reservations; `source_inspections.json` contains the explicit "
              "revisable annotations. `manifest.json` records code/content hashes and packet paths. "
              "A frozen generation/OCR snapshot preserves the original inspected material if the live "
              "database later changes. Live revalidation intentionally reports drift rather than concealing it.", "",
              "Format adaptations: one record per distinct page with one focal segment and all original "
              "`page_segments`; raw OCR offsets are Unicode code points; cumulative context has a separate "
              "non-evidence role; added scope/status/quality provenance, snapshots, metadata, extraction version, "
              "stability flag and explicit non-gold review status. Empty work keys are null, never fabricated. "
              "Scripture detector offsets remain labelled as block-relative detector text. Pair occurrence "
              "quotes are exact substrings of their own segment, even when that segment differs from the "
              "page’s focal segment. Source inspection covers only its quoted claim/issue.", ""]
    (out / "discovery_report.md").write_text("\n".join(lines), encoding="utf-8")
    manifest["artifact_sha256"]["discovery_report.md"] = digest((out / "discovery_report.md").read_bytes())
    write_json(out / "manifest.json", manifest)
    print(f"Wrote {out / 'discovery_report.md'}")


if __name__ == "__main__":
    main()

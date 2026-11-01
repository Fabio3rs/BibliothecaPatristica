# Bibliotheca Patristica — KG Extractor Playground design starter

This is a **design kit**, not a complete executable playground. No paid inference is performed.

- `kg-extraction-v1.schema.json`: JSON Schema for the LLM's compact page-local response.
- `page-input.demo.json`: synthetic physical-page input (metadata + v2 summary + structured OCR).
- `page-output.demo.json`: synthetic response that conforms to the schema.
- `extract_concepts_v1.system.txt`: draft English system prompt.

## Important contracts
- One physical page = one atomic extraction job.
- The model returns local IDs and evidence references only. Python adds document/page IDs, hashes, model, tokens, page-to-work mapping and OCR `bbox` values from the input snapshot.
- Summary `cumulative_summary` / inherited metadata are disambiguation context, not evidence for a claim attributed to the current physical page.
- OCR quote evidence must exactly occur in the indicated block (or be explicitly flagged for human review); summary quotes must occur in the indicated current segment.
- `bbox` is derived from parsed OCR block metadata, never hallucinated by the LLM.
- Global entity linking and deduplication are separate from local extraction.
- This schema intentionally allows empty concepts/entities/claims/events/relations arrays.
- Names, meanings and factual statements in the demo are synthetic illustrations, not authenticated historical quotations.

## Suggested reusable modules
- `snapshot_v2.py`: read-only select of v2 generations and validated OCR file resolution.
- `providers.py`: adapters for Ollama, OpenAI-compatible, and provider-specific APIs; feature negotiation for JSON Schema, images and reasoning effort.
- `prompts.py`: independently versioned templates and variants.
- `validation.py`: JSON Schema, local ID reference integrity, exact evidence quotes and source locator tests.
- `store.py`: SQLite experiments, page snapshots, runs, reviews and evaluation.
- `compare.py`: A/B comparison of normalized nodes, relation triples, evidence and costs.
- `ui.py` or separate frontend: page summary/OCR/facsimile vs nodes/relations/diff.

## Validation tiers
- Structural (automatic): parseable JSON Schema, ID uniqueness, existing endpoints, valid references.
- Source (automatic): quote membership and OCR block IDs, no fabricated metadata, input/prompt hashing.
- Semantic (review): concept correctness, duplicate granularity, historical attribution, relation precision and completeness.

## Provider-specific caveat
The uploaded summary playground uses `response_format={"type":"json_object"}` for OpenAI-compatible chat, which checks JSON mode but is not the same as strict schema validation. Use a vendor-supported structured-output mode where available, and always validate locally. Avoid sending unsupported knobs (`service_tier`, reasoning settings, image formats) blindly across providers.

## Implemented playground

The design kit above now has a local implementation. Its schema, system prompt and
synthetic input/output remain the single authoritative starter assets in this directory.

```bash
python scripts/playgrounds/kg_extractor_playground.py serve
```

Open `http://127.0.0.1:8766`. See [the implementation guide](../../docs/kg_extractor_playground.md)
for CLI, providers, source/shadow policy, benchmark loading, tests and limitations.
The validator in this kit delegates to the full playground validation pipeline.

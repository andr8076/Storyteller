# Development status

## Current objective
Evaluate character-specific, temporal knowledge and continue with model-backed quality checks when Build-Arch is available.

## Verified implementation
- The Jan gateway, import modes, and memory worker remain in place. No runtime or project migration was performed.
- Profile retrieval now scores both filenames and contents. Long profiles inject matching lines from anywhere in the file, bounded by the existing per-file and total context limits.
- Baseline before this change: 14 unit tests passed. After this change: 16 passed (`python -m unittest discover -s tests -v`). A disposable-world test covers a fact after the former 1,900-character cutoff and project isolation.
- Experimental character knowledge ledger: Catch Up and completed generated scenes can record explicit named perspectives from the existing extraction call. Each state retains its source sentence and source ID, preserves certainty history, survives retries, and exports current state to `character-knowledge.md`. Normal Texting retrieves relevant attributed states. Data Dump remains timeless and does not advance this chronological ledger.
- Comparative fixture: the old canon extractor incorrectly accepted an explicitly suspected vault location as world fact; now it rejects it while recording Ivo's suspicion, then updates Ivo's current state when he discovers the location. Historical states remain in SQLite. All 20 unit tests pass; no local model was run.

## Findings and decisions
- The old gateway matched profile filenames only and injected their initial 1,900 characters. A correctly stored late fact could be invisible to the writer.
- Narra is MIT licensed and offers structured character knowledge, events, relationships, and local semantic search. Its Rust, SurrealDB/RocksDB, and embedding stack would introduce a second memory store and integration work. No evidence yet shows it improves Storyteller's current extraction or retrieval enough to justify migration. Its knowledge ledger and temporal concepts remain candidates for a small later experiment.
- Adapted Narra's append-only certainty/provenance idea into an isolated Storyteller ledger. This avoids a premature wholesale migration while exercising a substantive capability. Narra's richer graph, temporal event queries, and semantic search remain open for comparison.
- This change introduces no model calls, migrations, or new Python packages. It reads eligible profile files per request; performance on very large profile collections has not been measured.

## Known limits
- Build-Arch was offline during this session. The local Qwen/Cydonia workflow and Sparkdrive integration were not exercised.
- Retrieval remains lexical; paraphrases without overlapping terms can be missed. Canon, summary, and source excerpts still use their existing ranking and truncation rules.
- Existing tests do not yet cover the full gateway and import path.
- Existing completed imports are not backfilled into the new knowledge ledger; their original sources remain intact. The current grouping uses model-proposed topic strings, so paraphrased topics may form separate histories until entity/topic reconciliation improves.
- The knowledge extraction prompt has not been evaluated on Qwen3.5-2B. Deterministic fixtures establish plumbing and guardrails, not extraction recall or model compliance.

## Next action
When Build-Arch is online, compare Qwen3.5-2B's character-knowledge output with the baseline on a short unrelated scene (suspicion, later discovery, mistaken belief), then check one isolated Catch Up and Jan gateway request. Independently investigate topic reconciliation to keep histories linked across paraphrases.

# Development status

## Current objective
Improve long-term narrative retrieval without risking existing projects or adding a new storage dependency.

## Verified implementation
- The Jan gateway, import modes, and memory worker remain in place. No runtime or project migration was performed.
- Profile retrieval now scores both filenames and contents. Long profiles inject matching lines from anywhere in the file, bounded by the existing per-file and total context limits.
- Baseline before this change: 14 unit tests passed. After this change: 16 passed (`python -m unittest discover -s tests -v`). A disposable-world test covers a fact after the former 1,900-character cutoff and project isolation.

## Findings and decisions
- The old gateway matched profile filenames only and injected their initial 1,900 characters. A correctly stored late fact could be invisible to the writer.
- Narra is MIT licensed and offers structured character knowledge, events, relationships, and local semantic search. Its Rust, SurrealDB/RocksDB, and embedding stack would introduce a second memory store and integration work. No evidence yet shows it improves Storyteller's current extraction or retrieval enough to justify migration. Its knowledge ledger and temporal concepts remain candidates for a small later experiment.
- This change introduces no model calls, migrations, or new Python packages. It reads eligible profile files per request; performance on very large profile collections has not been measured.

## Known limits
- Build-Arch was offline during this session. The local Qwen/Cydonia workflow and Sparkdrive integration were not exercised.
- Retrieval remains lexical; paraphrases without overlapping terms can be missed. Canon, summary, and source excerpts still use their existing ranking and truncation rules.
- Existing tests do not yet cover the full gateway and import path.

## Next action
When Build-Arch is online, run one isolated end-to-end retrieval check through Jan's gateway with a late-profile fact, then compare a small paraphrase case against the lexical baseline before considering a semantic or structured-memory dependency.

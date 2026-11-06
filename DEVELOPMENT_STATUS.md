# Development status

## Current objective
Improve source-backed long-term narrative memory, especially character beliefs and changing knowledge, while preserving the Jan gateway and all three modes.

## Verified implementation
- Normal Texting retrieves character knowledge and historical events from per-project, cited SQLite indexes. Earlier beliefs are available for explicit historical questions; ordinary requests prefer current state. Profiles and event sources remain human-readable.
- Catch Up records chronological attributed perspectives and advances continuation after completion. Data Dump records timeless reference knowledge without advancing chronology. Neither sends captured text to the creative model.
- Normal prompts select memory before asynchronously analyzing the latest author message, preventing same-request reinjection. Per-project response learning OFF persists.
- An optional dedicated local perspective model supplements the 2B worker for dense dialogue in Catch Up and Normal Texting (author messages and generated scenes), with source validation and fallback on failure. It runs in the background worker after the outgoing memory snapshot.
- Truncated large import responses split numbered sections into smaller model calls and retain original citations; identity proposals describing transient actions are rejected.

## Live Build-Arch results (isolated temporary projects, 2026-11-06)
- Two unrelated dialogue scenes: Qwen3.5-2B returned no attributed knowledge (10–14 seconds). A dedicated local Qwen3.5-9B pass recovered earlier false beliefs and later true locations, about 19–20 seconds extra per scene. The validator rejected an unsupported inference. A deterministic named-observation fallback recovered a later direct observation.
- Actual Jan/Cydonia gateway returned HTTP 200 in about 36 seconds. It injected prior memory, excluded the new fact in that request, and made the fact retrievable after the worker ran. The persistent OFF setting skipped learning the generated answer while still learning the user message.
- A 1,389-character excerpt copied from the original Sparkdrive story into a disposable Catch Up project completed in 55.33 seconds with 8 accepted facts and 14 review items, advancing continuation. The original file hash and modification time were unchanged. Earlier larger single responses had been truncated. Three accepted action-as-identity false positives are now blocked by a targeted deterministic test; the excerpt has not been rerun with this final filter.
- A live local 9B call through the new Normal Texting extraction path took 19.55 seconds and recovered an earlier belief and later discovery. It also exposed duplicate states because the literal fallback and model used different topic names and verb tense. Topic normalization, equivalent-claim deduplication, and cautious subject-matched topic reconciliation address this; the fix still needs a live rerun.
- `python -m unittest discover -s tests -q`: 53 passed after the topic/duplicate fix. No full manuscript import or personal project changes.

## Architecture decision and limitations
- The append-only knowledge ledger adapts the temporal and provenance idea seen in Narra without adopting its entire database and embedding stack. Other frameworks may still offer advantages.
- 9B improves explicit belief tracking on the two tested scenes, but adds latency and still proposes unsupported claims; keep it optional until a broader quality/cost comparison.
- Extraction can miss indirect or pronoun-heavy information. Retrieval is largely lexical. The full Sparkdrive manuscript and long-running Jan project behavior remain untested.

## Next action
Rerun the live 9B Normal Texting scene to check the duplicate fix, then compare additional dialogue styles and decide whether its latency warrants enabling it by default.

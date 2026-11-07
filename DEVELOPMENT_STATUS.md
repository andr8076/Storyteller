# Development status

## Current objective
Finish the 0.8.0-rc1 acceptance check on Build-Arch and Jan. The functional scope is fixed: local manual launch, safe imports, persistent character history, current/historical retrieval, request snapshot isolation, and optional reply learning. Further narrative research is outside this release gate.

## Implemented
- Jan gateway, Data Dump, Catch Up, author-message learning, and persistent reply-learning ON/OFF remain in place. Imports do not contact the creative model; Catch Up advances continuation only on completion.
- N in the control panel persistently enables narrative memory per project and applies on the next request. It reads one SQLite character-history snapshot shared by imports and normal conversation learning; it does not combine competing current-state answers from two stores.
- New narrative_memory.py applies the corrected episode projection to existing history. Explicit negation can move an earlier belief to history across different topic labels; historical answers include evidence and supersession links. Unrelated facts remain separate. Later Data Dumps remain baseline knowledge and cannot rewind narrative observations.
- With N and the optional K local model enabled, Catch Up uses one broad episode call in place of the focused perspective call. Validated character perspectives enter the shared ordered history before the section completes. Actions/statements also remain in the experimental audit ledger. With N off, the existing shadow endpoint remains an isolated comparison.
- Normal Texting continues to publish through the same character history; it needs no extra inference for narrative retrieval. The gateway finishes selecting memory before queuing the new prompt for learning.
- Perspective merging prefers a complete validated statement over an equivalent fragment and preserves word order, negation, and spatial qualifiers.
- The source_order column is an additive write-time schema extension. Old databases can be read without migration, and all existing history is preserved. The readable character-knowledge.md follows the selected view.
- Projection now indexes candidate propositions rather than comparing every record with every earlier record. A 600-record synthetic fixture fell from 2.436s to 0.004s locally.

## Verified
- 81 tests pass locally on the release-candidate code.
- Actual HTTP gateway handler with a local mock writer: current versus historical knowledge, later conversation correction, exclusion of same-request learning from injection, and Catch Up/Data Dump isolation.
- Captured live 9B output replayed through Catch Up: named witness enters shared history and retrieval; exactly one broad call, no duplicate focused call, retries do not duplicate knowledge, source unchanged.
- Persistent N toggle, legacy database read without modification, project isolation, baseline/timeline precedence, and retrieval budget.
- Prior Build-Arch evidence: Jan/Cydonia gateway worked; response-learning OFF worked; local model startup/stop cleanup worked; a 1,389-character Sparkdrive excerpt imported without modifying the original.
- Current Build-Arch availability check: offline. This release candidate has not been deployed or exercised with live Jan. No model was loaded during this development cycle.

## Important findings and boundaries
- The broader 9B pass captured 3/3 character perspectives versus 2/3 for the focused pass in the saved flute scene; times were 64.74s versus 29.48s. Two live calls used 614 generated tokens. The base 2B response was mocked empty to isolate that comparison.
- Exact live outputs and the original temporal failure are retained in experiments/results/narrative_memory_2026-11-06.json. experiments/compare_narrative_memory.py supports replay without inference; the explicit-correction probe now passes.
- A burned object later being found does not by itself prove the earlier statement false. The deterministic regression uses destroyed versus never destroyed. Do not silently infer arbitrary contradictions.
- Reconciliation covers equivalent copular statements and explicit negation. Semantic opposites, implied corrections, aliases, flashback chronology, and exhaustive extraction remain limited. Retrieval is lexical. Full-manuscript and long-running Jan quality have not been established.
- Existing shadow-only episode records are preserved but are not silently backfilled into shared character history. N can immediately project existing character history; new imports publish richer observations.
- The architecture adapts provenance/append-only ideas considered in Narra; no framework/database migration or external inference is required.
- Relevant previous commits: 4347d41 validated-perspective merge; e32f046 saved live comparison; 2168e9c corrected temporal views and complete observations.

## Single next action
Deploy this release candidate to Build-Arch when available and perform one disposable-project Jan smoke check: enable N, query current and earlier character knowledge after a correction, confirm a new prompt fact appears only on the next request, and stop Storyteller. If it passes, accept 0.8 as the functional release; do not reopen architecture research as a prerequisite.

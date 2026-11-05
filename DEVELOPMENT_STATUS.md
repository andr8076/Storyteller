# Development status

Updated: 2026-11-05

## Current work
- Feature: Data Dump. NOT ready to advance.
- Objective: evaluate full-document imports and conservative conflict handling.
- User clarified testing scope: use CLI/OpenAI-compatible gateway API and local model endpoints; defer Jan GUI and ongoing chats. Do not spend further UI automation effort on this milestone.
- Verified commits: `1e39223` routing, `c126d18` source grounding, `7efa718` conflict quarantine. This checkpoint adds heading context and process recovery tests.

## Testing
- Independent component: PARTIAL overall; 53/53 automated tests PASS locally and on Build-Arch. Source grounding, heading scope, negation, attribution, malformed state and conflicts covered.
- Incoming interface: PASS for tested streaming/non-streaming HTTP capture, durable retries, concurrent duplicate capture and project isolation.
- Outgoing interface: PARTIAL overall; exact source/evidence persistence, deduplication, memory write isolation and tested conflict quarantine PASS. Author-directed supersession remains absent.
- Full integration: PASS for tested API workflows: actual gateway -> queue -> importer subprocess -> local CPU Qwen -> persisted memory. Jan UI deferred by user; not an acceptance blocker for this milestone.
- Controlled SIGKILL/restart test verifies committed sections are not analyzed again, the interrupted section is retried, events are unique, continuation bytes unchanged and creative requests zero.
- Real Qwen crash/restart also PASS: reference excerpt 3,820 characters/2 sections, killed after section 1, completed after restart; 6 source-supported facts, 12 rejected candidates. Unrelated 108-character input yielded 4 supported facts and a subsequent conflicting eye-colour import was quarantined. Both cases: zero creative requests, unchanged continuation and originals, retry without duplicate events.
- Commands: `python3 -m unittest discover -s tests -q`; `python3 scripts/check_data_dump.py --source <private-reference> --start-line 753 --end-line 878 --crash-restart --timeout 600`.
- Full reference document run is IN PROGRESS; no result claimed yet.

## Implemented
- Durable capture receipts prevent expiry/history leakage; corrupt routing state fails closed.
- Data Dump selects bounded source-statement indexes; Python copies exact statements. Negation, numbers and uncertainty wording survive unchanged.
- Headings are context, not selectable facts; original heading scope carries across chunks and resumes. Bare labels such as Visual Effects are excluded.
- Name completion cannot join names to headings on the following line.
- Folder locks serialize memory writes/imports. Re-armed identical lore does not duplicate canonical facts.
- Import database connections close and recovery clears stale errors.
- Conflict review and persistence share one lock. Uncertain/conflicting candidates retain provenance in review.jsonl. Their raw sections are excluded from retrieval and preserved under imports/review-sources.
- CPU memory port can be isolated using STORYTELLER_MEMORY_PORT.
- Tests: test_data_dump.py, test_dump_grounding.py, test_canon_review.py, test_import_process_recovery.py. Disposable live harness: scripts/check_data_dump.py.

## Evidence and limitations
- Original 14-test baseline missed five reproduced routing failures.
- Free paraphrase introduced unsupported causation/reversed negation. Copy-text prompting repeated/truncated output; larger token budget alone failed. Bounded statement selection fixed completion.
- Heading-context experiment initially accepted a bare Visual Effects label; the new label filter rejects it. Count alone is not a quality metric.
- Conflict model first rejected compatible appearance/occupation facts, then approved changed eye colour. Deterministic changed-clause/negation checks override those approvals. General paraphrased contradiction detection remains uncertain.
- Structured epistemic categories and explicit author-directed correction/supersession remain missing. Extraction is conservative; entity/category choices and important-fact coverage still need evaluation.
- Original story/reference and personal projects unchanged. Installed application not replaced; development branch only.
- Private crash/restart results: /tmp/storyteller-dump-results-ly_vjucb. Full-document run: /tmp/storyteller-dump-results-ieyue462. Isolated runtime: /tmp/storyteller-dump-test-mmphti93. These temporary paths may disappear; never commit private results.

## Next action
Inspect the full-document API import result, audit accepted facts against their source, and reproduce the most important generalizable failure before changing code.

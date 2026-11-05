# Development status

Updated: 2026-11-05

## Current work
- Feature: Data Dump. NOT ready to advance.
- Session checkpointed at user request. Await updated architectural instructions before further development.
- Last objective: evaluate full-document imports and conservative conflict handling.
- User clarified testing scope: use CLI/OpenAI-compatible gateway API and local model endpoints; defer Jan GUI and ongoing chats. Do not spend further UI automation effort on this milestone.
- Verified commits: `1e39223` routing, `c126d18` source grounding, `7efa718` conflict quarantine. `56bf021` adds heading context and process recovery tests.

## Testing
- Independent component: PARTIAL overall; 53/53 automated tests PASS locally and on Build-Arch. Source grounding, heading scope, negation, attribution, malformed state and conflicts covered.
- Incoming interface: PASS for tested streaming/non-streaming HTTP capture, durable retries, concurrent duplicate capture and project isolation.
- Outgoing interface: PARTIAL overall; exact source/evidence persistence, deduplication, memory write isolation and tested conflict quarantine PASS. Author-directed supersession remains absent.
- Full integration: PASS for tested API workflows: actual gateway -> queue -> importer subprocess -> local CPU Qwen -> persisted memory. Jan UI deferred by user; not an acceptance blocker for this milestone.
- Controlled SIGKILL/restart test verifies committed sections are not analyzed again, the interrupted section is retried, events are unique, continuation bytes unchanged and creative requests zero.
- Real Qwen crash/restart also PASS: reference excerpt 3,820 characters/2 sections, killed after section 1, completed after restart; 6 source-supported facts, 12 rejected candidates. Unrelated 108-character input yielded 4 supported facts and a subsequent conflicting eye-colour import was quarantined. Both cases: zero creative requests, unchanged continuation and originals, retry without duplicate events.
- Commands: `python3 -m unittest discover -s tests -q`; `python3 scripts/check_data_dump.py --source <private-reference> --start-line 753 --end-line 878 --crash-restart --timeout 600`.
- Full reference document experiment: INCONCLUSIVE, not running. It captured 55,261 characters after the unrelated fixture passed, but no final result was observed. At session close the process session and temporary result directory were absent. Do not count this as PASS or FAIL; the cause of disappearance is unknown.

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
- Session-close inspection confirmed the temporary runtime and both result directories are now absent. Previously observed crash/restart results remain summarized above; raw private artifacts cannot currently be reinspected. No Storyteller gateway/importer or llama-server test process was found.
- Original fixture locations still exist on Build-Arch: /home/andr8076/Desktop/sparkdrive.txt and /home/andr8076/Desktop/STORY BIBLE — DATA DUMP. Reference imports are not Sparkdrive-prose extraction tests.

## Preservation / unfinished work
- Branch: fix/data-dump-isolation; draft PR: https://github.com/andr8076/Storyteller/pull/1. Nothing merged or deployed to the installed application.
- All substantive source/test changes are committed through 56bf021. Compared scratch file hashes against its GitHub tree: only five untouched support/test files differ by trailing whitespace; no uncommitted implementation remains. Scratch copies are retained.
- Unfinished: full-document scale/quality audit, broader semantic conflict reliability, structured epistemic classification and author-directed correction/supersession. These are limitations, not verified features.
- Reusable automated and local-model harnesses are committed. Recreate a disposable checkout/config for future live tests; do not rely on vanished /tmp paths. Keep private fixtures/results on Build-Arch, preferably in a dedicated persistent test-artifact directory outside the repository.

## Next action
Read the user's new architectural instructions first, then reconcile them with this checkpoint. If continuing the current Data Dump implementation, recreate the isolated API harness and rerun the inconclusive full-document experiment with private results retained outside /tmp; audit source attribution and conflict behavior before deciding on further changes.

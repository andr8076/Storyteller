# Development status

Updated: 2026-11-05

## Current work
- Feature: Data Dump. NOT ready to advance.
- Objective: improve heading-scoped extraction without relaxing source grounding.
- Conflict handling now quarantines detected/uncertain revisions and their raw source sections. Broad semantic conflict review is model-assisted and not fully reliable.
- Verified commits: `1e39223` (routing), `c126d18` (source-statement grounding and bounded output).

## Testing
- Independent component: PARTIAL overall; 49/49 regression tests PASS locally and on Build-Arch. Grounding tests reject changed negation, causal paraphrases, questions, wrong cited indexes, and object-as-subject attribution.
- Incoming interface: PASS for tested capture and HTTP gateway paths: streaming/non-streaming, durable retries, corrupt routing state, concurrent duplicate capture and project isolation.
- Outgoing interface: PARTIAL; persisted source/evidence, empty updates, interrupted-import recovery, re-armed deduplication and concurrent writers PASS. Tested changed-clause conflicts stay out of current canon and retrieval. Author-directed conflict resolution is not implemented.
- Full integration: PARTIAL. Actual gateway -> queue -> importer subprocess -> CPU Qwen -> memory PASS; actual Jan GUI is UNTESTED. No visible Jan window was found in the remotely accessible X desktop, though Jan processes exist.
- Command: `python3 -m unittest discover -s tests -q`.
- Manual harness: `python3 scripts/check_data_dump.py --source <private-reference> --start-line 753 --end-line 878 --timeout 600`.
- Latest live run: unrelated input 108 characters, 4 supported facts, 2 rejected candidates; reference excerpt 3,820 characters/2 sections, 4 copied statements, 11 rejected candidates. A subsequent conflicting eye-colour import was quarantined, retained for review, and absent from canonical profiles/events and retrieval sources. Both recorded 0 creative requests, unchanged continuation files, exact source capture and retry without duplicate events. Test-owned processes stopped.
- Source statements were checked against reference input, NOT treated as facts extracted from Sparkdrive prose. Belief/uncertainty wording remains verbatim, but structured epistemic classification is still missing.

## Implemented
- Durable import receipts prevent expiry/history leakage; malformed state fails closed.
- Data Dump model selects source-statement numbers, entities and categories; Python copies facts from the source. Separate bounded JSON schema caps output at 12 records.
- Source grounding preserves negation/qualifiers and validates heading scope and subject attribution. No story-specific cases added.
- Name completion no longer treats a heading on the next line as a surname.
- Folder locks serialize memory writers/imports. Re-arming identical lore does not duplicate event/canon entries.
- Import SQLite connections close; successful recovery clears stale errors.
- Conflict review is a separate `canon_review.py` module. Review and persistence share a write lock; existing handwritten profiles also participate. Incomplete/uncertain assessments cannot approve new facts. Conflict provenance is preserved in review.jsonl; affected sections go to imports/review-sources instead of retrieval memory.
- Tests: `tests/test_data_dump.py`, `tests/test_dump_grounding.py`, `tests/test_canon_review.py`; disposable live harness: `scripts/check_data_dump.py`.

## Experiments and remaining limitations
- Original 14 tests all passed but lacked Data Dump coverage. Five routing failures were reproduced before the routing fix.
- Free paraphrase accepted unsupported causation and reversed negation. Requiring the model to copy text led to omissions/rewording and repetition. Increasing output budget alone (1350 -> 3072) still truncated responses. Bounded statement-number selection completed the larger import.
- An intermediate harness wrongly required nonadjacent citations to be contiguous; corrected to verify each original evidence fragment.
- Reference extraction remains conservative and misses meaningful heading-scoped facts. Model entity/category selection remains imperfect; do not equate harness PASS with extraction completeness.
- Conflict baseline reproduced simultaneous eye-colour claims. The first model reviewer falsely called appearance and occupation conflicting; a revised prompt then falsely approved a colour change. Deterministic changed-clause/negation detection now overrides such approvals; distinct attributes remain compatible in the live fixture. Do not treat this as general semantic proof.
- Explicit author-directed resolution/supersession, large full-document import, real process interruption/restart, and Jan UI workflow remain unverified.
- Original story/reference files and existing projects are unchanged. Code is on a development branch; installed application has not been replaced.
- Private latest results on Build-Arch: `/tmp/storyteller-dump-results-9l0jop19`; isolated runtime: `/tmp/storyteller-dump-test-mmphti93`. Temporary paths may disappear. Never commit private fixtures/results.

## Next action
Improve source numbering/heading context so the model selects actual statements and correct subjects rather than headings or mentioned objects. Preserve current grounding/conflict regression baselines; then run a real interrupted import and restart.

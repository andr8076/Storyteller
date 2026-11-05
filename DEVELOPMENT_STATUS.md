# Development status

Updated: 2026-11-05

## Current work
- Feature: Data Dump. NOT ready to advance to Catch Up.
- Objective: prevent unsupported interpretations from entering canon.
- Confirmed failure: lexical grounding accepted a causal claim combining two source observations; reference headings also lose entity attribution.
- Routing fixes are verified; extraction remains PARTIAL.

## Testing
- Independent component: PARTIAL overall; capture, concurrent duplicate capture, cancellation, project isolation, malformed-state handling PASS.
- Incoming interface: PASS for tested gateway captures, streamed/non-streamed acknowledgements, durable retries and history filtering.
- Outgoing interface: PARTIAL; persistence/resume/idempotency and exact evidence PASS; semantic correctness FAIL in live reference sample.
- Full integration: PARTIAL; real gateway -> queue -> import subprocess -> local Qwen -> persistent memory PASS for transport. Actual Jan GUI not exercised; extraction quality fails acceptance.
- Baseline: 14/14 existing tests PASS at `53bb536`. Added focused suite initially reproduced 5 routing failures; current 26/26 tests PASS locally (23/23 intermediate revision also PASS on Build-Arch).
- Live command: `python3 scripts/check_data_dump.py --source <private-reference> --start-line 753 --end-line 807 --timeout 600`.
- Live results: unrelated 108-character input: 4 facts/0 rejects; private reference 1,650-character excerpt: 1 fact/10 rejects. Both had 0 creative endpoint requests, exact captured source, retry without duplicate events, and byte-identical continuation files. Test-owned processes stopped.

## Confirmed causes and fixes
- Capture retry protection expired after 180 seconds and remembered only the latest capture. Now uses durable project receipts.
- History filtering required an untouched adjacent acknowledgement. Now always removes captured user messages and optionally removes recognized acknowledgements.
- Corrupt mode state silently became Normal Texting. Now corrupt state/receipts fail closed.
- Extractor lexical overlap does not prove semantic entailment. Live accepted claim added causation absent from source; rejected candidates also showed invented relationships. Do not relax validation to raise fact counts.

## Completed work
- Branch: `fix/data-dump-isolation`; baseline checkpoint commit `6354af2`.
- This cycle: durable import routing and regression suite (`tests/test_data_dump.py`), plus reusable disposable real-model harness (`scripts/check_data_dump.py`).
- Originals and existing personal projects untouched. No private input or output is committed.
- Private results on Build-Arch: `/tmp/storyteller-dump-results-gdlx1v6t`; test runtime: `/tmp/storyteller-dump-test-mmphti93`. Temporary paths may disappear.

## Remaining limitations
- Actual Jan GUI workflow untested.
- Semantic grounding, reference-heading attribution, conflicting updates, repeat imports under a new capture token, and concurrent memory writers need further testing.
- Python 3.14 reports an existing unclosed SQLite connection warning in importer tests.

## Next action
Add a general regression for an unsupported causal paraphrase accepted from adjacent source sentences; tighten Data Dump grounding and rerun unrelated/reference live fixtures.

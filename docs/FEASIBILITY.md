# Compatibility evidence and contract

## Current API check (2026-10-20, plugin 0.2.2)

Hermes HEAD checked: `ce28ca328d3b85ac8663e602136eb69b027cd056`.
Compared with the 2026-10-15 snapshot, `tools/write_approval.py`,
`tools/memory_tool.py` and `tools/memory_tool_store.py` are unchanged. Public native
handler registration, tracked tasks/unload and readiness properties remain
compatible. The Telegram adapter at `plugins/platforms/telegram/adapter.py` has
topic-routing updates; the plugin continues to use its configured destination,
not inferred originating topics. No approval API migration is needed.

New integration tests load this directory plugin through the real PluginContext,
wire it through the actual Telegram adapter and exercise PTB first-match routing.
Both initial and late registration place approval handlers before the core
catch-all; unrelated callback data still reaches core. Repeated rewire does not
duplicate handlers, and delivery waits for both adapter and Application readiness.
The Application and network I/O remain fake; no live delivery is claimed.

Cards now display the pinned native ID and origin to distinguish separate Hermes
proposals from duplicate transport delivery. Origin is informational only, not
authorization or routing; unknown values receive a fixed fallback label.

Verification: **95 passed** on this snapshot; **81 passed, 14 skipped** on
`802e0b1daebad8a60da496db1a070fe13ddc45bd` (pre-patch baseline).

## Native pending cleanup (plugin 0.2.1)

Saved/rejected decisions are committed to SQLite before invoking public
`discard_pending("memory", id)`. The native record must still match its pinned
hash. Scans retry cleanup, including saved/rejected records from previous plugin
versions. Refused, invalid and uncertain rows are never automatically discarded.
Cleanup failure does not change a known disposition or replay a write. SQLite
keeps the complete audit. Native deletion is not atomic with apply or hash checks;
do not concurrently modify/approve native pending through another interface.

Verification on the same current/baseline revisions below: 90 passed / 76 passed,
14 skipped. Regression tests cover native queue removal, legacy cleanup, preserved
audit and rewritten records, and cleanup failures/retries without write replay.

## Current API check (2026-10-15, plugin 0.2.0)

Hermes HEAD checked: `3b043e19005fea400a8e556334526ef1d21679e4`.
Public pending APIs, configured store replay and Telegram registration remain
compatible. The model-facing memory tool now uses `patch` (unique span replacement)
instead of `replace`, and recommends atomic `operations` arrays. Background review
also stages destructive consolidation operations; filtering only old single-entry
formats left these records in native pending without Telegram cards.

The transport now validates every operation, renders full pinned before/after text
for patches, and offers one decision for the complete batch. Replay passes the
original payload to `apply_memory_pending` exactly once, preserving the native
lock, sequential pins, final budget checks and all-or-nothing mutation. It does not
split batches. Unknown formats, ambiguous patches and missing pins fail closed.

Verification: 78 tests pass on current HEAD; 64 pass and 14 native patch tests skip
on `802e0b1daebad8a60da496db1a070fe13ddc45bd`. New regression tests exercise real
`memory_tool` calls under `background_review` provenance for both stores, waiting
for approval, owner rejection, full span rendering, atomic patch/remove/add replay,
stale-member refusal without partial writes, unrelated writers, invalid members,
and complete Unicode batch delivery before activation. Telegram network I/O is fake.

Skills use the separate native `skills.write_approval` gate and `/skills` review
surface; this memory transport neither applies nor approves skill proposals.
See upstream [memory documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/).

## Historical baseline evidence

Baseline source: Hermes revision `8ffb3c655000f57f489a93cd2b32b5b71dc5c2e4`.
The line references below describe that baseline. Also verified against
`802e0b1daebad8a60da496db1a070fe13ddc45bd` with Python 3.11 and PTB 22.8:
54 tests pass, including non-modal decisions, expired callback acknowledgement,
directory loading, cancelled delivery quarantine,
resource cleanup after factory failure and General topic callbacks.

| Public surface | Source |
| --- | --- |
| Native Telegram registrar | `hermes_cli/plugins.py:858–879` |
| Factory invocation before core callbacks | `gateway/platforms/base.py:2308–2338`, Telegram adapter `3300–3303` |
| Tracked tasks/unload | `hermes_cli/plugins.py:410–429` |
| Native staging/get/list | `tools/write_approval.py:74–117` |
| Pinned replay | `tools/memory_tool.py:307–324` |
| Atomic exact-entry check | `tools/memory_tool_store.py:245–274`, `318–361` |
| Official factory docs | https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/ |

The previous feasibility-only artifact imposed a whole-file snapshot precondition.
That is outside the approved product contract. Native `matched_entry` is the
correct entry-level precondition: changes to other entries do NOT revoke approval.
The six earlier expected-failure snapshot probes have been changed into affirmative
regression checks for unrelated-writer compatibility, not hidden or weakened stale
entry tests. Real changed-target refusal tests remain, for both targets.

No public whole-file CAS or transactional pending-consume+apply API is assumed.
This plugin never borrows private native locks, patches core, changes prompt cache,
or claims exactly-once remote messaging. The README documents crash quarantine,
SQLite audit records, text-pin ABA limits and manual reconciliation.

# Compatibility evidence and contract

Source: Hermes revision `8ffb3c655000f57f489a93cd2b32b5b71dc5c2e4`.

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
retained pending audit records, text-pin ABA limits and manual reconciliation.

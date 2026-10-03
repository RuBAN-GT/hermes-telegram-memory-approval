# Hermes Telegram Memory Approval

Deployable Python plugin for **entry-level** review of Hermes' native MEMORY.md
and USER.md proposals in the existing Telegram gateway. No second bot, polling
client, core patches, private memory locks, or prompt-cache mutation.

## Contract

- One native `add`, `replace`, or `remove` per card; exactly **✅ Сохранить** and
  **❌ Отклонить**. Full proposed entry is shown, and replacements/deletions show
  the full pinned original. Plain text is chunked by UTF-16 length; buttons are
  sent only after all chunks have confirmed delivery.
- Only the configured numeric owner can act, in the bound chat, topic and exact
  final card message. Pending records are SHA-256 pinned in durable SQLite; any
  record rewrite/missing record invalidates the card, never updates its payload.
- Replace/remove replay through public `apply_memory_pending` with native
  `matched_entry`, checked atomically inside native mutation. A changed/missing
  target is refused. **Independent entries changing MUST NOT invalidate approval.**
  Add saves the immutable exact proposed text, subject to native limits.
- One-entry `operations` arrays are supported, including the `new_text` alias; original native batch replay is preserved. Multi-entry atomic batches are **unsupported and fail closed** (no card, no replay). Never
  split an existing atomic batch into independent writes. Recreate single-op
  proposals only when they are independently valid, or review the original batch
  through the native interface. Legacy destructive proposals without pins fail closed.

## Compatibility and installation

Verified against Hermes source revision
`8ffb3c655000f57f489a93cd2b32b5b71dc5c2e4`, Python 3.11 and PTB 22.8.
Requires Unix `fcntl`, native pending/pinned replay APIs, an **in-process** plugin
runtime and Telegram gateway. Host-isolated plugins cannot receive the native PTB
Application. This transport covers only native memory, not OpenViking, skills,
files, or arbitrary third-party providers. Test newer Hermes before rollout.

Installation instructions below are for the operator; this repository does not
change configuration or enable itself. Select the intended profile first and
verify `HERMES_HOME`. Native staging is the safety gate, not a fail-open middleware.

```bash
hermes config set memory.write_approval true
hermes config set plugins.isolation in_process
# Local directory installation (copy the WHOLE repo including package and manifest):
mkdir -p "$HERMES_HOME/plugins/telegram-memory-approval"
cp -R /path/to/this/repo/. "$HERMES_HOME/plugins/telegram-memory-approval/"
hermes config set plugins.entries.telegram-memory-approval.settings.owner_user_id 123456789
hermes config set plugins.entries.telegram-memory-approval.settings.fallback_chat_id -1001234567890
# Optional: omit topic setting for General/private chat; set a real topic ID otherwise.
hermes config set plugins.entries.telegram-memory-approval.settings.fallback_topic_id 7
hermes config set plugins.entries.telegram-memory-approval.settings.poll_seconds 5
hermes plugins enable telegram-memory-approval
```

After copying/installing a directory plugin, run `hermes pm repair` for plugin dependencies. **Hot reload is not supported:** native factory deduplication requires a full gateway restart after installation, disable/enable or code changes. An adapter Application rebuild also requires a full gateway restart for this transport; ordinary startup is readiness-gated through public adapter and Application state.

Restart the existing Telegram gateway using your installation's normal service
manager. Check plugin logs and a synthetic proposal before trusting production.
The manifest declares PTB; ensure plugin dependency preparation succeeds. Alternatively,
install the wheel into a disposable Hermes environment: its
`hermes_agent.plugins` entry point registers the same plugin. Do not install both
forms simultaneously. Once published, Hermes' repository installer can install
this directory package. No GitHub URL is invented here.

**Routing:** native pending records contain no authenticated originating chat/topic.
The plugin therefore always uses explicit fallback chat/topic, never a summary,
username, ambient process environment or guessed session. If fallback is General,
leave `fallback_topic_id` unset/null. All cards go there, including background proposals.

## Architecture

- `bridge.py`: public native pending reads, strict single-op validation, immutable
  canonical record hash, configured native store replay.
- `render.py`: complete before/after exact text and conservative Telegram chunks.
- `store.py`: SQLite WAL, FULL synchronous transactions and compare-state claims.
- `service.py`: bounded new deliveries per scan, scoped owner/card callbacks,
  terminal refusal/rejection and quarantined uncertain side effects.
- `plugin.py`: `register_telegram_handler(factory)`, real PTB handler,
  `ctx.spawn_task`/`ctx.on_unload`, per-profile runtime lock and recovery.

State lives in `$HERMES_HOME/plugin-state/telegram-memory-approval/proposals.sqlite3`.
A Unix advisory lock prevents two live transports recovering each other's work.
SQLite unique native IDs and conditional claims prevent duplicate deliveries and
callback replay. Only final confirmed cards become active; existing active cards
survive restart without another send. Sending/applying rows become `uncertain`.
State is persisted before side effects, not after a callback acknowledgement.

## Security and operational limits

Native `memory.write_approval` **must remain on**. Loading refuses when off; scans
and callbacks also refuse while off. This plugin does not turn the gate on and
cannot prevent native writes after an operator turns it off. Local trusted code,
the native CLI approval path and direct filesystem writers are outside its policy.
Do not concurrently approve these records through another interface.

Native pending records are retained for audit, including saved and rejected ones:
there is no public atomic native consume+apply API. SQLite is the authoritative
transport disposition and prevents redisplay/replay. A retained native record is
**not** permission to approve again in the CLI. Archive terminal records after
reconciling them, without deleting the SQLite ledger. Unsupported batches remain
native pending for deliberate native review. Polling enumerates native pending
files; manage audit growth for large deployments (100 new cards maximum per scan,
serial sends). This is a single-profile transport, not a distributed write service.

The reviewed entry contract is **not whole-file snapshot CAS**. Unrelated writes
are valid. An identical entry deleted and recreated cannot be distinguished from
its original by text pinning; duplicate identical entries have native first-match
semantics. Local pending files and ledger are trusted, not signed adversarial data.
Noncanonical leading/trailing whitespace and native § separators are refused,
not silently transformed into different text from the card.

Telegram sends have no idempotency key. A timeout may mean delivery occurred;
uncertain sends are never resent or activated automatically. A crash between
native apply and recording its result is also uncertain, never replayed. This
provides fail-closed at-most-once attempts, **not distributed exactly-once delivery**.
A network error removing buttons cannot reauthorize a terminal row.

### Manual reconciliation and rollback

Stop the gateway/disable the plugin before reconciling uncertain rows. Inspect
SQLite `native_id`, `state`, `record`, `detail`, and native memory with the public
Hermes interfaces. Check the Telegram topic for any orphan card. Determine whether
the exact entry was applied; never simply reset a row to active/sending. Archive
its native pending record after confirming disposition. If necessary stage a
**new** proposal and review a new card. Keep the ledger (treat it as sensitive
memory data); backup SQLite only with a consistent SQLite backup or while stopped.

Rollback: disable `telegram-memory-approval` using `hermes plugins disable`, restart
the gateway, and retain native `memory.write_approval` for native manual review.
Memory writes already saved are not automatically undone; propose a fresh pinned
replacement/removal. Do not wipe the ledger or pending files as a rollback shortcut.

## Verification

No live Telegram token is needed. Tests use real Hermes pending and memory files
in temporary `HERMES_HOME`, fake Telegram I/O, real PTB types and genuine Hermes
directory loader/PluginContext registration and lifecycle.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
HERMES_SOURCE=/path/to/read-only/hermes-checkout .venv/bin/python -m pytest -q
.venv/bin/python -m build
```

CI pins the tested upstream revision. Coverage includes both memory targets,
stale-target refusal, unrelated native writers, exact text chunks, payload rewrite,
non-owner/cross-topic/wrong-message rejection, callback repeat, uncertain sends,
restart recovery, SQLite claims and actual factory registration/cleanup.
Verified locally: **48 tests passed**, with no expected failures, in a clean test
virtualenv. Both wheel and source distribution builds succeeded.
Live gateway installation/network delivery is intentionally **not** claimed tested.
See [compatibility evidence](docs/FEASIBILITY.md). MIT licensed.

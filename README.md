# Hermes Telegram Memory Approval

Deployable Python plugin for **entry-level** review of Hermes' native MEMORY.md
and USER.md proposals in the existing Telegram gateway. No second bot, polling
client, core patches, private memory locks, or prompt-cache mutation.

## Contract

- Native `add`, `patch`, `remove` and legacy `replace`; exactly **✅ Сохранить** and
  **❌ Отклонить**. Full proposed entry is shown, and replacements/deletions show
  the full pinned original. Span patches show the full resulting entry, preserving
  all untouched text (not just the replacement span). Plain text is chunked by UTF-16 length; buttons are
  sent only after all chunks have confirmed delivery.
- Button decisions update the existing card and remove its buttons, without
  modal dialogs or additional messages. Access/validation errors use a short
  non-modal Telegram notification.
- Only the configured numeric owner can act, in the bound chat, topic and exact
  final card message. Pending records are SHA-256 pinned in durable SQLite; any
  record rewrite/missing record invalidates the card, never updates its payload.
- Replace/remove replay through public `apply_memory_pending` with native
  `matched_entry`, checked atomically inside native mutation. A changed/missing
  target is refused. **Independent entries changing MUST NOT invalidate approval.**
  Add saves the immutable exact proposed text, subject to native limits.
- `operations` arrays are supported, including the `new_text` alias. Every operation
  is shown in order, with **one decision for the entire atomic batch**. The original
  native batch is replayed once, never split into independent writes. Invalid or
  unknown operations and legacy destructive proposals without pins fail closed.

## Compatibility and installation

Verified against current Hermes source revision
`3b043e19005fea400a8e556334526ef1d21679e4` (2026-10-15), Python 3.11 and PTB 22.8.
Also tested against `802e0b1daebad8a60da496db1a070fe13ddc45bd` (before span patches).
Requires Unix `fcntl`, native pending/pinned replay APIs, an **in-process** plugin
runtime and Telegram gateway. Host-isolated plugins cannot receive the native PTB
Application. This transport covers only native memory, not OpenViking, skills,
files, or arbitrary third-party providers. Test newer Hermes before rollout.

Installation instructions below are for the operator; this repository does not
change configuration or enable itself. Select the intended profile first and
verify `HERMES_HOME`. Native staging is the safety gate, not a fail-open middleware.

```bash
# Install directly from the repository, but configure before enabling.
hermes plugins install https://github.com/RuBAN-GT/hermes-telegram-memory-approval --no-enable
hermes config set memory.write_approval true
hermes config set plugins.isolation in_process
hermes config set plugins.entries.telegram-memory-approval.settings.owner_user_id 123456789
hermes config set plugins.entries.telegram-memory-approval.settings.fallback_chat_id -1001234567890
# Optional: omit topic setting for General/private chat; set a real topic ID otherwise.
hermes config set plugins.entries.telegram-memory-approval.settings.fallback_topic_id 7
hermes config set plugins.entries.telegram-memory-approval.settings.poll_seconds 5
hermes plugins enable telegram-memory-approval
```

Use your own numeric Telegram user/chat IDs, not the example values. The owner
must be able to read the configured destination, and the existing gateway bot
must be able to send there. All approved memory text is visible to that chat's
members: prefer a private chat or a restricted group.

The installer may ask for consent to install the declared Python dependency.
Do not skip dependency preparation. If dependencies need repair, run
`hermes pm repair`. **Hot reload is not supported:** native factory deduplication
requires a full gateway restart after installation, disable/enable or code changes.
An adapter Application rebuild also requires a full gateway restart for this
transport; ordinary startup is readiness-gated through public adapter and
Application state. Setting `plugins.isolation` affects all plugins in the profile;
only run trusted plugins in-process.

Restart the existing Telegram gateway using your installation's normal service
manager. Check plugin logs and a synthetic proposal before trusting production.
The manifest declares PTB; ensure plugin dependency preparation succeeds. Alternatively,
install the wheel into a disposable Hermes environment: its
`hermes_agent.plugins` entry point registers the same plugin. Do not install both
forms simultaneously. The Git repository installer above is the recommended
installation path; manually copying the repository is not required.

### Naming and updates

| Surface | Name |
| --- | --- |
| GitHub repository / Python distribution | `hermes-telegram-memory-approval` |
| Hermes manifest ID, enable/update commands and settings key | `telegram-memory-approval` |
| Python import package | `telegram_memory_approval` |
| Display label | Telegram Memory Approval |

The `hermes-` repository prefix identifies the host ecosystem; Hermes resolves
the installed plugin ID from `plugin.yaml`, not from the repository's name.

```bash
# Run in the SAME Hermes profile / HERMES_HOME as the gateway.
hermes gateway stop
hermes plugins update telegram-memory-approval
hermes config set memory.write_approval true
hermes gateway start
hermes gateway status
```

Version **0.2.0** adds current Hermes span patches and complete atomic batches,
including background self-improvement proposals previously left without Telegram
cards. Review any installer prompts; if updating fails, fix the reported error
before starting the gateway. For a gateway managed outside the Hermes CLI, use its
normal service manager to stop/start it instead. A full process restart is required.
Existing settings and the SQLite ledger are retained; reinstallation or clearing
pending records is not needed. Previously unsupported valid proposals will be
picked up on the next scan. Already decided/uncertain records are not resent.

### Check background self-improvement

1. Confirm `memory.write_approval: true` in the gateway's active profile and
   `plugins.isolation: in_process`. Ask Hermes to propose a harmless temporary
   memory fact. It must appear as a Telegram card **before** entering MEMORY.md or
   USER.md. Reject it and verify that the fact was not saved.
2. Background proposals use the same configured destination as foreground ones.
   `patch` cards show full before/after entries; batch cards enumerate every
   operation and approve/reject them together. Merely appearing in `/memory pending`
   means **staged, not saved**. This plugin retains even decided native pending
   records for audit; the SQLite ledger prevents replay (see below).
3. Self-improvement can also modify **skills**. These use a separate gate:
   `hermes config set skills.write_approval true`. This plugin does **not** send
   skill approval cards; review them with `/skills pending`, `/skills diff <id>`
   and `/skills approve <id>` or `/skills reject <id>`. Restart the gateway after
   configuration changes. Do not use `approve all` to clear an unfamiliar queue.
4. If memory proposals still have no cards, check gateway logs for
   `Unsupported pending proposal`, `Pending scan failed`, or `Delivery uncertain`,
   and verify the profile, configured chat/topic and loaded plugin version.
   Invalid/legacy unpinned proposals require recreation; uncertain deliveries need
   manual reconciliation, not deleting/resetting the ledger.

**Routing:** native pending records contain no authenticated originating chat/topic.
The plugin therefore always uses explicit fallback chat/topic, never a summary,
username, ambient process environment or guessed session. If fallback is General,
leave `fallback_topic_id` unset/null (Telegram may report General as thread `1`).
All cards go there, including background proposals.

## Architecture

- `bridge.py`: public native pending reads, strict operation/batch validation, immutable
  canonical record hash, configured native atomic replay.
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
On unload, the ledger and runtime lock remain open until the polling task
finishes cancellation; interrupted sends become `uncertain`, not retryable.

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
reconciling them, without deleting the SQLite ledger. Unsupported or invalid proposals
remain native pending for deliberate native review. Polling enumerates native pending
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

Coverage includes both memory targets,
stale-target refusal, unrelated native writers, exact text chunks, payload rewrite,
non-owner/cross-topic/wrong-message rejection, callback repeat, uncertain sends,
restart recovery, SQLite claims and actual factory registration/cleanup.
Verified locally on current Hermes: **78 tests passed**, with no skips or expected
failures. On the pre-patch baseline: **64 passed, 14 skipped** (native span-patch
tests only). Both wheel and source distribution builds succeeded.
Live gateway installation/network delivery is intentionally **not** claimed tested.
See [compatibility evidence](docs/FEASIBILITY.md). MIT licensed.

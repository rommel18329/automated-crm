# Fix: Shared dialer warm-up and softphone call lifecycle
**Started:** 2026-10-13
**Status:** review

## Approved scope
User approved the exact additive SQL and implementation plan in this chat
(after correcting an accidental "Change the plan" response).
Tracked baseline: 968732e. Existing runtime files and unrelated work preserved.

- Shared warm-up from placed calls, deduplicated by Twilio Call SID.
- Chicago dates; completed days only; existing thresholds and 14-day reset unchanged.
- New numbers inherit qualifying pool days only since their enrollment/warm-up date.
- Locked, atomic JSON statistics writes; JSON is no longer warm-up's source.
- Flagging/retiring a number does not erase its consumption of today's pool cap.
- Browser call generations prevent old disconnect/error/accept events from changing
  a newer call. Pending connects cannot overlap, and late connections after Hang Up
  are disconnected. A timed-out join pauses for explicit retry.
- Undialed no-agent batches release claims and return leads to the queue.
- No engine/scoring or message sending changes.

## Migration and recovery
Backup before additive migration: `crm.db.backup-warmup-20261013-182209`.
Exact CREATE statements live in `db.DIALER_HISTORY_MIGRATION`; they are applied
explicitly, never automatically during `init_db` or dashboard startup.
For a fresh install, apply that constant in an explicit migration before dialing.
No existing schema or constraints were changed.

```sql
INSERT OR IGNORE INTO dialer_warmup_credit VALUES
 ('2026-10-06',15,'15 multi-line winner interactions'),
 ('2026-10-09',11,'11 multi-line winner interactions'),
 ('2026-10-12',120,'120 distinct Twilio call IDs in placement log');
```

Imported 120 distinct placed-call IDs from the October 12 supervisor log.
Verified and imported today's 30 outbound pool calls directly from Twilio, using
the same approved parameterized INSERT OR IGNORE statement. The JSON file held
only 12 of those calls. Imports preserve their actual Chicago date; unknown agent
is stored as blank rather than guessed. Historical day credits are lower bounds,
combined with ledger counts using MAX, never added together.

The two reported stuck batches were both no_agent with no winner; Twilio confirmed
zero active conferences for both IDs after the user ended the queue.

## Validation
Regression tests exercise placement deduplication, concurrent writes, profiles
sharing progress, historical-credit overlap, midnight, database isolation, new
numbers, flagged-number usage, late SDK events, pending-connect cancellation,
JavaScript syntax and recovery of undialed timed-out batches.
Expected current pool: day 4/10, 30/175 used, 145 remaining, 15 per number.
Full suite: `python -m pytest tests/ -q --tb=short` — 127 passed.
Both profile queues were checked idle before reloading the webhook/dashboard
under the existing supervisor; no live call was placed for validation.

## Rollback
Revert code if needed and leave the additive history tables intact to preserve
new call records. Do not restore the whole backup over subsequent live CRM work.
Old popup tabs must be closed/reopened after deployment to load the new handlers.

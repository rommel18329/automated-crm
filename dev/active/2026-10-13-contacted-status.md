# Fix: Contacted lead status and filtering
**Started:** 2026-10-13
**Status:** review

## Goal
Keep manual dialer/dashboard classifications consistent with contact stage and Leads filters.

## Approved plan
- Add a shared manual-status update; preserve advanced stages and follow-up dates.
- Dialer status saves today's contact date; dashboard edits preserve historical dates.
- Non-cold classifications take precedence over stale not_contacted stages in filters.
- Back up crm.db and repair stages for IDs 661,712,729,752,776,787,797,836,926,941,972.
- User separately approved restoring last-contact dates from dialer status notes for IDs 712,729,752,776,787,797,836,941,972.
- Add regression coverage and run pytest tests/.

## Scope
Engine scoring and messaging are unchanged. Tracked baseline was clean at 40d537a;
existing untracked runtime files and unrelated work are preserved.
Eleven other cold leads have inbound history with not_contacted stages, including
missed/forwarded calls; no blanket conversation inference is made for those records.

## Results
- 119 tests passed with `python -m pytest tests/ -q --tb=short` outside the Windows
  sandbox (sandbox temp permissions and a Streamlit timeout blocked earlier runs).
- 11 contact stages repaired; nine dialer history dates checked/restored.
- Mark (729) is warm / initial_contact / last contacted 2026-10-12.
- No non-cold leads remain hidden by not_contacted.
- Backup: crm.db.backup-contact-status-20261013-175602.
- Reloaded idle webhook and dashboard services under the existing supervisor.

## Approved SQL applied after backup
```sql
UPDATE leads SET conversation_stage = 'initial_contact'
WHERE conversation_stage = 'not_contacted'
  AND id IN (661,712,729,752,776,787,797,836,926,941,972);

UPDATE leads SET last_contact_date = MAX(COALESCE(last_contact_date, ''),
  (SELECT MAX(substr(timestamp, 1, 10)) FROM interactions
   WHERE lead_id = leads.id AND content IN
     ('Marked COLD from dialer', 'Marked WARM from dialer', 'Marked HOT from dialer')))
WHERE id IN (712,729,752,776,787,797,836,941,972)
  AND EXISTS (SELECT 1 FROM interactions WHERE lead_id = leads.id AND content IN
    ('Marked COLD from dialer', 'Marked WARM from dialer', 'Marked HOT from dialer'));
```

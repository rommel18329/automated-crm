# Rule: No Deletes Without Permission

Never delete a file, function, class, or database record without explicit user permission.

## What This Covers
- Deleting source files (`.py`, `.md`, config files)
- Removing functions or methods from existing code
- Dropping database tables or columns
- Truncating or clearing data in `crm.db`
- Removing imports, constants, or configuration entries

## What To Do Instead
- If code seems unused, flag it with a comment like `# TODO: verify unused before removing`
- If a function needs replacement, add the new one first and ask before removing the old
- If a file needs deletion, state the file path and reason, then wait for confirmation

## Why
Accidental deletions are hard to recover without clean git history. Lead data in crm.db
is especially critical — partial deletes can silently corrupt the follow-up queue.

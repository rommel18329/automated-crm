# Rule: Git Safety

Always commit working code before starting something new.

## Before Starting Any Feature or Fix
1. Run `git status` — confirm the working tree is clean or has only expected changes
2. If uncommitted changes exist, commit them first with a clear message
3. Create a new branch if the change is non-trivial

## Commit Standards
- Commit message must describe what changed and why (not just "fix" or "update")
- Never commit broken tests — run `pytest tests/` first
- Never use `git push --force` unless explicitly asked
- Never amend published commits

## Risky Git Operations — Always Confirm First
- `git reset --hard` — discards local changes permanently
- `git checkout -- .` — reverts all unstaged changes
- `git push --force` — can overwrite upstream work
- Deleting branches that aren't merged

## Branch Strategy
- `main` — stable, tested code only
- Feature branches — name as `feature/<short-description>`
- Fix branches — name as `fix/<short-description>`

## Why
crm.db is gitignored, so git history is the only safety net for code changes.
Half-finished features left uncommitted make it impossible to recover a known-good
state if something breaks during development.

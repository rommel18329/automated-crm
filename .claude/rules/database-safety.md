# Rule: Database Safety

Always show the user the exact change before touching crm.db or any schema definition.

## Schema Changes (crm_engine/db.py)
Before modifying any CREATE TABLE statement, adding/removing columns, or changing
CHECK/FOREIGN KEY constraints:
1. Show the exact SQL diff
2. Explain what data might be affected
3. Write a migration plan (how to update existing rows)
4. Wait for explicit approval

## Data Changes
Before running any INSERT, UPDATE, or DELETE that affects production data:
1. Show the WHERE clause and estimated row count
2. Confirm it is reversible (or note that it is not)
3. Wait for explicit approval

## Never Do Without Asking
- `DROP TABLE` or `DROP COLUMN`
- `DELETE FROM leads` or any bulk delete
- Altering the `status` CHECK constraint (cold|warm|hot|dead|contract)
- Modifying foreign key relationships

## Safe To Do Without Asking
- SELECT queries (read-only)
- INSERT of new rows (new leads, new interactions)
- UPDATE of a single lead by ID when user explicitly requested it

## Why
crm.db is gitignored — there is no automatic backup. A bad schema migration or bulk
DELETE cannot be undone by git. The approval queue and lead history are the core of
the system; data loss here means lost business context.

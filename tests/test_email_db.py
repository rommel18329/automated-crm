from concurrent.futures import ThreadPoolExecutor
import sqlite3

from crm_engine import db
from crm_engine.gmail_client import MAILBOX


def attempt(number):
    return dict(attempt_id=str(number), mailbox=MAILBOX, thread_id='t1', basis_message_id='m1',
                recipient='seller@example.com', subject='Property', approved_text='Thanks',
                approved_by='RJ', approved_at='2026-09-29T12:00:00+00:00',
                rfc_message_id=f'<{number}@example.com>', send_state='sending', updated_at='now')


def test_migration_is_exactly_approved_and_idempotent(tmp_path):
    from pathlib import Path
    approved = (Path(__file__).resolve().parents[1] / 'dev/active/2026-09-29-email-review.md').read_text(encoding='utf-8')
    assert db.EMAIL_MIGRATION.strip() == approved.split('```sql\n')[1].split('```')[0].strip()
    path = tmp_path / 'crm.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE existing_leads (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute("INSERT INTO existing_leads VALUES (1, 'Original')")
    backup = db.migrate_email(path)
    assert backup.exists()
    db.migrate_email(path)
    assert db.email_ready(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT name FROM existing_leads').fetchone()[0] == 'Original'
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='email_reviews'").fetchone()[0] == 0


def test_concurrent_claim_allows_only_one_send(tmp_path):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    def claim(number):
        try:
            db.claim_email_send(attempt(number), path)
            return True
        except sqlite3.IntegrityError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(claim, [1, 2])) == [False, True]
    assert len(db.email_attempts(MAILBOX, path)) == 1


def test_unknown_blocks_retry_but_definite_failure_allows_new_manual_attempt(tmp_path):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    db.claim_email_send(attempt(1), path)
    db.finish_email_send('1', 'unknown', 'now', db_path=path)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        db.claim_email_send(attempt(2), path)
    db.finish_email_send('1', 'failed', 'now', db_path=path)
    db.claim_email_send(attempt(2), path)


def test_failed_sync_retains_success_cursor(tmp_path):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    db.save_email_sync(MAILBOX, 'yesterday', None, 'cursor', path)
    db.save_email_sync(MAILBOX, None, 'offline', db_path=path)
    status = db.email_sync_status(MAILBOX, path)
    assert status['last_success_at'] == 'yesterday'
    assert status['sync_cursor'] == 'cursor'
    assert status['last_error'] == 'offline'

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json

import pytest

from crm_engine import db, caller_id_pool as cp


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / 'crm.db'
    db.init_db(path)
    with db.get_conn(path) as conn:
        conn.executescript(db.DIALER_HISTORY_MIGRATION)
    monkeypatch.setattr(db, 'crm_today', lambda: date(2026, 10, 13))
    monkeypatch.setattr(cp, '_STATS_PATH', tmp_path / 'stats.json')
    return path


def seed_calls(path, day, count, prefix='a'):
    for n in range(count):
        db.record_dialer_call(f'{prefix}{day}-{n}', '+15155550100',
                             'rom' if n % 2 else 'gabby', path,
                             datetime(2026, 10, day, 18, tzinfo=timezone.utc))


def test_shared_progress_counts_placements_not_dispositions(history):
    seed_calls(history, 6, 10)
    seed_calls(history, 9, 10)
    seed_calls(history, 12, 10)
    seed_calls(history, 13, 20)
    assert cp.pool_warmup_days_completed(history) == 3
    assert cp.pool_daily_cap(3) == 175
    with db.get_conn(history) as conn:
        assert conn.execute('SELECT COUNT(*) FROM dispositions').fetchone()[0] == 0


def test_duplicates_credits_and_chicago_midnight(history):
    seed_calls(history, 12, 9)
    stamp = datetime(2026, 10, 13, 1, tzinfo=timezone.utc)  # still Oct 12 Chicago
    assert db.record_dialer_call('midnight', '+15155550100', 'rom', history, stamp)
    assert not db.record_dialer_call('midnight', '+15155550100', 'rom', history, stamp)
    with db.get_conn(history) as conn:
        conn.execute("INSERT INTO dialer_warmup_credit VALUES ('2026-10-12',8,'test')")
    assert db.dialer_day_counts(history) == [{'call_day': '2026-10-12', 'calls': 10}]
    assert cp.pool_warmup_days_completed(history) == 1


def test_low_volume_and_database_isolation(history, tmp_path):
    seed_calls(history, 12, 9)
    assert cp.pool_warmup_days_completed(history) == 0
    seed_calls(history, 12, 1, prefix='extra')
    assert cp.pool_warmup_days_completed(history) == 1
    other = tmp_path / 'other.db'
    with db.get_conn(other) as conn:
        conn.executescript(db.DIALER_HISTORY_MIGRATION)
    assert cp.pool_warmup_days_completed(other) == 0


def test_new_number_does_not_inherit_old_days(history):
    seed_calls(history, 6, 10)
    seed_calls(history, 9, 10)
    seed_calls(history, 12, 10)
    old = db.add_caller_number('+15155550100', '515', db_path=history)
    new = db.add_caller_number('+15155550101', '515', db_path=history)
    db.update_caller_number(old, {'added_date':'2026-07-01', 'warmup_started':'2026-07-01'}, history)
    db.update_caller_number(new, {'added_date':'2026-10-13', 'warmup_started':'2026-10-13'}, history)
    nums = {n['id']: n for n in db.fetch_caller_pool(history)}
    assert cp._effective_cap(nums[old], db_path=history) == 15
    assert cp._effective_cap(nums[new], db_path=history) == 10


def test_concurrent_placements_preserve_json_and_ledger(history):
    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda n: cp.record_placed('+15155550100', f'call-{n}',
                                               'rom' if n % 2 else 'gabby', history), range(40)))
    cp.record_placed('+15155550100', 'call-0', 'rom', history)
    stats = json.loads(cp._STATS_PATH.read_text())['+15155550100']
    assert stats['placed'] == 40
    assert sum(stats['daily'].values()) == 40
    assert sum(row['calls'] for row in db.dialer_day_counts(history)) == 40


def test_flagged_number_usage_still_consumes_pool_capacity(history):
    number_id = db.add_caller_number('+15155550100', '515', db_path=history)
    db.add_caller_number('+15155550101', '515', db_path=history)
    db.update_caller_number(number_id, {'status':'flagged', 'calls_today':80,
                                      'calls_today_date':'2026-10-13'}, history)
    # Counter reads use the same local calendar day as warm-up.
    assert cp.pool_status(history)['remaining_today'] == 0
    assert cp.pool_status(history)['used_today'] == 80
    assert cp.pick_number('5155559999', history) is None

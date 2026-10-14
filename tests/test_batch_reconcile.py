"""Multi-line batches must close even when Twilio status callbacks are lost."""
import webhook_server as ws


class _Call:
    def __init__(self, status):
        self.status = status

    def fetch(self):
        return self


class _FakeTwilio:
    def __init__(self, states):
        self.states = states          # sid -> status (or callable)
        self.fetched = []

    def calls(self, sid):
        self.fetched.append(sid)
        st = self.states[sid]
        return _Call(st() if callable(st) else st)


def _batch(n, resolved=(), winner=None, status="ringing"):
    lead_ids = list(range(1, n + 1))
    b = {"batch_id": "t", "lead_ids": lead_ids, "total": n, "winner": winner,
         "status": status, "resolved_count": 0,
         "call_sids": {lid: f"CA{lid}" for lid in lead_ids}}
    for lid in resolved:
        ws._mark_resolved(b, lid)
    ws._multi_batches["t"] = b
    return b


def test_mark_resolved_is_idempotent():
    b = _batch(3)
    ws._mark_resolved(b, 1)
    ws._mark_resolved(b, 1)
    assert b["resolved_count"] == 1 and b["status"] == "ringing"
    ws._mark_resolved(b, 2)
    ws._mark_resolved(b, 3)
    assert b["status"] == "no_answer"


def test_lost_callbacks_are_reconciled_from_twilio():
    # Today's case: 10 lines, 7 callbacks arrived, 3 lost.
    b = _batch(10, resolved=range(1, 8))
    tw = _FakeTwilio({f"CA{i}": "no-answer" for i in range(1, 11)})
    ws._reconcile_batch("t", tw, sleep=lambda s: None)
    assert b["status"] == "no_answer" and b["resolved_count"] == 10
    assert sorted(tw.fetched) == ["CA10", "CA8", "CA9"]   # only the missing ones


def test_cap_closes_batch_even_if_twilio_says_in_progress():
    b = _batch(2, resolved=[1])
    tw = _FakeTwilio({"CA1": "completed", "CA2": "in-progress"})
    ws._reconcile_batch("t", tw, first_wait=40, step=10, cap=60, sleep=lambda s: None)
    assert b["status"] == "no_answer"


def test_reconciler_leaves_connected_batch_alone():
    b = _batch(3, resolved=[1], winner=2, status="connected")
    tw = _FakeTwilio({f"CA{i}": "no-answer" for i in range(1, 4)})
    ws._reconcile_batch("t", tw, sleep=lambda s: None)
    assert b["status"] == "connected" and tw.fetched == []


def test_late_callback_after_reconcile_does_not_double_count():
    b = _batch(2, resolved=[1])
    ws._reconcile_batch("t", _FakeTwilio({"CA1": "completed", "CA2": "busy"}), sleep=lambda s: None)
    ws._mark_resolved(b, 2)   # the real callback finally shows up
    assert b["resolved_count"] == 2 and b["status"] == "no_answer"


def test_stuck_batch_no_longer_locks_agent():
    b = _batch(2, resolved=[1])
    b["agent"] = "gabby"
    assert ws._agent_batch_busy("gabby")
    ws._reconcile_batch("t", _FakeTwilio({"CA1": "completed", "CA2": "failed"}), sleep=lambda s: None)
    assert not ws._agent_batch_busy("gabby")

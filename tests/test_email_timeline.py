"""email_timeline: GV exclusion, name matching, timestamp normalization."""
from crm_engine import email_timeline as et
from crm_engine.gmail_client import MAILBOX


def test_counterparty_excludes_gv_forward():
    tj = {"messages": [{"from": "kelly <17732178686.13093695663.tok@txt.voice.google.com>"}]}
    assert et._counterparty(tj) == ("", "")


def test_counterparty_real_email():
    tj = {"messages": [{"from": "Jon Kutas <jkutas@gmail.com>"}, {"from": MAILBOX}]}
    name, addr = et._counterparty(tj)
    assert addr == "jkutas@gmail.com"


def test_counterparty_skips_us():
    tj = {"messages": [{"from": MAILBOX}, {"from": "Jon Kutas <jkutas@gmail.com>"}]}
    assert et._counterparty(tj)[1] == "jkutas@gmail.com"


def test_match_lead_first_name_prefix():
    leads = [{"id": 42, "name": "Jonathan Kutas"}, {"id": 7, "name": "Sam Smith"}]
    idx = et._build_lastname_index(leads)
    m = et._match_lead("Jon Kutas", idx)
    assert m and m["id"] == 42


def test_match_lead_ambiguous_returns_none():
    leads = [{"id": 1, "name": "Alice Brown"}, {"id": 2, "name": "Bob Brown"}]
    idx = et._build_lastname_index(leads)
    assert et._match_lead("Carol Brown", idx) is None


def test_norm_ts_to_utc():
    assert et._norm_ts("2026-09-30T16:33:33-05:00") == "2026-09-30T21:33:33"
    assert et._norm_ts("2026-10-09T22:16:55+00:00") == "2026-10-09T22:16:55"


def test_gmail_thread_url():
    u = et.gmail_thread_url("abc123")
    assert u.endswith("/#all/abc123") and MAILBOX in u

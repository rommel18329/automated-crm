"""Google Voice send (relay address) + outbound Sent-email parsing."""
from crm_engine.gmail_client import gv_relay_address, GV_LINE
from crm_engine import gv_gmail_import as gv


def test_relay_address_formats():
    want = f"{GV_LINE}.13093695663@txt.voice.google.com"
    assert gv_relay_address("309-369-5663") == want
    assert gv_relay_address("+1 (309) 369-5663") == want
    assert gv_relay_address("13093695663") == want


def test_relay_address_rejects_bad():
    import pytest
    with pytest.raises(ValueError):
        gv_relay_address("12345")


def test_parse_gv_sent_strips_quote():
    msg = {
        "to": "17732178686.13093695663.abcTOKEN@txt.voice.google.com",
        "date": "Wed, 30 Sep 2026 16:33:33 -0500",
        "body": ("Got it, I'll get you a cash number today.\n"
                 "On Wed, Sep 30, 2026 at 4:20 PM kelly wrote:\n"
                 "> It's on sale\n"),
    }
    out = gv.parse_gv_sent(msg)
    assert out is not None
    assert out["phone"] == "309-369-5663"
    assert out["text"] == "Got it, I'll get you a cash number today."
    assert out["ts"].isoformat() == "2026-09-30T21:33:33"  # 16:33 -05:00 -> 21:33 UTC


def test_parse_gv_sent_ignores_non_gv():
    assert gv.parse_gv_sent({"to": "someone@example.com", "body": "hi"}) is None

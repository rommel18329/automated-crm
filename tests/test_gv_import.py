"""Parser tests for Google Voice -> CRM import (no network)."""
from __future__ import annotations

from crm_engine.gv_gmail_import import parse_gv_email, _extract_message, _fmt_phone


def _email(subject, frm, body):
    return {"subject": subject, "from": frm, "body": body,
            "date": "Wed, 30 Sep 2026 21:23:06 +0000"}


GV_BODY = ("\r\n<https://voice.google.com>\r\nIt's on sale\r\n"
           "YOUR ACCOUNT <https://voice.google.com> HELP CENTER\r\n"
           "<https://support.google.com/voice#topic=1707989> HELP FORUM\r\n"
           "This email was sent to you because you indicated that you'd like to receive\r\n")


def test_parses_sender_name_number_and_message():
    msg = _email(
        "New text message from JOANN ( cash buyer) (773) 226-1355",
        '"JOANN ( cash buyer) (SMS)" <17732178686.17732261355.tH5WV4NMhP@txt.voice.google.com>',
        GV_BODY,
    )
    p = parse_gv_email(msg)
    assert p is not None
    assert p["phone"] == "773-226-1355"          # 2nd From segment, not the GV number
    assert p["name"] == "JOANN ( cash buyer)"
    assert p["text"] == "It's on sale"
    assert p["ts"].year == 2026 and p["ts"].month == 9


def test_multiline_message_strips_footer():
    body = ("\r\n<https://voice.google.com>\r\nLine one\r\nLine two\r\n"
            "YOUR ACCOUNT <https://voice.google.com> HELP CENTER\r\n")
    msg = _email("New text message from Bob (515) 111-2222",
                 '"Bob (SMS)" <15150000000.15151112222.x@txt.voice.google.com>', body)
    p = parse_gv_email(msg)
    assert p["text"] == "Line one\nLine two"
    assert p["phone"] == "515-111-2222"


def test_non_gv_email_is_ignored():
    msg = _email("Re: contract", '"Someone" <someone@gmail.com>', "hello there")
    assert parse_gv_email(msg) is None


def test_phone_formatting():
    assert _fmt_phone("17732261355") == "773-226-1355"
    assert _fmt_phone("7732261355") == "773-226-1355"
    assert _fmt_phone("12345") is None


def test_extract_handles_missing_footer():
    assert _extract_message("\r\n<https://voice.google.com>\r\nJust this\r\n") == "Just this"

"""Parser + dedup helpers for the Google Voice Takeout backfill."""
from crm_engine import gv_takeout_import as gv


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


SAMPLE = '''<html><body><div class="hChatLog hfeed">
<div class="message"><abbr class="dt" title="2026-05-20T14:57:43.743-05:00">x</abbr>:
<cite class="sender vcard"><a class="tel" href="tel:+17732178686"><abbr class="fn">Me</abbr></a></cite>:
<q>Hey, we&#39;re still interested!</q>
</div><div class="message"><abbr class="dt" title="2026-05-20T14:59:57.673-05:00">y</abbr>:
<cite class="sender vcard"><a class="tel" href="tel:+16183223680"><span class="fn">Cletus</span></a></cite>:
<q>Available after 11 tomorrow</q>
</div></div></body></html>'''


def test_parse_directions_and_number(tmp_path):
    f = _write(tmp_path, "Cletus - Text - 2026-05-20T19_57_43Z.html", SAMPLE)
    msgs = gv.parse_text_file(f)
    assert len(msgs) == 2
    assert msgs[0]["direction"] == "outbound"   # from our GV line ("Me")
    assert msgs[1]["direction"] == "inbound"
    assert msgs[1]["their_digits"] == "6183223680"
    assert "still interested" in msgs[0]["body"]


def test_timestamps_convert_to_utc(tmp_path):
    f = _write(tmp_path, "x - Text - 2026-05-20T19_57_43Z.html", SAMPLE)
    msgs = gv.parse_text_file(f)
    # 14:57 -05:00 -> 19:57 UTC
    assert msgs[0]["ts"] == "2026-05-20T19:57:43"


def test_number_from_filename_when_all_outbound(tmp_path):
    body = SAMPLE.replace(
        '<a class="tel" href="tel:+16183223680"><span class="fn">Cletus</span></a>',
        '<a class="tel" href="tel:+17732178686"><abbr class="fn">Me</abbr></a>')
    f = _write(tmp_path, "+13125459119 - Text - 2026-05-20T19_57_43Z.html", body)
    msgs = gv.parse_text_file(f)
    assert gv._thread_number(msgs, f) == "3125459119"


def test_norm():
    assert gv._norm("  Hey   There ") == "hey there"

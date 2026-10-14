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


def _msg(iso, tel, name, body):
    return (f'<div class="message"><abbr class="dt" title="{iso}">x</abbr>:'
            f'<cite class="sender vcard"><a class="tel" href="tel:{tel}"><abbr class="fn">{name}</abbr></a></cite>:'
            f'<q>{body}</q></div>')


def _thread(*msgs):
    return '<html><body><div class="hChatLog hfeed">' + "".join(msgs) + '</div></body></html>'


ME = "+17732178686"


def _db(tmp_path):
    from crm_engine import db
    path = tmp_path / "t.db"
    db.init_db(path)
    return db, path


def test_outbound_only_file_resolved_by_contact_name(tmp_path):
    # Paul's 2026-10-05 text sat alone in its own file: no reply -> no number.
    calls = tmp_path / "Calls"; calls.mkdir()
    _write(calls, "Paul(303 W MAIN ST,IL) - Text - 2026-10-01T19_07_22Z.html", _thread(
        _msg("2026-10-01T14:07:22-05:00", "+16189720618", "Paul", "Sooner the better")))
    _write(calls, "Paul(303 W MAIN ST,IL) - Text - 2026-10-05T19_13_06Z.html", _thread(
        _msg("2026-10-05T14:13:06-05:00", ME, "Me", "At $25k I just don't think we'd make it work")))
    db, path = _db(tmp_path)
    lid = db.create_lead("PAUL WATKINS", "618-972-0618", path)
    plan = gv.plan_import(calls, path)
    added = [b for m in plan["matched"] if m["lead_id"] == lid for _, _, b in m["to_add"]]
    assert any("$25k" in b for b in added)
    assert plan["stats"]["no_number"] == 0 and plan["stats"]["by_name"] == 1


def test_ambiguous_contact_name_is_not_guessed(tmp_path):
    calls = tmp_path / "Calls"; calls.mkdir()
    _write(calls, "Cash buyer - Text - 2026-01-01T00_00_00Z.html", _thread(
        _msg("2026-01-01T00:00:00-06:00", "+13125550001", "A", "hi")))
    _write(calls, "Cash buyer - Text - 2026-01-02T00_00_00Z.html", _thread(
        _msg("2026-01-02T00:00:00-06:00", "+13125550002", "B", "hi")))
    _write(calls, "Cash buyer - Text - 2026-01-03T00_00_00Z.html", _thread(
        _msg("2026-01-03T00:00:00-06:00", ME, "Me", "who gets this?")))
    data = gv.scan(calls)
    assert data["no_number"] == 1
    assert all("who gets this?" not in m["body"] for ms in data["by_num"].values() for m in ms)


def test_alt_phone_matches_lead(tmp_path):
    calls = tmp_path / "Calls"; calls.mkdir()
    _write(calls, "+16308702418 - Text - 2026-04-01T00_00_00Z.html", _thread(
        _msg("2026-04-01T10:00:00-05:00", "+16308702418", "Cindy", "Call me on this one")))
    db, path = _db(tmp_path)
    lid = db.create_lead("CINDY HARDEN", "630-555-0000", path)
    db.update_lead(lid, {"timeline": "Address: 1 Main St. Notes: Alt phones: 630-870-2418, 630-555-1111"}, path)
    plan = gv.plan_import(calls, path)
    assert [m["lead_id"] for m in plan["matched"]] == [lid]
    assert plan["matched"][0]["to_add"][0][2] == "Call me on this one"


def test_reworded_copy_is_not_duplicated_but_distinct_texts_are_kept(tmp_path):
    from datetime import datetime
    calls = tmp_path / "Calls"; calls.mkdir()
    _write(calls, "+16189720618 - Text - 2026-10-05T19_13_06Z.html", _thread(
        _msg("2026-10-05T14:13:06-05:00", ME, "Me", "Hey, I looked at it.\nAt $25k it won&#39;t work."),
        _msg("2026-10-05T14:14:00-05:00", ME, "Me", "Also, is the outbuilding included?"),
        _msg("2026-10-05T14:15:00-05:00", "+16189720618", "Paul", "ok")))
    db, path = _db(tmp_path)
    lid = db.create_lead("PAUL WATKINS", "618-972-0618", path)
    # Hand-pasted copy: different line breaks/apostrophe, 6s earlier.
    db.add_interaction(lid, "text", "Hey, I looked at it. At $25k it won’t work.", "outbound",
                       path, ts=datetime(2026, 10, 5, 19, 13, 0), channel="gv")
    plan = gv.plan_import(calls, path)
    bodies = [b for _, _, b in plan["matched"][0]["to_add"]]
    assert bodies == ["Also, is the outbuilding included?", "ok"] or sorted(bodies) == sorted(
        ["Also, is the outbuilding included?", "ok"])

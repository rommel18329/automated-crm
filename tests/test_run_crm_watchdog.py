"""Public-funnel watchdog + log files in the run_crm supervisor."""
import run_crm


def test_repairs_after_three_public_failures_while_local_ok():
    w = run_crm.FunnelWatch(threshold=3, cooldown=60)
    assert not w.record(False, True, 0)
    assert not w.record(False, True, 5)
    assert w.record(False, True, 10)          # third in a row -> repair


def test_public_success_resets_the_count():
    w = run_crm.FunnelWatch(threshold=3, cooldown=60)
    w.record(False, True, 0)
    w.record(False, True, 5)
    w.record(True, True, 10)
    assert not w.record(False, True, 15)
    assert not w.record(False, True, 20)


def test_local_down_is_left_to_the_restart_path():
    w = run_crm.FunnelWatch(threshold=3, cooldown=60)
    for t in range(0, 50, 5):
        assert not w.record(False, False, t)


def test_cooldown_prevents_reset_loop():
    w = run_crm.FunnelWatch(threshold=3, cooldown=60)
    for t in (0, 5):
        w.record(False, True, t)
    assert w.record(False, True, 10)          # repair at t=10
    fired = [t for t in range(15, 75, 5) if w.record(False, True, t)]
    assert fired == [70]                      # next repair only once 60s have passed


def test_append_log_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(run_crm, "LOG_MAX_BYTES", 50)
    for i in range(10):
        run_crm._append_log("webhook", f"line {i} " + "x" * 20, log_dir=tmp_path)
    assert (tmp_path / "webhook.log").exists()
    assert (tmp_path / "webhook.log.1").exists()
    assert "line 9" in (tmp_path / "webhook.log").read_text(encoding="utf-8")


def test_append_log_never_raises(tmp_path):
    bad = tmp_path / "afile"
    bad.write_text("not a dir")
    run_crm._append_log("x", "hello", log_dir=bad)   # mkdir fails -> swallowed

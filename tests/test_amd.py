"""Unit tests for the self-built AMD classifier (crm_engine/amd_detector.py).

Two modes:

LIVE mode (default — the instant-bridge dialer, where the agent is already
talking to whoever answered):
- a sustained pure tone (voicemail beep)          -> machine
- ANY amount of speech / conversation             -> NO verdict (never kill)
- silence/speech until the live timeout           -> unknown (fail-open)

LEGACY screen-first mode (live_call=False):
- a short greeting followed by silence            -> human
- a long continuous greeting (VM message)         -> machine
- pure silence                                    -> unknown (fail-open)
"""
from __future__ import annotations

import numpy as np

from crm_engine.amd_detector import (
    AmdClassifier, mulaw_to_float, SAMPLE_RATE, DECISION_TIMEOUT,
    LIVE_DECISION_TIMEOUT,
)

rng = np.random.default_rng(42)


def silence(seconds: float) -> np.ndarray:
    n = int(SAMPLE_RATE * seconds)
    return (rng.normal(0, 0.002, n)).astype(np.float32)   # faint line noise


def speech_like(seconds: float) -> np.ndarray:
    """Amplitude-modulated noise ~ speech energy without real phonemes."""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4.0 * t)    # syllable-ish 4 Hz
    return (rng.normal(0, 0.12, n) * envelope).astype(np.float32)


def tone(seconds: float, hz: float = 1000.0, amp: float = 0.3) -> np.ndarray:
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def run(clf: AmdClassifier, audio: np.ndarray, chunk: int = 160) -> str | None:
    for i in range(0, len(audio), chunk):
        v = clf.feed(audio[i:i + chunk])
        if v:
            return v
    return clf.verdict


# ── LIVE mode (default): beep is the ONLY machine signal ───────────────────

def test_live_beep_is_machine():
    audio = np.concatenate([silence(0.4), tone(0.5)])
    clf = AmdClassifier()
    assert run(clf, audio) == "machine"
    assert clf.reason == "beep"


def test_live_conversation_never_killed():
    # The 2026-07-09 regression: continuous conversation audio must NOT be
    # classified as machine on a live call — this hung up on real people.
    audio = np.concatenate([silence(0.3), speech_like(6.0)])
    assert run(AmdClassifier(), audio) is None


def test_live_short_greeting_gets_no_human_verdict_either():
    # Live mode issues no speech-pattern verdicts at all.
    audio = np.concatenate([silence(0.3), speech_like(0.7), silence(1.5)])
    assert run(AmdClassifier(), audio) is None


def test_live_times_out_unknown():
    audio = silence(LIVE_DECISION_TIMEOUT + 0.5)
    assert run(AmdClassifier(), audio) == "unknown"


# ── LEGACY screen-first mode ────────────────────────────────────────────────

def test_legacy_beep_is_machine():
    audio = np.concatenate([silence(0.4), tone(0.5)])
    assert run(AmdClassifier(live_call=False), audio) == "machine"


def test_legacy_short_greeting_then_silence_is_human():
    audio = np.concatenate([silence(0.3), speech_like(0.7), silence(1.2)])
    assert run(AmdClassifier(live_call=False), audio) == "human"


def test_legacy_long_continuous_greeting_is_machine():
    audio = np.concatenate([silence(0.3), speech_like(3.2)])
    assert run(AmdClassifier(live_call=False), audio) == "machine"


def test_legacy_pure_silence_times_out_unknown():
    audio = silence(DECISION_TIMEOUT + 0.5)
    assert run(AmdClassifier(live_call=False), audio) == "unknown"


def test_mulaw_roundtrip_shape_and_range():
    raw = bytes(range(256))
    pcm = mulaw_to_float(raw)
    assert pcm.shape == (256,)
    assert float(np.max(np.abs(pcm))) <= 1.0
    # mu-law decode must produce both polarities
    assert (pcm > 0).any() and (pcm < 0).any()

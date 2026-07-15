"""Self-built answering machine detection (AMD).

Classifies the first seconds of callee audio (8 kHz mu-law mono, as delivered
by Twilio Media Streams) as human or machine using three heuristics:

- HUMAN: a short greeting ("Hello?" / "This is Bob") followed by silence —
  people answer, speak briefly, then wait for you to talk.
- MACHINE: near-continuous speech for several seconds — voicemail greetings
  don't pause to listen.
- BEEP: a sustained narrowband tone is a certain machine signal.

Pure numpy, no I/O — the WebSocket server (amd_server.py) feeds it decoded
audio and acts on the verdict. Deliberately fails toward 'human'/'unknown'
so a real person is never hung up on by a false machine call.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 8000
FRAME_SAMPLES = 160            # 20 ms frames

# Tunables (seconds unless noted)
SPEECH_RMS = 0.015             # absolute floor for "this frame has speech"
NOISE_MULT = 3.0               # ...or N x the adaptive noise floor
HUMAN_MIN_GREETING = 0.15      # shorter than this is a click/noise, not a word
HUMAN_MAX_GREETING = 1.9       # human greetings are short
HUMAN_SILENCE_AFTER = 0.7      # quiet this long after the greeting => human
SEG_BRIDGE_GAP = 0.35          # gaps shorter than this join two speech bursts
MACHINE_CONTINUOUS = 2.6       # one bridged speech run this long => machine
DECISION_TIMEOUT = 4.5         # give up and say 'unknown' (treated as human)
BEEP_MIN_HZ, BEEP_MAX_HZ = 500.0, 2200.0
BEEP_DOMINANCE = 0.55          # fraction of spectral energy in the peak => tonal
BEEP_RUN_WINDOWS = 5           # consecutive tonal windows (~100 ms) => beep

# Live-bridge mode (the instant-connect dialer): the agent is ALREADY talking
# to whoever answered, so speech-pattern rules are meaningless — conversation,
# background noise, and far-end echo of the agent's own voice all read as
# "continuous speech" (which got two real people hung up on, 2026-07-09).
# The ONLY machine signal trusted on a live call is the voicemail BEEP, held
# a bit longer so hold-music notes can't fake it.
LIVE_DECISION_TIMEOUT = 25.0   # listen this long for a beep, then 'unknown'
LIVE_BEEP_RUN_WINDOWS = 10     # ~200 ms sustained tone required in live mode

_BIAS = 0x84


def mulaw_to_float(data: bytes) -> np.ndarray:
    """Decode G.711 mu-law bytes to float32 in [-1, 1]."""
    u = np.frombuffer(data, dtype=np.uint8).astype(np.int32)
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + _BIAS
    t <<= (u & 0x70) >> 4
    mag = t - _BIAS
    pcm = np.where(u & 0x80, -mag, mag).astype(np.float32)
    return pcm / 32768.0


class AmdClassifier:
    """Feed audio chunks; returns 'human' / 'machine' / 'unknown' once, then
    keeps returning the same verdict. Returns None while undecided."""

    def __init__(self, live_call: bool = True) -> None:
        # live_call=True (the instant-bridge dialer, now the only flow):
        # beep-only machine detection. False = legacy screen-first heuristics.
        self.live_call = live_call
        self.verdict: str | None = None
        self.reason: str = ""
        self.t = 0.0                    # seconds of audio processed
        self._residual = np.empty(0, dtype=np.float32)
        self._tail = np.empty(0, dtype=np.float32)   # last 512 samples for FFT
        self._noise_rms: list[float] = []
        # speech-segment state
        self._seg_start: float | None = None   # start of current bridged run
        self._last_speech_end: float | None = None
        self._total_speech = 0.0
        self._first_speech: float | None = None
        self._beep_run = 0

    # ── public ────────────────────────────────────────────────────────────
    def feed(self, pcm: np.ndarray) -> str | None:
        if self.verdict:
            return self.verdict
        buf = np.concatenate([self._residual, pcm])
        n_frames = len(buf) // FRAME_SAMPLES
        for i in range(n_frames):
            frame = buf[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            self._process_frame(frame)
            if self.verdict:
                break
        self._residual = buf[n_frames * FRAME_SAMPLES:]
        return self.verdict

    # ── internals ─────────────────────────────────────────────────────────
    def _threshold(self) -> float:
        if len(self._noise_rms) < 5:
            return SPEECH_RMS
        floor = float(np.percentile(self._noise_rms, 20))
        return max(SPEECH_RMS, floor * NOISE_MULT)

    def _process_frame(self, frame: np.ndarray) -> None:
        self.t += FRAME_SAMPLES / SAMPLE_RATE
        rms = float(np.sqrt(np.mean(frame * frame)))
        self._noise_rms.append(rms)
        if len(self._noise_rms) > 150:
            self._noise_rms.pop(0)

        # Beep detection on a rolling 512-sample window
        self._tail = np.concatenate([self._tail, frame])[-512:]
        if len(self._tail) == 512 and rms > 0.01:
            win = self._tail * np.hanning(512)
            spec = np.abs(np.fft.rfft(win))
            power = spec * spec
            freqs = np.fft.rfftfreq(512, 1.0 / SAMPLE_RATE)
            band = (freqs >= BEEP_MIN_HZ) & (freqs <= BEEP_MAX_HZ)
            total = float(power.sum()) + 1e-12
            if band.any():
                peak_i = int(np.argmax(np.where(band, power, 0.0)))
                lo, hi = max(0, peak_i - 2), peak_i + 3
                dominance = float(power[lo:hi].sum()) / total
                self._beep_run = self._beep_run + 1 if dominance >= BEEP_DOMINANCE else 0
                _need = LIVE_BEEP_RUN_WINDOWS if self.live_call else BEEP_RUN_WINDOWS
                if self._beep_run >= _need:
                    self._decide("machine", "beep")
                    return
        else:
            self._beep_run = 0

        if self.live_call:
            # Live bridged call: NO speech-pattern verdicts — the beep above
            # is the only kill signal. Time out quietly to 'unknown'.
            if self.t >= LIVE_DECISION_TIMEOUT:
                self._decide("unknown", "timeout (live)")
            return

        speech = rms > self._threshold()
        if speech:
            if self._first_speech is None:
                self._first_speech = self.t
            if (self._seg_start is None
                    or (self._last_speech_end is not None
                        and self.t - self._last_speech_end > SEG_BRIDGE_GAP)):
                self._seg_start = self.t     # new bridged run
            self._last_speech_end = self.t
            self._total_speech += FRAME_SAMPLES / SAMPLE_RATE
            # MACHINE: one long, barely-interrupted talk run
            if self._seg_start is not None and (self.t - self._seg_start) >= MACHINE_CONTINUOUS:
                self._decide("machine", "continuous speech")
                return
        else:
            # HUMAN: short greeting then real silence
            if (self._last_speech_end is not None
                    and (self.t - self._last_speech_end) >= HUMAN_SILENCE_AFTER
                    and HUMAN_MIN_GREETING <= self._total_speech <= HUMAN_MAX_GREETING):
                self._decide("human", "short greeting then silence")
                return

        if self.t >= DECISION_TIMEOUT:
            self._decide("unknown", "timeout")

    def _decide(self, verdict: str, reason: str) -> None:
        self.verdict = verdict
        self.reason = reason

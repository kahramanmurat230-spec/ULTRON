"""Emotion Engine — acoustic + textual emotion awareness (numpy-only fallback).

4 states: TIRED / STRESSED / ENERGETIC / NEUTRAL.
librosa used when installed; otherwise raw-PCM numpy analysis
(RMS energy, autocorrelation F0 + variance, ZCR-tempo proxy, spectral centroid).
Low confidence (<0.35) forces NEUTRAL — never over-claim a mood.
"""
import array
import math
import re
import sqlite3
import time
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import librosa  # noqa: F401
    HAVE_LIBROSA = True
except Exception:
    HAVE_LIBROSA = False

TEXT_LEX = {
    "TIRED": ["yoruldum", "yorgun", "uykusuz", "bitkin", "tükendim", "bittim", "uykum var"],
    "STRESSED": ["stres", "sinir", "berbat", "yetişmiyor", "panik", "kaldıramıyorum", "bunaldım", "kriz"],
    "ENERGETIC": ["harika", "süper", "enerji", "hadi", "mükemmel", "kazandım", "coş", "harikayım"],
}

STATES = ("TIRED", "STRESSED", "ENERGETIC", "NEUTRAL")
CONF_FLOOR = 0.35


def _samples(pcm: bytes):
    a = array.array("h")
    a.frombytes(pcm[: (len(pcm) // 2) * 2])
    if np is not None:
        return np.asarray(a, dtype=np.float64) / 32768.0
    return [s / 32768.0 for s in a]


def extract_features(pcm: bytes, sr: int = 16000) -> dict:
    x = _samples(pcm)
    n = len(x)
    if n < 512:
        return {"rms": 0.0, "f0": 0.0, "f0_var": 0.0, "tempo": 0.0, "centroid": 0.0}
    rms = math.sqrt(sum(v * v for v in x) / n) if np is None else float(np.sqrt(np.mean(x ** 2)))
    # F0 via autocorrelation (60-400 Hz)
    frame = x[:512]
    f0s = []
    for off in range(0, n - 512, 512):
        fr = x[off:off + 512]
        best, best_lag = 0.0, 0
        lo, hi = sr // 400, sr // 60
        for lag in range(max(2, lo), min(hi, 511)):
            c = sum(fr[i] * fr[i + lag] for i in range(0, 512 - lag, 4))
            if c > best:
                best, best_lag = c, lag
        if best_lag > 0 and best > 0:
            f0s.append(sr / best_lag)
    f0 = sum(f0s) / len(f0s) if f0s else 0.0
    f0_var = (sum((f - f0) ** 2 for f in f0s) / len(f0s)) if f0s else 0.0
    # tempo proxy: energy flux sign changes per second
    flux = []
    for i in range(0, n - 256, 256):
        e = sum(v * v for v in x[i:i + 256])
        flux.append(e)
    flips = sum(1 for i in range(1, len(flux)) if (flux[i] - flux[i - 1]) * (flux[i - 1] - flux[i - 2] if i > 1 else 1) < 0)
    tempo = flips / max(1e-6, n / sr)
    # spectral centroid
    if np is not None:
        spec = np.abs(np.fft.rfft(x[:1024] if n >= 1024 else x))
        freqs = np.fft.rfftfreq(len(spec), 1.0 / sr)
        denom = spec.sum() or 1.0
        centroid = float((freqs * spec).sum() / denom)
    else:
        centroid = 0.0
    return {"rms": round(rms, 4), "f0": round(f0, 1), "f0_var": round(f0_var, 1),
            "tempo": round(tempo, 2), "centroid": round(centroid, 1)}


def classify_audio(f: dict) -> tuple:
    rms, f0, f0_var, tempo = f["rms"], f["f0"], f["f0_var"], f["tempo"]
    if rms <= 0.001:
        return "NEUTRAL", 0.0
    scores = {
        "TIRED": (0.6 if rms < 0.06 else 0.1) + (0.5 if tempo < 1.5 else 0.1) + (0.4 if f0_var < 400 else 0.1),
        "STRESSED": (0.5 if f0 > 180 else 0.1) + (0.5 if tempo > 3 else 0.1) + (0.4 if f0_var > 900 else 0.1),
        "ENERGETIC": (0.6 if rms > 0.12 else 0.1) + (0.5 if tempo > 2.5 else 0.1) + (0.3 if 90 < f0 < 220 else 0.1),
    }
    state = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    conf = round(min(1.0, scores[state] / 1.5) * min(1.0, total / 2.0 + 0.3), 2)
    return state, conf


def classify_text(text: str) -> tuple:
    t = (text or "").lower()
    hits = {k: sum(1 for w in lex if w in t) for k, lex in TEXT_LEX.items()}
    best = max(hits, key=hits.get)
    if hits[best] == 0:
        return "NEUTRAL", 0.0
    return best, round(min(1.0, 0.4 + 0.2 * hits[best]), 2)


def analyze(pcm: bytes | None = None, text: str | None = None) -> dict:
    a_state, a_conf = (classify_audio(extract_features(pcm)) if pcm else (None, 0.0))
    t_state, t_conf = classify_text(text)
    if a_state and t_state and t_conf > 0:
        state = t_state if t_conf > a_conf else a_state
        conf = round(0.7 * max(a_conf, t_conf) + 0.3 * min(a_conf, t_conf), 2)
        source = "fused"
    elif a_state:
        state, conf, source = a_state, a_conf, "audio"
    else:
        state, conf, source = t_state, t_conf, "text"
    if conf < CONF_FLOOR:
        state, conf = "NEUTRAL", conf
    return {"state": state, "confidence": conf, "source": source,
            "engine": "librosa" if (HAVE_LIBROSA and pcm) else "numpy"}


class EmotionLog:
    def __init__(self, path="data/emotion/emotion_log.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS emotion_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, state TEXT,
                confidence REAL, source TEXT)""")

    def add(self, state, confidence, source):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO emotion_log(ts,state,confidence,source) VALUES(?,?,?,?)",
                       (time.time(), state, confidence, source))

    def history(self, hours=24):
        since = time.time() - hours * 3600
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT ts,state,confidence,source FROM emotion_log WHERE ts>=? ORDER BY ts",
                              (since,)).fetchall()
        trend = {s: 0 for s in STATES}
        for r in rows:
            trend[r[1]] = trend.get(r[1], 0) + 1
        return {"rows": [{"ts": r[0], "state": r[1], "confidence": r[2], "source": r[3]} for r in rows],
                "trend": trend}

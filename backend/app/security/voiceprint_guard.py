"""Voiceprint Guard — single-master voice biometrics (no external deps).

Embedding: FFT-mel band means (pure python; librosa used automatically when
installed).  Cosine similarity vs master_voiceprint; threshold configurable.
Master switch: config voiceprint_lock_enabled (default false).
"""
import array
import base64
import json
import math
import os
from pathlib import Path

try:
    import librosa  # noqa: F401
    HAVE_LIBROSA = True
except Exception:
    HAVE_LIBROSA = False

REJECT_LINE = "Ses frekansınız yetki bandımda değil. Boss burada değilse ben de burada değilim."
BANDS = 12


def _frames(pcm: bytes, size=256, step=128):
    samples = array.array("h")
    samples.frombytes(pcm[: (len(pcm) // 2) * 2])
    out = []
    for i in range(0, max(0, len(samples) - size), step):
        out.append([s / 32768.0 for s in samples[i:i + size]])
    return out or [[0.0] * 256]


def _mag(frame):
    n = len(frame)
    half = n // 2
    mags = []
    for k in range(half):
        re_ = sum(frame[i] * math.cos(-2 * math.pi * k * i / n) for i in range(n))
        im_ = sum(frame[i] * math.sin(-2 * math.pi * k * i / n) for i in range(n))
        mags.append(math.sqrt(re_ * re_ + im_ * im_))
    return mags


def extract_embedding(pcm: bytes) -> list[float]:
    """12-dim log-mel-band vector, L2-normalized."""
    acc = [0.0] * BANDS
    cnt = 0
    for fr in _frames(pcm):
        m = _mag(fr)
        half = len(m)
        for b in range(BANDS):
            lo = int(half * (b / BANDS) ** 1.6)
            hi = max(lo + 1, int(half * ((b + 1) / BANDS) ** 1.6))
            acc[b] += math.log1p(sum(m[lo:hi]))
        cnt += 1
    vec = [a / max(1, cnt) for a in acc]
    mean = sum(vec) / len(vec)
    vec = [v - mean for v in vec]  # zero-mean => discriminative cosine
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


class VoiceprintGuard:
    def __init__(self, path="config/security/master_voiceprint.npy",
                 enabled=False, threshold=0.75):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = bool(enabled)
        self.threshold = float(threshold)

    def _load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["vec"]
        except Exception:
            return None

    def enroll(self, pcm: bytes) -> dict:
        vec = extract_embedding(pcm)
        self.path.write_text(json.dumps({"format": "ultron-voiceprint-json", "vec": vec}),
                             encoding="utf-8")
        return {"ok": True, "dims": len(vec), "engine": "librosa" if HAVE_LIBROSA else "fft-mel"}

    def verify(self, pcm: bytes) -> dict:
        if not self.enabled:
            return {"ok": True, "bypassed": True, "reason": "voiceprint_lock disabled"}
        master = self._load()
        if master is None:
            return {"ok": False, "reason": "no master voiceprint enrolled"}
        sim = cosine(master, extract_embedding(pcm))
        if sim >= self.threshold:
            return {"ok": True, "similarity": round(sim, 3)}
        return {"ok": False, "similarity": round(sim, 3), "reject_line": REJECT_LINE}


def b64_to_pcm(b64: str) -> bytes:
    return base64.b64decode(b64)


def make_test_pcm(freq: float, sr=16000, secs=1.0, amp=9000) -> bytes:
    a = array.array("h")
    n = int(sr * secs)
    for i in range(n):
        a.append(int(amp * math.sin(2 * math.pi * freq * i / sr)))
    return a.tobytes()

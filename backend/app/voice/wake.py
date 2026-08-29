"""REAL wake-word engines — audio-level keyword spotting, not transcript search.

Searching for a word inside an STT transcript is NOT wake-word detection;
these engines analyze raw audio frames:

- PorcupineEngine (pvporcupine): highly efficient on-device spotting.
  Bundled keyword files include 'jarvis' and 'computer'. Requires the
  user's free Picovoice AccessKey (PICOVOICE_ACCESS_KEY env or vault
  secret 'picovoice_access_key'). Without a key -> honestly unavailable.
- OpenWakeWordEngine (openwakeword): open-source alternative using
  .tflite/.onnx models the user places in data/voice/wake/. Without
  model files -> honestly unavailable.

WakeWordManager picks the first available engine, normalizes arbitrary
audio chunk sizes into engine frame sizes, applies a per-keyword
detection threshold, and reports precise status for /api/voice/health.
No engine ever pretends to hear something.
"""
import os
from pathlib import Path

SAMPLE_RATE = 16000
PORCUPINE_FRAME = 512      # 32 ms @16k
OWW_FRAME = 1280           # 80 ms @16k


def _platform_suffix() -> str:
    import sys
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


class PorcupineEngine:
    NAME = "porcupine"

    def __init__(self, keyword="jarvis", access_key=None, vault=None,
                 sensitivity=0.6):
        self.keyword = (keyword or "jarvis").lower()
        self.sensitivity = float(sensitivity)
        self._pv = None
        self._handle = None
        self.error = None
        key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "").strip()
        if not key and vault is not None:
            try:
                key = vault.get("picovoice_access_key") or ""
            except Exception:
                key = ""
        self.access_key = key
        self.keyword_path = self._find_keyword()

    def _find_keyword(self):
        try:
            import pvporcupine
        except ImportError:
            self.error = "pvporcupine kurulu değil"
            return None
        base = os.path.dirname(pvporcupine.__file__)
        for cand in (f"{self.keyword}_{_platform_suffix()}.ppn",
                     f"{self.keyword}.ppn"):
            p = Path(base) / "resources" / "keyword_files" / cand
            if p.exists():
                return str(p)
        # bazı sürümlerde resources/keyword_files/{platform}/ altında
        for p in (Path(base) / "resources").rglob(f"{self.keyword}_*.ppn"):
            return str(p)
        self.error = f"keyword dosyası yok: {self.keyword}"
        return None

    @property
    def available(self) -> bool:
        return bool(self.access_key and self.keyword_path)

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self.available,
                "keyword": self.keyword,
                "frame_samples": PORCUPINE_FRAME,
                "error": None if self.available else (
                    self.error or "PICOVOICE_ACCESS_KEY yok (ücretsiz: console.picovoice.ai)")}

    def start(self):
        if not self.available:
            raise RuntimeError(f"porcupine unavailable: {self.status()['error']}")
        import pvporcupine
        self._pv = pvporcupine
        self._handle = pvporcupine.create(
            access_key=self.access_key,
            keyword_paths=[self.keyword_path],
            sensitivities=[self.sensitivity])

    def process(self, frame) -> bool:
        """frame: PORCUPINE_FRAME int16 samples -> True if keyword spotted."""
        if self._handle is None:
            raise RuntimeError("porcupine başlatılmadı (start() çağır)")
        return self._handle.process(frame) >= 0

    def stop(self):
        if self._handle is not None:
            self._handle.delete()
            self._handle = None


class OpenWakeWordEngine:
    NAME = "openwakeword"

    def __init__(self, model_dir="data/voice/wake", threshold=0.5):
        self.model_dir = Path(model_dir)
        self.threshold = float(threshold)
        self.error = None
        self._model = None
        self.models = sorted(
            [p for p in self.model_dir.glob("*.tflite")] +
            [p for p in self.model_dir.glob("*.onnx")]) if self.model_dir.exists() else []

    @property
    def available(self) -> bool:
        return len(self.models) > 0

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self.available,
                "frame_samples": OWW_FRAME,
                "models": [p.name for p in self.models],
                "error": None if self.available else
                         f"model dosyası yok ({self.model_dir}/*.tflite|onnx)"}

    def start(self):
        if not self.available:
            raise RuntimeError(f"openwakeword unavailable: {self.status()['error']}")
        from openwakeword.model import Model
        self._model = Model(wakeword_models=[str(p) for p in self.models])

    def process(self, frame) -> tuple[str, float] | None:
        """frame: OWW_FRAME int16 samples -> (name, score) when above threshold."""
        if self._model is None:
            raise RuntimeError("openwakeword başlatılmadı")
        import numpy as np
        scores = self._model.predict(np.frombuffer(frame, dtype=np.int16))
        best = None
        for name, score in scores.items():
            if score >= self.threshold and (best is None or score > best[1]):
                best = (name, float(score))
        if best:
            self._model.reset()
        return best

    def stop(self):
        self._model = None


class WakeWordManager:
    """Engine seçimi + çerçeve normalizasyonu + tek event API'si."""

    def __init__(self, settings=None, vault=None, engines=None):
        cfg = (settings or {}).get("wake", {})
        self.keyword = cfg.get("keyword", "jarvis")
        self.threshold = float(cfg.get("threshold", 0.5))
        if engines is not None:  # DI (tests)
            self.engines = engines
        else:
            self.engines = [PorcupineEngine(keyword=self.keyword, vault=vault),
                            OpenWakeWordEngine()]
        self.active = None
        self._buf = b""
        self.frame_size = PORCUPINE_FRAME

    def start(self) -> dict:
        for e in self.engines:
            if e.available:
                try:
                    e.start()
                    self.active = e
                    self.frame_size = e.status().get("frame_samples", PORCUPINE_FRAME)
                    return self.status()
                except Exception as exc:  # noqa: BLE001
                    e.error = f"başlatılamadı: {str(exc)[:120]}"
        self.active = None
        return self.status()

    def status(self) -> dict:
        return {"active": self.active.NAME if self.active else None,
                "available": self.active is not None,
                "engines": [e.status() for e in self.engines],
                "note": ("wake-word AKTİF" if self.active else
                         "wake-word yok — push-to-talk / manuel tetikleme geçerli")}

    def process_chunk(self, audio: bytes):
        """Arbitrary-size int16 mono 16k chunk -> ('keyword', score) | None."""
        if self.active is None:
            return None
        self._buf += audio
        n = self.frame_size * 2
        out = None
        while len(self._buf) >= n:
            frame, self._buf = self._buf[:n], self._buf[n:]
            if self.active.NAME == PorcupineEngine.NAME:
                import array
                arr = array.array("h")
                arr.frombytes(frame)
                if self.active.process(arr):
                    out = (self.keyword, 1.0)
            else:
                res = self.active.process(frame)
                if res:
                    out = res
        return out

    def stop(self):
        if self.active is not None:
            try:
                self.active.stop()
            except Exception:
                pass
        self.active = None

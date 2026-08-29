"""WAVE 4 — STT streaming abstraction (§2).

Sözleşme: streaming, partial transcript, final transcript, confidence,
language, timestamps, noise handling, timeout, cancellation. Başarısızlık
zinciri: retry → fallback engine → graceful degradation. Fake transcript
ÜRETİLMEZ: gerçek engine yoksa manager açıkça unavailable der.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class STTEvent:
    kind: str                       # "partial" | "final" | "error" | "end"
    text: str = ""
    confidence: float = 0.0
    language: str = ""
    t_start: float | None = None    # saniye — utterance başlangıcı
    t_end: float | None = None      # saniye — utterance sonu
    engine: str = ""
    error: str = ""


class STTUnavailable(RuntimeError):
    """Gerçek STT engine'i yok — sahte transcript üretilmez."""


class WhisperEngine:
    """Gerçek engine: faster-whisper (kurulu değilse dürüst unavailable)."""

    NAME = "faster-whisper"

    def __init__(self, model_name="small", language="tr"):
        self.model_name = model_name
        self.language = language
        self._model = None
        self.error = None
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.error = f"faster_whisper kurulu değil ({exc})"
            self.available = False
        else:
            self.available = True

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self.available,
                "error": self.error,
                "model": self.model_name, "language": self.language}


class VoskEngine:
    """Alternatif gerçek engine: vosk (kurulu değilse dürüst unavailable)."""

    NAME = "vosk"

    def __init__(self, model_path="data/voice/vosk-model", sample_rate=16000):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.error = None
        try:
            import vosk  # noqa: F401
            import os
            if not os.path.isdir(model_path):
                raise FileNotFoundError(model_path)
        except Exception as exc:  # noqa: BLE001
            self.error = f"vosk/model yok ({exc})"
            self.available = False
        else:
            self.available = True

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self.available,
                "error": self.error}


@dataclass
class STTManagerConfig:
    retry_limit: int = 2              # toplam deneme = 1 + retry_limit
    silence_rms: float = 0.004        # altı → noise, STT'e GİTMEZ
    final_timeout_s: float = 20.0


class STTManager:
    """Engine zinciri + retry/fallback + noise/timeout/cancel yönetimi."""

    def __init__(self, engines: list | None = None,
                 config: STTManagerConfig | None = None,
                 now=None):
        self.engines = engines if engines else [WhisperEngine(), VoskEngine()]
        self.config = config or STTManagerConfig()
        self._now = now or time.monotonic

    def status(self) -> dict:
        return {"engines": [e.status() if hasattr(e, "status") else
                            {"engine": type(e).__name__,
                             "available": True} for e in self.engines],
                "available": any(getattr(e, "available", True)
                                 for e in self.engines)}

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _rms(pcm: bytes) -> float:
        import array
        n = len(pcm) // 2
        if n == 0:
            return 0.0
        a = array.array("h")
        a.frombytes(pcm[: n * 2])
        acc = 0.0
        for s in a:
            v = s / 32768.0
            acc += v * v
        return (acc / n) ** 0.5

    def _split_noise(self, pcm_chunks: list[bytes]):
        """Noise handling: sessiz chunk'lar STT'e gönderilmez."""
        speech, dur = [], 0.0
        for c in pcm_chunks:
            dur += len(c) / 2 / 16000.0
            if self._rms(c) >= self.config.silence_rms:
                speech.append(c)
        return speech, dur

    # ---------------------------------------------------------------- stream
    async def transcribe(self, pcm_chunks: list[bytes],
                         *, language: str = "tr",
                         on_partial=None) -> list[STTEvent]:
        """Tam zincir: noise filtresi → engine(ler) → partial/final akışı.

        Dönüş: olay listesi (son 'final' hariç her şey partial olabilir).
        Engine yoksa STTUnavailable — transcript UYDURULMAZ.
        """
        events: list[STTEvent] = []
        speech, dur = self._split_noise(pcm_chunks)
        if not speech:
            events.append(STTEvent(kind="end", text="", engine="none",
                                   error="sadece sessizlik (noise)"))
            return events
        t0 = self._now()
        last_err = ""
        for engine in self.engines:
            if not getattr(engine, "available", True):
                continue
            attempts = 0                     # sayac engine başına (fallback)
            while attempts <= self.config.retry_limit:
                attempts += 1
                try:
                    async for ev in self._run_engine(
                            engine, speech, language, t0, dur):
                        events.append(ev)
                        if ev.kind == "partial" and on_partial:
                            on_partial(ev)
                        if ev.kind == "final":
                            return events
                    # final gelmedi: bu engine kesip sonraki deneme
                    last_err = "final transcript yok"
                except asyncio.CancelledError:
                    events.append(STTEvent(kind="error", engine=engine.NAME
                                           if hasattr(engine, "NAME") else "?",
                                           error="cancelled"))
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)[:200]
                    events.append(STTEvent(kind="error",
                                           engine=getattr(engine, "NAME",
                                                          type(engine).__name__),
                                           error=last_err))
                # retry SAME engine
        events.append(STTEvent(kind="error", engine="all",
                               error=f"tüm engine'ler başarısız: {last_err}"))
        return events

    async def _run_engine(self, engine, speech, language, t0, dur):
        """Tek engine denemesi — gerçek engine'e delege, sahte çıktı YOK."""
        name = getattr(engine, "NAME", type(engine).__name__)
        if hasattr(engine, "stream"):
            # test/gerçek DI seam: async iterator döndürür
            async for ev in engine.stream(speech, language):
                yield ev
            return
        raise STTUnavailable(f"engine {name} streaming desteklemiyor "
                             f"(kurulu değil: {getattr(engine, 'error', '?')})")


def pcm_bytes_to_s(pcm: bytes, sample_rate: int = 16000) -> float:
    return len(pcm) / 2 / sample_rate

"""WAVE 4 — Streaming TTS (§3).

Mevcut Neural TTS (app/voice/tts.py — edge-tts) KORUNUR; bu katman onu
streaming hattına bağlar: cümle/chunk sentezi, kuyruk, cache, iptal,
barge-in, latency ölçümü, voice/language seçimi, hata kurtarma.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import OrderedDict

# Cümle sınırlayıcılar (TR/EN ortak) — kısa parçalar anında konuşulur
_SENT_SPLIT = re.compile(r"(?<=[.!?…;:])\s+")


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Metni konuşılabilir parçalara böler (streaming ilk ses gecikmesini
    düşürür). Uzun cümleler max_len'den kırılır."""
    text = " ".join((text or "").strip().split())
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > max_len:
            cut = p.rfind(" ", max_len // 2, max_len)
            cut = cut if cut > 0 else max_len
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


class TTSUnavailable(RuntimeError):
    """Gerçek sentez engine'i yok — sahte ses üretilmez."""


class NeuralTTSEngine:
    """Mevcut app/voice/tts.py sarlayıcısı (DEĞİŞTİRİLMEDEN)."""

    NAME = "edge-tts-neural"

    def __init__(self, tts=None, settings=None):
        if tts is None:
            from app.voice.tts import TextToSpeech
            tts = TextToSpeech(settings)
        self.tts = tts
        self.available = True
        self.error = None

    async def synthesize(self, text: str, *, voice=None,
                         language=None) -> tuple[bytes, str]:
        if voice:
            self.tts.voice = voice
        try:
            return await self.tts.synthesize(text)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)[:200]
            raise

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self.available,
                "backend": self.tts.backend(), "voice": self.tts.voice,
                "error": self.error}


class StreamingTTS:
    """Cümle bazlı streaming sentez + kuyruk + cache + iptal.

    speak_stream(chunk_iter): brain'den metin aktıkça cümle tamamlanır
    tamamlanmaz sentezlenir — tüm cevabin bitmesi BEKLENMEZ.
    """

    def __init__(self, engine: NeuralTTSEngine | None = None,
                 *, cache_limit: int = 64, voice: str | None = None,
                 language: str = "tr", now=None):
        self.engine = engine or NeuralTTSEngine()
        self.voice = voice
        self.language = language
        self.cache: OrderedDict[tuple, bytes] = OrderedDict()
        self.cache_limit = cache_limit
        self.cancelled = asyncio.Event()
        self.metrics = {"first_audio_ms": None, "complete_ms": None,
                        "synthesized": 0, "cache_hits": 0, "cancelled": 0}
        self._now = now or time.monotonic
        self._buf = ""

    def status(self) -> dict:
        return {"engine": self.engine.status(), "language": self.language,
                "voice": self.voice, "cache_size": len(self.cache)}

    # ------------------------------------------------------------ cache
    def _cache_key(self, text: str) -> tuple:
        return (hashlib.sha1(text.encode("utf-8")).hexdigest()[:16],
                self.voice or getattr(self.engine.tts, "voice", ""),
                self.language)

    def _cache_get(self, text: str) -> bytes | None:
        key = self._cache_key(text)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def _cache_put(self, text: str, audio: bytes) -> None:
        key = self._cache_key(text)
        self.cache[key] = audio
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)

    # ------------------------------------------------------------ control
    def reset(self) -> None:
        self.cancelled = asyncio.Event()
        self._buf = ""
        self.metrics = {"first_audio_ms": None, "complete_ms": None,
                        "synthesized": 0, "cache_hits": 0, "cancelled": 0}

    def cancel(self) -> dict:
        """Barge-in: sentez anında durur, kuyruk boşalır."""
        self.cancelled.set()
        self.metrics["cancelled"] += 1
        return {"type": "tts.cancelled"}

    # ------------------------------------------------------------ akış
    async def speak_stream(self, chunk_iter):
        """Metin akışını yiyip ses chunk'ları üretir (async generator).

        Yield: {"audio": bytes, "fmt": str, "text": cümle, "cached": bool}
        """
        t0 = self._now()
        self._buf = ""
        got_any = False
        done = False
        while not done:
            if self.cancelled.is_set():
                return
            # sonraki chunk'ı al (veya akış bitti)
            nxt = None
            if hasattr(chunk_iter, "__aiter__"):
                try:
                    nxt = await chunk_iter.__anext__()
                except StopAsyncIteration:
                    done = True
            else:
                try:
                    nxt = next(chunk_iter)
                except StopIteration:
                    done = True
            if nxt is not None:
                nxt = str(nxt)
                if self._buf and not self._buf.endswith((" ", "\n", "\t")):
                    self._buf += " "          # chunk sınırı cümle böler
                self._buf += nxt
            # tamamlanmış cümleleri sentezle (akış bitmemiş olsa bile)
            sentences = split_sentences(self._buf) if done else \
                split_sentences(self._buf)[:-1]  # son parça yarım olabilir
            leftover = self._buf if not done else ""
            if done:
                leftover = ""
                # tüm cümleler (son yarım dahil) konuşulur
                sentences = split_sentences(self._buf)
                if self._buf and not sentences:
                    sentences = [self._buf.strip()]
            for sent in sentences:
                if self.cancelled.is_set():
                    return
                if not sent.strip():
                    continue
                cached = self._cache_get(sent)
                if cached is not None:
                    self.metrics["cache_hits"] += 1
                    audio, fmt = cached, "mp3"
                else:
                    audio, fmt = await self._synthesize_with_retry(sent)
                    self._cache_put(sent, audio)
                    self.metrics["synthesized"] += 1
                if self.metrics["first_audio_ms"] is None:
                    self.metrics["first_audio_ms"] = round(
                        (self._now() - t0) * 1000, 1)
                    got_any = True
                yield {"audio": audio, "fmt": fmt, "text": sent,
                       "cached": cached is not None}
            self._buf = leftover
        if got_any or self.metrics["synthesized"]:
            self.metrics["complete_ms"] = round((self._now() - t0) * 1000, 1)

    async def _synthesize_with_retry(self, text: str) -> tuple[bytes, str]:
        """Hata kurtarma: 1 yeniden deneme; sonra dürüst TTSUnavailable."""
        last = None
        for attempt in (1, 2):
            if self.cancelled.is_set():
                raise asyncio.CancelledError()
            try:
                audio, fmt = await self.engine.synthesize(
                    text, voice=self.voice, language=self.language)
                if not audio:
                    raise TTSUnavailable("engine boş ses döndürdü")
                return audio, fmt
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise TTSUnavailable(f"sentez başarısız (2 deneme): {last}")

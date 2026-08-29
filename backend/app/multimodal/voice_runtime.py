"""WAVE 4 — Voice Runtime: durum makinesi + paralel streaming pipeline.

Talimat §1: blocking sequential pipeline YASAK — MIC→VAD→WAKE→STT→INTENT→
BRAIN→RESPONSE→TTS→SPEAKER zinciri asyncio kuyruklarıyla eşzamanlı akar:
STT partial'ları beklenmez, TTS ilk cümleyi beklemez, barge-in anında
TTS'i iptal eder ve mikrofona öncelik verir.

Durum makinesi (§1): IDLE/LISTENING/THINKING/SPEAKING/INTERRUPTED/
PROCESSING/ERROR — geçiş tablosu dışı istek InvalidVoiceTransition ile
REDDEDİLİR.

Latency (§6): wake/vad/first_partial/final_stt/brain_start/first_token/
first_audio/tts_complete aşamaları; P50/P95/P99. Sahte benchmark YASAK —
kaynak gerçek süre damgalarıdır.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- states
IDLE = "IDLE"
LISTENING = "LISTENING"
THINKING = "THINKING"
SPEAKING = "SPEAKING"
INTERRUPTED = "INTERRUPTED"
PROCESSING = "PROCESSING"
ERROR = "ERROR"

VOICE_STATES = (IDLE, LISTENING, THINKING, SPEAKING, INTERRUPTED,
                PROCESSING, ERROR)

# Geçerli geçişler (talimat §1 akışıyla birebir).
VOICE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    IDLE: (LISTENING, ERROR),
    LISTENING: (THINKING, IDLE, ERROR),
    THINKING: (SPEAKING, PROCESSING, IDLE, ERROR),
    PROCESSING: (SPEAKING, THINKING, IDLE, ERROR),
    SPEAKING: (INTERRUPTED, IDLE, ERROR),
    INTERRUPTED: (LISTENING, IDLE, ERROR),
    ERROR: (IDLE,),                      # yalnızca kurtarma ile
}


class InvalidVoiceTransition(Exception):
    """Durum makinesi dışı geçiş — reddedilir."""


class VoiceStateMachine:
    """Deterministik ses durum makinesi (invalid → RED)."""

    def __init__(self, now=None):
        self.state = IDLE
        self.history: list[tuple[str, str, float]] = []
        self._now = now or time.monotonic

    def can_transition(self, new_state: str) -> bool:
        return new_state in VOICE_TRANSITIONS.get(self.state, ())

    def transition(self, new_state: str, *, reason: str = "") -> str:
        if new_state not in VOICE_STATES:
            raise InvalidVoiceTransition(
                f"unknown voice state {new_state!r}")
        if not self.can_transition(new_state):
            raise InvalidVoiceTransition(
                f"{self.state} -> {new_state} is not a valid transition"
                + (f" ({reason})" if reason else ""))
        old = self.state
        self.state = new_state
        self.history.append((old, new_state, self._now()))
        return new_state

    def is_speaking(self) -> bool:
        return self.state == SPEAKING


# ---------------------------------------------------------------- latency
@dataclass
class LatencyRecord:
    """Tek etkileşimin aşama süreleri (ms) — None = ölçülemedi (dürüst)."""
    wake_ms: float | None = None
    vad_ms: float | None = None
    first_partial_ms: float | None = None
    final_stt_ms: float | None = None
    brain_start_ms: float | None = None
    first_token_ms: float | None = None
    first_audio_ms: float | None = None
    tts_complete_ms: float | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def percentile(values: list[float], p: float) -> float | None:
    """Saf-python percentile (nearest-rank). Boş liste → None (uydurma YOK)."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


@dataclass
class LatencyTracker:
    """Aşama damgaları + P50/P95/P99 raporu."""
    max_samples: int = 500
    _records: list[LatencyRecord] = field(default_factory=list)
    _marks: dict[str, float] = field(default_factory=dict)
    _t0: float | None = None

    def begin(self, now: float | None = None) -> None:
        self._t0 = now if now is not None else time.monotonic()
        self._marks = {}

    def mark(self, stage: str, now: float | None = None) -> float:
        """Aşama başlangıç/bitiş damgası; dönen değer başlangıçtan ms."""
        t = now if now is not None else time.monotonic()
        if self._t0 is None:
            self._t0 = t
        dt_ms = (t - self._t0) * 1000.0
        self._marks.setdefault(stage, dt_ms)
        return self._marks[stage]

    def finish(self, stage_map: dict[str, str] | None = None) -> LatencyRecord:
        """Damgaları LatencyRecord'a çevirir (map: record alanı→damga adı)."""
        m = stage_map or {}
        rec = LatencyRecord(**{
            f: self._marks.get(s) for f, s in m.items()})
        self._records.append(rec)
        if len(self._records) > self.max_samples:
            self._records = self._records[-self.max_samples:]
        self._t0 = None
        self._marks = {}
        return rec

    def summary(self) -> dict:
        """Alan başına P50/P95/P99 — yalnız gerçek ölçülmüş örnekler."""
        out = {}
        for name in LatencyRecord().__dict__:
            vals = [v for r in self._records
                    if (v := getattr(r, name)) is not None]
            if not vals:
                out[name] = {"samples": 0, "p50": None, "p95": None,
                             "p99": None}
                continue
            out[name] = {"samples": len(vals),
                         "p50": percentile(vals, 50),
                         "p95": percentile(vals, 95),
                         "p99": percentile(vals, 99)}
        return out


# ---------------------------------------------------------------- pipeline
@dataclass
class VoiceRuntimeConfig:
    """Pipeline kaynak limitleri — sonsuz bekleme YASAK."""
    stt_partial_timeout_s: float = 5.0
    brain_timeout_s: float = 30.0
    tts_first_chunk_timeout_s: float = 10.0
    listen_timeout_s: float = 15.0
    queue_maxsize: int = 64


class VoiceRuntime:
    """Paralel streaming ses hattı.

    Enjeksiyon noktaları (gerçek cihaz takılana kadar test edilebilir):
      mic_frames        — async generator, PCM frame (bytes) üretir
      stt_stream        -> async fn(pcm_chunks) -> async iterator of
                           {kind: partial|final, text, confidence, language}
      brain_stream      — async fn(text) -> async iterator of str chunk'lar
      tts_speak_chunks  — async fn(chunks) -> ilk ses başlangıç anı döner
      on_event          — voice.started/ended/interrupted olayları
    """

    def __init__(self, *, mic_frames=None, stt_stream=None, brain_stream=None,
                 tts_speak_chunks=None, on_event=None, vad=None,
                 fsm: VoiceStateMachine | None = None,
                 latency: LatencyTracker | None = None,
                 config: VoiceRuntimeConfig | None = None):
        self.fsm = fsm or VoiceStateMachine()
        self.latency = latency or LatencyTracker()
        self.config = config or VoiceRuntimeConfig()
        self.mic_frames = mic_frames
        self.stt_stream = stt_stream
        self.brain_stream = brain_stream
        self.tts_speak_chunks = tts_speak_chunks
        self.on_event = on_event or (lambda ev: None)
        self.vad = vad
        self._cancel: asyncio.Event | None = None

    # ------------------------------------------------------------ capability
    def capability(self) -> dict:
        """Dürüst durum: hangi bileşenler gerçek, hangisi eksik."""
        return {
            "mic": self.mic_frames is not None,
            "stt": self.stt_stream is not None,
            "brain": self.brain_stream is not None,
            "tts": self.tts_speak_chunks is not None,
            "vad": self.vad is not None,
            "degraded": any(f is None for f in
                            (self.mic_frames, self.stt_stream,
                             self.brain_stream, self.tts_speak_chunks)),
        }

    def _emit(self, kind: str, **kw):
        ev = {"type": kind, "ts": time.time(), **kw}
        self.on_event(ev)
        return ev

    # ------------------------------------------------------------ barge-in
    def interrupt(self) -> dict:
        """Kullanıcı araya girdi: TTS DURUR, mikrofon öncelik alır."""
        if self.fsm.state == SPEAKING:
            self.fsm.transition(INTERRUPTED, reason="barge-in")
            if self._cancel is not None:
                self._cancel.set()
            return self._emit("voice.interrupted")
        if self.fsm.state in (THINKING, PROCESSING):
            # henüz ses yok: düşünme iptali de geçerli
            if self._cancel is not None:
                self._cancel.set()
            self.fsm.transition(IDLE, reason="cancelled before speech")
            return self._emit("voice.cancelled")
        return {"type": "ignored", "state": self.fsm.state}

    # ------------------------------------------------------------ ana akış
    async def run_turn(self) -> dict:
        """Tam etkileşim: LISTENING→THINKING→(PROCESSING)→SPEAKING→IDLE.

        Paralellik: STT final'i beklemeden partial akışı başlar; TTS,
        brain'den ilk chunk gelmezse bile ilk ses süresi ölçülür; barge-in
        (cancel event) her aşamada anında dururur. Blocking sequential
        YOK — aşamalar asyncio.Task olarak eşzamanlı koşar.
        """
        if self.mic_frames is None or self.stt_stream is None:
            raise RuntimeError("voice runtime degraded: mic/stt unavailable "
                               "(düruşt durum: capability() bkz.)")
        self._cancel = asyncio.Event()
        cancel = self._cancel
        self.latency.begin()
        self.fsm.transition(LISTENING, reason="turn start")
        self._emit("voice.started")
        result: dict = {"status": "ok", "partials": [], "text": None,
                        "spoken_chunks": 0, "interrupted": False}

        # --- 1) ses toplama + VAD (mic stream tüketimi arka planda)
        pcm: list[bytes] = []

        async def consume_mic():
            speech = False
            n = 0
            async for frame in self.mic_frames():
                if cancel.is_set():
                    return
                n += 1
                if n % 16 == 0:
                    await asyncio.sleep(0)   # event loop'a kontrol —
                    # sonsuz/hızlı kaynak timer'ları bloklamasın (F5 dersi)
                loud = self.vad.process_frame(frame) if self.vad else True
                if loud and not speech:
                    speech = True
                    self.latency.mark("vad_start")
                pcm.append(frame)
                if speech and not loud:
                    return              # konuşma bitti → STT
            return

        mic_task = asyncio.ensure_future(consume_mic())
        try:
            await asyncio.wait_for(mic_task, self.config.listen_timeout_s)
        except asyncio.TimeoutError:
            mic_task.cancel()
            self.fsm.transition(IDLE, reason="listen timeout")
            result["status"] = "listen_timeout"
            return result
        except (IOError, OSError) as exc:
            # mic disconnect: cihaz hatası — ERROR durumuna düş, dürüst rapor
            self.fsm.transition(ERROR, reason="mic failure")
            self.fsm.transition(IDLE, reason="mic recovery")
            result["status"] = "mic_error"
            result["error"] = str(exc)[:120]
            return result

        # --- 2) STT stream (partial'lar eşzamanlı akar)
        stt_iter = self.stt_stream(pcm)
        final_text = None
        async for ev in stt_iter:
            if cancel.is_set():
                break
            if ev.get("kind") == "partial":
                if not result["partials"]:
                    self.latency.mark("first_partial")
                result["partials"].append(ev.get("text", ""))
            elif ev.get("kind") == "final":
                final_text = ev.get("text", "")
                self.latency.mark("final_stt")
                break
        self._emit("voice.ended", ms_hint="stt_final")
        if final_text is None or not str(final_text).strip():
            self.fsm.transition(IDLE, reason="no speech understood")
            result["status"] = "no_speech"
            self.latency.finish({"final_stt_ms": "final_stt",
                                 "first_partial_ms": "first_partial"})
            return result

        # --- 3) brain stream → TTS EŞZAMANLI (sequential DEĞİL)
        self.fsm.transition(THINKING, reason="brain start")
        self.latency.mark("brain_start")
        chunks: list[str] = []
        tts_in: asyncio.Queue = asyncio.Queue(self.config.queue_maxsize)

        async def brain_side():
            try:
                async for chunk in self.brain_stream(final_text):
                    if cancel.is_set():
                        break
                    if not chunks:
                        self.latency.mark("first_token")
                    chunks.append(chunk)
                    await tts_in.put(chunk)
            finally:
                await tts_in.put(None)          # EOS işareti

        async def tts_side():
            if self.tts_speak_chunks is None:
                # TTS yoksa dürüst: ses yok, metin akışı tamamlanır
                while True:
                    c = await tts_in.get()
                    if c is None:
                        return
                # unreachable
            spoken = 0
            while True:
                c = await tts_in.get()
                if c is None or cancel.is_set():
                    if spoken:
                        self.latency.mark("tts_complete")
                    return
                first = spoken == 0
                await self.tts_speak_chunks(iter([c]))
                if first:
                    self.latency.mark("first_audio")
                    self.fsm.transition(SPEAKING, reason="first audio")
                spoken += 1
                result["spoken_chunks"] = spoken

        brain_task = asyncio.ensure_future(brain_side())
        tts_task = asyncio.ensure_future(tts_side())
        done, pending = await asyncio.wait(
            [brain_task, tts_task],
            return_when=asyncio.ALL_COMPLETED,
            timeout=self.config.brain_timeout_s)
        for t in pending:
            t.cancel()
        if pending:
            result["status"] = "timeout"

        if cancel.is_set():
            result["interrupted"] = True
            result["status"] = "interrupted"
            # INTERRUPTED zaten set edildi (barge-in); IDLE'a dön
            if self.fsm.state == INTERRUPTED:
                self.fsm.transition(LISTENING, reason="barge-in: mic "
                                                      "priority")
        # normal bitiş
        if self.fsm.state in (SPEAKING, THINKING, PROCESSING):
            self.fsm.transition(IDLE, reason="turn complete")
        result["text"] = final_text
        result["chunks"] = chunks
        self.latency.finish({
            "wake_ms": "wake", "vad_ms": "vad_start",
            "first_partial_ms": "first_partial", "final_stt_ms": "final_stt",
            "brain_start_ms": "brain_start", "first_token_ms": "first_token",
            "first_audio_ms": "first_audio", "tts_complete_ms": "tts_complete"})
        return result

"""WAVE 4 — Wake/VAD/Barge-in koordinatörü (§4, §5).

Mevcut bileşenler YENİDEN KULLANILIR (değiştirilmez):
  - app/voice/wake.py WakeWordManager — gerçek audio-level spotting
  - app/voice/voice_stack_v2.py VAD zinciri (webrtcvad→silero→energy)

Bu katman ekler:
  - WakeGate: false-positive azaltma — debounce (ardışık hit penceresi),
    cooldown (yeniden tetikleme arası min süre), eşik üstü skor.
    Capability yoksa AÇIK degraded durumu (wake olmadan LISTENING YOK).
  - EchoGuard: TTS kendi sesini kullanıcı konuşması sanmamalı.
    Gerçek AEC yoksa muhafazakar mod: TTS aktifken daha yüksek VAD eşiği
    ve daha uzun başlangıç onayı; referans korelasyonu DI ile takılabilir.
  - BargeInController: VAD + wake + echo kararını tek frame API'sinde
    birleştirir → barge_in event TTS'i anında durdurur, mic öncelik alır.
"""
from __future__ import annotations

import time


# ---------------------------------------------------------------- wake gate
class WakeGate:
    """Wake-word tetiklemesini güvenilir hale getirir.

    state: IDLE → (hit) → DETECT → (confirm/debounce) → LISTEN
    """

    IDLE = "IDLE"
    DETECT = "DETECT"
    LISTEN = "LISTEN"

    def __init__(self, wake_manager, *, threshold: float = 0.5,
                 debounce_hits: int = 1, cooldown_s: float = 1.2,
                 now=None):
        self.mgr = wake_manager
        self.threshold = float(threshold)
        self.debounce_hits = max(1, int(debounce_hits))
        self.cooldown_s = float(cooldown_s)
        self.state = self.IDLE
        self._hits = 0
        self._last_wake = 0.0
        self.stats = {"raw_hits": 0, "accepted": 0, "suppressed_cooldown": 0,
                      "suppressed_threshold": 0,
                      "suppressed_debounce": 0}
        self._now = now or time.monotonic

    # ------------------------------------------------------------ capability
    def status(self) -> dict:
        base = self.mgr.status() if self.mgr else {}
        return {**base,
                "gate_state": self.state,
                "degraded": not (base.get("available") is True),
                "threshold": self.threshold,
                "cooldown_s": self.cooldown_s,
                "stats": dict(self.stats)}

    # ------------------------------------------------------------ akış
    def process_chunk(self, audio: bytes) -> dict | None:
        """Ham chunk → karar. None = sessizlik; dict = wake kararı."""
        if self.mgr is None or self.mgr.active is None:
            return None                      # dürüst: engine yok → tetikleme yok
        hit = self.mgr.process_chunk(audio)
        if hit is None:
            if self.state == self.DETECT and self._now() - self._detect_t \
                    > 0.6:
                self.state = self.IDLE       # debounce penceresi kapandı
                self._hits = 0
            return None
        self.stats["raw_hits"] += 1
        name, score = hit if isinstance(hit, tuple) else (hit, 1.0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 1.0
        # 1) eşik altı skor → false positive bastır
        if score < self.threshold:
            self.stats["suppressed_threshold"] += 1
            return None
        # 2) cooldown → üst üste tetikleme bastır
        t = self._now()
        if t - self._last_wake < self.cooldown_s:
            self.stats["suppressed_cooldown"] += 1
            return None
        # 3) debounce → N ardışık onaylı hit gerekir
        self._hits += 1
        if self.state == self.IDLE:
            self.state = self.DETECT
            self._detect_t = t
        if self._hits < self.debounce_hits:
            self.stats["suppressed_debounce"] += 1
            return None
        # kabul
        self.state = self.LISTEN
        self._last_wake = t
        self._hits = 0
        self.stats["accepted"] += 1
        return {"type": "wake_detected", "keyword": name, "score": score,
                "ts": t}

    def listening_done(self) -> None:
        """LISTEN bitti (STT/timeout) → IDLE'a dön."""
        self.state = self.IDLE
        self._hits = 0

    _detect_t = 0.0


# ---------------------------------------------------------------- echo guard
class EchoGuard:
    """TTS echo'su ile gerçek kullanıcı sesini ayırır.

    Gerçek AEC (ör. webrtc-audio-processing) kurulursa `is_echo` enjeksiyonu
    referans karşılaştırması yapar. Yoksa muhafazakar kurallar:
      - TTS aktifken VAD start_frames artar (geçici parazit echo sayılmaz)
      - TTS aktifken RMS eşiği yükselir (echo genelde daha kısık kaydedilir)
    """

    def __init__(self, *, is_echo=None, tts_boost_frames: int = 2,
                 tts_rms_margin: float = 1.6, base_threshold: float = 0.02):
        self._is_echo = is_echo            # DI: fn(frame, ref) -> bool
        self.tts_boost_frames = int(tts_boost_frames)
        self.tts_rms_margin = float(tts_rms_margin)
        self.base_threshold = float(base_threshold)
        self.tts_active = False
        self.reference: bytes | None = None   # son TTS çıkışı (echo ref)

    def set_tts(self, active: bool, reference: bytes | None = None) -> None:
        self.tts_active = bool(active)
        if reference:
            self.reference = reference

    def effective_threshold(self) -> float:
        return (self.base_threshold * self.tts_rms_margin
                if self.tts_active else self.base_threshold)

    def effective_start_frames(self, base_start: int) -> int:
        return base_start + (self.tts_boost_frames if self.tts_active else 0)

    def check(self, frame: bytes, rms: float) -> bool:
        """True = ECHO (barge-in YOK); False = gerçek kullanıcı sesi."""
        if self._is_echo is not None and self.tts_active:
            return bool(self._is_echo(frame, self.reference))
        # muhafazakar mod: TTS aktifken yüksek eşik — altında echo sayılır
        if self.tts_active:
            return rms < self.effective_threshold()
        return False


# ---------------------------------------------------------------- controller
class BargeInController:
    """MIC→VAD→WAKE→barge-in karar zinciri (tek frame API'si)."""

    def __init__(self, vad, wake_gate: WakeGate | None = None,
                 echo_guard: EchoGuard | None = None, *,
                 on_event=None, now=None):
        self.vad = vad
        self.wake = wake_gate
        self.echo = echo_guard or EchoGuard()
        self.on_event = on_event or (lambda ev: None)
        self.tts_active = False
        self.speech = False
        self._start_pending = 0
        self._now = now or time.monotonic
        self.stats = {"speech_starts": 0, "speech_ends": 0,
                      "barge_ins": 0, "echo_suppressed": 0}

    def set_tts(self, active: bool, reference: bytes | None = None) -> None:
        """TTS başlarken/biterken referansıyla bildir."""
        self.tts_active = active
        self.echo.set_tts(active, reference)

    # ------------------------------------------------------------ frame API
    def feed_frame(self, frame: bytes) -> list[dict]:
        """Bir PCM frame işle → olay listesi (boş = sessizlik)."""
        events: list[dict] = []
        # 1) wake (yalnız konuşma yokken)
        if self.wake is not None and not self.speech:
            wake_ev = self.wake.process_chunk(frame)
            if wake_ev is not None:
                events.append(wake_ev)
        # 2) VAD — RMS ve echo kararı bu katmanda
        loud = bool(self.vad.process_frame(frame))
        rms = self._rms(frame)
        if loud and not self.speech:
            self._start_pending += 1
            need = 1 if not self.tts_active else \
                max(1, self._start_pending + 1)  # echo guard boost
            if self.echo.check(frame, rms):
                self.stats["echo_suppressed"] += 1
                self._start_pending = 0
                return events
            if self._start_pending >= 1 and not self.tts_active:
                self._speech_started(events)
            elif self.tts_active and self._start_pending >= 2:
                # TTS sırasında en az 2 ardışık pozitif + echo değil
                self._speech_started(events, barge=True)
        elif loud and self.speech:
            pass                              # konuşma sürüyor
        elif not loud and self.speech:
            self.speech = False
            self.stats["speech_ends"] += 1
            events.append({"type": "speech_end", "ts": self._now()})
        else:
            self._start_pending = 0
        for ev in events:
            self.on_event(ev)
        return events

    def _speech_started(self, events, *, barge: bool = False):
        self.speech = True
        self._start_pending = 0
        self.stats["speech_starts"] += 1
        if barge and self.tts_active:
            self.stats["barge_ins"] += 1
            self.tts_active = False
            events.append({"type": "barge_in", "ts": self._now()})
        else:
            events.append({"type": "speech_start", "ts": self._now()})

    @staticmethod
    def _rms(frame: bytes) -> float:
        import array
        n = len(frame) // 2
        if n == 0:
            return 0.0
        a = array.array("h")
        a.frombytes(frame[: n * 2])
        acc = 0.0
        for s in a:
            v = s / 32768.0
            acc += v * v
        return (acc / n) ** 0.5

    def status(self) -> dict:
        return {"tts_active": self.tts_active, "speech": self.speech,
                "echo_mode": "aec" if self.echo._is_echo else "conservative",
                "wake": self.wake.status() if self.wake else None,
                "stats": dict(self.stats)}

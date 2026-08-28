"""Voice Stack V2 — real-time voice with VAD, barge-in and latency metrics.

Backend order: webrtcvad -> silero -> EnergyVAD (pure-python, no deps).
The fixed 8-second window of LiveVoice is replaced by stream+VAD when this
stack is used: speech end auto-triggers STT.  Barge-in: if VAD fires while
`tts_active`, the interrupt callback cancels TTS and we switch to listening.
Latency tracker records stt_ms / llm_ms / tts_ms per request.
"""
import array
import math
import time
import threading


class EnergyVAD:
    """RMS-energy VAD with hangover hysteresis. Frame = 30 ms int16 mono."""

    def __init__(self, threshold: float = 0.02, hangover_frames: int = 12,
                 sample_rate: int = 16000, frame_ms: int = 30):
        self.threshold = threshold
        self.hangover = hangover_frames
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self._below = 0
        self._speech = False

    def _rms(self, frame: bytes) -> float:
        n = len(frame) // 2
        if n == 0:
            return 0.0
        samples = array.array("h")
        samples.frombytes(frame[: n * 2])
        acc = 0
        for s in samples:
            v = s / 32768.0
            acc += v * v
        return math.sqrt(acc / n)

    def process_frame(self, frame: bytes) -> bool:
        loud = self._rms(frame) > self.threshold
        if loud:
            self._below = 0
            self._speech = True
        elif self._speech:
            self._below += 1
            if self._below >= self.hangover:
                self._speech = False
        return self._speech


class _HysteresisVAD:
    """Normalize all VAD backends to the same start/end behavior."""
    def __init__(self, raw, start_frames=1, end_frames=10):
        self.raw = raw
        self.start_frames = start_frames
        self.end_frames = end_frames
        self._positive = 0
        self._negative = 0
        self._speech = False

    def process_frame(self, frame: bytes) -> bool:
        loud = bool(self.raw.process_frame(frame))
        if loud:
            self._positive += 1
            self._negative = 0
            if not self._speech and self._positive >= self.start_frames:
                self._speech = True
        else:
            self._positive = 0
            if self._speech:
                self._negative += 1
                if self._negative >= self.end_frames:
                    self._speech = False
            else:
                self._negative = 0
        return self._speech


class _WebRtcVAD:
    def __init__(self, sample_rate=16000, frame_ms=30):
        import webrtcvad
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("WebRTC VAD requires 8/16/32/48 kHz")
        if frame_ms not in (10, 20, 30):
            raise ValueError("WebRTC VAD requires 10/20/30 ms frames")
        self.vad = webrtcvad.Vad(2)
        self.sr = sample_rate
        self.fms = frame_ms

    def process_frame(self, frame: bytes) -> bool:
        expected = self.sr * self.fms // 1000 * 2
        if len(frame) != expected:
            return False
        return bool(self.vad.is_speech(frame, self.sr))


class _SileroVAD:
    def __init__(self, sample_rate=16000):
        import numpy as np
        import torch
        from silero_vad import load_silero_vad
        self.np = np
        self.torch = torch
        self.model = load_silero_vad()
        self.sr = sample_rate

    def process_frame(self, frame: bytes) -> bool:
        arr = self.np.frombuffer(frame, dtype=self.np.int16).astype(self.np.float32) / 32768.0
        tensor = self.torch.from_numpy(arr.copy())
        try:
            prob = self.model(tensor, self.sr)
        except TypeError:
            prob = self.model(tensor)
        if isinstance(prob, (tuple, list)):
            prob = prob[0]
        return float(prob.item() if hasattr(prob, "item") else prob) > 0.5


class VoiceStackV2:
    def __init__(self, on_event=None, on_interrupt=None, sample_rate: int = 16000, store=None):
        self.sr = sample_rate
        self.on_event = on_event
        self.on_interrupt = on_interrupt
        self.store = store
        self.vad_kind = "energy"
        try:
            self.vad = _HysteresisVAD(_WebRtcVAD(sample_rate))
            self.vad_kind = "webrtcvad"
        except Exception:
            try:
                self.vad = _HysteresisVAD(_SileroVAD(sample_rate))
                self.vad_kind = "silero"
            except Exception:
                self.vad = EnergyVAD(sample_rate=sample_rate)
        self.speech = False
        self.tts_active = False
        self.buffer = bytearray()
        self._speech_t0 = 0.0
        self.metrics = {"stt_ms": None, "llm_ms": None, "tts_ms": None, "vad": self.vad_kind}
        self._marks: dict = {}

    # ------------------------------------------------------------- streaming
    def tts_start(self) -> None:
        self.tts_active = True

    def tts_stop(self) -> None:
        self.tts_active = False

    def feed_frame(self, frame: bytes) -> dict | None:
        loud = self.vad.process_frame(frame)
        ev = None
        if loud and not self.speech:
            if self.tts_active:
                self.tts_active = False
                if self.on_interrupt:
                    self.on_interrupt()
                ev = {"type": "barge_in", "ts": time.time()}
            else:
                ev = {"type": "speech_start", "ts": time.time()}
            self.speech = True
            self._speech_t0 = time.time()
            self.buffer = bytearray()
        if self.speech:
            self.buffer.extend(frame)
        if not loud and self.speech:
            self.speech = False
            ev = {"type": "speech_end", "ts": time.time(),
                  "ms": round((time.time() - self._speech_t0) * 1000, 1),
                  "bytes": len(self.buffer)}
        if ev and self.on_event:
            self.on_event(ev)
        return ev

    # ------------------------------------------------------------- latency
    def mark(self, stage: str) -> None:
        now = time.time()
        if stage not in self._marks:
            self._marks[stage] = now
        else:
            key = f"{stage}_ms"
            self.metrics[key] = round((now - self._marks.pop(stage)) * 1000, 1)

    def get_metrics(self) -> dict:
        return dict(self.metrics)

    def commit(self) -> None:
        """Persist latency metrics (auto-insert per voice interaction)."""
        m = self.metrics
        if self.store and any(m.get(k) is not None for k in ("stt_ms", "llm_ms", "tts_ms")):
            try:
                self.store.insert(m.get("stt_ms"), m.get("llm_ms"), m.get("tts_ms"), m.get("vad"))
            except Exception:
                pass
        self.reset_metrics()

    def reset_metrics(self) -> None:
        self.metrics = {"stt_ms": None, "llm_ms": None, "tts_ms": None, "vad": self.vad_kind}
        self._marks = {}


class LiveVoiceV2:
    """Stream-based live voice (replaces the fixed 8s window when deps exist)."""

    def __init__(self, agent, tts, settings, stack: VoiceStackV2):
        self.agent = agent
        self.tts = tts
        self.settings = settings
        self.stack = stack
        self.running = False
        self.sr = int(settings.get("voice", {}).get("sample_rate", 16000))
        self.wake = settings.get("wake_word", "ultron").lower()
        self._whisper_model = None

    def _transcribe(self, pcm: bytes) -> str:
        import io
        import wave
        from faster_whisper import WhisperModel
        if self._whisper_model is None:
            self._whisper_model = WhisperModel(
                self.settings.get("voice", {}).get("stt_model", "small"),
                device="auto", compute_type="int8"
            )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(bytes(pcm))
        buf.seek(0)
        seg, _ = self._whisper_model.transcribe(buf, language="tr", vad_filter=True)
        return " ".join(s.text for s in seg).strip()

    def run(self) -> None:
        import sounddevice as sd
        self.running = True
        frame_samples = int(self.sr * 0.03)

        def process_utterance(pcm: bytes):
            try:
                text = self._transcribe(pcm)
                self.stack.mark("stt")
                if not text or self.wake not in text.lower():
                    return
                cmd = text.lower().split(self.wake, 1)[1].strip(" ,.:;-") or text
                self.stack.mark("llm")
                answer = self.agent.handle(cmd)
                self.stack.mark("llm")
                try:
                    self.stack.tts_start()
                    self.stack.mark("tts")
                    self.tts.speak(answer)
                    self.stack.mark("tts")
                finally:
                    self.stack.tts_stop()
            except Exception as exc:
                if self.stack.on_event:
                    self.stack.on_event({"type": "voice_error", "error": str(exc)[:300], "ts": time.time()})

        def callback(indata, frames, t, status):
            ev = self.stack.feed_frame(indata[:, 0].tobytes())
            if ev and ev["type"] == "speech_end":
                threading.Thread(target=process_utterance, args=(bytes(self.stack.buffer),), daemon=True).start()

        with sd.InputStream(samplerate=self.sr, channels=1, dtype="int16",
                            blocksize=frame_samples, callback=callback):
            while self.running:
                time.sleep(0.2)

    def stop(self) -> None:
        self.running = False

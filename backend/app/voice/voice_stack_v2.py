"""Voice Stack V2 — real-time voice with VAD, wake-word gating, barge-in and metrics."""
import array
import math
import time
import threading
from enum import Enum


class VoiceState(str, Enum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


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
        acc = sum((s / 32768.0) ** 2 for s in samples)
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
    """Real wake-word gated streaming voice. No transcript wake-word fallback."""
    def __init__(self, agent, tts, settings, stack: VoiceStackV2):
        self.agent = agent
        self.tts = tts
        self.settings = settings
        self.stack = stack
        self.running = False
        self.state = VoiceState.DISARMED
        self.last_error = None
        self.sr = int(settings.get("voice", {}).get("sample_rate", 16000))
        wake_cfg = settings.get("wake", {})
        self.wake = str(wake_cfg.get("keyword", settings.get("wake_word", "ultron"))).lower()
        self._whisper_model = None
        self.wake_manager = None
        try:
            from app.voice.wake import WakeWordManager
            self.wake_manager = WakeWordManager(settings)
        except Exception as exc:
            self.last_error = str(exc)[:300]

    @property
    def available(self) -> bool:
        return self.wake_manager is not None and self.wake_manager.active is not None

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "available": self.available,
            "wake": self.wake_manager.status() if self.wake_manager else None,
            "last_error": self.last_error,
            "vad": self.stack.vad_kind,
        }

    def _set_state(self, state: VoiceState, error=None):
        self.state = state
        self.last_error = error
        if self.stack.on_event:
            self.stack.on_event({"type": "voice_state", "state": state.value, "error": error, "ts": time.time()})

    def arm(self) -> bool:
        if not self.wake_manager:
            self._set_state(VoiceState.DISARMED, self.last_error or "wake manager unavailable")
            return False
        try:
            status = self.wake_manager.start()
        except Exception as exc:
            self._set_state(VoiceState.ERROR, str(exc)[:300])
            return False
        if not status.get("available") or self.wake_manager.active is None:
            self._set_state(VoiceState.DISARMED, status.get("note") or "real wake engine unavailable")
            return False
        self._set_state(VoiceState.ARMED)
        return True

    def disarm(self):
        try:
            if self.wake_manager:
                self.wake_manager.stop()
        finally:
            self._set_state(VoiceState.DISARMED)

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

    def _handle_utterance(self, pcm: bytes):
        try:
            if not self.available or self.state not in (VoiceState.ARMED, VoiceState.SPEAKING):
                return
            hit = self.wake_manager.process_chunk(pcm)
            if hit is None:
                return
            self._set_state(VoiceState.LISTENING)
            text = self._transcribe(pcm)
            self.stack.mark("stt")
            if not text:
                self._set_state(VoiceState.ARMED)
                return
            self._set_state(VoiceState.PROCESSING)
            self.stack.mark("llm")
            answer = self.agent.handle(text)
            self.stack.mark("llm")
            if answer is None:
                self._set_state(VoiceState.ARMED)
                return
            self._set_state(VoiceState.SPEAKING)
            self.stack.tts_start()
            try:
                self.stack.mark("tts")
                self.tts.speak(answer)
                self.stack.mark("tts")
            finally:
                self.stack.tts_stop()
                self._set_state(VoiceState.ARMED)
            self.stack.commit()
        except Exception as exc:
            self.stack.tts_stop()
            self._set_state(VoiceState.ERROR, str(exc)[:300])
            if self.stack.on_event:
                self.stack.on_event({"type": "voice_error", "error": str(exc)[:300], "ts": time.time()})

    def run(self) -> None:
        import sounddevice as sd
        if not self.arm():
            return
        self.running = True
        frame_samples = int(self.sr * 0.03)

        def callback(indata, frames, t, status):
            frame = indata[:, 0].tobytes()
            ev = self.stack.feed_frame(frame)
            if ev and ev["type"] == "barge_in":
                if self.state == VoiceState.SPEAKING:
                    self._set_state(VoiceState.LISTENING)
                return
            if ev and ev["type"] == "speech_end":
                threading.Thread(target=self._handle_utterance, args=(bytes(self.stack.buffer),), daemon=True).start()

        try:
            with sd.InputStream(samplerate=self.sr, channels=1, dtype="int16",
                                blocksize=frame_samples, callback=callback):
                while self.running:
                    time.sleep(0.2)
        except Exception as exc:
            self._set_state(VoiceState.ERROR, str(exc)[:300])
        finally:
            self.running = False
            if self.state != VoiceState.DISARMED:
                self.disarm()

    def stop(self) -> None:
        self.running = False
        self.disarm()

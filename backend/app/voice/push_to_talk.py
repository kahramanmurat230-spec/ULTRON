"""Local push-to-talk voice bridge.

Uses the existing VoiceStackV2, local Whisper loader, Agent and local TTS.
No cloud service or wake-word engine is required for a manual voice turn.
"""
from __future__ import annotations

import io
import threading
import wave
from pathlib import Path


class PushToTalkVoice:
    def __init__(self, agent, tts, settings, stack=None):
        from app.voice.voice_stack_v2 import VoiceStackV2
        from app.voice.local_whisper import load_whisper

        self.agent = agent
        self.tts = tts
        self.settings = settings
        self.stack = stack or VoiceStackV2()
        self._whisper_model = None
        self._whisper_info = None
        self._load_whisper = load_whisper
        self.sr = int(settings.get("voice", {}).get("sample_rate", 16000))
        self.running = False
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {
            "mode": "push-to-talk",
            "running": self.running,
            "whisper": self._whisper_info,
            "tts": self.tts.backend(),
            "vad": self.stack.vad_kind,
            "local_only": True,
        }

    def _transcribe(self, pcm: bytes) -> str:
        if self._whisper_model is None:
            model_path = self.settings.get("voice", {}).get("stt_model", "models/whisper-tiny")
            self._whisper_model, self._whisper_info = self._load_whisper(model_path)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sr)
            wav.writeframes(pcm)
        buf.seek(0)
        segments, _ = self._whisper_model.transcribe(buf, language="tr", vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def record(self, seconds: float = 6.0) -> bytes:
        import sounddevice as sd
        seconds = max(1.0, min(float(seconds), 15.0))
        frames = sd.rec(int(seconds * self.sr), samplerate=self.sr,
                        channels=1, dtype="int16")
        sd.wait()
        return frames.tobytes()

    def listen_once(self, seconds: float = 6.0) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        self.running = True
        try:
            pcm = self.record(seconds)
            self.stack.mark("stt")
            text = self._transcribe(pcm)
            self.stack.mark("stt")
            if not text:
                return {"status": "no_speech", "text": "", "answer": None}

            self.stack.mark("llm")
            answer = self.agent.handle(text)
            self.stack.mark("llm")
            if answer is None:
                return {"status": "no_answer", "text": text, "answer": None}

            self.stack.tts_start()
            try:
                self.stack.mark("tts")
                backend = self.tts.speak(answer)
                self.stack.mark("tts")
            finally:
                self.stack.tts_stop()

            result = {
                "status": "ok",
                "text": text,
                "answer": answer,
                "tts_backend": backend,
                "metrics": self.stack.get_metrics(),
            }
            self.stack.commit()
            return result
        finally:
            self.running = False
            self._lock.release()

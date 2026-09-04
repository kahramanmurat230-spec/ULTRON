"""Stage 4 voice controller: wake -> listen -> command -> processing -> speaking.

The controller is deliberately engine-agnostic. Real wake/VAD/STT/TTS objects are
injected by the application; missing engines never become fake success.
"""
from __future__ import annotations
from enum import Enum
import time


class VoiceState(str, Enum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class VoiceController:
    def __init__(self, wake=None, stack=None, on_command=None, cooldown_s=0.7):
        self.wake = wake
        self.stack = stack
        self.on_command = on_command
        self.cooldown_s = float(cooldown_s)
        self.state = VoiceState.DISARMED
        self.last_wake_at = 0.0
        self.last_error = None

    @property
    def available(self) -> bool:
        return self.wake is not None and getattr(self.wake, "active", None) is not None

    def status(self) -> dict:
        wake_status = self.wake.status() if self.wake is not None else {
            "active": None, "available": False, "engines": [],
            "note": "wake manager yok"
        }
        return {"state": self.state.value, "available": self.available,
                "wake": wake_status, "last_error": self.last_error}

    def arm(self) -> dict:
        self.last_error = None
        if self.wake is None:
            self.state = VoiceState.DISARMED
            self.last_error = "wake manager yok"
            return self.status()
        status = self.wake.start()
        self.state = VoiceState.ARMED if status.get("available") else VoiceState.DISARMED
        if not status.get("available"):
            self.last_error = "gerçek wake engine kullanılamıyor"
        return self.status()

    def disarm(self) -> None:
        if self.wake is not None:
            self.wake.stop()
        self.state = VoiceState.DISARMED

    def on_wake(self, detected=None) -> bool:
        """Enter listening only after a real wake detection."""
        if self.state not in (VoiceState.ARMED, VoiceState.SPEAKING):
            return False
        now = time.monotonic()
        if now - self.last_wake_at < self.cooldown_s:
            return False
        self.last_wake_at = now
        self.state = VoiceState.LISTENING
        return True

    def begin_processing(self) -> None:
        if self.state == VoiceState.LISTENING:
            self.state = VoiceState.PROCESSING

    def submit_command(self, text: str):
        text = (text or "").strip()
        if not text:
            self.state = VoiceState.ARMED if self.available else VoiceState.DISARMED
            return None
        self.begin_processing()
        if self.state != VoiceState.PROCESSING:
            return None
        try:
            result = self.on_command(text) if self.on_command else None
            self.state = VoiceState.SPEAKING if result is not None else (VoiceState.ARMED if self.available else VoiceState.DISARMED)
            return result
        except Exception as exc:
            self.last_error = str(exc)[:300]
            self.state = VoiceState.ERROR
            return None

    def finish_speaking(self) -> None:
        self.state = VoiceState.ARMED if self.available else VoiceState.DISARMED

    def recover(self) -> None:
        self.last_error = None
        self.state = VoiceState.ARMED if self.available else VoiceState.DISARMED

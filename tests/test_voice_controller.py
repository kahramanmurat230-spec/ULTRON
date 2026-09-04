from app.voice.voice_controller import VoiceController, VoiceState


class FakeWake:
    def __init__(self, available=True):
        self.active = object() if available else None
        self.started = False

    def start(self):
        self.started = True
        return {"available": self.active is not None}

    def stop(self):
        self.active = None

    def status(self):
        return {"available": self.active is not None, "active": "fake" if self.active else None}


def test_arm_requires_real_wake_engine():
    c = VoiceController(FakeWake(False))
    status = c.arm()
    assert status["state"] == VoiceState.DISARMED.value
    assert status["available"] is False


def test_wake_listen_process_speak_cycle():
    seen = []
    c = VoiceController(FakeWake(True), on_command=lambda text: seen.append(text) or "ok", cooldown_s=0)
    c.arm()
    assert c.state == VoiceState.ARMED
    assert c.on_wake() is True
    assert c.state == VoiceState.LISTENING
    assert c.submit_command("hava nasıl") == "ok"
    assert seen == ["hava nasıl"]
    assert c.state == VoiceState.SPEAKING
    c.finish_speaking()
    assert c.state == VoiceState.ARMED


def test_empty_command_returns_to_armed():
    c = VoiceController(FakeWake(True), cooldown_s=0)
    c.arm()
    c.on_wake()
    assert c.submit_command("   ") is None
    assert c.state == VoiceState.ARMED


def test_command_exception_enters_error_without_fake_success():
    def fail(_):
        raise RuntimeError("brain unavailable")
    c = VoiceController(FakeWake(True), on_command=fail, cooldown_s=0)
    c.arm()
    c.on_wake()
    assert c.submit_command("test") is None
    assert c.state == VoiceState.ERROR
    assert "brain unavailable" in c.last_error

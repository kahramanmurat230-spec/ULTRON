from app.core.runtime import UltronRuntime


def test_runtime_exposes_live_voice_shutdown_hook():
    assert callable(getattr(UltronRuntime, "stop_live_voice", None))


class _FakeLegacyLiveVoice:
    """Stand-in for app.voice.live_voice.LiveVoice (the fake, substring-based
    wake-word detector). start() must NEVER be called by start_live_voice()."""

    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass


def test_start_live_voice_never_falls_back_to_fake_transcript_wake_word():
    """Regression: when the real acoustic wake-word stack (VoiceStackV2/
    LiveVoiceV2, or sounddevice/faster-whisper) is unavailable,
    UltronRuntime.start_live_voice() must report an honest error instead of
    silently starting the legacy transcript-substring "wake word" hack
    (app.voice.live_voice.LiveVoice), which is a fake-success anti-pattern
    explicitly disallowed for voice acceptance."""
    fake = type("FakeRuntime", (), {})()
    fake.live_voice = _FakeLegacyLiveVoice()
    fake.agent = None
    fake.tts = None
    fake.settings = {}
    # sounddevice/faster_whisper are not installed in this environment, so the
    # try-block inside start_live_voice() raises before reaching LiveVoiceV2 —
    # exercising exactly the failure path the fallback used to (mis)handle.
    UltronRuntime.start_live_voice(fake)
    assert fake.live_voice.started is False
    assert getattr(fake, "live_voice_error", None)

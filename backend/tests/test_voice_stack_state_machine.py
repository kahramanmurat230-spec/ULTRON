from app.voice.voice_stack_v2 import LiveVoiceV2, VoiceState


class FakeWake:
    active = object()

    def __init__(self, available=True):
        self.available = available
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return {"available": self.available}

    def stop(self):
        self.stopped = True

    def process_chunk(self, pcm):
        return True

    def status(self):
        return {"available": self.available, "active": True}


class FakeStack:
    vad_kind = "energy"
    def __init__(self):
        self.events = []
        self.tts_active = False
        self.on_event = self.events.append
    def mark(self, stage):
        return None
    def tts_start(self):
        self.tts_active = True
    def tts_stop(self):
        self.tts_active = False
    def commit(self):
        return None


class FakeAgent:
    def __init__(self, answer="ok"):
        self.answer = answer
        self.commands = []
    def handle(self, text):
        self.commands.append(text)
        return self.answer


class FakeTTS:
    def __init__(self):
        self.answers = []
    def speak(self, answer):
        self.answers.append(answer)


def make_voice():
    voice = LiveVoiceV2(FakeAgent(), FakeTTS(), {"voice": {"sample_rate": 16000}}, FakeStack())
    voice.wake_manager = FakeWake()
    return voice


def test_real_wake_is_required_for_arming():
    voice = LiveVoiceV2(FakeAgent(), FakeTTS(), {"voice": {"sample_rate": 16000}}, FakeStack())
    voice.wake_manager = FakeWake(available=False)
    assert voice.arm() is False
    assert voice.state is VoiceState.DISARMED


def test_wake_to_processing_to_speaking_to_armed():
    voice = make_voice()
    assert voice.arm() is True
    assert voice.state is VoiceState.ARMED
    voice._transcribe = lambda pcm: "merhaba ultron"
    voice._handle_utterance(b"pcm")
    assert voice.agent.commands == ["merhaba ultron"]
    assert voice.tts.answers == ["ok"]
    assert voice.state is VoiceState.ARMED


def test_agent_failure_enters_error_without_fake_success():
    voice = make_voice()
    voice.arm()
    voice._transcribe = lambda pcm: "test"
    voice.agent.handle = lambda text: (_ for _ in ()).throw(RuntimeError("brain down"))
    voice._handle_utterance(b"pcm")
    assert voice.state is VoiceState.ERROR
    assert voice.tts.answers == []
    assert voice.last_error == "brain down"

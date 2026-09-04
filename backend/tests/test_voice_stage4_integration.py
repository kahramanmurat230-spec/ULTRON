from app.voice.voice_stack_v2 import LiveVoiceV2, VoiceState


class FakeStack:
    vad_kind = "energy"
    on_event = None

    def __init__(self):
        self.marks = []
        self.tts_started = False
        self.tts_stopped = False
        self.committed = False

    def mark(self, stage):
        self.marks.append(stage)

    def tts_start(self):
        self.tts_started = True

    def tts_stop(self):
        self.tts_stopped = True

    def commit(self):
        self.committed = True


class FakeWake:
    def __init__(self, hit=False):
        self.active = object()
        self.hit = hit
        self.calls = 0

    def start(self):
        return {"available": True}

    def stop(self):
        self.active = None

    def status(self):
        return {"available": self.active is not None, "active": "fake" if self.active else None}

    def process_chunk(self, _pcm):
        self.calls += 1
        return self.hit


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)
        return "fake"


class FakeAgent:
    def __init__(self, answer="tamam"):
        self.calls = []
        self.answer = answer

    def handle(self, text):
        self.calls.append(text)
        return self.answer


def make_voice(wake_hit=False, answer="tamam"):
    stack = FakeStack()
    tts = FakeTTS()
    agent = FakeAgent(answer)
    voice = LiveVoiceV2(agent, tts, {"voice": {}}, stack)
    voice.wake_manager = FakeWake(wake_hit)
    return voice, stack, tts, agent


def test_voice_state_enum_is_complete():
    assert [s.value for s in VoiceState] == [
        "DISARMED", "ARMED", "LISTENING", "PROCESSING", "SPEAKING", "ERROR"
    ]


def test_voice_status_reports_unavailable_wake_without_fake_success():
    voice = LiveVoiceV2(object(), object(), {"voice": {}}, FakeStack())
    voice.wake_manager = None
    assert voice.available is False
    assert voice.arm() is False
    assert voice.state is VoiceState.DISARMED


def test_no_wake_detection_never_reaches_stt_or_agent():
    voice, stack, tts, agent = make_voice(wake_hit=False)
    assert voice.arm() is True
    voice._transcribe = lambda _pcm: (_ for _ in ()).throw(AssertionError("STT must not run"))
    voice._handle_utterance(b"audio")
    assert voice.wake_manager.calls == 1
    assert agent.calls == []
    assert tts.spoken == []
    assert stack.committed is False
    assert voice.state is VoiceState.ARMED


def test_real_wake_detection_runs_stt_agent_and_tts():
    voice, stack, tts, agent = make_voice(wake_hit=True)
    voice.arm()
    voice._transcribe = lambda _pcm: "hava nasıl"
    voice._handle_utterance(b"audio")
    assert voice.wake_manager.calls == 1
    assert agent.calls == ["hava nasıl"]
    assert tts.spoken == ["tamam"]
    assert stack.marks == ["stt", "stt", "llm", "llm", "tts", "tts"]
    assert stack.committed is True
    assert voice.state is VoiceState.ARMED


def test_agent_failure_enters_error_without_fake_success():
    voice, stack, tts, agent = make_voice(wake_hit=True)
    voice.arm()
    voice._transcribe = lambda _pcm: "test"
    agent.handle = lambda _text: (_ for _ in ()).throw(RuntimeError("brain unavailable"))
    voice._handle_utterance(b"audio")
    assert voice.state is VoiceState.ERROR
    assert tts.spoken == []
    assert stack.tts_stopped is True


def test_barge_in_allows_the_interrupted_utterance_without_second_wake():
    voice, stack, tts, agent = make_voice(wake_hit=False)
    voice.state = VoiceState.SPEAKING
    voice._barge_in_pending = True
    voice._transcribe = lambda _pcm: "dur"
    voice._handle_utterance(b"audio")
    assert voice.wake_manager.calls == 0
    assert agent.calls == ["dur"]
    assert tts.spoken == ["tamam"]
    assert voice.state is VoiceState.ARMED

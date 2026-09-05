from app.voice.voice_stack_v2 import LiveVoiceV2, VoiceState


class Stack:
    vad_kind = "energy"
    on_event = None
    def mark(self, stage): pass
    def tts_start(self): pass
    def tts_stop(self): pass
    def commit(self): pass


class Wake:
    active = object()
    def start(self): return {"available": True}
    def stop(self): pass
    def status(self): return {"available": True, "active": True}
    def process_chunk(self, pcm): return None


def test_unrecognized_audio_never_reaches_agent():
    class Agent:
        def handle(self, text): raise AssertionError("agent must not be called")
    voice = LiveVoiceV2(Agent(), object(), {"voice": {}}, Stack())
    voice.wake_manager = Wake()
    voice.arm()
    voice._handle_utterance(b"audio")
    assert voice.state is VoiceState.ARMED

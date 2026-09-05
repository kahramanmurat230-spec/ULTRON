from app.core.self_awareness import SelfAwareness


class Registry:
    def names(self):
        return ["calculate", "delete_path"]

    def get(self, name):
        return {
            "calculate": {"fn": lambda **_: 1, "dangerous": False},
            "delete_path": {"fn": lambda **_: 1, "dangerous": True},
        }[name]


class TTS:
    def backend(self):
        return "piper-local"


class Brain:
    model = "qwen3:8b"


def test_report_is_read_only_and_truthful():
    report = SelfAwareness(Registry(), tts=TTS(), brain=Brain()).report()
    assert report["read_only"] is True
    assert report["tool_count"] == 2
    assert report["local_tts"] == {"available": True, "backend": "piper-local"}
    assert report["brain"]["available"] is True
    assert report["tools"][1]["dangerous"] is True


def test_unavailable_local_tts_is_not_faked():
    class NoTTS:
        def backend(self):
            return None

    report = SelfAwareness(tts=NoTTS()).report()
    assert report["local_tts"] == {"available": False, "backend": None}

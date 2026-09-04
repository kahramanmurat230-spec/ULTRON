from app.core.self_awareness import SelfAwareness


class FakeRegistry:
    def names(self):
        return ["safe_tool", "dangerous_tool"]

    def get(self, name):
        return {
            "safe_tool": {"fn": lambda: None, "dangerous": False},
            "dangerous_tool": {"fn": lambda: None, "dangerous": True},
        }[name]


class FakeTTS:
    def backend(self):
        return "piper-local"


class FakeBrain:
    model = "test-model"


def test_stage8_runtime_self_awareness_is_read_only_and_truthful():
    report = SelfAwareness(
        registry=FakeRegistry(),
        doctor=None,
        tts=FakeTTS(),
        brain=FakeBrain(),
    ).report()

    assert report["read_only"] is True
    assert report["tool_count"] == 2
    assert {x["name"] for x in report["tools"]} == {"safe_tool", "dangerous_tool"}
    assert next(x for x in report["tools"] if x["name"] == "dangerous_tool")["dangerous"] is True
    assert report["local_tts"] == {"available": True, "backend": "piper-local"}
    assert report["brain"] == {"available": True, "model": "test-model"}


def test_stage8_unavailable_tts_is_not_faked():
    class MissingTTS:
        def backend(self):
            return None

    report = SelfAwareness(tts=MissingTTS()).report()
    assert report["local_tts"] == {"available": False, "backend": None}

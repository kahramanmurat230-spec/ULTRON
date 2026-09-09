import base64

import pytest
from PIL import Image

from app.vision.vision_llm import VisionLLM


class FakeBrain:
    base_url = "http://127.0.0.1:11434"

    def __init__(self):
        self.calls = []

    def _post(self, path, payload):
        self.calls.append((path, payload))
        return {"model": payload["model"], "message": {"content": "Ekranda ULTRON var."}}


def test_resolve_model_prefers_configured_model():
    brain = FakeBrain()
    llm = VisionLLM(brain, {"vision": {"model": "llava:7b", "enabled": True}})
    assert llm.resolve_model(["qwen3:8b", "llava:7b", "llama3.2-vision:11b"]) == "llava:7b"


def test_resolve_model_falls_back_to_installed_vision_family():
    brain = FakeBrain()
    llm = VisionLLM(brain, {"vision": {"model": "missing:vision", "enabled": True}})
    assert llm.resolve_model(["qwen3:8b", "llava:7b"]) == "llava:7b"
    assert llm.resolve_model(["qwen3:8b"]) is None


def test_analyze_sends_real_image_payload(tmp_path):
    image_path = tmp_path / "screen.png"
    Image.new("RGB", (8, 8), (12, 34, 56)).save(image_path)
    brain = FakeBrain()
    llm = VisionLLM(brain, {"vision": {"model": "llava:7b", "enabled": True}})

    result = llm.analyze(str(image_path), "Bu ekranı analiz et.", ["llava:7b"])

    assert result == "Ekranda ULTRON var."
    assert len(brain.calls) == 1
    path, payload = brain.calls[0]
    assert path == "/api/chat"
    assert payload["model"] == "llava:7b"
    assert payload["stream"] is False
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert message["content"] == "Bu ekranı analiz et."
    encoded = message["images"][0]
    assert base64.b64decode(encoded) == image_path.read_bytes()


def test_analyze_refuses_missing_image(tmp_path):
    brain = FakeBrain()
    llm = VisionLLM(brain, {"vision": {"model": "llava:7b", "enabled": True}})
    with pytest.raises(FileNotFoundError):
        llm.analyze(str(tmp_path / "missing.png"), available_models=["llava:7b"])


def test_analyze_refuses_without_vision_model(tmp_path):
    image_path = tmp_path / "screen.png"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(image_path)
    brain = FakeBrain()
    llm = VisionLLM(brain, {"vision": {"model": "missing:vision", "enabled": True}})
    with pytest.raises(RuntimeError, match="Vision modeli kurulu değil"):
        llm.analyze(str(image_path), available_models=["qwen3:8b"])

"""Tests for the local-only multi-model layer; no network required."""
import pytest

from app.core.local_multi_model import LocalModelResult, LocalMultiModel


def test_local_endpoint_rejects_remote_hosts():
    with pytest.raises(ValueError):
        LocalMultiModel("https://api.example.com/v1")


def test_local_endpoint_accepts_ollama_default():
    client = LocalMultiModel()
    assert client.base_url == "http://127.0.0.1:11434/v1"


def test_race_deduplicates_models_and_preserves_input_order(monkeypatch):
    client = LocalMultiModel(max_workers=3)
    calls = []

    def fake_ask_one(model, messages, **kwargs):
        calls.append(model)
        return LocalModelResult(model=model, ok=True, content=f"answer:{model}")

    monkeypatch.setattr(client, "ask_one", fake_ask_one)
    results = client.race(["qwen3:8b", "qwen3:8b", "llama3.2:3b"], [{"role": "user", "content": "test"}])

    assert [r.model for r in results] == ["qwen3:8b", "llama3.2:3b"]
    assert sorted(calls) == ["llama3.2:3b", "qwen3:8b"]


def test_best_fastest_ignores_failed_results():
    results = [
        LocalModelResult("slow", True, "ok", 900),
        LocalModelResult("failed", False, "", 10, "boom"),
        LocalModelResult("fast", True, "ok", 100),
    ]
    assert LocalMultiModel.best_fastest(results).model == "fast"


def test_judge_uses_only_local_transport(monkeypatch):
    client = LocalMultiModel()
    captured = {}

    def fake_request(method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"choices": [{"message": {"content": "winner"}}]}

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.judge(
        [LocalModelResult("a", True, "A", 10), LocalModelResult("b", True, "B", 20)],
        judge_model="a",
    )
    assert result.content == "winner"
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["model"] == "a"


def test_race_and_judge_falls_back_to_fastest_if_judge_fails(monkeypatch):
    client = LocalMultiModel()
    results = [LocalModelResult("slow", True, "S", 100), LocalModelResult("fast", True, "F", 10)]
    monkeypatch.setattr(client, "race", lambda *args, **kwargs: results)
    monkeypatch.setattr(client, "judge", lambda *args, **kwargs: None)
    chosen, returned = client.race_and_judge(["slow", "fast"], [])
    assert chosen.model == "fast"
    assert returned == results

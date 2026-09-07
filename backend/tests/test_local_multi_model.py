"""Local multi-model evaluation tests; all transport is mocked/local-only."""
import pytest
from app.core.local_multi_model import LocalModelResult, LocalMultiModel


def test_local_endpoint_rejects_remote_hosts():
    with pytest.raises(ValueError): LocalMultiModel("https://api.example.com/v1")


def test_local_endpoint_accepts_ollama_default():
    assert LocalMultiModel().base_url == "http://127.0.0.1:11434/v1"


def test_race_deduplicates_models(monkeypatch):
    client = LocalMultiModel(max_workers=3); calls = []
    def fake(model, messages, **kwargs):
        calls.append(model); return LocalModelResult(model=model, ok=True, content=f"answer:{model}")
    monkeypatch.setattr(client, "ask_one", fake)
    results = client.race(["qwen3:8b", "qwen3:8b", "llama3.2:3b"], [{"role":"user","content":"test"}])
    assert [r.model for r in results] == ["qwen3:8b", "llama3.2:3b"]
    assert sorted(calls) == ["llama3.2:3b", "qwen3:8b"]


def test_scoring_prefers_substantive_structured_answer():
    client = LocalMultiModel()
    results = [LocalModelResult("short", True, "ok", 10), LocalModelResult("rich", True, "## Plan\n- Adım 1: kodu analiz et.\n- Adım 2: testi çalıştır.\n- Sonucu doğrula.", 20)]
    scored = client.score_results(results, "coding")
    assert scored[0].model == "rich" and scored[0].score > 0
    assert set(scored[0].dimensions) == {"substance","directness","completeness","structure","actionability"}


def test_judge_uses_only_local_transport(monkeypatch):
    client = LocalMultiModel(); captured = {}
    def fake_request(method, path, body=None):
        captured.update(method=method, path=path, body=body); return {"choices":[{"message":{"content":"winner"}}]}
    monkeypatch.setattr(client, "_request", fake_request)
    result = client.judge([LocalModelResult("a",True,"A"),LocalModelResult("b",True,"B")], judge_model="a")
    assert result.content == "winner" and captured["path"] == "/chat/completions" and captured["body"]["model"] == "a"


def test_race_and_judge_returns_local_judge(monkeypatch):
    client = LocalMultiModel()
    results = [LocalModelResult("a", True, "A", 100), LocalModelResult("b", True, "B", 20)]
    monkeypatch.setattr(client, "race", lambda *a, **k: results)
    monkeypatch.setattr(client, "judge", lambda *a, **k: LocalModelResult("judge:b", True, "B-final"))
    chosen, returned = client.race_and_judge(["a","b"], [], judge_model="b")
    assert chosen.model == "judge:b" and returned == results


def test_race_and_judge_falls_back_to_score(monkeypatch):
    client = LocalMultiModel()
    results = [LocalModelResult("slow", True, "S", 100), LocalModelResult("fast", True, "F", 10)]
    monkeypatch.setattr(client, "race", lambda *a, **k: results)
    monkeypatch.setattr(client, "judge", lambda *a, **k: None)
    chosen, _ = client.race_and_judge(["slow","fast"], [], judge_model="fast")
    assert chosen is not None

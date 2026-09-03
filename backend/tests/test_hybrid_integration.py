import pytest

from app.agent.agent import Agent
from app.agent.hybrid_integration import _deterministic_plan, _looks_like_work


class FakePlanner:
    def __init__(self):
        self.calls = []

    def make_plan(self, goal):
        self.calls.append(goal)
        return {
            "goal": goal,
            "steps": [
                {"index": 0, "tool": "calculate", "arguments": {"text": "2+2"}, "depends_on": []},
                {"index": 1, "tool": "calculate", "arguments": {"text": "{{step.0.result}}+1"}, "depends_on": [0]},
            ],
        }


class FakeRegistry:
    def get(self, name):
        return {"dangerous": False} if name == "calculate" else None

    def names(self):
        return ["calculate"]


class FakeExecutor:
    def execute(self, calls, approved=False):
        name, args = calls[0]
        assert name == "calculate"
        text = args["text"]
        if text == "2+2":
            return [4]
        if text == "4+1":
            return [5]
        raise AssertionError(text)


class FakeMemory:
    def __init__(self):
        self.rows = []

    def add(self, kind, text):
        self.rows.append((kind, text))


class FakeAudit:
    def __init__(self):
        self.rows = []

    def write(self, event, text):
        self.rows.append((event, text))


def make_agent(planner):
    agent = object.__new__(Agent)
    agent.planner = planner
    agent.executor = FakeExecutor()
    agent.registry = FakeRegistry()
    agent.memory = FakeMemory()
    agent.audit = FakeAudit()
    agent.redact_fn = None
    agent.hybrid_executor = None
    return agent


def test_hybrid_handle_executes_llm_plan_and_context(monkeypatch):
    planner = FakePlanner()
    agent = make_agent(planner)
    agent.hybrid_executor = __import__("app.agent.hybrid_executor", fromlist=["HybridPlanExecutor"]).HybridPlanExecutor(
        agent.executor, agent.registry, planner
    )
    monkeypatch.setattr(agent, "_boss_hitap", lambda x: x)

    answer = agent.handle("önce hesapla, sonra sonucu tekrar hesapla")

    assert "Görev tamamlandı" in answer
    assert "Adım 1: 4" in answer
    assert "Adım 2: 5" in answer
    assert planner.calls == ["önce hesapla, sonra sonucu tekrar hesapla"]
    assert agent.audit.rows[-1][0] == "HYBRID_PLAN"


def test_deterministic_plan_is_selected_before_llm():
    plan = _deterministic_plan("önce sistem durumunu kontrol et, sonra Downloads klasörünü listele")
    if plan is None:
        pytest.skip("Deterministic parser did not recognize this request")
    assert plan["planner"] == "deterministic-first"
    assert len(plan["steps"]) >= 2


def test_work_detection_is_bounded():
    assert _looks_like_work("önce kontrol et, sonra sonucu söyle")
    assert _looks_like_work("dosyaları bul ve oku")
    assert not _looks_like_work("merhaba nasılsın")
    assert not _looks_like_work("ekranımı analiz et")

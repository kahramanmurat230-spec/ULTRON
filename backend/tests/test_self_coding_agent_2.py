from app.code_agent.self_coding_agent import SelfCodingAgent


class DummyBrain:
    def ask(self, prompt, system=None):
        return '{"summary":"safe patch","files":[],"tests":["pytest"]}'


def test_2_requires_approval(tmp_path):
    agent = SelfCodingAgent(DummyBrain(), tmp_path)
    out = agent.run(approved=False)
    assert out["status"] == "APPROVAL_REQUIRED"
    assert out["phase"] == "APPROVAL"


def test_2_reuses_bounded_repair_and_exposes_phase(tmp_path, monkeypatch):
    agent = SelfCodingAgent(DummyBrain(), tmp_path)
    monkeypatch.setattr(agent.code_agent, "self_coding_loop", lambda **kwargs: {
        "ok": True, "status": "VERIFIED", "iterations": [], "final": {"passed": True}
    })
    out = agent.run(approved=True)
    assert out["ok"] is True
    assert out["status"] == "VERIFIED"
    assert out["phase"] == "VERIFIED"


def test_2_limits_are_propagated(tmp_path):
    agent = SelfCodingAgent(DummyBrain(), tmp_path)
    assert agent.code_agent.MAX_REPAIR_ITERATIONS == 3
    assert agent.code_agent.MAX_PATCH_FILES == 8
    assert agent.code_agent.MAX_CHANGED_LINES == 4000
    assert agent.code_agent.MAX_TEST_TIMEOUT == 180.0

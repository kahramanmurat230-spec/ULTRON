from pathlib import Path

from app.code_agent.code_agent import CodeAgent


class DummyBrain:
    def ask(self, prompt, system=None):
        return '{"summary":"safe patch","files":[],"tests":["pytest"]}'


def test_loop_requires_approval(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    out = agent.self_coding_loop(approved=False)
    assert out["status"] == "APPROVAL_REQUIRED"


def test_loop_retries_after_failure_and_stops_on_pass(tmp_path, monkeypatch):
    agent = CodeAgent(DummyBrain(), tmp_path)
    calls = {"test": 0, "patch": 0}

    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(agent, "propose_patch", lambda *a, **k: {"summary": "patch", "files": []})

    def fake_test(timeout=180):
        calls["test"] += 1
        return {"returncode": 1 if calls["test"] == 1 else 0,
                "stdout": "FAIL once" if calls["test"] == 1 else "2 passed",
                "stderr": "", "passed": calls["test"] > 1}

    monkeypatch.setattr(agent, "test", fake_test)
    out = agent.self_coding_loop(approved=True, max_iterations=3)
    assert out["ok"] is True
    assert out["status"] == "VERIFIED"
    assert calls["test"] == 2
    assert out["iterations"][0]["status"] == "FAIL_ROLLED_BACK"
    assert out["iterations"][0]["rolled_back"] is True
    assert out["iterations"][1]["status"] == "PASS"


def test_loop_caps_iterations(tmp_path, monkeypatch):
    agent = CodeAgent(DummyBrain(), tmp_path)
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(agent, "propose_patch", lambda *a, **k: {"summary": "patch", "files": []})
    monkeypatch.setattr(agent, "test", lambda timeout=180: {"returncode": 1, "stdout": "FAIL", "stderr": "", "passed": False})
    out = agent.self_coding_loop(approved=True, max_iterations=99)
    assert out["status"] == "EXHAUSTED"
    assert len(out["iterations"]) == agent.MAX_REPAIR_ITERATIONS
    assert all(x["rolled_back"] for x in out["iterations"])


def test_patch_rejects_path_outside_workspace(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    outside = Path(tmp_path).parent / "outside.txt"
    try:
        agent._apply_patch_checked({"files": [{"path": "../outside.txt", "content": "bad"}]})
    except PermissionError:
        pass
    else:
        raise AssertionError("outside patch was accepted")

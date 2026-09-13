from app.agent.autonomous_runtime import AutonomousRuntimeAdapter


class DummyCodeAgent:
    def __init__(self):
        self.calls = []

    def analyze(self, paths=None, goal=None):
        self.calls.append(("analyze", goal))
        return {"ok": True, "goal": goal}

    def self_coding_loop(self, paths=None, goal=None, approved=False, max_iterations=3, timeout=180):
        self.calls.append(("code", approved, goal))
        return {"ok": True, "status": "COMPLETED"}


def test_runtime_adapter_never_self_authorizes():
    code = DummyCodeAgent()
    adapter = AutonomousRuntimeAdapter(code)
    out = adapter.run("improve tests", runtime_approved=False)
    assert out["status"] == "APPROVAL_REQUIRED"
    assert not any(call[0] == "code" for call in code.calls)


def test_runtime_adapter_forwards_verified_runtime_approval():
    code = DummyCodeAgent()
    adapter = AutonomousRuntimeAdapter(code)
    out = adapter.run("improve tests", runtime_approved=True, max_iterations=2)
    assert out["ok"] is True
    assert code.calls[-1] == ("code", True, "improve tests")

from app.agent.autonomous_task import AutonomousTask


class DummyCodeAgent:
    def analyze(self, **kwargs):
        return "analysis-ok"

    def self_coding_loop(self, **kwargs):
        assert kwargs["approved"] is True
        return {"ok": True, "status": "VERIFIED", "iterations": []}


def test_autonomous_task_stops_at_mutation_gate_without_approval():
    out = AutonomousTask(DummyCodeAgent()).run("improve tests", approved=False)
    assert out["status"] == "APPROVAL_REQUIRED"
    assert out["steps"][0]["stage"] == "ANALYZE"


def test_autonomous_task_continues_after_explicit_approval():
    out = AutonomousTask(DummyCodeAgent()).run("improve tests", approved=True)
    assert out["ok"] is True
    assert out["status"] == "VERIFIED"

import pytest

from app.agent.gui_agent import GUIAction, GUIAgent, GUIAutomationMatcher


def test_matcher_exact_beats_partial_and_confidence_breaks_ties():
    elements = [
        {"text": "Kaydet ve kapat", "conf": 99, "box": {"x": 0, "y": 0, "w": 10, "h": 10}},
        {"text": "KAYDET", "conf": 60, "box": {"x": 20, "y": 20, "w": 10, "h": 10}},
        {"text": "kaydet", "conf": 92, "box": {"x": 30, "y": 30, "w": 10, "h": 10}},
    ]
    assert GUIAutomationMatcher.text(elements, "kaydet")["conf"] == 92
    assert GUIAutomationMatcher.text(elements, "kayd") ["text"] == "Kaydet ve kapat"


def test_plan_requires_observation_and_uses_center_of_box():
    agent = GUIAgent(max_steps=3)
    with pytest.raises(RuntimeError):
        agent.plan_text_click("Kaydet")
    agent._last_observation = {"elements": [{"text": "Kaydet", "conf": 95, "box": {"x": 10, "y": 20, "w": 40, "h": 20}}]}
    action = agent.plan_text_click("Kaydet")
    assert action.action == "click"
    assert action.args == {"x": 30, "y": 30}
    assert action.dangerous


def test_mutating_action_is_blocked_without_approval():
    calls = []

    class Fake:
        def click(self, x, y):
            calls.append((x, y))
            return {"clicked": [x, y]}

    agent = GUIAgent(automation=Fake())
    result = agent.execute(GUIAction("click", {"x": 1, "y": 2}))
    assert result["blocked"] is True
    assert result["reason"] == "approval_required"
    assert calls == []


def test_approved_action_dispatches_and_records_audit():
    calls, audit = [], []

    class Fake:
        def click(self, x, y):
            calls.append((x, y))
            return {"clicked": [x, y]}

        def read_screen_elements(self):
            return {"count": 1, "elements": [{"text": "after"}]}

    agent = GUIAgent(automation=Fake(), audit=lambda event, payload: audit.append((event, payload)))
    agent._last_observation = {"count": 1, "elements": [{"text": "before"}]}
    result = agent.execute(GUIAction("click", {"x": 3, "y": 4}), approved=True)
    assert result["ok"] is True
    assert calls == [(3, 4)]
    assert result["verification"]["verified"] is True
    assert [x[0] for x in audit] == ["gui.action.start", "gui.action.finish"]


def test_plan_is_bounded_and_stops_after_failure():
    class Fake:
        def click(self, x, y):
            return {"ok": False, "error": "failed"}

    agent = GUIAgent(automation=Fake(), max_steps=2)
    results = agent.execute_plan([
        GUIAction("click", {"x": 1, "y": 1}),
        GUIAction("click", {"x": 2, "y": 2}),
    ], approved=True, verify=False)
    assert len(results) == 1
    with pytest.raises(ValueError):
        agent.execute_plan([GUIAction("click", {"x": 1, "y": 1})] * 3, approved=True)

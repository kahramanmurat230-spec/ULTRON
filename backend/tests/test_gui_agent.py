from app.agent.gui_agent import GUIAction, GUIAgent, GUIAutomationMatcher


class FakeGUI:
    def __init__(self):
        self.calls = []

    def click_text(self, text, verify=True):
        self.calls.append(("click_text", text, verify))
        return {"found": True, "verification": {"action_effective": True}}

    def read_screen_elements(self):
        return {"elements": []}


def test_target_prefers_exact_then_confidence():
    elements = [{"text": "Ayarlar", "conf": .5}, {"text": "ayarlar", "conf": .9}, {"text": "Ayarlar gelişmiş", "conf": 1.0}]
    assert GUIAutomationMatcher.find(elements, "Ayarlar")["conf"] == .9


def test_gui_action_requires_approval():
    gui = FakeGUI()
    out = GUIAgent(gui).execute([GUIAction("click_text", params={"text": "Ayarlar"})], approved=False)
    assert out["status"] == "WAITING_APPROVAL"
    assert gui.calls == []


def test_gui_action_executes_and_verifies():
    gui = FakeGUI()
    out = GUIAgent(gui).execute([GUIAction("click_text", params={"text": "Ayarlar"})], approved=True)
    assert out["ok"] is True
    assert gui.calls == [("click_text", "Ayarlar", True)]


def test_gui_action_limit():
    gui = FakeGUI()
    actions = [GUIAction("press", params={"key": "enter"}) for _ in range(9)]
    out = GUIAgent(gui).execute(actions, approved=True)
    assert out["status"] == "REJECTED"

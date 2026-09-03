"""Bounded GUI agent: OBSERVE -> TARGET -> APPROVE -> ACT -> VERIFY."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class GUIAction:
    kind: str
    target: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)

class GUIAgent:
    MAX_ACTIONS = 8
    def __init__(self, automation, approved=False):
        self.automation = automation
        self.approved = approved
    def observe(self):
        return self.automation.read_screen_elements()
    @staticmethod
    def target(elements, text):
        return GUIAutomationMatcher.find(elements, text)
    def execute(self, actions, approved=None):
        if len(actions) > self.MAX_ACTIONS:
            return {"ok": False, "status": "REJECTED", "error": "GUI action limit exceeded"}
        approval = self.approved if approved is None else approved
        results = []
        for action in actions:
            if action.kind in {"click_text", "click", "double_click", "right_click", "type_text", "press", "hotkey", "scroll"} and not approval:
                results.append({"ok": False, "status": "WAITING_APPROVAL", "action": action.kind})
                return {"ok": False, "status": "WAITING_APPROVAL", "results": results}
            try:
                if action.kind == "click_text":
                    out = self.automation.click_text(action.params["text"], verify=True)
                elif action.kind == "click":
                    out = self.automation.click(action.target["x"], action.target["y"])
                elif action.kind == "double_click":
                    out = self.automation.double_click(action.target["x"], action.target["y"])
                elif action.kind == "right_click":
                    out = self.automation.right_click(action.target["x"], action.target["y"])
                elif action.kind == "type_text":
                    out = self.automation.type_text(action.params["text"])
                elif action.kind == "press":
                    out = self.automation.press(action.params["key"])
                elif action.kind == "hotkey":
                    out = self.automation.hotkey(action.params["keys"])
                elif action.kind == "scroll":
                    out = self.automation.scroll(action.params.get("amount", -600))
                else:
                    raise ValueError(f"unsupported GUI action: {action.kind}")
                results.append({"ok": True, "action": action.kind, "output": out})
            except Exception as exc:
                results.append({"ok": False, "action": action.kind, "error": str(exc)})
                return {"ok": False, "status": "FAILED", "results": results}
        return {"ok": True, "status": "DONE", "results": results}

class GUIAutomationMatcher:
    @staticmethod
    def find(elements, text):
        q = (text or "").strip().lower()
        exact = [e for e in elements or [] if str(e.get("text", "")).strip().lower() == q]
        if exact:
            return max(exact, key=lambda e: float(e.get("conf", 0)))
        partial = [e for e in elements or [] if q and q in str(e.get("text", "")).lower()]
        return max(partial, key=lambda e: float(e.get("conf", 0)), default=None)

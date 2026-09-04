"""Bounded computer-use orchestration for GUI Agent 2.0.

The agent separates observation/planning from execution. Mutating actions require
an explicit approval token supplied by the caller; this module does not bypass
ULTRON's existing approval, sandbox, or audit layers.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable, Mapping, Optional

from app.automation.gui import GUIAutomation


SAFE_ACTIONS = frozenset({"observe", "find_text", "find_window"})
MUTATING_ACTIONS = frozenset({
    "click", "double_click", "right_click", "move", "scroll",
    "type", "press", "hotkey", "click_text", "focus_window",
    "locate_and_click",
})
ALLOWED_ACTIONS = SAFE_ACTIONS | MUTATING_ACTIONS


@dataclass(frozen=True)
class GUIAction:
    action: str
    args: Mapping[str, Any]
    reason: str = ""

    @property
    def dangerous(self) -> bool:
        return self.action in MUTATING_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["dangerous"] = self.dangerous
        return out


class GUIAutomationMatcher:
    """Deterministic, headless-testable target matching."""

    @staticmethod
    def text(elements: Iterable[Mapping[str, Any]], query: str) -> Optional[Mapping[str, Any]]:
        q = str(query or "").strip().casefold()
        if not q:
            return None
        exact = []
        partial = []
        for element in elements or []:
            value = str(element.get("text", "")).strip()
            if not value:
                continue
            folded = value.casefold()
            confidence = float(element.get("conf", 0.0) or 0.0)
            if folded == q:
                exact.append((confidence, element))
            elif q in folded:
                partial.append((confidence, element))
        pool = exact or partial
        return max(pool, key=lambda item: item[0])[1] if pool else None


class GUIAgent:
    """Bounded GUI action runner with explicit approval and verification hooks."""

    def __init__(
        self,
        automation: Optional[GUIAutomation] = None,
        audit: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
        max_steps: int = 12,
    ) -> None:
        if max_steps < 1 or max_steps > 50:
            raise ValueError("max_steps must be between 1 and 50")
        self.gui = automation or GUIAutomation()
        self.audit = audit
        self.max_steps = max_steps
        self._last_observation: Optional[dict[str, Any]] = None

    def _record(self, event: str, payload: Mapping[str, Any]) -> None:
        if self.audit:
            self.audit(event, payload)

    def observe(self) -> dict[str, Any]:
        result = self.gui.read_screen_elements()
        self._last_observation = result
        self._record("gui.observe", {"count": result.get("count", 0)})
        return result

    def plan_text_click(self, text: str) -> GUIAction:
        """Create a click plan from the latest observation; never clicks itself."""
        if not self._last_observation:
            raise RuntimeError("observe() must run before planning a text click")
        element = GUIAutomationMatcher.text(self._last_observation.get("elements", []), text)
        if element is None:
            raise LookupError(f"GUI target not found: {text}")
        box = element.get("box") or {}
        for key in ("x", "y", "w", "h"):
            if key not in box:
                raise ValueError("GUI target has no complete bounding box")
        x = int(box["x"] + box["w"] / 2)
        y = int(box["y"] + box["h"] / 2)
        return GUIAction("click", {"x": x, "y": y}, reason=f"click text: {text}")

    def execute(self, action: GUIAction, approved: bool = False, verify: bool = True) -> dict[str, Any]:
        if action.action not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported GUI action: {action.action}")
        if action.dangerous and not approved:
            result = {"ok": False, "blocked": True, "reason": "approval_required", "action": action.action}
            self._record("gui.blocked", result)
            return result

        self._record("gui.action.start", action.to_dict())
        result = self._dispatch(action)
        result = dict(result or {})
        result.setdefault("ok", True)
        result["action"] = action.action

        if verify and action.dangerous and result.get("ok"):
            result["verification"] = self._verify(action, result)
        self._record("gui.action.finish", result)
        return result

    def execute_plan(self, actions: Iterable[GUIAction], approved: bool = False, verify: bool = True) -> list[dict[str, Any]]:
        actions = list(actions)
        if len(actions) > self.max_steps:
            raise ValueError(f"GUI plan exceeds max_steps={self.max_steps}")
        results = []
        for action in actions:
            result = self.execute(action, approved=approved, verify=verify)
            results.append(result)
            if not result.get("ok") or result.get("blocked"):
                break
        return results

    def _dispatch(self, action: GUIAction) -> Mapping[str, Any]:
        a = action.args
        fn = {
            "observe": self.gui.read_screen_elements,
            "find_text": lambda: {"element": GUIAutomationMatcher.text(a.get("elements", []), a.get("text", ""))},
            "find_window": lambda: self.gui.find_window(a["title"]),
            "click": lambda: self.gui.click(a["x"], a["y"]),
            "double_click": lambda: self.gui.double_click(a["x"], a["y"]),
            "right_click": lambda: self.gui.right_click(a["x"], a["y"]),
            "move": lambda: self.gui.move(a["x"], a["y"]),
            "scroll": lambda: self.gui.scroll(a["amount"], a.get("x"), a.get("y")),
            "type": lambda: self.gui.type_text(a["text"]),
            "press": lambda: self.gui.press(a["key"]),
            "hotkey": lambda: self.gui.hotkey(a["keys"]),
            "click_text": lambda: self.gui.click_text(a["text"], verify=False),
            "focus_window": lambda: self.gui.focus_window(a["title"]),
            "locate_and_click": lambda: self.gui.locate_and_click(a["image"], a.get("confidence", .8)),
        }[action.action]
        return fn()

    def _verify(self, action: GUIAction, result: Mapping[str, Any]) -> Mapping[str, Any]:
        if action.action in {"click_text", "locate_and_click"} and "verification" in result:
            return result["verification"]
        try:
            current = self.gui.read_screen_elements()
            before = self._last_observation
            self._last_observation = current
            if before is None:
                return {"verified": False, "reason": "no_before_observation"}
            before_text = [str(x.get("text", "")) for x in before.get("elements", [])]
            after_text = [str(x.get("text", "")) for x in current.get("elements", [])]
            changed = before_text != after_text
            return {"verified": changed, "method": "screen_ocr_delta", "changed": changed}
        except Exception as exc:  # honest verification failure
            return {"verified": False, "indeterminate": True, "error": str(exc)[:160]}

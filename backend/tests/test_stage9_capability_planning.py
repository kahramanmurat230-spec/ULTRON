import pytest

from app.agent.planner import Planner


class FakeRegistry:
    def __init__(self):
        self.items = {
            "safe_tool": {"fn": lambda: None, "dangerous": False},
            "dangerous_tool": {"fn": lambda: None, "dangerous": True},
            "offline_tool": {"fn": None, "dangerous": False},
        }

    def names(self):
        return list(self.items)

    def get(self, name):
        return self.items[name]


class FakeBrain:
    def chat(self, *args, **kwargs):
        raise AssertionError("make_plan should not be needed for validation tests")


def planner():
    return Planner(FakeBrain(), FakeRegistry())


def test_validation_rejects_registered_but_unavailable_tool():
    with pytest.raises(ValueError, match="kullanılamıyor"):
        planner().validate_plan({"steps": [{"tool": "offline_tool", "arguments": {}}]})


def test_validation_keeps_dangerous_tool_marked_but_does_not_authorize_it():
    result = planner().validate_plan({"steps": [{"tool": "dangerous_tool", "arguments": {}}]})
    assert result["steps"][0]["tool"] == "dangerous_tool"
    assert result["steps"][0]["dangerous"] is True


def test_validation_accepts_callable_safe_tool():
    result = planner().validate_plan({"steps": [{"tool": "safe_tool", "arguments": {}}]})
    assert result["steps"][0]["available"] is True

import pytest

from app.agent.planner import Planner


class Brain:
    def __init__(self, content):
        self.content = content

    def chat(self, messages, tools=None):
        return {"message": {"content": self.content}}


class Registry:
    def names(self):
        return ["list_directory", "read_text", "system_status", "delete_path"]


def planner(content):
    return Planner(Brain(content), Registry())


def test_valid_plan_is_normalized_and_bounded():
    p = planner('{"goal":"x","steps":[{"tool":"list_directory","arguments":{"root":"Downloads"},"reason":"inspect"},{"tool":"read_text","arguments":{"path":"a.txt"},"reason":"read","depends_on":[0]}]}')
    out = p.make_plan("x")
    assert out["planner"] == "local-hybrid"
    assert out["bounded"] is True
    assert out["steps"][1]["depends_on"] == [0]
    assert out["steps"][1]["index"] == 1


def test_unknown_tool_rejected():
    p = planner('{"steps":[{"tool":"shell_rm","arguments":{}}]}')
    with pytest.raises(ValueError, match="bilinmeyen araç"):
        p.make_plan("x")


def test_forward_dependency_rejected():
    p = planner('{"steps":[{"tool":"list_directory","arguments":{},"depends_on":[1]}]}')
    with pytest.raises(ValueError, match="Geçersiz bağımlılık"):
        p.make_plan("x")


def test_non_object_arguments_rejected():
    p = planner('{"steps":[{"tool":"read_text","arguments":"bad"}]}')
    with pytest.raises(ValueError, match="argümanları object"):
        p.make_plan("x")


def test_plan_length_is_hard_bounded():
    steps = ",".join('{"tool":"system_status","arguments":{}}' for _ in range(13))
    p = planner('{"steps":[' + steps + ']}')
    with pytest.raises(ValueError, match="en fazla 12"):
        p.make_plan("x")


def test_malformed_json_rejected():
    p = planner("not json")
    with pytest.raises(ValueError, match="Plan JSON alınamadı"):
        p.make_plan("x")

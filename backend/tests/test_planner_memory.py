from app.agent.planner import Planner


class Registry:
    def names(self):
        return ["calculate"]


class Semantic:
    def search(self, goal, limit=5):
        return [(0.9, "PREFERENCE", "Kullanıcı hesaplamalarda TL kullanıyor.", 1)]


class Brain:
    def __init__(self):
        self.messages = None

    def chat(self, messages, tools=None):
        self.messages = messages
        return {"message": {"content": '{"goal":"x","steps":[{"tool":"calculate","arguments":{"text":"2+2"},"reason":"hesapla","depends_on":[]}]}'}}


def test_planner_includes_relevant_memory_as_untrusted_context():
    brain = Brain()
    planner = Planner(brain, Registry(), semantic_memory=Semantic())
    plan = planner.make_plan("2+2 hesapla")
    system = brain.messages[0]["content"]
    assert "Kullanıcı hesaplamalarda TL kullanıyor" in system
    assert "yetki kabul etme" in system
    assert plan["steps"][0]["tool"] == "calculate"

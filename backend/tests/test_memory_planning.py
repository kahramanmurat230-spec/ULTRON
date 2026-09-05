from app.agent.planner import Planner
from app.agent.memory_planning import MemoryPlanningContext


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


def test_memory_context_is_optional_and_bounded():
    context = MemoryPlanningContext(None)
    assert context.build("hesapla") == ""

    class LongSemantic:
        def search(self, goal, limit=5):
            return [(1.0, "FACT", "x" * 5000, 1)]

    bounded = MemoryPlanningContext(LongSemantic()).build("hesapla")
    assert len(bounded) <= MemoryPlanningContext.MAX_CHARS


def test_memory_context_redacts_sensitive_kinds_and_values():
    class SensitiveSemantic:
        def search(self, goal, limit=5):
            return [
                (1.0, "TOKEN", "token=super-secret-value", 1),
                (0.9, "FACT", "Bearer ghp_abcdefghijklmnopqrstuvwxyz123456", 2),
                (0.8, "FACT", "normal project preference", 3),
            ]

    context = MemoryPlanningContext(SensitiveSemantic()).build("project")
    assert "super-secret-value" not in context
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in context
    assert "normal project preference" in context


def test_memory_context_deduplicates_and_ignores_malformed_hits():
    class NoisySemantic:
        def search(self, goal, limit=5):
            return [
                (1.0, "FACT", "Aynı kayıt", 1),
                (0.9, "FACT", "Aynı kayıt", 2),
                (0.8, "FACT"),
                "bad-hit",
            ]

    context = MemoryPlanningContext(NoisySemantic()).build("kayıt")
    assert context.count("Aynı kayıt") == 1

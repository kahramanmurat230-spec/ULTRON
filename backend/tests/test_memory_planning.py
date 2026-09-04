from app.agent.memory_planning import MemoryPlanningContext


class FakeSemanticMemory:
    def search(self, goal, limit=5):
        assert goal == "kahve makinesi aç"
        assert limit == 6
        return [
            (0.9, "PREFERENCE", "Kullanıcı kahveyi şekersiz tercih ediyor.", 2),
            (0.8, "DEVICE", "Kahve makinesi mutfakta.", 1),
        ]


def test_memory_context_is_relevant_and_bounded():
    context = MemoryPlanningContext(FakeSemanticMemory()).build("kahve makinesi aç")
    assert "[PREFERENCE]" in context
    assert "[DEVICE]" in context
    assert len(context) <= MemoryPlanningContext.MAX_CHARS


def test_empty_goal_does_not_query_memory():
    class ExplodingMemory:
        def search(self, *_args, **_kwargs):
            raise AssertionError("memory should not be queried")

    assert MemoryPlanningContext(ExplodingMemory()).build("") == ""

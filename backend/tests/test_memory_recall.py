"""Regression coverage for deterministic V16 long-term memory recall."""

import pytest

from app.agent.agent import Agent
from app.memory.semantic_memory import SemanticMemory
from app.memory.sqlite_memory import Memory


TEST_FACT = "V16_MEMORY_TEST_2026 benim test kodumdur"


class FakeBrain:
    def __init__(self):
        self.chat_calls = []

    def chat(self, messages, tools=None):
        self.chat_calls.append((messages, tools))
        return {"message": {"content": "Sistemler normal."}}


class FakeExecutor:
    def execute(self, calls, approved=False):
        raise AssertionError(f"Recall/chat test must not execute tools: {calls}")


class FakeRegistry:
    def ollama_tools(self, include_dangerous=True):
        return []

    def names(self):
        return []


class FakeAudit:
    def __init__(self):
        self.rows = []

    def write(self, event, text):
        self.rows.append((event, text))


class FakeTTS:
    def speak(self, text):
        raise AssertionError(f"Unexpected TTS request: {text}")


def make_agent(tmp_path):
    memory = Memory(path=tmp_path / "memory.db")
    brain = FakeBrain()
    agent = Agent(
        brain=brain,
        executor=FakeExecutor(),
        memory=memory,
        registry=FakeRegistry(),
        settings={"persona_guard_mode": "reframe"},
        audit=FakeAudit(),
        tts=FakeTTS(),
        semantic_memory=SemanticMemory(memory),
    )
    return agent, memory, brain


@pytest.mark.parametrize("question", [
    "ULTRON, test kodumu hatırlıyor musun?",
    "Benim test kodum ne?",
    "Geçen sana söylediğim test kodu neydi?",
    "Sana söylediğim test kodu neydi?",
    "Kaydettiğim test kodu neydi?",
    "Not aldığın test kodu neydi?",
])
def test_natural_turkish_recall_returns_the_saved_test_code(tmp_path, question):
    agent, memory, brain = make_agent(tmp_path)
    memory.add("FACT", TEST_FACT)

    answer = agent.handle(question)

    assert answer == "Evet Patron. Test kodunuz V16_MEMORY_TEST_2026."
    assert brain.chat_calls == [], "explicit recall must not fall through to LLM chat"


@pytest.mark.parametrize("question", [
    "test kodu neydi?",
    "benim cihazım neydi?",
    "hatırlıyor musun?",
    "geçen söylediğim şey neydi?",
])
def test_recall_detector_accepts_turkish_recall_patterns(question):
    assert Agent._is_memory_recall(question)


def test_fact_recall_selects_coherent_low_score_fact_not_generic_fact(tmp_path):
    agent, memory, _brain = make_agent(tmp_path)
    # The generic FACT can receive a higher token-overlap score for "test",
    # but lacks the requested "kod" field.  The lower-scored detailed FACT
    # must win after agent-side relevance validation.
    memory.add("FACT", "Test sonuçları başarılıdır")
    memory.add("FACT", TEST_FACT)

    raw_hits = agent.semantic_memory.search("ULTRON, test kodumu hatırlıyor musun?", limit=10)
    test_code_hit = next(hit for hit in raw_hits if hit[2] == TEST_FACT)
    generic_hit = next(hit for hit in raw_hits if hit[2] == "Test sonuçları başarılıdır")
    assert test_code_hit[0] < generic_hit[0]

    answer = agent.handle("ULTRON, test kodumu hatırlıyor musun?")

    assert answer == "Evet Patron. Test kodunuz V16_MEMORY_TEST_2026."


def test_durable_fact_is_selected_before_higher_scoring_conversation(tmp_path):
    agent, memory, brain = make_agent(tmp_path)
    memory.add("FACT", TEST_FACT)
    memory.add("conversation", "USER: Test kodunuz CONVERSATION_WRONG olarak yazıldı.")

    answer = agent.handle("ULTRON, test kodumu hatırlıyor musun?")

    assert "V16_MEMORY_TEST_2026" in answer
    assert "CONVERSATION_WRONG" not in answer
    assert brain.chat_calls == [], "recall must bypass normal LLM/tool routing"


def test_fact_has_priority_over_other_meaningful_long_term_categories(tmp_path):
    agent, memory, _brain = make_agent(tmp_path)
    memory.add("PREFERENCE", "PREFERENCE_WRONG benim test kodumdur")
    memory.add("FACT", TEST_FACT)

    answer = agent.handle("ULTRON, test kodumu hatırlıyor musun?")

    assert "V16_MEMORY_TEST_2026" in answer
    assert "PREFERENCE_WRONG" not in answer


def test_existing_memory_write_persistence_and_semantic_search_are_unchanged(tmp_path):
    agent, memory, _brain = make_agent(tmp_path)
    answer = agent.handle(f"not al: {TEST_FACT}")
    assert "Kaydettim (FACT)." in answer

    # Reopen to prove that the existing durable write path still persists.
    reopened = Memory(path=memory.path)
    semantic = SemanticMemory(reopened)
    hits = semantic.search("test kodumu", limit=5)

    assert any(kind == "FACT" and content == TEST_FACT for _score, kind, content, _created in hits)
    assert any(kind == "FACT" and content == TEST_FACT
               for kind, content, _created in reopened.recent(10))


def test_existing_profile_recall_path_is_not_regressed(tmp_path):
    agent, _memory, _brain = make_agent(tmp_path)
    agent.handle("Benim adım Ada")

    answer = agent.handle("Benim adım neydi?")

    assert "Adınız Ada." in answer


def test_normal_chat_still_uses_the_existing_llm_conversation_path(tmp_path):
    agent, _memory, brain = make_agent(tmp_path)

    answer = agent.handle("Merhaba, nasılsın?")

    assert "Sistemler normal." in answer
    assert len(brain.chat_calls) == 1

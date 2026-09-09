"""P1 regression: Turkish terminal command extraction.

"terminalde X çalıştır" must extract exactly X — never "de X çalıştır"
(the locative suffix is part of the trigger word, not the command).
"""
import asyncio

from agent import Agent, _extract_terminal_command


# ------------------------------------------------- direct extractor contract
def test_terminalde_prefix_is_not_part_of_command():
    assert _extract_terminal_command("terminalde rm -rf / çalıştır") == "rm -rf /"
    assert _extract_terminal_command("terminalde ls -la") == "ls -la"
    assert _extract_terminal_command("terminal'de python x.py çalıştır") == "python x.py"
    assert _extract_terminal_command("terminalda git status çalıştır") == "git status"
    assert _extract_terminal_command("terminale echo merhaba yaz") == "echo merhaba yaz"


def test_english_and_plain_prefixes_still_work():
    assert _extract_terminal_command("run echo hello") == "echo hello"
    assert _extract_terminal_command("terminal git status") == "git status"
    assert _extract_terminal_command("çalıştır: python --version") == "python --version"
    assert _extract_terminal_command("komut: git status") == "git status"
    assert _extract_terminal_command("terminalde çalıştır git status") == "git status"
    assert _extract_terminal_command("run git push origin main") == "git push origin main"


def test_postposition_form_requires_terminal_mention():
    # with an explicit terminal mention the pre-trigger part is the command
    assert _extract_terminal_command("python --version'ı terminalde çalıştır") == "python --version"
    assert _extract_terminal_command("git status komutunu çalıştır") == "git status"
    # without a terminal mention the old fall-through behaviour is preserved
    assert _extract_terminal_command("python --version çalıştır") is None
    assert _extract_terminal_command("spotify'ı çalıştır") is None


def test_open_terminal_requests_fall_through():
    # "terminali aç" = open the terminal app, not a shell command
    assert _extract_terminal_command("terminali aç") is None
    assert _extract_terminal_command("terminal aç") is None
    # but with an explicit run verb it stays a command
    assert _extract_terminal_command("terminalde open dosya.txt çalıştır") == "open dosya.txt"


def test_case_and_garbage_inputs():
    # command case is preserved (old code lowercased it)
    assert _extract_terminal_command("run echo Hello World") == "echo Hello World"
    assert _extract_terminal_command("terminalde echo Merhaba Dünya") == "echo Merhaba Dünya"
    assert _extract_terminal_command("") is None
    assert _extract_terminal_command("   ") is None
    assert _extract_terminal_command("çalıştır") is None
    assert _extract_terminal_command("terminal") is None


def test_trailing_trigger_words_are_stripped():
    assert _extract_terminal_command("terminalde rm -rf / çalıştır!") == "rm -rf /"
    assert _extract_terminal_command("run echo hi çalıştır") == "echo hi"


# ------------------------------------------------- intent-level integration
class FakeTools:
    def __init__(self):
        self.calls = []

    async def execute(self, name, arg=""):
        self.calls.append((name, arg))
        return {"ok": True, "output": f"ran {name}"}


class FakeMemory:
    def add_session(self, kind, text):
        pass

    def clear_all(self):
        pass

    def status(self):
        return {"session_count": 0, "persistent_count": 0}


class Recorder:
    async def broadcast(self, payload):
        pass

    async def on_activity(self, text, kind="info"):
        pass


def make_agent():
    return Agent(
        broadcast=Recorder().broadcast,
        tools=FakeTools(),
        memory=FakeMemory(),
        telemetry=None,
        get_ai_status=lambda: {"connected": False},
        on_activity=Recorder().on_activity,
    )


def test_intent_parses_terminalde_correctly():
    agent = make_agent()
    intent = agent.parse_intent("terminalde echo merhaba çalıştır")
    assert intent is not None
    assert intent["kind"] == "terminal"
    assert intent["arg"] == "echo merhaba"          # not "de echo merhaba"


def test_intent_open_terminal_is_not_shell():
    agent = make_agent()
    intent = agent.parse_intent("terminali aç")
    assert intent is None or intent["kind"] != "terminal"


def test_agent_run_executes_exact_command():
    agent = make_agent()
    out = asyncio.run(agent.run("terminalde echo hi çalıştır", approved=False))
    assert out.get("ok") is True
    name, arg = agent.tools.calls[0]
    assert name == "terminal" and arg == "echo hi"


def test_agent_run_requests_approval_for_non_allowlisted():
    agent = make_agent()
    asked = []

    def ask(text, risks):
        asked.append((text, risks))
        return "tid-9"

    agent.request_approval = ask
    asyncio.run(agent.run("terminalde rm -rf / çalıştır", approved=False))
    assert asked, "approval must be requested for non-allowlisted shell"
    assert agent.tools.calls == [], "shell must NOT execute before approval"
    # the approval risk carries the exact extracted command
    assert any("rm -rf /" in r for _t, risks in asked for r in risks)

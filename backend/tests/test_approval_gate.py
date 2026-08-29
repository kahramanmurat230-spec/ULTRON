"""Server-side approval gate tests (PHASE 1 security).

Rules locked here:
- V15 `run <cmd>` shell intent must NOT execute a non-allowlisted command
  without approval; it must request approval and enter WAITING_APPROVAL.
- Allowlisted read-only shell commands execute directly.
- With server-side approval (approved=True), the shell command executes.
"""
import asyncio

from agent import Agent, SAFE_SHELL_COMMANDS


class FakeTools:
    def __init__(self):
        self.calls = []

    async def execute(self, name, arg=""):
        self.calls.append((name, arg))
        return {"ok": True, "output": f"ran {name}"}


class FakeMemory:
    def __init__(self):
        self.rows = []

    def add_session(self, kind, text):
        self.rows.append((kind, text))

    def clear_all(self):
        pass

    def status(self):
        return {"session_count": 0, "persistent_count": 0}


class Recorder:
    def __init__(self):
        self.states = []
        self.approvals = []

    async def broadcast(self, payload):
        if payload.get("type") == "agent":
            self.states.append(payload["state"])

    async def on_activity(self, text, kind="info"):
        pass


def make_agent(tools, approval_cb=None):
    rec = Recorder()
    agent = Agent(
        broadcast=rec.broadcast,
        tools=tools,
        memory=FakeMemory(),
        telemetry=None,
        get_ai_status=lambda: {"connected": False},
        on_activity=rec.on_activity,
        request_approval=approval_cb,
    )
    return agent, rec


def test_safe_shell_allowlist_contains_readonly_only():
    for cmd in SAFE_SHELL_COMMANDS:
        first = cmd.split()[0].lower()
        assert first in {
            "echo", "dir", "ver", "whoami", "hostname", "date", "time",
            "tasklist", "ipconfig", "python", "node", "npm", "git", "pip",
        }, f"unexpected command family in allowlist: {cmd}"


def test_terminal_intent_still_parsed():
    agent, _ = make_agent(FakeTools())
    intent = agent.parse_intent("run echo hello")
    assert intent is not None and intent["kind"] == "terminal"


def test_dangerous_shell_requires_approval():
    tools = FakeTools()
    asked = []

    def ask_approval(text, risks):  # sync callable returning a pending task id
        asked.append((text, risks))
        return "tid-1"

    agent, rec = make_agent(tools, approval_cb=ask_approval)
    out = asyncio.run(agent.run("run del /f重要 file.txt", approved=False))
    assert asked, "approval must be requested for non-allowlisted shell"
    assert tools.calls == [], "shell must NOT execute before approval"
    assert "WAITING_APPROVAL" in rec.states
    assert out.get("result") == "waiting-approval"


def test_dangerous_shell_with_server_approval_executes():
    tools = FakeTools()
    agent, _ = make_agent(tools)
    asyncio.run(agent.run("run echo APPROVED_OK", approved=True))
    assert tools.calls and tools.calls[0][0] == "terminal"


def test_allowlisted_shell_runs_without_approval():
    tools = FakeTools()
    agent, rec = make_agent(tools)
    asyncio.run(agent.run("run echo hi", approved=False))
    assert tools.calls and tools.calls[0][0] == "terminal"
    assert "WAITING_APPROVAL" not in rec.states


def test_no_approval_callback_blocks_dangerous_shell():
    tools = FakeTools()
    agent, rec = make_agent(tools, approval_cb=None)
    out = asyncio.run(agent.run("run format c:", approved=False))
    assert tools.calls == []
    assert "ERROR" in rec.states
    assert not out.get("ok")

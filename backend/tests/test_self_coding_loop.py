from pathlib import Path

import pytest

from app.code_agent.code_agent import CodeAgent


class DummyBrain:
    def ask(self, prompt, system=None):
        return '{"summary":"safe patch","files":[],"tests":["pytest"]}'


class DummyAudit:
    def __init__(self):
        self.events = []

    def write(self, event, message):
        self.events.append((event, message))


def test_loop_requires_approval(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    out = agent.self_coding_loop(approved=False)
    assert out["status"] == "APPROVAL_REQUIRED"


def test_loop_retries_after_failure_and_stops_on_pass(tmp_path, monkeypatch):
    agent = CodeAgent(DummyBrain(), tmp_path)
    calls = {"test": 0}
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(
        agent,
        "propose_patch",
        lambda *a, **k: {"summary": "patch", "files": [{"path": "ok.py", "content": "x = 1\n"}]},
    )

    def fake_test(timeout=180):
        calls["test"] += 1
        return {
            "returncode": 1 if calls["test"] == 1 else 0,
            "stdout": "FAIL once" if calls["test"] == 1 else "2 passed",
            "stderr": "",
            "passed": calls["test"] > 1,
        }

    monkeypatch.setattr(agent, "test", fake_test)
    out = agent.self_coding_loop(approved=True, max_iterations=3)
    assert out["ok"] is True
    assert out["status"] == "VERIFIED"
    assert calls["test"] == 2
    assert out["iterations"][0]["status"] == "FAIL_ROLLED_BACK"
    assert out["iterations"][0]["rolled_back"] is True
    assert out["iterations"][1]["status"] == "PASS"
    assert (tmp_path / "ok.py").read_text() == "x = 1\n"


def test_loop_caps_iterations(tmp_path, monkeypatch):
    agent = CodeAgent(DummyBrain(), tmp_path)
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(
        agent,
        "propose_patch",
        lambda *a, **k: {"summary": "patch", "files": [{"path": "ok.py", "content": "x = 1\n"}]},
    )
    monkeypatch.setattr(
        agent,
        "test",
        lambda timeout=180: {"returncode": 1, "stdout": "FAIL", "stderr": "", "passed": False},
    )
    out = agent.self_coding_loop(approved=True, max_iterations=99)
    assert out["status"] == "EXHAUSTED"
    assert len(out["iterations"]) == agent.MAX_REPAIR_ITERATIONS
    assert all(x["rolled_back"] for x in out["iterations"])
    assert not (tmp_path / "ok.py").exists()


def test_patch_rejects_path_outside_workspace(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    outside = Path(tmp_path).parent / "outside.txt"
    with pytest.raises(PermissionError):
        agent._apply_patch_checked({"files": [{"path": "../outside.txt", "content": "bad"}]})
    assert not outside.exists()


def test_patch_rejects_absolute_path(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    with pytest.raises(PermissionError):
        agent._apply_patch_checked({"files": [{"path": str(tmp_path / "bad.py"), "content": "x = 1\n"}]})


def test_patch_rejects_invalid_python_before_any_write(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    with pytest.raises(ValueError, match="Sözdizimi hatalı"):
        agent._apply_patch_checked(
            {
                "files": [
                    {"path": "first.py", "content": "x = 1\n"},
                    {"path": "second.py", "content": "def broken(:\n"},
                ]
            }
        )
    assert not first.exists()
    assert not second.exists()


def test_patch_rejects_invalid_json_before_write(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    target = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="Geçersiz JSON"):
        agent._apply_patch_checked({"files": [{"path": "bad.json", "content": "{broken"}]})
    assert not target.exists()


def test_patch_rejects_duplicate_paths(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    with pytest.raises(ValueError, match="Tekrarlanan patch yolu"):
        agent._apply_patch_checked(
            {
                "files": [
                    {"path": "a.py", "content": "x = 1\n"},
                    {"path": "./a.py", "content": "x = 2\n"},
                ]
            }
        )


def test_patch_rejects_size_limits(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    with pytest.raises(ValueError, match="dosya boyutu sınırı"):
        agent._apply_patch_checked(
            {"files": [{"path": "huge.py", "content": "x\n" * (agent.MAX_FILE_CHARS // 2 + 1)}]}
        )


def test_patch_rejects_protected_security_file(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    with pytest.raises(PermissionError):
        agent._apply_patch_checked(
            {"files": [{"path": "app/security/risk.py", "content": "x = 1\n"}]}
        )


def test_collect_rejects_paths_outside_project(tmp_path):
    agent = CodeAgent(DummyBrain(), tmp_path)
    with pytest.raises(PermissionError):
        agent.collect([".."])


def test_loop_reports_generation_failure(tmp_path, monkeypatch):
    agent = CodeAgent(DummyBrain(), tmp_path)
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(agent, "propose_patch", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad JSON")))
    out = agent.self_coding_loop(approved=True)
    assert out["status"] == "PATCH_GENERATION_FAILED"
    assert out["iterations"] == []


def test_loop_audits_start_rollback_and_verify(tmp_path, monkeypatch):
    audit = DummyAudit()
    agent = CodeAgent(DummyBrain(), tmp_path, audit=audit)
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: "analysis")
    monkeypatch.setattr(
        agent,
        "propose_patch",
        lambda *a, **k: {"summary": "patch", "files": [{"path": "ok.py", "content": "x = 1\n"}]},
    )
    monkeypatch.setattr(
        agent,
        "test",
        lambda timeout=180: {"returncode": 0, "stdout": "1 passed", "stderr": "", "passed": True},
    )
    out = agent.self_coding_loop(approved=True)
    assert out["status"] == "VERIFIED"
    assert [event for event, _ in audit.events] == ["SELF_CODING_START", "SELF_CODING_VERIFIED"]

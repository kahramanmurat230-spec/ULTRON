"""E2E: the UNIFIED production path over real HTTP.

Boots the REAL backend (server.py subprocess) plus a STAND-IN local LLM
(a tiny HTTP service on the configured Ollama endpoint — the actual local
model cannot run in this sandbox; everything else is production code):

  POST /api/tasks (general goal)
    → SupervisorAgent routes to kind="plan"
    → REAL Planner calls the brain over real HTTP
    → plan staged into the persistent TaskEngine
    → dangerous write_text step stops in WAITING_APPROVAL
    → POST /api/tasks/{id}/approve (server-side one-shot approval)
    → REAL Executor + FilesystemSandbox + UndoJournal write the file
    → StepVerifier independently observes the file on disk
    → verification + evidence-based report → COMPLETED
    → OutcomeLearner writes "Verified outcome ..." into the REAL V16 memory
    → GET /api/memory/v16/search finds it (planner consumes this store)

Also proves the audit pipeline still runs E2E for audit-keyword goals and
that the durable event trail (/api/events) recorded the whole task run.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E2E_OUT_REL = "data/e2e_plan/rapor.md"   # inside the sandbox write roots

PLAN_JSON = json.dumps({
    "goal": "e2e haftalik raporu yaz",
    "steps": [
        {"tool": "write_text",
         "arguments": {"path": E2E_OUT_REL, "content": "ULTRON E2E rapor icerigi"},
         "reason": "raporu olustur"},
        {"tool": "read_text", "arguments": {"path": E2E_OUT_REL},
         "reason": "yazilani oku", "depends_on": [0]},
    ],
}, ensure_ascii=False)

_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER")
_CREATED = []  # task ids created by this module (cleaned up at the end)


class _FakeOllama(BaseHTTPRequestHandler):
    """Stand-in for the local LLM only. Returns a fixed validated plan for
    /api/chat (the single planner call of this E2E)."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path.startswith("/api/chat"):
            body = json.dumps({"message": {"content": PLAN_JSON}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def fake_ollama():
    srv = ThreadingHTTPServer(("127.0.0.1", 11434), _FakeOllama)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:11434"
    srv.shutdown()
    srv.server_close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(port, path, timeout=10):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(port, path, body=None, timeout=60):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


@pytest.fixture(scope="module")
def server(fake_ollama):
    port = _free_port()
    env = dict(os.environ, ULTRON_PORT=str(port), ULTRON_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen(
        [sys.executable, "-B", "server.py"], cwd=BACKEND_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    deadline = time.time() + 40
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited during boot")
        try:
            status, _ = _get(port, "/api/system", timeout=2)
            ready = status == 200
            break
        except Exception:
            time.sleep(0.3)
    if not ready:
        proc.terminate()
        raise RuntimeError("server did not become ready in 40s")
    yield port
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        time.sleep(1.0)
    except Exception:
        proc.terminate()


def _wait_status(port, task_id, wanted, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        _, t = _get(port, f"/api/tasks/{task_id}")
        last = t
        if t.get("status") in wanted:
            return t
        time.sleep(0.3)
    raise TimeoutError(f"task {task_id} stuck in {last and last.get('status')}")


@pytest.fixture(scope="module")
def completed_plan_task(server):
    """Drive one general goal through the whole unified pipeline."""
    port = server
    status, resp = _post(port, "/api/tasks",
                         {"goal": "e2e haftalik raporu yaz"})
    assert status == 200 and resp["ok"] is True
    tid = resp["task_id"]
    _CREATED.append(tid)

    # planner runs (real HTTP to the stand-in brain), stages the plan, then
    # the dangerous write step must stop for SERVER-SIDE approval
    t = _wait_status(port, tid, ("WAITING_APPROVAL",) + _TERMINAL, timeout=60)
    assert t["status"] == "WAITING_APPROVAL", t["status"]
    assert t["kind"] == "plan"
    # nothing written yet — approval is a precondition, not a formality
    assert not os.path.exists(os.path.join(BACKEND_DIR, E2E_OUT_REL))
    approval_step = t["steps"][1]
    assert approval_step["worker"] == "tool_step"

    status, res = _post(port, f"/api/tasks/{tid}/approve")
    assert status == 200 and res["ok"] is True

    t = _wait_status(port, tid, _TERMINAL, timeout=120)
    return tid, t


def test_unified_plan_task_completes_with_verified_effect(server, completed_plan_task):
    tid, t = completed_plan_task
    port = server
    assert t["status"] == "COMPLETED", json.dumps(t["steps"], ensure_ascii=False)[:800]
    workers_seq = [s["worker"] for s in t["steps"]]
    assert workers_seq == ["planner", "tool_step", "tool_step",
                           "verification", "report"]
    assert all(s["status"] == "SUCCESS" for s in t["steps"])

    # REAL effect on disk (real executor + filesystem sandbox)
    out_path = os.path.join(BACKEND_DIR, E2E_OUT_REL)
    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        assert "ULTRON E2E rapor" in f.read()

    # independent verification flags persisted in the task
    write_out = t["steps"][1]["result"]["output"]
    assert write_out["executed"] is True
    assert write_out["succeeded"] is True
    assert write_out["verified"]["mode"] == "filesystem"
    assert write_out["verified"]["verified"] is True

    # evidence-based final report persisted
    assert t["result"]["ok"] is True
    assert t["result"].get("final_report")

    # one-shot approval consumed
    _, after = _get(port, f"/api/tasks/{tid}")
    assert after["user_approved"] is False


def test_outcome_learned_into_memory_the_planner_reads(server, completed_plan_task):
    port = server
    _, hits = _get(port, "/api/memory/v16/search?q=" + urllib.parse.quote("e2e haftalik raporu"))
    assert isinstance(hits, list) and hits, "outcome record not found in memory"
    outcome_hits = [h for h in hits if h["kind"] == "task_outcome"
                    and h["content"].startswith("Verified outcome ")]
    assert outcome_hits, hits
    content = outcome_hits[0]["content"]
    assert "goal=e2e haftalik raporu yaz" in content
    assert "status=SUCCEEDED" in content


def test_durable_event_trail_recorded_the_run(server, completed_plan_task):
    port = server
    _, ev = _get(port, "/api/events")
    assert ev["ok"] is True and ev["stats"]["events"] > 0
    types = [e["event_type"] for e in ev["events"]]
    assert any(t.startswith("task.") for t in types)


def test_audit_pipeline_still_runs_e2e(server):
    port = server
    status, resp = _post(port, "/api/tasks",
                         {"goal": "repo analizi ve testleri calistir"})
    assert status == 200 and resp["ok"] is True
    _CREATED.append(resp["task_id"])
    t = _wait_status(port, resp["task_id"], _TERMINAL, timeout=180)
    assert t["status"] == "COMPLETED"
    assert t["kind"] == "supervisor"
    workers = {s["worker"] for s in t["steps"]}
    assert {"code_analysis", "tests", "verification", "report"} <= workers


def test_cleanup_e2e_artifacts(server, completed_plan_task):
    # keep the repo + shared task DB clean: cancel any non-terminal E2E
    # tasks and remove the E2E output dir
    port = server
    for tid in list(_CREATED):
        try:
            _, t = _get(port, f"/api/tasks/{tid}")
            if t.get("status") not in _TERMINAL:
                _post(port, f"/api/tasks/{tid}/cancel")
        except Exception:
            pass
    p = os.path.join(BACKEND_DIR, "data", "e2e_plan")
    if os.path.isdir(p):
        shutil.rmtree(p)
    assert not os.path.exists(p)

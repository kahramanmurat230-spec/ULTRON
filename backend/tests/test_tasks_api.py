"""P0-2 / P0-3 endpoint regression: real server, real HTTP.

Boots the actual aiohttp backend (server.py) as a subprocess and proves:
- GET /api/tasks returns 200 JSON (was a 500: engine.list(status=...) mismatch)
- GET /api/tasks?status=... filters by the persisted status
- POST /api/tasks/{id}/pause pauses a RUNNING task (was a 500: no engine.pause)
- POST /api/tasks/{id}/resume resumes it and the task runs to completion
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(port, path, timeout=10):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(port, path, body=None, timeout=30):
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
def server():
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
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def _wait_status(port, tid, wanted, timeout=60):
    deadline = time.time() + timeout
    task = {}
    while time.time() < deadline:
        _, task = _get(port, f"/api/tasks/{tid}")
        if task.get("status") in wanted:
            return task
        time.sleep(0.25)
    raise AssertionError(f"task {tid} never reached {wanted} (last: {task.get('status')})")


def test_tasks_list_endpoint_returns_json_not_500(server):
    port = server
    status, body = _get(port, "/api/tasks")
    assert status == 200
    assert isinstance(body.get("tasks"), list)
    for t in body["tasks"]:
        assert t["status"] in ("PENDING", "RUNNING", "WAITING_APPROVAL", "PAUSED",
                               "FAILED", "RECOVERING", "COMPLETED", "CANCELLED",
                               "SCHEDULED", "WAITING_BRAIN", "DEAD_LETTER")


def test_status_filter_pause_resume_end_to_end(server):
    port = server
    # create a real supervisor task; the tests worker step takes a few seconds,
    # giving a stable RUNNING window to observe and pause (the analysis step
    # guarantees a verifiable output even when the repo-root pytest run fails)
    status, body = _post(port, "/api/tasks", {"goal": "repo analizi ve testleri calistir"})
    assert status == 200 and body.get("ok") is True
    tid = body["task_id"]

    # P0-2: status filter reflects the RUNNING task
    deadline = time.time() + 30
    seen_running = False
    running = {"tasks": []}
    while time.time() < deadline:
        _, running = _get(port, "/api/tasks?status=RUNNING")
        if any(t["id"] == tid for t in running["tasks"]):
            seen_running = True
            break
        _, task = _get(port, f"/api/tasks/{tid}")
        if task["status"] in _TERMINAL:
            break
        time.sleep(0.2)
    assert seen_running, "task never appeared under ?status=RUNNING"
    assert all(t["status"] == "RUNNING" for t in running["tasks"])

    # P0-3: pause a RUNNING task — was a 500 before. Retry briefly: the very
    # first attempt may race the PENDING->RUNNING transition at task start.
    paused_ok = False
    deadline = time.time() + 30
    while time.time() < deadline:
        status, res = _post(port, f"/api/tasks/{tid}/pause")
        assert status != 500, f"pause must never 500: {res}"
        if status == 200 and res.get("ok"):
            paused_ok = True
            break
        _, task = _get(port, f"/api/tasks/{tid}")
        if task["status"] in _TERMINAL:
            break
        time.sleep(0.3)
    assert paused_ok, "could not pause the RUNNING task"
    paused = _wait_status(port, tid, {"PAUSED"}, timeout=30)
    assert paused["status"] == "PAUSED"

    # the status filter now sees it under PAUSED (and not under RUNNING)
    _, pa = _get(port, "/api/tasks?status=PAUSED")
    assert any(t["id"] == tid for t in pa["tasks"])
    _, ru = _get(port, "/api/tasks?status=RUNNING")
    assert not any(t["id"] == tid for t in ru["tasks"])

    # pause of a PAUSED task is an honest 400, not a fake ok
    status, res = _post(port, f"/api/tasks/{tid}/pause")
    assert status == 400 and res["ok"] is False

    # resume: the runner is re-spawned and the task completes
    status, res = _post(port, f"/api/tasks/{tid}/resume")
    assert status == 200 and res["ok"] is True
    done = _wait_status(port, tid, {"COMPLETED", "FAILED"}, timeout=120)
    assert done["status"] == "COMPLETED"

    # completed task shows up under the COMPLETED filter
    _, comp = _get(port, "/api/tasks?status=COMPLETED")
    assert any(t["id"] == tid for t in comp["tasks"])


def test_pause_of_terminal_task_is_honest_error(server):
    port = server
    status, body = _post(port, "/api/tasks", {"goal": "kod analizi yap"})
    tid = body["task_id"]
    done = _wait_status(port, tid, {"COMPLETED", "FAILED", "PAUSED"}, timeout=90)
    if done["status"] == "COMPLETED":
        status, res = _post(port, f"/api/tasks/{tid}/pause")
        assert status == 400 and res["ok"] is False

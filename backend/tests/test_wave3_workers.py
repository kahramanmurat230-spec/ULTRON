"""WAVE 3 / Worker abstraction: lifecycle, deterministic state machine,
capability-role binding (escalation reddi), worker limiti, kalıcılık."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.worker import (  # noqa: E402
    ALLOWED_WORKER_TRANSITIONS, InvalidWorkerTransition, Worker,
    WorkerLimitExceeded, WorkerRegistry, WORKER_ROLES, WORKER_STATES,
)


def reg(tmp, max_workers=500):
    return WorkerRegistry(db_path=os.path.join(tmp, "workers.db"),
                          max_workers=max_workers)


# ------------------------------------------------------------ lifecycle
def test_worker_full_lifecycle(tmp_path):
    w = Worker("task-1", "RESEARCH", trace_id="t0", depends_on=["w-a"])
    assert w.state == "PENDING" and w.attempts == 0
    w.transition("READY")
    w.transition("RUNNING")
    assert w.attempts == 1 and w.started_at is not None
    w.transition("SUCCEEDED")
    assert w.finished_at is not None and w.is_terminal
    d = w.to_dict()
    for k in ("worker_id", "task_id", "parent_task_id", "role", "capabilities",
              "permissions", "budget", "state", "trace_id", "created_at",
              "started_at", "finished_at"):
        assert k in d
    assert d["depends_on"] == ["w-a"]


def test_worker_schema_fields(tmp_path):
    w = Worker("t", "SYSTEM", parent_task_id="parent", budget={"token": 5},
               priority=3)
    assert w.parent_task_id == "parent" and w.role == "SYSTEM"
    assert w.capabilities == ("SYSTEM_READ",)     # rol varsayılanı
    assert w.priority == 3


def test_waiting_retrying_transitions(tmp_path):
    w = Worker("t", "BROWSER")
    w.transition("READY"); w.transition("RUNNING"); w.transition("WAITING")
    assert w.wait_since is not None
    w.transition("RUNNING"); w.transition("FAILED")
    w.transition("RETRYING"); w.transition("RUNNING")
    assert w.attempts == 3
    w.transition("TIMEOUT"); w.transition("RETRYING")
    w.transition("RUNNING"); w.transition("SUCCEEDED")


def test_invalid_transitions_rejected(tmp_path):
    w = Worker("t", "CODING")
    for bad in ("RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "TIMEOUT"):
        with pytest.raises(InvalidWorkerTransition):
            w.transition(bad)
    w.transition("READY")
    with pytest.raises(InvalidWorkerTransition):
        w.transition("PENDING")                    # geri dönüş yok
    w.transition("RUNNING"); w.transition("SUCCEEDED")
    for bad in ("RUNNING", "READY", "FAILED", "WAITING"):
        with pytest.raises(InvalidWorkerTransition):
            w.transition(bad)                      # terminal
    with pytest.raises(InvalidWorkerTransition):
        w.transition("BOGUS")


def test_all_states_and_roles_defined(tmp_path):
    assert set(WORKER_STATES) == {"PENDING", "READY", "RUNNING", "WAITING",
                                  "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT",
                                  "BLOCKED", "RETRYING"}
    assert set(WORKER_ROLES) >= {"RESEARCH", "CODING", "VISION", "BROWSER",
                                 "COMPUTER", "SYSTEM", "MEMORY", "VERIFICATION",
                                 "REVIEWER"}
    # her state makinede tanımlı (determinizm)
    assert set(ALLOWED_WORKER_TRANSITIONS) == set(WORKER_STATES)


# ------------------------------------------------------------ roles/caps
def test_unknown_role_rejected(tmp_path):
    with pytest.raises(ValueError):
        Worker("t", "WIZARD")


def test_capability_escalation_rejected_at_construction(tmp_path):
    with pytest.raises(ValueError, match="escalation"):
        Worker("t", "RESEARCH", capabilities=["WEB_SEARCH", "RUN_TEST"])
    with pytest.raises(ValueError, match="escalation"):
        Worker("t", "MEMORY", capabilities=["WRITE_WORKSPACE", "SYSTEM_READ"]
               ) if False else Worker("t", "SYSTEM",
                                      capabilities=["WRITE_WORKSPACE"])


def test_narrower_capability_set_allowed(tmp_path):
    w = Worker("t", "CODING", capabilities=["READ_WORKSPACE"])
    assert w.capabilities == ("READ_WORKSPACE",)


# ------------------------------------------------------------ persistence
def test_registry_persist_and_reload(tmp_path):
    r = reg(str(tmp_path))
    w = Worker("task-9", "VISION")
    w.transition("READY")
    r.save(w)
    r.close()
    r2 = reg(str(tmp_path))
    got = r2.get(w.worker_id)
    assert got.state == "READY" and got.role == "VISION"
    assert got.capabilities == w.capabilities
    assert len(r2.for_task("task-9")) == 1


def test_registry_worker_limit_enforced(tmp_path):
    r = reg(str(tmp_path), max_workers=3)
    for i in range(3):
        w = Worker(f"t{i}", "SYSTEM")
        w.transition("READY")
        r.save(w)
    with pytest.raises(WorkerLimitExceeded):
        w4 = Worker("t4", "SYSTEM")
        w4.transition("READY")
        r.save(w4)
    # terminal olanlar sayılmaz → yer açılır
    done = Worker("t0b", "SYSTEM")
    done.transition("READY"); done.transition("RUNNING")
    done.transition("SUCCEEDED")
    r.save(done)


def test_registry_updates_state(tmp_path):
    r = reg(str(tmp_path))
    w = Worker("t", "RESEARCH")
    r.save(w)
    w.transition("READY"); w.transition("RUNNING"); w.transition("SUCCEEDED")
    r.save(w)
    assert r.get(w.worker_id).state == "SUCCEEDED"
    assert r.active_count() == 0

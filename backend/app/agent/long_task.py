"""Long Task Engine — bounded, resumable multi-step execution."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable


class LongTaskEngine:
    """Execute a finite plan with checkpoints, deadline and bounded retries."""

    def __init__(self, checkpoint_path="data/long_tasks/checkpoint.json", max_steps=32, max_retries=1, clock=time.monotonic):
        self.checkpoint_path = Path(checkpoint_path)
        self.max_steps = int(max_steps)
        self.max_retries = int(max_retries)
        self.clock = clock

    def _save(self, state):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self):
        if not self.checkpoint_path.exists():
            return None
        return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))

    def run(self, steps: Iterable[dict], execute: Callable[[dict], object], approved=False, deadline_s=None, task_id=None):
        plan = list(steps)
        if len(plan) > self.max_steps:
            raise ValueError(f"Long task step limit exceeded: {len(plan)} > {self.max_steps}")
        loaded = self.load() if task_id else None
        state = loaded if loaded and loaded.get("task_id") == task_id else None
        if state is None:
            state = {"task_id": task_id or uuid.uuid4().hex, "status": "RUNNING", "next_step": 0, "results": []}
        start = self.clock()
        while state["next_step"] < len(plan):
            idx = state["next_step"]
            if deadline_s is not None and self.clock() - start >= deadline_s:
                state["status"] = "CANCELLED"
                state["reason"] = "hard_deadline"
                self._save(state)
                return state
            step = plan[idx]
            attempts = 0
            while True:
                try:
                    result = execute(step)
                    state["results"].append({"index": idx, "ok": True, "result": result})
                    state["next_step"] = idx + 1
                    self._save(state)
                    # A step that finishes after the hard deadline is successful work,
                    # but the overall task must still stop and remain resumable.
                    if deadline_s is not None and self.clock() - start >= deadline_s:
                        state["status"] = "CANCELLED"
                        state["reason"] = "hard_deadline"
                        self._save(state)
                        return state
                    break
                except Exception as exc:
                    attempts += 1
                    if attempts > self.max_retries or step.get("retryable") is False:
                        state["status"] = "ERROR"
                        state["reason"] = str(exc)
                        state["failed_step"] = idx
                        self._save(state)
                        return state
        state["status"] = "DONE"
        self._save(state)
        return state

    def clear(self):
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

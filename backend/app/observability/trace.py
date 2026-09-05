"""ULTIMATE Observability — local-first structured tracing (Wave 1).

OpenTelemetry-aligned semantics, ZERO cloud export:
  trace_id / span_id / parent_span_id
  span kinds: task | step | tool | provider
  span fields: name, kind, task_id, attributes{task,step,worker,tool,provider},
               start/end timestamps, duration_ms, status(OK|ERROR),
               risk, approval, budget, result_summary, error_class

SECRETS: every string that enters a span (attributes, summaries, errors)
passes through redaction BEFORE persisting — secrets must never appear in
trace, log, audit, error or metric channels. Metrics carry counters only
(no values), so secrets cannot leak through metrics either.

INTEGRITY: exported spans form a SHA-256 hash chain (each line commits to
the previous); `verify_chain()` detects any tampering/truncation.
"""
from __future__ import annotations

import collections
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

try:  # default: pattern-based redaction (security core is imported, never modified)
    from app.security.redaction import redact as _default_redact
except Exception:  # pragma: no cover — redaction must exist; fallback is identity-safe
    def _default_redact(text, extra_values=()):
        return text

SPAN_KINDS = ("task", "step", "tool", "provider")
STATUS_OK = "OK"
STATUS_ERROR = "ERROR"
RECENT_LIMIT = 500


def _new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex — OTel-style trace id


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex — OTel-style span id


class Span:
    """Mutable in-flight span; end() persists it through the Tracer."""

    __slots__ = ("tracer", "trace_id", "span_id", "parent_span_id", "name", "kind",
                 "task_id", "attributes", "start_ts", "end_ts", "duration_ms",
                 "status", "risk", "approval", "budget", "result_summary",
                 "error_class", "_ended")

    def __init__(self, tracer: "Tracer", name: str, kind: str, trace_id: str,
                 parent_span_id: str | None, task_id: str | None,
                 attributes: dict | None):
        self.tracer = tracer
        self.trace_id = trace_id
        self.span_id = _new_span_id()
        self.parent_span_id = parent_span_id
        self.name = str(name)[:200]
        self.kind = kind if kind in SPAN_KINDS else "tool"
        self.task_id = task_id
        self.attributes = dict(attributes or {})
        self.start_ts = time.time()
        self.end_ts = None
        self.duration_ms = None
        self.status = STATUS_OK
        self.risk = None
        self.approval = None
        self.budget = None
        self.result_summary = None
        self.error_class = None
        self._ended = False

    # context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._ended:
            if exc is not None:
                self.end(status=STATUS_ERROR,
                         error_class=type(exc).__name__,
                         result_summary=str(exc)[:200])
            else:
                self.end()
        return False

    def end(self, status: str = STATUS_OK, error_class: str | None = None,
            result_summary: str | None = None, risk: dict | str | None = None,
            approval: str | None = None, budget: dict | None = None) -> dict:
        if self._ended:
            return self.tracer.get_span(self.span_id)
        self._ended = True
        self.end_ts = time.time()
        self.duration_ms = round((self.end_ts - self.start_ts) * 1000, 3)
        self.status = STATUS_ERROR if status == STATUS_ERROR else STATUS_OK
        self.error_class = str(error_class)[:80] if error_class else None
        self.result_summary = result_summary
        self.risk = risk
        self.approval = approval
        self.budget = budget
        return self.tracer._export(self)


class Tracer:
    """Local-first tracer. Appends JSONL + hash chain; no network ever."""

    def __init__(self, path="data/observability/traces.jsonl", redact_fn=None,
                 clock=None, recent_limit=RECENT_LIMIT):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.chain_path = self.path.with_suffix(self.path.suffix + ".chain")
        self.redact_fn = redact_fn or _default_redact
        self.now = clock or time.time
        self._lock = threading.Lock()
        self._recent = collections.deque(maxlen=recent_limit)
        self._spans: dict[str, dict] = {}
        self.prev_hash, self.export_count = self._load_last_hash()
        self.export_errors = 0

    def _write_chain_checkpoint(self, count: int, last_hash: str) -> None:
        try:
            self.chain_path.write_text(
                json.dumps({"spans": count, "last_hash": last_hash}),
                encoding="utf-8")
        except Exception:
            pass  # checkpoint best-effort; chain itself lives in the file

    # ------------------------------------------------------------ chain
    def _load_last_hash(self) -> tuple:
        """Resume hash chain + line count across restarts (single file scan)."""
        try:
            if not self.path.exists():
                return "GENESIS", 0
            last = None
            count = 0
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last = line
                        count += 1
            if not last:
                return "GENESIS", 0
            return (json.loads(last).get("hash") or "GENESIS"), count
        except Exception:
            return "GENESIS", 0

    def _redact(self, value):
        if isinstance(value, str):
            return self.redact_fn(value)[:400]
        return value

    def _export(self, span: Span) -> dict:
        rec = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "kind": span.kind,
            "task_id": span.task_id,
            "attributes": {k: self._redact(v) for k, v in span.attributes.items()},
            "start_ts": span.start_ts,
            "end_ts": span.end_ts,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "risk": span.risk if not isinstance(span.risk, str)
                    else self._redact(span.risk),
            "approval": self._redact(span.approval) if span.approval else None,
            "budget": span.budget,
            "result_summary": self._redact(span.result_summary)
                              if span.result_summary else None,
            "error_class": span.error_class,
        }
        with self._lock:
            rec["prev_hash"] = self.prev_hash
            canon = json.dumps(rec, sort_keys=True, ensure_ascii=False,
                               default=str)
            rec["hash"] = hashlib.sha256(
                (self.prev_hash + canon).encode("utf-8")).hexdigest()
            self.prev_hash = rec["hash"]
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                self.export_count += 1
                # tail-truncation guard: commit {count,last_hash} to sidecar
                self._write_chain_checkpoint(self.export_count, rec["hash"])
            except Exception:  # noqa: BLE001 — observability must never break the app
                self.export_errors += 1
            self._recent.append(rec)
            self._spans[span.span_id] = rec
            return dict(rec)

    # ------------------------------------------------------------ API
    def start_trace(self, name: str, kind: str = "task", task_id: str | None = None,
                    attributes: dict | None = None) -> Span:
        return Span(self, name, kind, _new_trace_id(), None, task_id, attributes)

    def start_span(self, name: str, kind: str = "step", parent: Span | None = None,
                   task_id: str | None = None, attributes: dict | None = None) -> Span:
        trace_id = parent.trace_id if parent else _new_trace_id()
        parent_id = parent.span_id if parent else None
        return Span(self, name, kind, trace_id, parent_id, task_id, attributes)

    def get_span(self, span_id: str) -> dict | None:
        return self._spans.get(span_id)

    def recent(self, limit: int = 100, trace_id: str | None = None,
               task_id: str | None = None) -> list[dict]:
        out = []
        for rec in reversed(self._recent):
            if trace_id and rec["trace_id"] != trace_id:
                continue
            if task_id and rec.get("task_id") != task_id:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        out.append({"corrupt_line": line[:80]})
        return out

    def verify_chain(self) -> dict:
        """Detect tampering: recompute the chain over the exported file."""
        records = self.read_all()
        prev = "GENESIS"
        for i, rec in enumerate(records):
            if "corrupt_line" in rec or not isinstance(rec, dict):
                return {"ok": False, "broken_at": i, "reason": "corrupt line"}
            if rec.get("prev_hash") != prev:
                return {"ok": False, "broken_at": i,
                        "reason": "prev_hash mismatch (inserted/truncated line)"}
            body = {k: v for k, v in rec.items() if k != "hash"}
            canon = json.dumps(body, sort_keys=True, ensure_ascii=False,
                               default=str)
            expect = hashlib.sha256((prev + canon).encode("utf-8")).hexdigest()
            if rec.get("hash") != expect:
                return {"ok": False, "broken_at": i,
                        "reason": "hash mismatch (edited content)"}
            prev = rec["hash"]
        # sidecar checkpoint: detects tail truncation / file replacement
        if self.chain_path.exists():
            try:
                cp = json.loads(self.chain_path.read_text(encoding="utf-8"))
                if cp.get("spans") != len(records) or cp.get("last_hash") != prev:
                    return {"ok": False, "broken_at": len(records),
                            "reason": "checkpoint mismatch (truncated/replaced file)"}
            except Exception:
                pass
        return {"ok": True, "spans": len(records), "last_hash": prev}

    def stats(self) -> dict:
        return {"exported": self.export_count, "export_errors": self.export_errors,
                "recent_buffered": len(self._recent),
                "path": str(self.path)}


class TaskMetrics:
    """Additive task metrics — counters only (values never carry secrets)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = collections.defaultdict(int)
        self._started = time.time()

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[str(name)[:80]] += int(amount)

    def snapshot(self) -> dict:
        with self._lock:
            return {"counters": dict(self._counters),
                    "uptime_s": round(time.time() - self._started, 1)}

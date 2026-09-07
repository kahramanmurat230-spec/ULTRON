"""Model abstraction + capability registry + router with fallback.

Task types: GENERAL / CODING / VISION / FAST.

Routing rules (deliberately conservative — the router must NOT switch models
without reason):
  1. explicit settings override (llm.routing.<task>) — only if installed
  2. the configured primary model, when its name matches the task capability
  3. the first INSTALLED model whose capability matches the task
  4. otherwise the primary model (never None; the caller handles offline)

Adds: per-model health tracking, one retry with a fallback model,
structured-output helper (ask_json), context-budget fitting, and an explicit
local-only multi-model race that cannot use remote endpoints.
"""
import json
import time
from dataclasses import dataclass, field


class TaskType:
    GENERAL = "GENERAL"
    CODING = "CODING"
    VISION = "VISION"
    FAST = "FAST"


CAPABILITY_HINTS = {
    TaskType.CODING: ("coder", "code", "codellama", "starcoder", "deepseek", "devstral"),
    TaskType.VISION: ("llava", "vision", "bakllava", "minicpm-v", "moondream", "llama3.2-vision"),
    TaskType.FAST: ("1.5b", "1b", "2b", "3b", "4b", "tiny", "small", "mini", "phi", "gemma2:2", "qwen2.5:3"),
}


@dataclass
class ModelHealth:
    ok: bool = True
    calls: int = 0
    failures: int = 0
    last_error: str | None = None
    last_latency_ms: float | None = None
    last_used: float | None = field(default=None)


def model_matches(model: str, task: str) -> bool:
    m = (model or "").lower()
    hints = CAPABILITY_HINTS.get(task, ())
    return any(h in m for h in hints)


class ModelRouter:
    def __init__(self, brain, settings, get_models=None, sleep=None):
        self.brain = brain
        self.settings = settings or {}
        self.get_models = get_models or (lambda: [])
        self.backoff_s = float((settings or {}).get("llm", {}).get("retry_backoff_s", 0.5))
        self._sleep = sleep or time.sleep
        self._health: dict[str, ModelHealth] = {}
        self._local_multi_model = None

    def resolve(self, task: str, available_models=None) -> str:
        models = list(available_models) if available_models is not None else list(self.get_models() or [])
        primary = self.brain.model
        if not models:
            return primary
        override = (self.settings.get("llm", {}).get("routing", {}) or {}).get(task.lower())
        if override and override in models:
            return override
        if len(models) == 1:
            return models[0]
        if task == TaskType.GENERAL or model_matches(primary, task):
            return primary
        for m in models:
            if m != primary and model_matches(m, task):
                return m
        return primary

    def _attempts(self, task: str, models, max_retries: int = 1) -> list[str]:
        primary = self.resolve(task, models)
        general = self.resolve(TaskType.GENERAL, models)
        others = [m for m in models if m not in (primary, general)]
        fallback = general if general != primary else (others[0] if others else primary)
        attempts = [primary]
        if fallback != primary:
            attempts.append(fallback)
        return attempts[: max_retries + 1]

    def chat(self, task: str, messages, tools=None, max_retries: int = 1):
        models = list(self.get_models() or [])
        attempts = self._attempts(task, models, max_retries)
        last_exc = None
        for i, m in enumerate(attempts):
            if i:
                self._sleep(self.backoff_s)
            t0 = time.time()
            try:
                res = self.brain.chat(messages, tools=tools, model=m)
                self._record(m, True, (time.time() - t0) * 1000)
                return res
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._record(m, False, (time.time() - t0) * 1000, str(exc))
        raise last_exc

    def ask(self, task: str, prompt: str, system: str = "") -> str:
        models = list(self.get_models() or [])
        last_exc = None
        for i, m in enumerate(self._attempts(task, models)):
            if i:
                self._sleep(self.backoff_s)
            t0 = time.time()
            try:
                out = self.brain.ask(prompt, system=system, model=m)
                self._record(m, True, (time.time() - t0) * 1000)
                return out
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._record(m, False, (time.time() - t0) * 1000, str(exc))
        raise last_exc

    def ask_json(self, task: str, prompt: str, system: str = "") -> dict:
        content = self.ask(task, prompt, system=system)
        a, b = content.find("{"), content.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("JSON yanıt alınamadı.")
        return json.loads(content[a:b + 1])

    def race_local(self, models, messages, *, temperature: float = 0.2, max_tokens: int = 2048):
        """Race multiple Ollama/local models with zero cloud fallback."""
        from app.core.local_multi_model import LocalMultiModel

        model_list = list(dict.fromkeys(str(m).strip() for m in models if str(m).strip()))
        cfg = (self.settings.get("llm", {}).get("local_multi_model", {}) or {})
        base_url = cfg.get("base_url", "http://127.0.0.1:11434/v1")
        timeout_s = float(cfg.get("timeout_s", 120))
        max_workers = int(cfg.get("max_workers", min(3, max(1, len(model_list)))) )
        if self._local_multi_model is None or self._local_multi_model.base_url != base_url:
            self._local_multi_model = LocalMultiModel(
                base_url=base_url,
                timeout_s=timeout_s,
                max_workers=max_workers,
            )
        return self._local_multi_model.race(
            model_list,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _record(self, model: str, ok: bool, latency_ms: float, error: str | None = None):
        h = self._health.setdefault(model, ModelHealth())
        h.calls += 1
        h.ok = ok
        h.last_latency_ms = round(latency_ms, 1)
        h.last_used = time.time()
        if not ok:
            h.failures += 1
            h.last_error = (error or "")[:200]

    def health(self) -> dict:
        return {m: vars(h) for m, h in self._health.items()}


def fit_messages(messages, max_chars: int = 24000):
    """Fit messages into a character budget (~4 chars/token heuristic)."""
    if not messages:
        return []
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= max_chars:
        return list(messages)
    system = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system else messages
    budget = max_chars - (len(system.get("content") or "") if system else 0)
    kept: list = []
    for m in reversed(rest):
        cost = len(m.get("content") or "")
        if budget - cost < 0 and kept:
            break
        budget -= cost
        kept.append(m)
    kept.reverse()
    out = ([system] if system else []) + kept
    return out or ([system] if system else list(messages[-1:]))

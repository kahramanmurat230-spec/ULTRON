"""Model abstraction, capability routing, local multi-model evaluation and health."""
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
    return any(h in (model or "").lower() for h in CAPABILITY_HINTS.get(task, ()))


class ModelRouter:
    def __init__(self, brain, settings, get_models=None, sleep=None):
        self.brain = brain
        self.settings = settings or {}
        self.get_models = get_models or (lambda: [])
        self.backoff_s = float(self.settings.get("llm", {}).get("retry_backoff_s", 0.5))
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
        if len(models) == 1 or task == TaskType.GENERAL or model_matches(primary, task):
            return models[0] if len(models) == 1 else primary
        for model in models:
            if model != primary and model_matches(model, task):
                return model
        return primary

    def _attempts(self, task, models, max_retries=1):
        primary = self.resolve(task, models)
        general = self.resolve(TaskType.GENERAL, models)
        others = [m for m in models if m not in (primary, general)]
        fallback = general if general != primary else (others[0] if others else primary)
        return [primary] + ([fallback] if fallback != primary else [])[:max_retries]

    def _local_engine(self):
        from app.core.local_multi_model import LocalMultiModel
        cfg = self.settings.get("llm", {}).get("local_multi_model", {}) or {}
        base_url = cfg.get("base_url", "http://127.0.0.1:11434/v1")
        if self._local_multi_model is None or self._local_multi_model.base_url != base_url:
            self._local_multi_model = LocalMultiModel(
                base_url=base_url,
                timeout_s=float(cfg.get("timeout_s", 120)),
                max_workers=int(cfg.get("max_workers", 2)),
            )
        return self._local_multi_model, cfg

    def local_multi_enabled(self):
        return bool((self.settings.get("llm", {}).get("local_multi_model", {}) or {}).get("enabled", False))

    def _local_eval_options(self, cfg):
        scoring = self.settings.get("llm", {}).get("scoring", {}) or {}
        return {
            "liquid_min_delta": float(cfg.get("liquid_min_delta", scoring.get("liquid_min_delta", 8.0))),
            "min_quality_score": float(cfg.get("min_quality_score", scoring.get("min_quality_score", 0.0)))
                if cfg.get("min_quality_score") is not None else float(scoring.get("min_quality_score", 0.0)),
        }

    def chat(self, task, messages, tools=None, max_retries=1):
        models = list(self.get_models() or [])
        last_exc = None
        for i, model in enumerate(self._attempts(task, models, max_retries)):
            if i:
                self._sleep(self.backoff_s)
            started = time.time()
            try:
                result = self.brain.chat(messages, tools=tools, model=model)
                self._record(model, True, (time.time() - started) * 1000)
                msg = result.get("message", {}) if isinstance(result, dict) else {}
                # Tool-call turns stay deterministic. Final text turns are evaluated locally.
                if self.local_multi_enabled() and not msg.get("tool_calls"):
                    chosen, _ = self.race_local_and_judge(messages=messages, task=task)
                    if chosen and chosen.content:
                        return {"message": {"role": "assistant", "content": chosen.content}}
                return result
            except Exception as exc:
                last_exc = exc
                self._record(model, False, (time.time() - started) * 1000, str(exc))
        raise last_exc

    def ask(self, task, prompt, system=""):
        if self.local_multi_enabled():
            chosen, _ = self.race_local_and_judge(
                messages=([{"role": "system", "content": system}] if system else []) +
                         [{"role": "user", "content": prompt}],
                system=system,
                task=task,
            )
            if chosen and chosen.content:
                return chosen.content
        models = list(self.get_models() or [])
        last_exc = None
        for i, model in enumerate(self._attempts(task, models)):
            if i:
                self._sleep(self.backoff_s)
            started = time.time()
            try:
                out = self.brain.ask(prompt, system=system, model=model)
                self._record(model, True, (time.time() - started) * 1000)
                return out
            except Exception as exc:
                last_exc = exc
                self._record(model, False, (time.time() - started) * 1000, str(exc))
        raise last_exc

    def ask_json(self, task, prompt, system=""):
        content = self.ask(task, prompt, system=system)
        a, b = content.find("{"), content.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("JSON yanıt alınamadı.")
        return json.loads(content[a:b + 1])

    def race_local(self, models=None, messages=None, *, temperature=None, max_tokens=None, task=""):
        engine, cfg = self._local_engine()
        models = models or cfg.get("models") or list(self.get_models() or [])
        if not models:
            return []
        return engine.score_results(engine.race(
            models, messages or [],
            temperature=float(cfg.get("temperature", .2) if temperature is None else temperature),
            max_tokens=int(cfg.get("max_tokens", 2048) if max_tokens is None else max_tokens)), task)

    def race_local_and_judge(self, models=None, messages=None, *, system="", temperature=None, max_tokens=None, task=""):
        engine, cfg = self._local_engine()
        models = models or cfg.get("models") or list(self.get_models() or [])
        if not models:
            return None, []
        options = self._local_eval_options(cfg)
        return engine.race_and_judge(
            models, messages or [], judge_model=cfg.get("judge_model"), system=system, task=task,
            temperature=float(cfg.get("temperature", .2) if temperature is None else temperature),
            max_tokens=int(cfg.get("max_tokens", 2048) if max_tokens is None else max_tokens),
            liquid_min_delta=options["liquid_min_delta"],
            min_quality_score=options["min_quality_score"],
        )

    def _record(self, model, ok, latency_ms, error=None):
        health = self._health.setdefault(model, ModelHealth())
        health.calls += 1; health.ok = ok; health.last_latency_ms = round(latency_ms, 1); health.last_used = time.time()
        if not ok:
            health.failures += 1; health.last_error = (error or "")[:200]

    def health(self):
        return {model: vars(h) for model, h in self._health.items()}


def fit_messages(messages, max_chars=24000):
    if not messages:
        return []
    if sum(len(m.get("content") or "") for m in messages) <= max_chars:
        return list(messages)
    system = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system else messages
    budget = max_chars - (len(system.get("content") or "") if system else 0)
    kept = []
    for message in reversed(rest):
        cost = len(message.get("content") or "")
        if budget - cost < 0 and kept:
            break
        budget -= cost; kept.append(message)
    kept.reverse()
    return ([system] if system else []) + kept

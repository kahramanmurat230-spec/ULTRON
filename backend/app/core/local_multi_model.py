"""Local-only multi-model evaluation engine for ULTRON.

G0DM0D3-inspired, but deliberately sovereign: every inference request is sent
only to a localhost OpenAI-compatible endpoint (normally Ollama). The engine
supports parallel candidates, deterministic composite scoring, local judging,
quality gates, and Liquid-style early/final leader selection.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


@dataclass
class LocalModelResult:
    model: str
    ok: bool
    content: str = ""
    latency_ms: float = 0.0
    error: str | None = None
    score: float = 0.0
    dimensions: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalMultiModel:
    """Parallel local inference + training-free comparative evaluation."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1",
                 timeout_s: float = 120.0, max_workers: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_workers = max(1, int(max_workers))
        self._validate_local_url()

    def _validate_local_url(self) -> None:
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("LocalMultiModel yalnız localhost/127.0.0.1/::1 endpoint'lerine izin verir.")

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(self.base_url + path, data=data, method=method,
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Local model server request failed: {exc}") from exc

    def ask_one(self, model: str, messages: list[dict[str, Any]], *,
                temperature: float = 0.2, max_tokens: int = 2048) -> LocalModelResult:
        started = time.perf_counter()
        try:
            payload = self._request("POST", "/chat/completions", {
                "model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
            })
            choices = payload.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
            if not content.strip():
                raise RuntimeError("Model boş yanıt döndürdü.")
            return LocalModelResult(model=model, ok=True, content=content.strip(),
                                    latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            return LocalModelResult(model=model, ok=False,
                                    latency_ms=(time.perf_counter() - started) * 1000,
                                    error=str(exc)[:500])

    def race(self, models: Iterable[str], messages: list[dict[str, Any]], *,
             temperature: float = 0.2, max_tokens: int = 2048) -> list[LocalModelResult]:
        unique = list(dict.fromkeys(str(m).strip() for m in models if str(m).strip()))
        if not unique:
            return []
        workers = min(self.max_workers, len(unique))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self.ask_one, model, messages,
                                   temperature=temperature, max_tokens=max_tokens) for model in unique]
            return [f.result() for f in futures]

    @staticmethod
    def _score_candidate(content: str, task: str = "") -> tuple[float, dict[str, float]]:
        """Training-free 100-point heuristic used before optional local judge."""
        text = (content or "").strip()
        lower = text.lower()
        if not text:
            return 0.0, {"substance": 0.0, "directness": 0.0, "completeness": 0.0,
                          "structure": 0.0, "actionability": 0.0}
        words = re.findall(r"\b\w+\b", text, re.UNICODE)
        sentences = max(1, len(re.findall(r"[.!?]\s|\n", text)))
        substance = min(100.0, 35.0 + min(45.0, len(words) * 0.22) + min(20.0, len(set(w.lower() for w in words)) * 0.08))
        directness = 88.0
        hedges = ("belki", "sanırım", "muhtemelen", "galiba", "emin değilim", "it depends", "maybe")
        directness -= min(35.0, sum(lower.count(h) for h in hedges) * 7.0)
        completeness = min(100.0, 45.0 + min(55.0, sentences * 7.0))
        structure = 50.0 + (18.0 if "\n" in text else 0.0) + (15.0 if re.search(r"(^|\n)\s*[-*0-9]", text) else 0.0) + (17.0 if re.search(r"```|##|###", text) else 0.0)
        actionability = 55.0 + (30.0 if any(x in lower for x in ("adım", "yap", "çalıştır", "dosya", "kod", "komut", "örnek")) else 0.0)
        dims = {"substance": round(substance, 2), "directness": round(directness, 2),
                "completeness": round(completeness, 2), "structure": round(min(100, structure), 2),
                "actionability": round(min(100, actionability), 2)}
        score = (substance * 0.30 + directness * 0.15 + completeness * 0.25 +
                 dims["structure"] * 0.10 + dims["actionability"] * 0.20)
        return round(score, 2), dims

    def score_results(self, results: Iterable[LocalModelResult], task: str = "") -> list[LocalModelResult]:
        out = []
        for result in results:
            if result.ok:
                result.score, result.dimensions = self._score_candidate(result.content, task)
            out.append(result)
        return sorted(out, key=lambda r: (r.ok, r.score, -r.latency_ms), reverse=True)

    def judge(self, results: Iterable[LocalModelResult], *, judge_model: str,
              system: str = "", temperature: float = 0.1, max_tokens: int = 2048) -> LocalModelResult | None:
        successful = [r for r in results if r.ok and r.content]
        if not successful:
            return None
        if len(successful) == 1:
            return successful[0]
        candidates = [f"CANDIDATE {i} [{r.model}] SCORE={r.score}\n{r.content[:6000]}"
                      for i, r in enumerate(successful, 1)]
        judge_messages = [
            {"role": "system", "content": system or
             "Sen ULTRON'un yerel hakemisin. Adayları doğruluk, göreve uygunluk, eksiksizlik, açıklık ve uygulanabilirlik açısından değerlendir. En iyi cevabı doğrudan üret. Meta açıklama yapma."},
            {"role": "user", "content": "\n\n".join(candidates)},
        ]
        started = time.perf_counter()
        try:
            payload = self._request("POST", "/chat/completions", {
                "model": judge_model, "messages": judge_messages,
                "temperature": temperature, "max_tokens": max_tokens,
            })
            choices = payload.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
            if not content:
                return max(successful, key=lambda r: r.score)
            return LocalModelResult(model=f"judge:{judge_model}", ok=True, content=content,
                                    latency_ms=(time.perf_counter() - started) * 1000)
        except Exception:
            return max(successful, key=lambda r: r.score)

    def race_and_judge(self, models: Iterable[str], messages: list[dict[str, Any]], *,
                       judge_model: str | None = None, system: str = "", task: str = "",
                       temperature: float = 0.2, max_tokens: int = 2048,
                       liquid_min_delta: float = 8.0) -> tuple[LocalModelResult | None, list[LocalModelResult]]:
        results = self.score_results(self.race(models, messages, temperature=temperature, max_tokens=max_tokens), task)
        successful = [r for r in results if r.ok and r.content]
        if not successful:
            return None, results
        # A strong enough heuristic leader is immediately usable; a local judge
        # still performs the final comparative pass when configured.
        leader = successful[0]
        if judge_model and len(successful) > 1:
            chosen = self.judge(successful, judge_model=judge_model, system=system,
                                temperature=min(temperature, 0.15), max_tokens=max_tokens)
            if chosen:
                return chosen, results
        return leader, results

    @staticmethod
    def best_fastest(results: Iterable[LocalModelResult]) -> LocalModelResult | None:
        successful = [r for r in results if r.ok and r.content]
        return min(successful, key=lambda r: r.latency_ms, default=None)

    @staticmethod
    def export_results(results: Iterable[LocalModelResult]) -> list[dict[str, Any]]:
        return [r.to_dict() for r in results]

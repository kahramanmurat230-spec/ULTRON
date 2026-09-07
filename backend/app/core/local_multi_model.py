"""Local-only multi-model racing for ULTRON.

This module adds G0DM0D3-style multi-model comparison without cloud providers.
It talks only to OpenAI-compatible endpoints bound to localhost (Ollama by
 default at http://127.0.0.1:11434/v1). No API keys, billing, telemetry, or
remote fallback are used here.
"""
from __future__ import annotations

import concurrent.futures
import json
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalMultiModel:
    """Call several local OpenAI-compatible models in parallel.

    The transport intentionally rejects non-local URLs so this component
    cannot silently turn into a paid cloud dependency.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        timeout_s: float = 120.0,
        max_workers: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_workers = max(1, int(max_workers))
        self._validate_local_url()

    def _validate_local_url(self) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in LOCAL_HOSTS:
            raise ValueError(
                "LocalMultiModel yalnız localhost/127.0.0.1/::1 endpoint'lerine izin verir."
            )

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Local model server request failed: {exc}") from exc

    def ask_one(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalModelResult:
        started = time.perf_counter()
        try:
            payload = self._request(
                "POST",
                "/chat/completions",
                {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            choices = payload.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
            if not content:
                raise RuntimeError("Model boş yanıt döndürdü.")
            return LocalModelResult(
                model=model,
                ok=True,
                content=content,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return LocalModelResult(
                model=model,
                ok=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc)[:500],
            )

    def race(
        self,
        models: Iterable[str],
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> list[LocalModelResult]:
        unique_models = list(dict.fromkeys(str(m).strip() for m in models if str(m).strip()))
        if not unique_models:
            return []
        workers = min(self.max_workers, len(unique_models))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    self.ask_one,
                    model,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                for model in unique_models
            ]
            return [future.result() for future in futures]

    @staticmethod
    def best_fastest(results: Iterable[LocalModelResult]) -> LocalModelResult | None:
        successful = [r for r in results if r.ok and r.content]
        return min(successful, key=lambda r: r.latency_ms, default=None)

    @staticmethod
    def export_results(results: Iterable[LocalModelResult]) -> list[dict[str, Any]]:
        return [r.to_dict() for r in results]

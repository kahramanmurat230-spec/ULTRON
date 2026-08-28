import json
import urllib.request
import urllib.error

class Brain:
    """Local Ollama chat adapter with native tool-calling support."""
    def __init__(self, settings):
        cfg = settings["llm"]
        self.provider = cfg.get("provider", "ollama")
        self.model = cfg.get("model", "qwen2.5-coder:7b")
        self.base_url = cfg.get("base_url", "http://127.0.0.1:11434")
        self.timeout = int(cfg.get("timeout_seconds", 180))
        # diagnostics (V17.1 debug): last wire-level facts, read by /api/debug/*
        self.last_http_status = None
        self.last_response_model = None
        self.last_error = None

    def _post(self, endpoint, payload):
        from app.security import sovereign_privacy as sov
        sov.assert_local(self.base_url)  # sovereign mode: cloud LLM = architectural block
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url.rstrip("/") + endpoint,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self.last_http_status = getattr(r, "status", None)
                parsed = json.loads(r.read().decode("utf-8"))
            self.last_response_model = parsed.get("model") if isinstance(parsed, dict) else None
            self.last_error = None
            return parsed
        except urllib.error.URLError as e:
            self.last_http_status = None
            self.last_error = str(e)
            raise RuntimeError(
                "Ollama API'ye bağlanılamadı. Ollama'nın çalıştığından ve modelin kurulu olduğundan emin ol."
            ) from e

    def ask(self, prompt: str, system: str = "", history=None) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False
        })
        return data.get("message", {}).get("content", "").strip()

    def chat(self, messages, tools=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        return self._post("/api/chat", payload)

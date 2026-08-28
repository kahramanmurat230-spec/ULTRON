import base64
import json
import os
from pathlib import Path

DEBUG = os.environ.get("ULTRON_VISION_DEBUG", "1") != "0"

def _log(msg: str) -> None:
    if DEBUG:
        print(f"[ULTRON-VISION] {msg}", flush=True)

class VisionLLM:
    """Send a screenshot to a configured Ollama vision model."""
    def __init__(self, brain, settings):
        self.brain=brain
        self.model=settings.get('vision',{}).get('model','llama3.2-vision:11b')
        self.enabled=settings.get('vision',{}).get('enabled',True)

    def resolve_model(self, available_models=None):
        """Prefer the configured model; else the first installed llava/vision model.
        Returns None when no vision model is installed (caller must ERROR, never fake)."""
        if available_models is not None:
            if self.model in available_models:
                return self.model
            cand = next((m for m in available_models if any(k in m.lower() for k in ("llava", "vision"))), None)
            if cand:
                return cand
            return None
        return self.model

    def analyze(self, path, prompt='Ekranda ne görüyorsun? Önemli arayüz öğelerini ve metinleri Türkçe açıkla.',
                available_models=None):
        if not self.enabled: return 'Vision LLM devre dışı.'
        model = self.resolve_model(available_models)
        if model is None:
            raise RuntimeError('Vision modeli kurulu değil (beklenen: llava/vision ailesi).')
        p=Path(path)
        if not p.exists(): raise FileNotFoundError(path)
        if p.stat().st_size == 0: raise ValueError(f'PNG boş: {path}')
        raw=p.read_bytes()
        img=base64.b64encode(raw).decode('ascii')
        payload={
            'model': model,
            'messages':[{'role':'user','content':prompt,'images':[img]}],
            'stream':False
        }
        _log(f"endpoint={self.brain.base_url.rstrip('/')}/api/chat model={model} "
             f"image_attached=True bytes={len(raw)} b64_len={len(img)} path={p}")
        data=self.brain._post('/api/chat',payload)
        resp_model = data.get('model', '?')
        content = (data.get('message',{}).get('content') or '').strip()
        _log(f"response_model={resp_model} response_head={content[:120]!r}")
        if resp_model not in ('?', model) and model not in str(resp_model):
            _log(f"WARNING: request model {model} != response model {resp_model}")
        return content

"""ULTRON local TTS.

The TTS path is strictly offline: no cloud synthesis and no network fallback.
Primary engine is a locally installed Piper model. If Piper is unavailable,
local espeak-ng may be used when explicitly available. Missing engines are
reported as unavailable rather than faking successful audio.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class TextToSpeech:
    def __init__(self, settings=None):
        s = (settings or {}).get("voice", {})
        self.voice = os.getenv("ULTRON_TTS_VOICE", s.get("piper_voice", "tr"))
        self.rate = os.getenv("ULTRON_TTS_RATE", str(s.get("rate", "1.0")))
        self.pitch = os.getenv("ULTRON_TTS_PITCH", str(s.get("pitch", "0")))
        self.volume = os.getenv("ULTRON_TTS_VOLUME", str(s.get("volume", "1.0")))
        self.engine = os.getenv("ULTRON_TTS_ENGINE", str(s.get("tts", "piper-local"))).lower()
        self.piper_executable = os.getenv(
            "ULTRON_PIPER_EXECUTABLE", str(s.get("piper_executable", "piper"))
        )
        backend_root = Path(__file__).resolve().parents[2]
        default_model = backend_root / "data" / "voice" / "piper" / "tr_TR-fahrettin-medium.onnx"
        configured_model = os.getenv("ULTRON_PIPER_MODEL", str(s.get("piper_model", default_model)))
        self.piper_model = Path(configured_model).expanduser()
        if not self.piper_model.is_absolute():
            self.piper_model = backend_root / self.piper_model
        self.piper_model = self.piper_model.resolve()
        self.espeak_executable = os.getenv(
            "ULTRON_ESPEAK_EXECUTABLE", str(s.get("espeak_executable", "espeak-ng"))
        )
        self.espeak_voice = os.getenv("ULTRON_ESPEAK_VOICE", str(s.get("espeak_voice", "tr")))
        self._cache: dict[tuple[str, str, str, str, str], tuple[bytes, str]] = {}
        self._cache_limit = 32

    def _piper_ready(self) -> bool:
        return bool(shutil.which(self.piper_executable) and self.piper_model.is_file())

    def _espeak_ready(self) -> bool:
        return bool(shutil.which(self.espeak_executable))

    def backend(self):
        """Return only a real local engine; never report a cloud provider."""
        if self.engine in ("piper", "piper-local") and self._piper_ready():
            return "piper-local"
        if self.engine in ("piper", "piper-local", "espeak", "espeak-local") and self._espeak_ready():
            return "espeak-ng-local"
        return None

    def _piper_synthesize(self, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output = Path(tmp.name)
        try:
            cmd = [
                self.piper_executable,
                "--model", str(self.piper_model),
                "--output_file", str(output),
            ]
            # Piper's length_scale is the inverse of perceived speaking rate.
            try:
                rate = float(self.rate)
                if rate > 0:
                    cmd.extend(["--length_scale", str(max(0.5, min(2.0, 1.0 / rate)))])
            except (TypeError, ValueError):
                pass
            proc = subprocess.run(
                cmd, input=text, text=True, capture_output=True, timeout=30, check=False
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "piper failed").strip()[:300]
                raise RuntimeError(f"Piper synthesis failed: {detail}")
            audio = output.read_bytes() if output.is_file() else b""
            if not audio:
                raise RuntimeError("Piper returned no audio")
            return audio
        finally:
            output.unlink(missing_ok=True)

    def _espeak_synthesize(self, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output = Path(tmp.name)
        try:
            cmd = [self.espeak_executable, "-v", self.espeak_voice, "-w", str(output), text]
            proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "espeak-ng failed").decode(errors="replace").strip()[:300]
                raise RuntimeError(f"eSpeak synthesis failed: {detail}")
            audio = output.read_bytes() if output.is_file() else b""
            if not audio:
                raise RuntimeError("eSpeak-ng returned no audio")
            return audio
        finally:
            output.unlink(missing_ok=True)

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        text = " ".join((text or "").strip().split())[:600]
        if not text:
            raise ValueError("empty text")
        key = (text, self.engine, self.voice, self.rate, self.volume)
        cached = self._cache.get(key)
        if cached:
            return cached

        engine = self.backend()
        if engine is None:
            raise RuntimeError(
                "Local TTS unavailable: install Piper and place a compatible local model "
                "at ULTRON_PIPER_MODEL, or install espeak-ng. No network fallback is used."
            )
        if engine == "piper-local":
            audio = self._piper_synthesize(text)
        else:
            audio = self._espeak_synthesize(text)

        result = (audio, "wav")
        self._cache[key] = result
        if len(self._cache) > self._cache_limit:
            self._cache.pop(next(iter(self._cache)))
        return result

    async def warmup(self) -> bool:
        try:
            await self.synthesize("ULTRON hazır.")
            return True
        except Exception:
            return False

    def speak(self, text: str) -> str:
        """Synthesize locally and play locally when a playback utility exists."""
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("use synthesize() in async context")

        audio, _fmt = asyncio.run(self.synthesize(text))
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = Path(tmp.name)
            path.write_bytes(audio)
        try:
            try:
                from playsound3 import playsound
            except Exception:
                try:
                    from playsound import playsound
                except Exception:
                    playsound = None
            if playsound:
                playsound(str(path), block=True)
        finally:
            path.unlink(missing_ok=True)
        return self.backend() or "none"

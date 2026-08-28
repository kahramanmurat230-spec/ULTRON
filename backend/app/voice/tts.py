"""ULTRON Neural TTS — Microsoft Edge Neural voice, optimized for Turkish.

Primary engine: edge-tts / tr-TR-AhmetNeural.
No SAPI5 and no eSpeak fallback: if neural TTS is unavailable, the API reports
a clean error instead of silently producing a robotic voice.
"""
import io
import os
import asyncio
import wave
from functools import lru_cache


class TextToSpeech:
    def __init__(self, settings=None):
        s = (settings or {}).get("voice", {})
        self.voice = os.getenv("ULTRON_TTS_VOICE", s.get("neural_voice", "tr-TR-AhmetNeural"))
        self.rate = os.getenv("ULTRON_TTS_RATE", s.get("rate", "-6%"))
        self.pitch = os.getenv("ULTRON_TTS_PITCH", s.get("pitch", "-8Hz"))
        self.volume = s.get("volume", "+0%")
        self._cache = {}
        self._cache_limit = 32

    def backend(self):
        try:
            import edge_tts  # noqa: F401
            return "edge-tts-neural"
        except Exception:
            return None

    async def synthesize(self, text: str) -> tuple:
        text = " ".join((text or "").strip().split())[:600]
        if not text:
            raise ValueError("empty text")
        key = (text, self.voice, self.rate, self.pitch, self.volume)
        cached = self._cache.get(key)
        if cached:
            return cached
        try:
            import edge_tts
        except Exception as e:
            raise RuntimeError("Microsoft Neural TTS is not installed. Run the ULTRON installer.") from e

        try:
            chunks = []
            comm = edge_tts.Communicate(
                text, self.voice, rate=self.rate, pitch=self.pitch, volume=self.volume
            )
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    data = chunk.get("data", b"")
                    if data:
                        chunks.append(data)
            audio = b"".join(chunks)
            if not audio:
                raise RuntimeError("Neural TTS returned no audio")
        except Exception as e:
            raise RuntimeError(f"Neural TTS synthesis failed: {e}") from e

        result = (audio, "mp3")
        self._cache[key] = result
        if len(self._cache) > self._cache_limit:
            self._cache.pop(next(iter(self._cache)))
        return result

    async def warmup(self) -> bool:
        """Verify the neural engine by synthesizing a short phrase."""
        try:
            await self.synthesize("ULTRON hazır.")
            return True
        except Exception:
            return False

    def speak(self, text: str) -> str:
        """Server-side best effort playback using the same neural MP3 stream."""
        try:
            asyncio.get_running_loop()
            raise RuntimeError("use synthesize() in async context")
        except RuntimeError as e:
            if str(e) == "use synthesize() in async context":
                raise
        audio, _fmt = asyncio.run(self.synthesize(text))
        try:
            import tempfile
            from pathlib import Path
            path = Path(tempfile.gettempdir()) / "ultron_tts.mp3"
            path.write_bytes(audio)
            from playsound import playsound
            playsound(str(path), block=True)
        except Exception:
            # The browser is the normal playback target; synthesis itself succeeded.
            pass
        return self.backend() or "none"

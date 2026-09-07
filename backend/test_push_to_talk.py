"""ULTRON local end-to-end voice test.

Records from the default microphone, transcribes with local Whisper CUDA,
sends text through the existing UltronRuntime/Agent and speaks the answer
with the existing local Piper TTS. No cloud API is used.
"""
from __future__ import annotations

from app.core.runtime import UltronRuntime
from app.voice.push_to_talk import PushToTalkVoice


def main() -> None:
    runtime = UltronRuntime("config/settings.json")
    voice = PushToTalkVoice(runtime.agent, runtime.tts, runtime.settings)
    print("ULTRON LOCAL VOICE TEST")
    print("STT:", voice.status()["whisper"])
    print("TTS:", voice.status()["tts"])
    print("VAD:", voice.status()["vad"])
    print("6 saniye kayıt başlıyor. Konuşmaya başlayın...")
    result = voice.listen_once(6.0)
    print("RESULT:", result)


if __name__ == "__main__":
    main()

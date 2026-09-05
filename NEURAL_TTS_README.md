# ULTRON Local Voice

ULTRON TTS is fully local and does not use cloud synthesis or network fallbacks.

## Engine order

1. **Piper local** — primary TTS engine, using a locally installed compatible model.
2. **eSpeak-ng local** — optional local fallback when Piper is unavailable.
3. **Unavailable** — if neither local engine is available; ULTRON reports the failure honestly.

## Output

- Local synthesis returns WAV audio.
- Streaming TTS preserves the real audio format, including cached responses.
- Text is normalized and bounded before synthesis.
- In-memory cache is bounded to prevent unbounded growth.
- Barge-in/cancellation stops further streaming work.

## Privacy

No Microsoft Edge Neural TTS or other cloud TTS provider is required. Network access is not used as a TTS fallback.

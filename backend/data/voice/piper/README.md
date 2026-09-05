# ULTRON Local Piper Voice

Stage 5 uses an offline Piper model. No cloud TTS provider is used.

Place the licensed Piper ONNX model at:

`backend/data/voice/piper/tr_TR-ahmet-medium.onnx`

If the model has a different name or location, set `ULTRON_PIPER_MODEL`.

The `piper` executable must be installed locally and available on `PATH`, or set
`ULTRON_PIPER_EXECUTABLE` to its local path.

If Piper is unavailable, ULTRON may use a locally installed `espeak-ng` engine.
There is intentionally no network fallback.

Only use voice/model files whose licenses permit your intended use.

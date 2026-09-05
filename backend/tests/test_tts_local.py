import asyncio

from app.voice.tts import TextToSpeech


def test_backend_never_reports_cloud_engine(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    tts = TextToSpeech({"voice": {"tts": "piper-local", "piper_model": str(tmp_path / "missing.onnx")}})
    assert tts.backend() is None


def test_piper_is_selected_when_local_binary_and_model_exist(monkeypatch, tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/piper" if name == "piper" else None)
    tts = TextToSpeech({"voice": {"tts": "piper-local", "piper_model": str(model)}})
    assert tts.backend() == "piper-local"


def test_missing_local_tts_is_honest(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    tts = TextToSpeech({"voice": {"tts": "piper-local", "piper_model": str(tmp_path / "missing.onnx")}})
    try:
        asyncio.run(tts.synthesize("Merhaba"))
    except RuntimeError as exc:
        assert "No network fallback" in str(exc)
    else:
        raise AssertionError("missing local TTS must not report success")


def test_piper_synthesis_uses_local_subprocess(monkeypatch, tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/piper" if name == "piper" else None)

    def fake_run(cmd, **kwargs):
        output = tmp_path / "out.wav"
        output.write_bytes(b"RIFF-local-test")
        class Result:
            returncode = 0
            stdout = b""
            stderr = b""
        assert "--model" in cmd
        assert str(model) in cmd
        assert kwargs["input"] == "Merhaba"
        # The implementation uses a temporary output path, so locate it from args.
        output_arg = cmd[cmd.index("--output_file") + 1]
        with open(output_arg, "wb") as f:
            f.write(output.read_bytes())
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    audio, fmt = asyncio.run(TextToSpeech({"voice": {"tts": "piper-local", "piper_model": str(model)}}).synthesize("Merhaba"))
    assert audio == b"RIFF-local-test"
    assert fmt == "wav"

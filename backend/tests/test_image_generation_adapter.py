from app.multimodal.image_generation import ImageGenerationAdapter


def test_image_generation_never_claims_unconfigured_provider_is_available(monkeypatch):
    for key in ("ULTRON_IMAGE_ENDPOINT", "ULTRON_IMAGE_API_KEY", "ULTRON_IMAGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    adapter = ImageGenerationAdapter()
    status = adapter.status()
    assert status["available"] is False
    assert adapter.generate("test")["ok"] is False

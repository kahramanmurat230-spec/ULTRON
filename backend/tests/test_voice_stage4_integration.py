from app.voice.voice_stack_v2 import LiveVoiceV2, VoiceState


def test_voice_state_enum_is_complete():
    assert [s.value for s in VoiceState] == [
        "DISARMED", "ARMED", "LISTENING", "PROCESSING", "SPEAKING", "ERROR"
    ]


def test_voice_status_reports_unavailable_wake_without_fake_success():
    voice = LiveVoiceV2(object(), object(), {"voice": {}}, object())
    voice.wake_manager = None
    assert voice.available is False
    assert voice.arm() is False
    assert voice.state is VoiceState.DISARMED

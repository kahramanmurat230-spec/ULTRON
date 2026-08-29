"""PHASE 7: computer use — OCR matcher + honest headless behavior."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.automation.gui import GUIAutomation  # noqa: E402


def test_find_text_exact_beats_substring():
    els = [
        {"text": "Kaydet ve kapat", "conf": 90.0, "box": {"x": 0, "y": 0, "w": 10, "h": 10}},
        {"text": "Kaydet", "conf": 60.0, "box": {"x": 100, "y": 50, "w": 20, "h": 8}},
    ]
    hit = GUIAutomation.find_text_in_elements(els, "Kaydet")
    assert hit["text"] == "Kaydet"  # exact match wins despite lower conf


def test_find_text_case_and_substring():
    els = [{"text": "Dosya Aç...", "conf": 88.0, "box": {"x": 5, "y": 5, "w": 40, "h": 9}}]
    assert GUIAutomation.find_text_in_elements(els, "dosya aç") is not None
    assert GUIAutomation.find_text_in_elements(els, "yok boyle") is None
    assert GUIAutomation.find_text_in_elements([], "herhangi") is None
    assert GUIAutomation.find_text_in_elements(None, "x") is None


def test_conf_tiebreak():
    els = [
        {"text": "Sil", "conf": 50.0, "box": {}},
        {"text": "Sil", "conf": 91.0, "box": {}},
    ]
    hit = GUIAutomation.find_text_in_elements(els, "Sil")
    assert hit["conf"] == 91.0


def test_read_screen_elements_honest_without_display():
    gui = GUIAutomation()
    # headless sandbox: capture_screen veya tesseract yok → dürüst hata beklenir
    try:
        gui.read_screen_elements()
    except Exception as exc:  # noqa: BLE001
        assert any(k in str(exc).lower() for k in
                   ("pyautogui", "pytesseract", "tesseract", "pillow",
                    "imagegrab", "screen", "display", "x connection",
                    "xconnection", "no protocol", "xcb"))
        return
    pytest.skip("bu ortamda ekran erişimi var (beklenmedik)")


def test_click_text_honest_without_display():
    gui = GUIAutomation()
    with pytest.raises(Exception):
        gui.click_text("Kaydet")  # pyautogui yok → RuntimeError, sahte success yok

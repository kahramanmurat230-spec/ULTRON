import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.browser_workflow import parse_browser_plan, as_hybrid_plan


def test_browser_search_and_read_workflow():
    steps = parse_browser_plan("Google'da ULTRON yapay zeka ara, sonra sayfayı oku")
    assert [s["tool"] for s in steps] == ["browser_navigate", "browser_read"]
    plan = as_hybrid_plan(steps, "arama ve sonucu oku")
    assert plan["bounded"] is True
    assert plan["steps"][1]["depends_on"] == [0]


def test_browser_explicit_selector_actions():
    steps = parse_browser_plan("https://example.com aç, sonra css #q alanına 'ultron' yaz, sonra css #go tıkla")
    assert [s["tool"] for s in steps] == ["browser_navigate", "browser_type", "browser_click"]
    assert steps[1]["args"]["selector"] == "#q"
    assert steps[2]["args"]["selector"] == "#go"


def test_browser_verify_is_safe():
    steps = parse_browser_plan("https://example.com aç, sonra sayfada 'Merhaba' metnini doğrula")
    assert steps[1]["tool"] == "browser_verify"


def test_browser_incomplete_request_not_guessed():
    assert parse_browser_plan("bir siteye gir ve bir yere tıkla") == []

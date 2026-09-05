from app.agent.browser_workflow import parse_browser_plan


def test_natural_language_google_search_extracts_query_only():
    text = "Google'da 'ULTRON yapay zeka' kelimesini ara. Sayfanın başlığını ve ilk 3 sonucu oku. Hiçbir siteye giriş yapma ve hiçbir veri gönderme."
    steps = parse_browser_plan(text)
    assert [step["tool"] for step in steps] == ["browser_navigate", "browser_read"]
    assert "ULTRON%20yapay%20zeka" in steps[0]["args"]["url"] or "ULTRON+yapay+zeka" in steps[0]["args"]["url"]
    assert "kelimesini" not in steps[0]["args"]["url"]
    assert "Sayfan" not in steps[0]["args"]["url"]


def test_unquoted_search_still_stops_at_search_verb():
    steps = parse_browser_plan("Google'da ULTRON yapay zeka ara. Sonuçları oku.")
    assert [step["tool"] for step in steps] == ["browser_navigate", "browser_read"]
    assert "ULTRON+yapay+zeka" in steps[0]["args"]["url"] or "ULTRON%20yapay%20zeka" in steps[0]["args"]["url"]

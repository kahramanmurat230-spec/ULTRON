from pathlib import Path

from server import _desktop_frontend_asset


def test_desktop_frontend_asset_serves_spa_and_blocks_escape(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    index = dist / "index.html"
    script = assets / "app.js"
    index.write_text("<div id='root'></div>", encoding="utf-8")
    script.write_text("console.log('ultron')", encoding="utf-8")
    monkeypatch.setenv("ULTRON_FRONTEND_DIST", str(dist))

    assert _desktop_frontend_asset("") == index
    assert _desktop_frontend_asset("cockpit") == index
    assert _desktop_frontend_asset("assets/app.js") == script
    assert _desktop_frontend_asset("../../outside.txt") is None
    assert _desktop_frontend_asset("api/system") is None


def test_desktop_frontend_is_inactive_without_packaged_dist(monkeypatch):
    monkeypatch.delenv("ULTRON_FRONTEND_DIST", raising=False)
    assert _desktop_frontend_asset("cockpit") is None

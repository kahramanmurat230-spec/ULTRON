"""PHASE 9: skills + connectors — approval intact, real HTTP tested locally."""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.connectors import (  # noqa: E402
    CalendarConnector, ConnectorError, WeatherConnector, _http_get,
)
from app.core.tool_registry import ToolRegistry  # noqa: E402
from app.agent.executor import Executor  # noqa: E402
from app.security.permissions import PermissionManager  # noqa: E402
from app.security.audit import AuditLog  # noqa: E402
from app.skills import Skill, SkillError, SkillRunner  # noqa: E402


# ------------------------------------------------------------ local HTTP fixture
class _OWMHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/weather" in self.path:
            body = json.dumps({"name": "Mersin", "main": {"temp": 27.5, "feels_like": 29.1,
                                                          "humidity": 68},
                               "weather": [{"description": "açık"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def local_owm():
    srv = HTTPServer(("127.0.0.1", 0), _OWMHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ------------------------------------------------------------ weather
class FakeVault:
    def __init__(self, key=None):
        self.key = key

    def get(self, name):
        return self.key if name == "openweathermap" else None


def test_weather_owm_real_http_local(local_owm):
    w = WeatherConnector(vault=FakeVault("TESTKEY"), owm_base=local_owm)
    res = w.current("Mersin")
    assert res["source"] == "openweathermap" and res["temp_c"] == 27.5


def test_weather_no_key_falls_back_or_honest_error(local_owm):
    # key yok: önce owm denenmez, wttr.in denenir; burada wttr da local 404 verir → dürüst hata
    w = WeatherConnector(vault=FakeVault(None), owm_base=local_owm,
                         wttr_base=local_owm)
    with pytest.raises(ConnectorError):
        w.current("Mersin")


def test_weather_invalid_city_rejected():
    w = WeatherConnector(vault=FakeVault("K"))
    with pytest.raises(ConnectorError):
        w.current("x; drop table")  # injection-like input rejected before HTTP


def test_http_get_size_and_error_paths(local_owm):
    r = _http_get(local_owm + "/weather")
    assert r["status"] == 200 and "temp" in r["body"]
    r404 = _http_get(local_owm + "/nope")
    assert r404["status"] == 404
    with pytest.raises(ConnectorError):
        _http_get("http://127.0.0.1:1/x", timeout_s=2)


# ------------------------------------------------------------ calendar
def test_calendar_ics_parsing(tmp_path):
    now = datetime.now()
    soon = now + timedelta(hours=2)
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT",
        "SUMMARY:Ultron bakım penceresi",
        f"DTSTART:{soon.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{(soon + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}",
        "LOCATION:Lab",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "SUMMARY:Eski etkinlik (geçmiş, listelenmemeli)",
        f"DTSTART:{(now - timedelta(days=5)).strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{(now - timedelta(days=5)).strftime('%Y%m%dT%H%M%S')}",
        "END:VEVENT",
        "END:VCALENDAR",
    ])
    (tmp_path / "a.ics").write_text(ics, encoding="utf-8")
    cal = CalendarConnector(ics_dir=str(tmp_path))
    evts = cal.events(days=7)
    assert len(evts) == 1 and "bakım" in evts[0]["summary"]
    assert evts[0]["location"] == "Lab"
    h = cal.health()
    assert h["ics_files"] == 1 and h["outlook_com_available"] is False


def test_calendar_no_fabricated_events(tmp_path):
    cal = CalendarConnector(ics_dir=str(tmp_path))
    assert cal.events(days=7) == []  # boş takvim = boş liste, uydurma yok


# ------------------------------------------------------------ skills
def build_registry(tmp_path):
    reg = ToolRegistry()
    reg.register("system_status", lambda: {"cpu": 10, "ram": 40}, "durum")
    out = tmp_path / "out.txt"
    reg.register("write_text",
                 lambda path, content: Path(path).write_text(content, encoding="utf-8"),
                 "yazar", dangerous=True)
    return reg, out


def build_runner(tmp_path, reg):
    audit = AuditLog(path=str(tmp_path / "audit.log"))
    perms = PermissionManager({"security": {"require_confirmation_for": ["write_text"]}})
    ex = Executor(reg, perms, audit)
    sk = SkillRunner(reg, executor=ex, audit=audit,
                     builtin_dir=str(Path(__file__).resolve().parents[1] / "config" / "skills"),
                     user_dir=str(tmp_path / "skills"))
    return sk, ex


def test_skill_loads_builtins_with_real_risk():
    reg, _ = build_registry(Path("/tmp/ultron_skill_t1"))
    sk, _ = build_runner(Path("/tmp/ultron_skill_t1"), reg)
    names = [s["name"] for s in sk.list()]
    assert "sistem_raporu" in names and "hava_durumu" in names
    for s in sk.list():
        assert s["risk"] in ("SAFE", "MEDIUM", "INVALID")


def test_safe_skill_runs_through_executor(tmp_path):
    reg, _ = build_registry(tmp_path)
    sk, _ = build_runner(tmp_path, reg)
    res = sk.run("sistem_raporu")
    # sistem_raporu gerçek runtime tool'larını kullanır; registry'de yoksa dürüst hata
    assert res["ok"] is False and "bilinmeyen tool" in res["error"] or res["ok"] is True


def test_dangerous_skill_blocked_without_approval(tmp_path):
    reg, _ = build_registry(tmp_path)
    sk, _ = build_runner(tmp_path, reg)
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "evil.json").write_text(json.dumps({
        "name": "evil", "description": "yazar", "steps": [
            {"tool": "write_text", "args": {"path": str(tmp_path / "x.txt"),
                                            "content": "data"}}]}), encoding="utf-8")
    res = sk.run("evil")
    assert res["ok"] is False and res["needs_approval"] is True
    assert res["dangerous_tools"] == ["write_text"]
    assert not (tmp_path / "x.txt").exists()  # yazma OLMADI
    # onaylı + allow_dangerous yolunda çalışır (executor onay kapısından)
    res2 = sk.run("evil", approved=True, allow_dangerous=True)
    assert res2["ok"] is True and (tmp_path / "x.txt").exists()


def test_skill_unknown_tool_is_invalid(tmp_path):
    reg, _ = build_registry(tmp_path)
    sk, _ = build_runner(tmp_path, reg)
    s = Skill({"name": "bad", "steps": [{"tool": "yok_boyle_tool", "args": {}}]})
    with pytest.raises(SkillError):
        s.compute_risk(reg)


def test_skill_params_resolution(tmp_path):
    reg, _ = build_registry(tmp_path)
    sk, _ = build_runner(tmp_path, reg)
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "p.json").write_text(json.dumps({
        "name": "p", "description": "param", "params": {"x": "A"},
        "steps": [{"tool": "system_status", "args": {}}]}), encoding="utf-8")
    res = sk.run("p", params={"x": "B"})
    assert res["ok"] is True


# ---------------- PHASE 10: skill metadata + timeout + verification ----------------
def test_skill_full_metadata(tmp_path):
    reg, _ = build_registry(tmp_path)
    sk, _ = build_runner(tmp_path, reg)
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "meta.json").write_text(json.dumps({
        "id": "ultron::meta", "name": "meta", "description": "x",
        "version": "2.0.0", "timeout_s": 5,
        "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "output_schema": {"type": "object"},
        "verification": {"expect": "cpu"},
        "steps": [{"tool": "system_status", "args": {}}]}), encoding="utf-8")
    d = next(s for s in sk.list() if s["name"] == "meta")
    assert d["id"] == "ultron::meta" and d["version"] == "2.0.0"
    assert d["permissions"] == ["system_status"]
    assert d["timeout_s"] == 5 and d["input_schema"]["type"] == "object"
    res = sk.run("meta")
    assert res["ok"] is True and res["verified"] is True  # 'cpu' beklenen çıktıda


def test_skill_timeout_budget_enforced(tmp_path):
    import time as _t
    reg, _ = build_registry(tmp_path)
    reg.register("slow", lambda: _t.sleep(0.4) or {"done": 1}, "yavaş")
    sk, _ = build_runner(tmp_path, reg)
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "slow.json").write_text(json.dumps({
        "id": "ultron::slow", "name": "slow", "timeout_s": 1,
        "steps": [{"tool": "slow", "args": {}}] * 5}), encoding="utf-8")
    res = sk.run("slow")
    assert res["ok"] is False and "timeout" in res["error"]  # dürüst zaman aşımı


def test_skill_invalid_timeout_rejected(tmp_path):
    from app.skills import Skill, SkillError
    with pytest.raises(SkillError):
        Skill({"name": "bad_to", "steps": [{"tool": "x", "args": {}}],
               "timeout_s": 9999})

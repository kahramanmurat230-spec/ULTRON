"""PHASE 13: presence sources — real TCP reachability + honest unavailability."""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.presence.sources import PresenceSources  # noqa: E402


def tcp_server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    threading.Thread(target=lambda: (s.accept() and None), daemon=True).start()
    return s, port


def test_wifi_real_reachability():
    srv, port = tcp_server()
    try:
        ps = PresenceSources({"wifi_devices": {
            "phone": {"ip": "127.0.0.1", "port": port}}})
        r = ps.check_wifi("phone")
        assert r["available"] is True and r["latency_ms"] >= 0
        agg = ps.aggregate()
        assert agg["in_room"] is True and "wifi:phone" in agg["active_sources"]
        assert agg["confidence"] > 0
    finally:
        srv.close()


def test_wifi_unreachable_and_unconfigured():
    ps = PresenceSources({"wifi_devices": {
        "kapali": {"ip": "127.0.0.1", "port": 1}}})  # kapalı port
    r = ps.check_wifi("kapali")
    assert r["available"] is False and r.get("error")
    yok = ps.check_wifi("olmayan-cihaz")
    assert yok["available"] is False and "yapılandırılmamış" in yok["error"]


def test_camera_never_fakes_presence():
    ps = PresenceSources({})
    r = ps.check_camera()
    assert r["available"] is False and "simüle edilmez" in r["error"]


def test_bluetooth_honest_without_tool():
    ps = PresenceSources({})
    r = ps.check_bluetooth()
    assert r["available"] is False


def test_mobile_via_mesh_and_iot_real_only():
    ps = PresenceSources({}, mesh_mobile_online=lambda: True,
                         iot_list=lambda: [
                             {"device_id": "mock1", "is_simulated": True, "state": "on"},
                             {"device_id": "real1", "is_simulated": False, "state": "on"},
                             {"device_id": "real2", "is_simulated": False, "state": "off"}])
    assert ps.check_mobile()["available"] is True
    iot = ps.check_iot()
    assert iot["available"] is True and iot["active_real_devices"] == ["real1"]


def test_manual_ping_window():
    t = {"now": 1000.0}
    ps = PresenceSources({}, clock=lambda: t["now"])
    assert ps.aggregate()["sources"]["manual"]["available"] is False
    ps.manual_ping()
    assert ps.aggregate()["sources"]["manual"]["available"] is True
    t["now"] += 301
    assert ps.aggregate()["sources"]["manual"]["available"] is False  # 5dk pencere

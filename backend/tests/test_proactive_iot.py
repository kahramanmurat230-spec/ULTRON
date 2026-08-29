"""PHASE 11: spike-suppressed proactive monitoring + IoT real/mock separation."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.iot.iot_nexus import IoTNexus  # noqa: E402
from app.proactive.monitor import ProactiveMonitor  # noqa: E402


# ------------------------------------------------------------ proactive
def make_monitor(**cfg):
    defaults = {"sustained_samples": 3, "alarm_cooldown_seconds": 900,
                "hysteresis_percent": 5}
    defaults.update(cfg)
    m = ProactiveMonitor({"proactive": defaults}, lambda a: None)
    return m


def t0():
    return 1_000_000.0


def test_single_spike_does_not_alarm():
    m = make_monitor()
    now = t0()
    assert m.evaluate({"cpu_percent": 99.0}, now=now) == []       # tek spike
    assert m.evaluate({"cpu_percent": 99.0}, now=now + 10) == []  # 2. örnek
    m.evaluate({"cpu_percent": 20.0}, now=now + 20)               # düştü
    assert m.evaluate({"cpu_percent": 99.0}, now=now + 30) == []  # yeni tek spike


def test_sustained_high_alarms_once():
    m = make_monitor()
    now = t0()
    fired = []
    for i in range(4):
        fired += m.evaluate({"cpu_percent": 99.0}, now=now + i * 10)
    assert len(fired) == 1 and "CPU" in fired[0]


def test_cooldown_blocks_repeat_hysteresis_reset_requires_recovery():
    m = make_monitor()
    now = t0()
    fired = []
    for i in range(4):
        fired += m.evaluate({"cpu_percent": 99.0}, now=now + i * 10)
    assert len(fired) == 1
    m.evaluate({"cpu_percent": 92.0}, now=now + 100)  # limit-5=90 üstü -> toparlanma SAYILMAZ
    for i in range(5):
        fired += m.evaluate({"cpu_percent": 99.0}, now=now + 200 + i * 10)
    assert len(fired) == 1  # cooldown (900s) dolmadı -> tekrar alarm yok


def test_after_cooldown_new_sustained_alarm_fires():
    m = make_monitor()
    now = t0()
    fired = []
    for i in range(4):
        fired += m.evaluate({"cpu_percent": 99.0}, now=now + i * 10)
    assert len(fired) == 1
    m.evaluate({"cpu_percent": 50.0}, now=now + 50)   # hysteresis altı -> sayaç sıfır
    for i in range(4):
        fired += m.evaluate({"cpu_percent": 99.0}, now=now + 1000 + i * 10)
    assert len(fired) == 2  # cooldown geçti -> yeni sürekli alarm


def test_ram_and_disk_metrics_independent():
    m = make_monitor()
    now = t0()
    fired = []
    for i in range(4):
        fired += m.evaluate({"ram_percent": 97.0, "cpu_percent": 10.0,
                             "disk_percent": 97.0}, now=now + i * 10)
    assert len(fired) == 2
    assert any("RAM" in a for a in fired) and any("Disk" in a for a in fired)


# ------------------------------------------------------------ iot
class FakeVault:
    def get(self, name):
        return "VAULT-HA-TOKEN" if name == "homeassistant_token" else None


def test_iot_devices_flagged_simulated():
    with tempfile.TemporaryDirectory() as d:
        n = IoTNexus(db_path=os.path.join(d, "i.db"))
        devs = n.list()
        assert devs and all(d["is_simulated"] for d in devs)  # demo envanteri mock
        n.register("real_plug", "Gerçek Priz", "switch", "Salon",
                   driver="http", address="http://127.0.0.1:1/relay?state={action}")
        real = [x for x in n.list() if x["device_id"] == "real_plug"]
        assert real and real[0]["is_simulated"] is False
        only_real = n.list(include_simulated=False)
        assert [x["device_id"] for x in only_real] == ["real_plug"]


def test_iot_mock_control_labels_simulated():
    with tempfile.TemporaryDirectory() as d:
        n = IoTNexus(db_path=os.path.join(d, "i.db"))
        res = n.control("light_masa", "turn_on")
        assert res["ok"] is True and res["is_simulated"] is True


def test_iot_unreachable_http_driver_never_fakes():
    with tempfile.TemporaryDirectory() as d:
        n = IoTNexus(db_path=os.path.join(d, "i.db"))
        n.register("dead_plug", "Ölü Priz", "switch", "Salon",
                   driver="http", address="http://127.0.0.1:1/x?state={action}")
        res = n.control("dead_plug", "turn_on")
        assert res["ok"] is False and "error" in res


def test_iot_vault_token_resolution():
    with tempfile.TemporaryDirectory() as d:
        n = IoTNexus(db_path=os.path.join(d, "i.db"), vault=FakeVault())
        assert n._ha_token() == "VAULT-HA-TOKEN"
        n2 = IoTNexus(db_path=os.path.join(d, "i2.db"), ha_token="ARG-TOKEN")
        assert n2._ha_token() == "ARG-TOKEN"


def test_iot_fuzzy_match_real_vs_mock():
    with tempfile.TemporaryDirectory() as d:
        n = IoTNexus(db_path=os.path.join(d, "i.db"))
        m = n.fuzzy_match("masa lambasını aç")
        assert m and m["intent"] == "turn_on"
        dev = n.get(m["device_id"])
        assert dev["is_simulated"] is True  # demo cihaz olduğu açık

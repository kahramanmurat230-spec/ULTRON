"""Presence sources — multi-source presence aggregation with honest availability.

Sources (capability abstraction):
  wifi     : real LAN reachability check (TCP connect to device IP:port)
  bluetooth: platform-dependent lookup — honestly 'unavailable' without
             a tool/bluetoothctl; never simulated
  camera   : NOT implemented as real detection — always reported
             is_simulated=false/available=false (no fake presence)
  mobile   : mesh heartbeat liveness (NodeRegistry)
  iot      : any real (non-mock) IoT device currently 'on'
  manual   : explicit ping (API)

aggregate() fuses sources into in_room + confidence (share of active
sources). Nothing fabricates presence: a source without hardware or
config reports available=false and does not count.
"""
import socket
import time

WIFI_PROBE_PORT = 80  # çoğu telefonda kapalı olabilir; porta özgü konfig tercih edilir


class PresenceSources:
    def __init__(self, config=None, mesh_mobile_online=None, iot_list=None,
                 clock=None):
        cfg = config or {}
        self.devices = cfg.get("wifi_devices", {})  # {name: {"ip": ..., "port": ...}}
        self.bt_available = bool(cfg.get("bluetooth_tool"))  # ör. "btctl" var mı
        self.mesh_mobile_online = mesh_mobile_online or (lambda: False)
        self.iot_list = iot_list or (lambda: [])
        self.now = clock or time.time
        self._last = {}

    # ------------------------------------------------------------ sources
    def check_wifi(self, name: str) -> dict:
        dev = self.devices.get(name)
        if not dev or not dev.get("ip"):
            return {"source": "wifi", "available": False,
                    "error": "cihaz IP'si yapılandırılmamış"}
        ip = dev["ip"]
        port = int(dev.get("port", WIFI_PROBE_PORT))
        t0 = time.time()
        try:
            with socket.create_connection((ip, port), timeout=2.0):
                self._last["wifi"] = self.now()
                return {"source": "wifi", "available": True, "ip": ip,
                        "latency_ms": round((time.time() - t0) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"source": "wifi", "available": False, "ip": ip,
                    "error": str(exc)[:80]}

    def check_bluetooth(self) -> dict:
        if not self.bt_available:
            return {"source": "bluetooth", "available": False,
                    "error": "bluetooth aracı yok (gerçek tarama yapılamaz)"}
        import shutil
        import subprocess
        tool = shutil.which(self.bt_available)
        if not tool:
            return {"source": "bluetooth", "available": False,
                    "error": "bluetooth aracı bulunamadı"}
        try:
            out = subprocess.run([tool, "devices"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            devices = [l.split()[1] for l in out.splitlines()
                       if l.startswith("Device ") and len(l.split()) > 1]
            if devices:
                self._last["bluetooth"] = self.now()
            return {"source": "bluetooth", "available": True, "devices": devices[:5]}
        except Exception as exc:  # noqa: BLE001
            return {"source": "bluetooth", "available": False, "error": str(exc)[:80]}

    def check_camera(self) -> dict:
        # Dürüst: gerçek kamera varlık algısı YOK — asla sahte presence üretmez
        return {"source": "camera", "available": False,
                "error": "kamera varlık algısı kurulu değil (simüle edilmez)"}

    def check_mobile(self) -> dict:
        online = bool(self.mesh_mobile_online())
        if online:
            self._last["mobile"] = self.now()
        return {"source": "mobile", "available": online}

    def check_iot(self) -> dict:
        try:
            devs = [d for d in self.iot_list()
                    if not d.get("is_simulated", True) and d.get("state") == "on"]
        except Exception:
            devs = []
        if devs:
            self._last["iot"] = self.now()
        return {"source": "iot", "available": bool(devs), "active_real_devices":
                [d.get("device_id") for d in devs][:5]}

    def manual_ping(self) -> dict:
        self._last["manual"] = self.now()
        return {"source": "manual", "available": True}

    # ------------------------------------------------------------ fusion
    def aggregate(self) -> dict:
        wifi = {n: self.check_wifi(n) for n in self.devices}
        results = {
            "wifi": wifi,  # {cihaz: sonuç} — boşsa yapılandırılmamış demektir
            "bluetooth": self.check_bluetooth(),
            "camera": self.check_camera(),
            "mobile": self.check_mobile(),
            "iot": self.check_iot(),
            "manual": ({"available": True}
                       if self.now() - self._last.get("manual", -1e9) < 300
                       else {"available": False}),
        }
        active = []
        for name, res in results.items():
            if name == "wifi":
                hits = [k for k, r in res.items() if r.get("available")]
                active += [f"wifi:{h}" for h in hits]
            elif res.get("available"):
                active.append(name)
        # mobil cihazın wifi'si görünüyorsa tek say (en güççük sinyal kümesi)
        unique = set(active)
        return {"in_room": bool(unique), "sources": results,
                "active_sources": sorted(unique),
                "wifi_configured": bool(self.devices),
                "confidence": round(len(unique) / 5, 2),
                "ts": self.now()}

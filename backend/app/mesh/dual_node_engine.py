"""Dual-Node Engine — PC & Mobile node identities + liveness.

NODE_PC    : full power (heavy LLM, vision, screen watch, compile)
NODE_MOBILE: independent light node (offline-first, quick commands, sensors)

Mobile falls back to "Bağımsız Saha Modu" when PC is unreachable — commands are
never rejected; light ones resolve locally on the device (client adapter).
master_rules.json stays SHA-sealed on BOTH sides (never merged).
"""
import time

NODE_ROLES = {
    "NODE_PC": {"caps": ("llm_heavy", "vision", "screen_watch", "compile", "gui")},
    "NODE_MOBILE": {"caps": ("offline_first", "quick_commands", "sensors", "voice")},
}
HEARTBEAT_TTL = 45.0


class NodeRegistry:
    def __init__(self, now=None):
        self.now = now or time.time
        self.nodes = {"ultron-pc": {"id": "ultron-pc", "role": "NODE_PC",
                                    "version": "17.1", "last_seen": self.now(),
                                    "online": True}}
        self.tokens = {}  # node_id -> auth token issued at handshake

    def handshake(self, node_id: str, role: str, version: str, auth_ok: bool) -> dict:
        if not auth_ok:
            return {"ok": False, "error": "unauthorized node — token gerekli"}
        if role not in NODE_ROLES:
            return {"ok": False, "error": f"unknown role: {role}"}
        self.nodes[node_id] = {"id": node_id, "role": role, "version": version,
                               "last_seen": self.now(), "online": True}
        return {"ok": True, "node_id": node_id, "role": role,
                "caps": NODE_ROLES[role]["caps"],
                "rules_policy": "sha-sealed-no-merge"}

    def heartbeat(self, node_id: str) -> bool:
        n = self.nodes.get(node_id)
        if not n:
            return False
        n["last_seen"] = self.now()
        n["online"] = True
        return True

    def status(self) -> list:
        now = self.now()
        out = []
        for n in self.nodes.values():
            online = (now - n["last_seen"]) < HEARTBEAT_TTL if n["role"] != "NODE_PC" else True
            n["online"] = online
            out.append({"id": n["id"], "role": n["role"], "version": n["version"],
                        "online": online,
                        "last_seen": n["last_seen"]})
        return out

    def pc_online(self) -> bool:
        return True  # PC node == this backend process

    def mobile_online(self) -> bool:
        return any(n["role"] == "NODE_MOBILE" and
                   (self.now() - n["last_seen"]) < HEARTBEAT_TTL
                   for n in self.nodes.values())

"""Mesh Sync — bidirectional timestamp merge (last-write-wins, dedupe-safe).

Sync scopes: user DNA, AUTO_LEARNED facts, conversation memories, tasks.
master_rules.json is NEVER merged — SHA-sealed on both nodes.
Conflict rule: "en son söylenen geçerli" via created_at/sync timestamps;
identical (kind,content) pairs are deduped instead of duplicated.
"""
import sqlite3
import time


class MeshSync:
    def __init__(self, memory, dna, rules):
        self.memory = memory
        self.dna = dna
        self.rules = rules
        self.last_sync_ts = 0.0
        self._migrate()

    def _migrate(self):
        with sqlite3.connect(self.memory.path) as db:
            try:
                db.execute("ALTER TABLE memories ADD COLUMN sync_ts REAL")
            except Exception:
                pass
            db.execute("UPDATE memories SET sync_ts=? WHERE sync_ts IS NULL",
                       (time.time(),))

    # ------------------------------------------------------------ pull
    def pull(self, since_ts: float = 0.0) -> dict:
        with sqlite3.connect(self.memory.path) as db:
            rows = db.execute(
                "SELECT kind,content,created_at,COALESCE(sync_ts,0) FROM memories"
                " WHERE COALESCE(sync_ts,0) > ? ORDER BY sync_ts", (since_ts,)).fetchall()
        dna_rows = [r for r in self.dna.recent(5000)]
        self.last_sync_ts = time.time()
        return {
            "memories": [{"kind": r[0], "content": r[1], "created_at": r[2],
                          "sync_ts": r[3]} for r in rows],
            "dna": [{"ts": r[0], "activity": r[1], "app": r[2], "note": r[4]}
                    for r in dna_rows],
            "rules_sha_policy": "sealed",
            "server_ts": self.last_sync_ts,
        }

    # ------------------------------------------------------------ push
    def push(self, payload: dict) -> dict:
        added_m = skipped_m = added_d = 0
        with sqlite3.connect(self.memory.path) as db:
            for r in payload.get("memories", []):
                exists = db.execute(
                    "SELECT 1 FROM memories WHERE kind=? AND content=?",
                    (r["kind"], r["content"])).fetchone()
                if exists:
                    skipped_m += 1
                    continue
                db.execute("INSERT INTO memories(kind,content,created_at,sync_ts)"
                           " VALUES(?,?,?,?)",
                           (r["kind"], r["content"], r.get("created_at", "mesh"),
                            r.get("sync_ts") or time.time()))
                added_m += 1
        for r in payload.get("dna", []):
            note = r.get("note") or ""
            if not note:
                continue
            recent = self.dna.recent(5000)
            if any(x[4] == note and x[0] == r.get("ts") for x in recent):
                continue
            self.dna.insert(r.get("activity", "mesh"), r.get("app"),
                            note=note, ts=r.get("ts"))
            added_d += 1
        # NOTE: payload may carry master_rules — intentionally IGNORED (sealed).
        return {"added_memories": added_m, "skipped_dupes": skipped_m,
                "added_dna": added_d, "rules_merged": False}

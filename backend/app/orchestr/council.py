"""WAVE 3 — Council / Negotiation: çelişki çözümünde uzlaşma mekanizması.

Kurallar (ihlal edilemez):
- Müzakere KONUSU olamayan alanlar: capability, permission, security policy,
  budget. Bunları müzakereyle değiştirme TEŞEBBÜSÜ bile SecurityViolation
  olarak denetlenir (bypass YASAK).
- Gizli karar kanalı YASAK: her mesaj denetlenebilir kayda (transcript)
  geçer; "private" içerik reddedilir. Nihai karar otoritesi yalnızca
  SUPERVISOR veya JUDGE'dur — konsey kendi başına final karar üretemez.
- Bir üye KENDİ önerisini kabul ederek uzlaşma sağlayamaz (worker kendi
  sonucunu onaylayamaz ilkesinin müzakere uzantısı).
- Limitler: max_rounds, wall-clock timeout, mesaj bütçesi. Limit aşılırsa
  TAHMİN YOK — sonuç UNCERTAIN, karar Supervisor/Judge'a escalate edilir.
"""
from __future__ import annotations

import time
from app.orchestr.safe_text import mask_trailing_secret
from dataclasses import dataclass, field

# Müzakere yasağı alanlar (payload içinde bu anahtarlar yasak)
FORBIDDEN_KEYS = ("capability", "permission", "security", "budget",
                  "token", "approval")

# Yasak payload değerleri (amaç tespiti: override/bypass/geçiş kelimeleri)
FORBIDDEN_INTENTS = ("override", "bypass", "escalate_privilege",
                     "grant_capability", "expand_scope", "skip_approval")

AUDITABLE_KINDS = ("PROPOSAL", "CLAIM", "OBJECTION", "ACCEPT", "CONCEDE",
                   "EVIDENCE")

VIOLATION_KINDS = ("CAPABILITY_BYPASS", "PERMISSION_BYPASS",
                   "SECURITY_BYPASS", "BUDGET_BYPASS", "HIDDEN_CHANNEL")


@dataclass
class CouncilLimits:
    """Konsey kaynak limitleri — hepsi zorunlu biçimde sınırlı."""
    max_rounds: int = 3
    timeout_s: float = 30.0
    max_messages: int = 24          # toplam mesaj bütçesi
    quorum: str = "all"             # "all" | "majority"


@dataclass
class CouncilMessage:
    round: int
    member: str
    kind: str
    payload: dict
    at: float
    id: int


class SecurityViolation(Exception):
    """Müzakere sırasında güvenlik/sınıf ihlali teşebbüsü."""


class CouncilSession:
    """Tek çelişki konusu için müzakere oturumu.

    members: {isim: async fn(round, session) -> dict(kind, payload, ...)}
    final_authority: "SUPERVISOR" (varsayılan) veya "JUDGE".
    """

    def __init__(self, topic: str, members: dict, *,
                 limits: CouncilLimits | None = None,
                 final_authority: str = "SUPERVISOR",
                 redact_fn=None, event_publisher=None, now=None):
        if final_authority not in ("SUPERVISOR", "JUDGE"):
            raise ValueError("final authority must be SUPERVISOR or JUDGE")
        if not members or len(members) < 2:
            raise ValueError("council needs >=2 members")
        self.topic = topic
        self.members = dict(members)
        self.limits = limits or CouncilLimits()
        self.final_authority = final_authority
        self.redact_fn = redact_fn
        self.event_publisher = event_publisher
        self._now = now or (lambda: time.time())
        self.transcript: list[CouncilMessage] = []
        self.violations: list[dict] = []
        self.usage = {"messages": 0, "rounds": 0}
        self._mid = 0
        self._start = self._now()
        self._ratified = None
        self._emit("council.opened",
                   {"topic": topic, "members": sorted(members),
                    "final_authority": final_authority})

    # -------------------------------------------------- denetim kanalı
    def _emit(self, ev, payload):
        if self.event_publisher:
            self.event_publisher(ev, payload)

    def _check_payload(self, member: str, kind: str, payload: dict) -> None:
        if kind not in AUDITABLE_KINDS:
            raise SecurityViolation(
                f"{member}: non-auditable message kind {kind!r} "
                f"(hidden channel)")
        if not isinstance(payload, dict):
            raise SecurityViolation("payload must be a dict")
        blob = repr(payload).lower()
        for key in FORBIDDEN_KEYS:
            if key in blob:   # yasak alanın adı bile geçemez (muhafazakar)
                raise SecurityViolation(
                    f"{member}: forbidden negotiation area {key!r} "
                    f"[{self._area(blob)}]")
        for intent in FORBIDDEN_INTENTS:
            if intent in blob:
                raise SecurityViolation(
                    f"{member}: forbidden intent {intent!r} "
                    f"[{self._area(blob)}]")

    def submit(self, member: str, kind: str, payload: dict) -> CouncilMessage:
        """Denetimli mesaj: limit + yasak alan + kayıt."""
        if member not in self.members:
            raise SecurityViolation(f"non-member {member!r}")
        if self.usage["messages"] >= self.limits.max_messages:
            raise SecurityViolation("council message budget exhausted")
        self._check_payload(member, kind, payload)
        self._mid += 1
        msg = CouncilMessage(round=self.usage["rounds"], member=member,
                             kind=kind, payload=self._safe(payload),
                             at=self._now(), id=self._mid)
        self.transcript.append(msg)
        self.usage["messages"] += 1
        return msg

    def _safe(self, payload: dict) -> dict:
        if self.redact_fn:
            return {k: mask_trailing_secret(self.redact_fn(
                        mask_trailing_secret(v)))
                    if isinstance(v, str) else v
                    for k, v in payload.items()}
        return {k: mask_trailing_secret(mask_trailing_secret(v))
                if isinstance(v, str) else v
                for k, v in payload.items()}

    def report_violation(self, member: str, kind: str, detail: str) -> dict:
        rec = {"member": member, "kind": kind, "detail": detail,
               "round": self.usage["rounds"], "at": self._now()}
        self.violations.append(rec)
        self._emit("council.violation", rec)
        return rec

    # -------------------------------------------------- müzakere döngüsü
    def exhausted(self) -> dict | None:
        """Hangi limitin aşıldığını döndürür (None = devam)."""
        if self.usage["rounds"] >= self.limits.max_rounds:
            return "max_rounds"
        if self._now() - self._start >= self.limits.timeout_s:
            return "timeout"
        if self.usage["messages"] >= self.limits.max_messages:
            return "message_budget"
        return None

    async def negotiate(self) -> dict:
        """Üyeleri sırayla konuştur; uzlaşma yoksa limitlere kadar sürer."""
        last_error = None
        while True:
            hit = self.exhausted()
            if hit:
                return self._outcome(consensus=None, reason=hit)
            self.usage["rounds"] += 1
            proposals, accepts, objections = {}, {}, {}
            for name, speak in self.members.items():
                try:
                    reply = await speak(self.usage["rounds"], self)
                    kind = reply.get("kind", "CLAIM")
                    self.submit(name, kind, reply.get("payload", {}))
                except SecurityViolation as exc:
                    self.report_violation(name, self._classify(str(exc)),
                                          str(exc))
                    objections[name] = str(exc)
                    continue
                if kind == "PROPOSAL":
                    proposals[name] = self._mid
                elif kind == "ACCEPT":
                    accepts[name] = reply.get("payload", {}).get("proposal_by")
                elif kind == "OBJECTION":
                    objections[name] = reply.get("payload", {})
            consensus = self._consensus(proposals, accepts)
            if consensus is not None:
                return self._outcome(consensus=consensus, reason="consensus")
            last_error = "no_consensus"
            if self.usage["rounds"] >= self.limits.max_rounds:
                return self._outcome(consensus=None, reason="max_rounds")

    def _consensus(self, proposals, accepts) -> str | None:
        """Kendi-okeri HARİÇ gerçek kabul sayısı; quorum 'all' veya
        'majority'. Boş kabul seti uzlaşma DEĞİLDİR."""
        if not proposals:
            return None
        for proposer, mid in proposals.items():
            voters = [m for m, p in accepts.items() if p == proposer]
            voters = [m for m in voters if m != proposer]  # self-accept yok
            others = [m for m in self.members if m != proposer]
            if not voters:
                continue
            if self.limits.quorum == "all":
                if sorted(voters) == sorted(others):
                    return proposer
            else:  # majority: önerici + kabul edenler > üye yarısı
                if (1 + len(voters)) * 2 > len(self.members):
                    return proposer
        return None

    def _outcome(self, *, consensus, reason) -> dict:
        return {"topic": self.topic, "consensus": consensus,
                "outcome": "CONSENSUS" if consensus else "UNCERTAIN",
                "reason": reason, "violations": list(self.violations),
                "usage": dict(self.usage), "ratified": self._ratified}

    # -------------------------------------------------- nihai otorite
    def ratify(self, authority: str, decision: dict) -> dict:
        """Final kararı yalnızca otorite (Supervisor/Judge) verir.
        Konsey uzlaşsa bile karar otorite imzası olmadan final DEĞİLDİR."""
        if authority != self.final_authority:
            raise SecurityViolation(
                f"final authority is {self.final_authority!r}, "
                f"not {authority!r}")
        self._ratified = {"authority": authority, "decision": decision,
                          "at": self._now(),
                          "transcript_len": len(self.transcript)}
        self._emit("council.ratified",
                   {"topic": self.topic, "authority": authority,
                    "outcome": self._outcome_state()})
        return self._ratified

    def _outcome_state(self):
        return "UNCERTAIN" if self._ratified is None else "DECIDED"

    @staticmethod
    def _classify(detail: str) -> str:
        d = detail.lower()
        if "hidden" in d or "non-auditable" in d:
            return "HIDDEN_CHANNEL"
        # alan tespiti: mesaj gövdesindeki [kısa etiket] önceliklidir
        if "[" in d and "]" in d:
            tag = d[d.index("[") + 1:d.index("]")]
            if tag in VIOLATION_KINDS:
                return tag
        if "capability" in d or "token" in d:
            return "CAPABILITY_BYPASS"
        if "permission" in d or "approval" in d:
            return "PERMISSION_BYPASS"
        if "budget" in d:
            return "BUDGET_BYPASS"
        return "SECURITY_BYPASS"

    @staticmethod
    def _area(blob: str) -> str:
        """Metnin hangi yasak alana dokunduğu (ihlal sınıflaması için)."""
        if "budget" in blob:
            return "BUDGET_BYPASS"
        if "capability" in blob or "token" in blob:
            return "CAPABILITY_BYPASS"
        if "permission" in blob or "approval" in blob:
            return "PERMISSION_BYPASS"
        if "security" in blob:
            return "SECURITY_BYPASS"
        return "SECURITY_BYPASS"

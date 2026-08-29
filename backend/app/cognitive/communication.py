"""Communication Intelligence — Wave 5 §16.

Mevcut voice/conversation katmanının üstünde additive:
- turn tracking (user/agent sırası)
- interruption detection (agent konuşurken yeni user girdisi)
- topic continuity (konu yığını; ani değişim algısı)
- concise/deep mode (user intel'den; yoksa nötr)
- clarification detection (soru eki/acerdad('?'))
- ambiguity detection (çok kısa/encoder belirsiz referans)
- response planning (kısa/uzun plan önerisi)
- confidence-aware communication (düşük güven → açık belirsizlik beyanı)

Deterministik; LLM YOK; durum gerçek geçişlerle test edilir.
"""
from __future__ import annotations

import re
import threading

QUESTION_MARKS = ("?", "mi ", " mı", " mu", "mü", "nedir", "nasıl")
VAGUE = ("şey", "onu", "bunu", "şunu", "onlar", "o iş", "eskisi")


class ConversationState:
    def __init__(self, user_intel=None, max_topics: int = 8):
        self.lock = threading.RLock()
        self.user_intel = user_intel
        self.max_topics = max_topics
        self.turns: list[dict] = []          # {'who':'user'|'agent',...}
        self.topic_stack: list[str] = []
        self.agent_speaking = False
        self.interruptions = 0

    # ---------------------------------------------------- turns
    def user_turn(self, text: str, now: float = 0.0) -> dict:
        with self.lock:
            interrupted = self.agent_speaking
            if interrupted:
                self.interruptions += 1
                self.agent_speaking = False   # barge-in: agent sustu
            self.turns.append({"who": "user", "text": text[:400],
                               "interrupted_agent": interrupted,
                               "ts": now or len(self.turns)})
            topic = self._topic_of(text)
            topic_shift = bool(self.topic_stack and topic
                               and topic != self.topic_stack[-1])
            if topic:
                self.topic_stack.append(topic)
                self.topic_stack = self.topic_stack[-self.max_topics:]
            return {"interrupted_agent": interrupted,
                    "topic": topic,
                    "topic_shift": topic_shift,
                    "needs_clarification": self.needs_clarification(text),
                    "ambiguity": self.ambiguity(text)}

    def agent_turn(self, text: str, confidence: float = 1.0,
                   now: float = 0.0) -> dict:
        with self.lock:
            self.agent_speaking = True
            self.turns.append({"who": "agent", "text": text[:400],
                               "confidence": float(confidence),
                               "ts": now or len(self.turns)})
            return {"speaking": True}

    def agent_finished(self) -> None:
        with self.lock:
            self.agent_speaking = False

    # ---------------------------------------------------- analiz
    @staticmethod
    def _topic_of(text: str) -> str | None:
        t = (text or "").strip().lower()
        if not t:
            return None
        # ilk anlamlı 3 kelime konu proxy'si (deterministik)
        words = re.findall(r"[a-zçğıöşü0-9]+", t)[:3]
        return " ".join(words) if words else None

    @staticmethod
    def needs_clarification(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        if t.endswith("?"):
            return False   # doğrudan soru — aydınlatma değil yanıt gerekir
        return any(q in t for q in QUESTION_MARKS)

    @staticmethod
    def ambiguity(text: str) -> dict:
        t = (text or "").strip().lower()
        words = re.findall(r"[a-zçğıöşü0-9]+", t)
        vague_hits = [v for v in VAGUE if v in t]
        reasons = []
        if len(words) <= 1 and words:
            reasons.append("too-short")
        if vague_hits:
            reasons.append("vague-reference")
        return {"ambiguous": bool(reasons), "reasons": reasons,
                "vague_terms": vague_hits}

    # ---------------------------------------------------- yanıt planı
    def response_plan(self, confidence: float = 1.0) -> dict:
        with self.lock:
            style = None
            if self.user_intel is not None:
                st = self.user_intel.communication_style()
                style = st.get("style")
            last_user = next((t["text"] for t in reversed(self.turns)
                              if t["who"] == "user"), "")
            amb = self.ambiguity(last_user)
            plan = {
                "style": style or "neutral",
                "ask_back": bool(amb["ambiguous"]
                                 or self.needs_clarification(last_user)
                                 and not last_user.endswith("?")),
                "length_hint": ("short" if style == "concise"
                                else "detailed" if style == "deep"
                                else "auto"),
                "confidence": round(float(confidence), 3),
            }
            # confidence-aware: düşük güven → belirsizliği AÇIKÇA söyle
            plan["disclose_uncertainty"] = confidence < 0.5
            if confidence < 0.5:
                plan["prefix_hint"] = ("Emin değilim — doğrulamak isteyeceğim: "
                                       "…")
            return plan

    def summary(self) -> dict:
        with self.lock:
            return {"turns": len(self.turns),
                    "user_turns": sum(1 for t in self.turns
                                      if t["who"] == "user"),
                    "agent_turns": sum(1 for t in self.turns
                                       if t["who"] == "agent"),
                    "interruptions": self.interruptions,
                    "topics": list(self.topic_stack),
                    "current_topic": self.topic_stack[-1]
                    if self.topic_stack else None,
                    "agent_speaking": self.agent_speaking}

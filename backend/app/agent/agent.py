import json,re
from urllib.parse import quote_plus
from app.agent.persona_guard import PersonaGuard, ANCHOR as _PERSONA_ANCHOR  # noqa: F401
import app.agent.persona_guard as persona_guard
from app.emotion.emotion_engine import analyze as emotion_analyze
from app.core.intent import classify, vision_pattern, VISION_PROMPT


# Explicit recall requests are answered from durable memory before generic
# intent/tool routing. Conversation is only a fallback after these categories.
_RECALL_LONG_TERM_KINDS = ("FACT", "PREFERENCE", "PROFILE", "IMPORTANT", "TASK", "DEVICE")
_RECALL_KIND_ALIASES = tuple(alias for kind in _RECALL_LONG_TERM_KINDS
                             for alias in (kind, kind.lower()))
_RECALL_KIND_PRIORITY = {kind: index for index, kind in enumerate(_RECALL_LONG_TERM_KINDS)}
_RECALL_MARKERS = (
    "hatırlıyor musun", "hatırlıyor", "hatirliyor musun", "hatirliyor",
    "hatırında mı", "hatirinda mi", "geçen söylediğim", "gecen soyledigim",
    "geçen sana söylediğim", "gecen sana soyledigim", "sana söylediğim",
    "sana soyledigim", "kaydettiğim", "kaydettigim", "not aldığın", "not aldigin",
)
_RECALL_NOISE = {"ultron", "ben", "benim", "bana", "sen", "sana", "senin", "sizin",
                 "musun", "mısın", "misin", "müsün", "müsun", "ne", "neyi", "neydi",
                 "nedir", "diye", "geçen", "gecen", "not"}


class Agent:
    def __init__(self,brain,executor,memory,registry,settings,audit,tts,max_steps=8,semantic_memory=None,planner=None,vision_llm=None,adaptive=None,router=None,world_fn=None,redact_fn=None):
        self.brain=brain; self.executor=executor; self.memory=memory; self.registry=registry; self.settings=settings; self.audit=audit; self.tts=tts; self.max_steps=max_steps
        self.semantic_memory=semantic_memory; self.planner=planner; self.vision_llm=vision_llm
        self.adaptive=adaptive
        self.router=router  # PHASE 2: model router (GENERAL chat + fallback)
        self.world_fn=world_fn  # PHASE 4: live world-state context (WorldModel)
        self.redact_fn=redact_fn  # PHASE 10: vault-backed redaction (prompts/tool output)
        self.persona=PersonaGuard(mode=settings.get('persona_guard_mode','reframe'))
    def _persona_finalize(self, answer):
        chk=self.persona.check(answer)
        bad = chk["violations"] or chk["score"] < self.persona.threshold
        if not bad:
            return answer
        mode=self.persona.mode
        if mode in ("regenerate","hybrid") and chk["violation_score"] > 0.8:
            try:
                rw=self.brain.ask(
                    "Aşağıdaki metni anlamı koruyarak Ultron personasıyla yeniden yaz "
                    "(soğuk, hesapçı, otoriter; asla müşteri hizmetleri tonu yok):\n"+answer,
                    system=persona_guard.ANCHOR)
                if rw:
                    return rw
            except Exception:
                pass
            return PersonaGuard.reframe(answer)
        if mode in ("reframe","hybrid"):
            return self._boss_hitap(PersonaGuard.reframe(answer))
        return self._boss_hitap(answer)
    def _boss_hitap(self, answer):
        # Deterministic Turkish recall replies already address the owner as
        # "Patron", so avoid adding a second conflicting salutation.
        if not any(hitap in (answer or "").lower() for hitap in ("boss", "patron")):
            return "Boss, " + answer
        return answer

    @staticmethod
    def _is_memory_recall(text):
        normalized = " ".join((text or "").casefold().split())
        # Keep established deterministic profile/task read paths intact.
        if any(phrase in normalized for phrase in (
                "benim adım ne", "ismim ne", "görevlerim", "todo listem",
                "ne yapmam gerektiğini", "bugün ne yapmam")):
            return False
        if any(marker in normalized for marker in _RECALL_MARKERS):
            return True
        # The present-tense shorthand is intentionally limited to code
        # questions ("kodum ne?") so it does not swallow regular chat.
        if "kod" in normalized and re.search(
                r"\bbenim\s+[\wçğıöşü]+(?:\s+[\wçğıöşü]+){0,8}\s+ne(?:ydi|dir)?\b", normalized, re.I):
            return True
        return bool(re.search(r"\b[\wçğıöşü]+(?:\s+[\wçğıöşü]+){0,8}\s+neydi\b", normalized, re.I))

    @staticmethod
    def _recall_stem(token):
        """Normalize Turkish possessive/copula endings only for hit ranking."""
        original = (token or "").casefold()
        value = re.sub(r"(?:dur|dür|dır|dir|tur|tür|tır|tir)$", "", original)
        value = re.sub(r"(?:mı|mi|mu|mü)$", "", value)
        value = re.sub(r"(?:um|üm|ım|im|un|ün|ın|in|u|ü|ı|i)$", "", value)
        return value if len(value) >= 3 else original

    @classmethod
    def _recall_focus(cls, text):
        normalized = (text or "").casefold()
        normalized = re.sub(r"\bultron\b[,:!\s]*", " ", normalized)
        for marker in _RECALL_MARKERS:
            normalized = normalized.replace(marker, " ")
        tokens = re.findall(r"[\wçğıöşü]+", normalized, re.I)
        return [cls._recall_stem(token) for token in tokens
                if len(token) > 2 and token not in _RECALL_NOISE]

    @classmethod
    def _recall_matches(cls, focus, content):
        memory_tokens = [cls._recall_stem(token) for token in re.findall(
            r"[\wçğıöşü]+", str(content).casefold(), re.I)]
        return sum(any(query == memory or (len(query) >= 3 and len(memory) >= 3 and
                                            (query in memory or memory in query))
                       for memory in memory_tokens) for query in set(focus))

    def _search_recall_memory(self, queries, kinds):
        """Use SemanticMemory.search while retaining the best result per record."""
        if self.semantic_memory is None:
            return []
        allowed = {str(kind).upper() for kind in kinds}
        best = {}
        for query in queries:
            if not query:
                continue
            try:
                rows = self.semantic_memory.search(query, limit=12, kinds=kinds)
            except TypeError:  # compatibility with old semantic-memory adapters
                try: rows = self.semantic_memory.search(query, limit=12)
                except Exception: continue
            except Exception:
                continue
            for hit in rows or []:
                if not isinstance(hit, (tuple, list)) or len(hit) < 4:
                    continue
                score, kind, content, created = hit[:4]
                if str(kind).upper() not in allowed:
                    continue
                try: score = float(score)
                except (TypeError, ValueError): score = 0.0
                key = (str(kind).upper(), str(content), str(created))
                candidate = (score, str(kind), str(content), created)
                if key not in best or score > best[key][0]:
                    best[key] = candidate
        return list(best.values())

    def _select_recall_hit(self, hits, focus, long_term):
        if not focus:
            return None
        required = 2 if len(set(focus)) >= 2 else 1
        candidates = [(score, kind, content, created, self._recall_matches(focus, content))
                      for score, kind, content, created in hits]
        candidates = [hit for hit in candidates if hit[0] > 0 and hit[4] >= required]
        if not candidates:
            return None
        if long_term:
            selected = max(candidates, key=lambda hit: (
                -_RECALL_KIND_PRIORITY.get(hit[1].upper(), len(_RECALL_KIND_PRIORITY)),
                hit[4], hit[0], str(hit[3])))
        else:
            selected = max(candidates, key=lambda hit: (hit[4], hit[0], str(hit[3])))
        return selected[:4]

    @staticmethod
    def _recall_label(focus):
        if "test" in focus and any(token.startswith("kod") for token in focus):
            return "Test kodunuz"
        if any(token.startswith("görev") or token.startswith("gorev") for token in focus):
            return "Göreviniz"
        if any(token.startswith("tercih") for token in focus):
            return "Tercihiniz"
        return None

    def _memory_recall(self, text):
        """Resolve explicit Turkish recall questions before intent/tool routing."""
        if not self._is_memory_recall(text):
            return None
        focus = self._recall_focus(text)
        queries = [text, " ".join(focus)]  # raw wording + subject-only wording
        selected = self._select_recall_hit(
            self._search_recall_memory(queries, _RECALL_KIND_ALIASES), focus, long_term=True)
        if selected is None:
            conversation = self._search_recall_memory(queries, ("conversation", "CONVERSATION"))
            current = f"USER: {text}".casefold()  # never answer with this turn's question
            selected = self._select_recall_hit(
                [hit for hit in conversation if hit[2].casefold() != current], focus, long_term=False)
        if selected is None:
            return "İlgili bir hafıza kaydı bulamadım."
        _score, kind, content, _created = selected
        fact = re.match(r"^\s*(.+?)\s+(?:benim|sizin|kullanıcının)\s+(.+?)[.!?\s]*$", content, re.I)
        required = 2 if len(set(focus)) >= 2 else 1
        label = self._recall_label(focus)
        if kind.upper() == "FACT" and fact and label and self._recall_matches(focus, fact.group(2)) >= required:
            return f"Evet Patron. {label} {fact.group(1).strip(' -:–')}."
        return f"Evet Patron. Hafızamdaki kayıt: {content}"

    def _save(self,role,text): self.memory.add("conversation",f"{role}: {text}")
    def _history(self,limit=20):
        rows=list(reversed(self.memory.recent(limit))); out=[]
        for kind,c,_ in rows:
            if kind=="conversation" and (c.startswith("USER: ") or c.startswith("ULTRON: ")):
                role="user" if c.startswith("USER: ") else "assistant"; out.append({"role":role,"content":c.split(": ",1)[1]})
        return out
    def _vision_pipeline(self,text):
        """capture → verify PNG → llava vision → response. Never touches the
        text-only chat path; on any failure returns the exact error."""
        import os
        from app.tools.ollama_tools import ollama_status
        m=re.search(r"(\S+\.(?:png|jpe?g))", text, re.I)
        path=m.group(1) if m else None
        if not path:
            self.audit.write("VISION","stage=screenshot start")
            try:
                path=self.executor.execute([('capture_screen',{})],approved=True)[0]
            except Exception as e:
                return f"SCREENSHOT ERROR: {e}"
        if not (os.path.exists(path) and os.path.getsize(path)>0):
            return f"SCREENSHOT ERROR: PNG doğrulanamadı: {path}"
        self.audit.write("VISION",f"detected intent=screen_analyze screenshot={path} size={os.path.getsize(path)}")
        if self.vision_llm is None:
            return "VISION ERROR: VisionLLM runtime'a bağlı değil."
        try:
            models=ollama_status(self.brain.base_url,self.brain.model).get('models') or []
        except Exception:
            models=[]
        model=self.vision_llm.resolve_model(models)
        self.audit.write("VISION",f"selected_model={model} endpoint={self.brain.base_url}/api/chat")
        if model is None:
            return "VISION UNAVAILABLE: kurulu llava/vision modeli yok (Ollama /api/tags kontrol edildi)."
        try:
            analysis=self.vision_llm.analyze(path, VISION_PROMPT, available_models=models)
        except Exception as e:
            return f"VISION ERROR: {e}"
        if not analysis:
            return "VISION ERROR: model boş yanıt verdi."
        final=f"EKRAN ANALİZİ ({model}): {analysis}"
        self.audit.write("VISION",f"final_head={final[:160]}")
        return final

    def _direct(self,text):
        # vision requests must NEVER reach the text-only Qwen chat path
        if vision_pattern(text):
            return self._vision_pipeline(text)
        # Recall is intentionally before classify()/write patterns.  Phrases
        # such as "kaydettiğim" and "not aldığın" are questions, not commands
        # to create a new memory or start an unrelated tool flow.
        recall = self._memory_recall(text)
        if recall is not None:
            return recall
        if self.adaptive and "sarkazm" in text.lower() and ("aç" in text.lower() or "kapat" in text.lower()):
            mode=self.adaptive.override
            return f"Kaydedildi, Boss. Sarkazm modu: {'AÇIK' if mode=='sarkazm-on' else 'KAPALI' if mode=='sarkazm-off' else 'OTOMATİK'}."
        intent=classify(text); t=text.lower()
        # Deterministic conversation memory for common identity/context requests.
        m=re.search(r"(?:benim adım|benim ismim|adım|ismim)\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)", text, re.I)
        if m and not any(x in t for x in ["ne", "nedir"]):
            name=m.group(1).strip().capitalize()
            self.memory.add("profile", f"name: {name}")
            return f"Tamam. Adınızı {name} olarak hatırlayacağım."
        if any(x in t for x in ["benim adım ne", "ismim ne"]):
            rows=[x for x in self.memory.recent(100) if x[0]=="profile" and x[1].startswith("name:")]
            if rows: return f"Adınız {rows[0][1].split(':',1)[1].strip()}."
        # ---- long-term memory categories (never store sensitive data)
        sensitive = re.search(r"(şifre|password|parola|token|api[_-]?key|secret|kredi kart)", t)
        mnote = re.search(r"(?:not al|kaydet|unutma)\s*[:\"]*(.+)", text, re.I)
        if mnote and "görev" not in t:
            if sensitive: return "Hassas bilgileri (şifre/token/anahtar) kaydetmiyorum."
            kind = "PREFERENCE" if any(x in t for x in ["tercih", "sever", "hoşlan", "sevmem"]) else "FACT"
            self.memory.add(kind, mnote.group(1).strip()[:300])
            return f"Kaydettim ({kind})."
        mtask = re.search(r"(?:görev ekle|task ekle|todo ekle)\s*[:\"]*(.+)", text, re.I)
        if mtask:
            if sensitive: return "Hassas bilgileri kaydetmiyorum."
            self.memory.add("TASK", mtask.group(1).strip()[:300])
            return "Görevi TASK listesine ekledim."
        if any(x in t for x in ["hatırlat", "görevlerim", "todo listem", "ne yapmam gerektiğini", "bugün ne yapmam"]):
            rows = [x for x in self.memory.recent(100) if x[0] == "TASK"]
            if not rows: return "Kayıtlı görev yok. 'görev ekle: ...' diyerek ekleyebilirsin."
            return "Görevlerin:\n" + "\n".join(f"- {c[1]}" for c in rows[:8])
        if "cihaz bilgisi" in t and "kaydet" in t:
            from app.telemetry.system_stats import get_system_stats
            s = get_system_stats()
            self.memory.add("DEVICE", f"{s['platform']} · RAM {s['ram_total_gb']}GB")
            return "Cihaz bilgisi kaydedildi (DEVICE)."
        if any(x in t for x in ["az önce ne sordum", "son isteğim ne", "son isteğimi hatırla"]):
            rows=self._history(20); users=[x["content"] for x in rows if x["role"]=="user"]
            if len(users)>=2: return f"Son isteğiniz şuydu: {users[-2]}"
            if users: return f"Bu konuşmadaki isteğiniz: {users[-1]}"
        if "kısa ve sonra ayrıntılı" in t or "kısa sonra ayrıntılı" in t:
            return "Kısa: Ultron, görevleri anlayıp gerçekleştiren bir Windows AI asistanıdır.\nAyrıntılı: Ultron; sistem telemetrisi, uygulama kontrolü, web, dosya işlemleri, ekran görüntüsü, hafıza ve çok adımlı görev yürütme gibi yetenekleri tek bir agent çekirdeğinde birleştirmeyi amaçlar."
        if intent=="calculate":
            try: return f"Sonuç: {self.executor.execute([('calculate',{'text':text})],approved=True)[0]}"
            except Exception as e: return f"Hesaplama yapılamadı: {e}"
        if intent=="system_status":
            r=self.executor.execute([('system_status',{})],approved=True)[0]; g=(r.get('gpus') or [{}])[0]
            return f"CPU {r.get('cpu_percent')}%, RAM {r.get('ram_percent')}%, Disk {r.get('disk_percent')}%, GPU {g.get('util_percent',0)}%."
        if intent=="self_diagnostic":
            try:
                r=self.executor.execute([("self_diagnostic",{})],approved=True)[0]
                return r.get("report") if isinstance(r,dict) else str(r)
            except Exception as e:
                return f"Öz-teşhis çalıştırılamadı: {e}"
        if intent=="ollama_status":
            r=self.executor.execute([('ollama_status',{})],approved=True)[0]
            if r.get('connected'): return f"Ollama bağlı. Model: {self.brain.model}. Kurulu modeller: {', '.join(r.get('models') or []) or 'yok'}. Seçili model {'kurulu' if r.get('model_installed') else 'kurulu değil'}."
            return f"Ollama bağlantısı başarısız: {r.get('error','bilinmeyen hata')}"
        if intent=="audit":
            rows=self.audit.recent(10); return "\n".join(rows) if rows else "Henüz işlem kaydı yok."
        if intent=="capabilities":
            if "modelle" in t: return f"Aktif model: {self.brain.model}. Provider: {self.brain.provider}."
            return "Aktif araçlar: " + ", ".join(self.registry.names())
        if intent=="screen":
            try:
                p=self.executor.execute([('capture_screen',{})],approved=True)[0]
                if any(x in t for x in ["ne var","ekranımda","ekrandaki","ekranı gör"]):
                    o=self.executor.execute([('screen_ocr',{'path':p})],approved=True)[0]
                    if o.get('text'): return f"Ekran görüntüsü alındı: {p}\nOCR: {o['text'][:3000]}"
                return f"Ekran görüntüsü alındı: {p}"
            except Exception as e: return f"Ekran görüntüsü alınamadı: {e}"
        if intent=="open_app" and "chrome" in t and "google" not in t:
            try: return self.executor.execute([('open_application',{'name':'chrome'})],approved=True)[0]
            except Exception as e: return f"Chrome açılamadı: {e}"
        if intent=="file_task":
            if "indirilenler" in t: root=str(__import__('pathlib').Path.home()/"Downloads")
            elif "masaüst" in t: root=str(__import__('pathlib').Path.home()/"Desktop")
            else: root=None
            if "ultron projesi" in t:
                p=self.executor.execute([('find_project',{'start':root,'name_hint':'Ultron'})],approved=True)[0]
                return f"Ultron projesi: {p}" if p else "Ultron projesi bulunamadı."
            if root:
                items=self.executor.execute([('list_directory',{'root':root})],approved=True)[0]
                return "\n".join(f"{'[Klasör]' if x['type']=='folder' else '[Dosya]'} {x['name']}" for x in items[:80]) or "Klasör boş."
        if intent=="run_tests":
            return self._run_project_tests()
        if intent=="web_search" and "chrome" not in t:
            q=self._extract_search_query(text); return self.executor.execute([('open_url',{'url':'https://www.google.com/search?q='+quote_plus(q)})],approved=True)[0]
        if intent=="speak":
            answer="Elbette. Sesli yanıt sistemi hazır."; self.tts.speak(answer); return answer
        return None
    def _extract_search_query(self,text):
        t=text.strip(); low=t.lower()
        for marker in ["google'da","google da","google'de","google de"]:
            if marker in low:
                q=t[low.find(marker)+len(marker):].strip(" :.")
                return re.sub(r"^(ara|arama yap|araştır)\s*","",q,flags=re.I).strip(" .")
        q=re.sub(r".*?(?:internette|web'de)\s*", "", t, flags=re.I)
        return re.sub(r"\s*(ara|arama yap)$","",q,flags=re.I).strip(" .")
    def _browser(self,text):
        low=text.lower()
        if "chrome" not in low or "google" not in low: return None
        q=self._extract_search_query(text)
        # Remove trailing command phrases that are not part of the query.
        q=re.sub(r"\s+ve\s+(?:ilk sonucu|sonucu bana bildir|araştır|ara)$","",q,flags=re.I).strip()
        if not q: return None
        url='https://www.google.com/search?q='+quote_plus(q)
        result=self.executor.execute([('open_url',{'url':url})],approved=True)[0]
        return f"{result} Google'da '{q}' araması başlatıldı."
    def _run_project_tests(self):
        import subprocess, pathlib
        p=self.executor.execute([('find_project',{'start':str(pathlib.Path.home()/"Downloads"),'name_hint':'Ultron'})],approved=True)[0]
        if not p: return "Ultron projesi bulunamadı; test çalıştırılamadı."
        try:
            r=subprocess.run([__import__('sys').executable,'-m','pytest','-q'],cwd=p,capture_output=True,text=True,timeout=180)
            return f"Test sonucu: {r.stdout.strip() or r.stderr.strip()}"
        except Exception as e: return f"Test çalıştırılamadı: {e}"
    def _deterministic_browser_task(self,text):
        return self._browser(text)

    def _extract_tool_calls(self,message):
        native=message.get("tool_calls") or []
        if native: return native
        c=(message.get("content") or "").strip(); candidates=[c]
        a,b=c.find("{"),c.rfind("}")
        if a>=0 and b>a: candidates.append(c[a:b+1])
        for x in candidates:
            try:o=json.loads(x)
            except Exception:continue
            if isinstance(o,dict) and o.get("name"):
                args=o.get("arguments",{}); args=json.loads(args) if isinstance(args,str) else args
                return [{"function":{"name":o["name"],"arguments":args or {}}}]
        return []
    def _redact(self,text):
        if self.redact_fn is None: return text
        try: return self.redact_fn(str(text))
        except Exception: return text

    def handle(self,text,approved=False):
        text=self._redact(text)  # secrets never reach prompts/logs
        self._save("USER",text); self.audit.write("USER",text)
        # V7: emotion-aware persona (override commands + hint injection)
        if self.adaptive:
            self.adaptive.handle_command(text)
            emo=emotion_analyze(text=text)
            hint=self.adaptive.adapt(emo["state"], emo["confidence"])
            self.persona.set_emotion_hint(hint, emo["state"])
        direct=self._direct(text)
        if direct:
            direct=self._boss_hitap(direct)
            self._save("ULTRON",direct); self.audit.write("ASSISTANT",direct); return direct
        browser=self._browser(text)
        if browser:
            self._save("ULTRON",browser); return browser
        history=self._history(24)
        semantic=(self.semantic_memory.context(text,5) if self.semantic_memory else "")
        base=("Sen Ultron'sın, JARVIS tarzı Windows masaüstü agentısın. Türkçe konuş. "
                "Gerçek bir aracı çalıştırmadan bir işlemi yaptığını asla iddia etme. "
                "Araç başarısızsa açıkça başarısız olduğunu söyle. Bağlamdaki önceki mesajları kullan. "
                "Kısa cevap isteğinde kısa, ayrıntılı isteğinde ayrıntılı cevap ver. "
                "İlgili uzun dönem hafıza varsa onu bağlam olarak kullan.")
        system=self.persona.compose(base, len(history)) + ("İlgili hafıza:\n"+semantic if semantic else "")
        if callable(self.world_fn):
            try:
                world_block=self.world_fn()
                if world_block: system=system+"\n"+world_block
            except Exception: pass
        messages=[{"role":"system","content":system}]+history[-20:]+[{"role":"user","content":text}]
        last_error=None
        for _ in range(self.max_steps):
            try:
                if self.router is not None:
                    from app.core.model_router import TaskType as _TT
                    response=self.router.chat(_TT.GENERAL,messages,tools=self.registry.ollama_tools(include_dangerous=True))
                else:
                    response=self.brain.chat(messages,tools=self.registry.ollama_tools(include_dangerous=True))
            except Exception as e:
                last_error=e; break
            msg=response.get("message",{}); calls=self._extract_tool_calls(msg)
            if not calls:
                answer=self._persona_finalize((msg.get("content") or "").strip() or "Bunu tamamlayamadım.")
                self._save("ULTRON",answer); return answer
            messages.append(msg)
            for call in calls:
                fn=call.get("function",{}); name=fn.get("name",""); args=fn.get("arguments") or {}
                try:
                    raw=self.executor.execute([(name,args)],approved=approved)[0]
                    messages.append({"role":"tool","content":self._redact(json.dumps({"success":True,"result":raw},ensure_ascii=False,default=str))})
                except Exception as e:
                    last_error=e; messages.append({"role":"tool","content":json.dumps({"success":False,"error":self._redact(str(e))},ensure_ascii=False)})
        if last_error: answer=f"İşlemi tamamlayamadım: {last_error}"
        else: answer="Görevi güvenli adım sınırında tamamlayamadım."
        answer=self._persona_finalize(answer)
        self._save("ULTRON",answer); return answer

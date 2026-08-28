import re, json
from pathlib import Path
from app.core.brain import Brain
from app.core.tool_registry import ToolRegistry
from app.agent.executor import Executor
from app.agent.agent import Agent
from app.agent.planner import Planner
from app.memory.sqlite_memory import Memory
from app.memory.semantic_memory import SemanticMemory
from app.security.permissions import PermissionManager
from app.security.audit import AuditLog
from app.tools.system_tools import system_status
from app.tools.windows_tools import open_application, open_url, close_application, open_file, open_folder
from app.tools.file_tools import find_files, read_text, write_text, list_directory, find_project
from app.tools.browser_tools import search_web
from app.tools.diagnostic_tools import run_diagnostic
from app.tools.calculator import calculate
from app.tools.ollama_tools import ollama_status
from app.vision.screen import capture_screen, screen_ocr
from app.vision.vision_llm import VisionLLM
from app.voice.tts import TextToSpeech
from app.voice.live_voice import LiveVoice
from app.automation.gui import GUIAutomation
from app.code_agent.code_agent import CodeAgent
from app.code_intel.analyzer import CodeIntel
from app.proactive.monitor import ProactiveMonitor
from app.core import doctor as doctor_mod
from app.telemetry.system_stats import get_system_stats

class UltronRuntime:
    def __init__(self, settings_path='config/settings.json'):
        self.root=Path.cwd().resolve()
        self.settings_path=settings_path
        self.settings=json.loads(Path(settings_path).read_text(encoding='utf-8'))
        # V15.1 integration: per-request approval flag (set by the bridge).
        # self_repair(apply=True) and any PermissionManager-gated tool honour it.
        self.approved=False
        self.memory=Memory(); self.semantic_memory=SemanticMemory(self.memory); self.audit=AuditLog(); self.permissions=PermissionManager(self.settings)
        self.registry=ToolRegistry(); self.brain=Brain(self.settings); self.tts=TextToSpeech()
        self.vision_llm=VisionLLM(self.brain,self.settings); self.gui=GUIAutomation(); self.code_agent=CodeAgent(self.brain,self.root); self.code_intel=CodeIntel(str(self.root)); self._register_tools()
        self.executor=Executor(self.registry,self.permissions,self.audit)
        self.planner=Planner(self.brain,self.registry)
        # V7: emotion + semantic v2 (before Agent — it consumes adaptive)
        from app.agent.adaptive_persona import AdaptivePersona
        from app.emotion.emotion_engine import EmotionLog
        from app.memory.semantic_memory_v2 import SemanticMemoryV2
        self.adaptive=AdaptivePersona()
        self.emotion_log=EmotionLog()
        self.semv2=SemanticMemoryV2(self.memory)
        self.agent=Agent(self.brain,self.executor,self.memory,self.registry,self.settings,self.audit,self.tts,semantic_memory=self.semantic_memory,planner=self.planner,vision_llm=self.vision_llm,adaptive=self.adaptive)
        self.live_voice=LiveVoice(self.agent,self.tts,self.settings)
        self.proactive=ProactiveMonitor(self.settings,self._proactive_event)

    def self_diagnostic(self):
        """Run a read-only health scan and classify only real errors."""
        import time
        try:
            stats = get_system_stats()
        except Exception as exc:
            stats = {"error": str(exc)}

        data_dir = self.root / "data"
        db_paths = [str(x) for x in data_dir.rglob("*.db")] if data_dir.exists() else []
        doctor = doctor_mod.run_doctor({
            "ollama_host": self.brain.base_url,
            "db_paths": db_paths,
            "rules_path": str(self.root / "config/security/master_rules.json"),
            "ports": (8000, 5173, 5174),
            "allow_busy_ports": True,
        }, persona=self.agent.persona)

        try:
            ollama = ollama_status(self.brain.base_url, self.brain.model)
        except Exception as exc:
            ollama = {"connected": False, "error": str(exc), "models": []}

        checks = {
            "agent": self.agent is not None,
            "tools": bool(self.registry.names()),
            "memory": self.memory is not None,
            "semantic_memory": self.semantic_memory is not None,
            "planner": self.planner is not None,
            "neural_voice": self.tts.backend() == "edge-tts-neural",
            "vision": self.vision_llm is not None,
            "proactive": self.proactive is not None,
        }
        components = {k: {"status": "OK" if v else "WARN"} for k, v in checks.items()}
        components["ollama"] = {
            "status": "OK" if ollama.get("connected") else "WARN",
            "model": self.brain.model,
            "installed": bool(ollama.get("model_installed")),
            "models": ollama.get("models") or [],
        }
        components["doctor"] = {
            "status": {"PASS": "OK", "WARN": "WARN", "FAIL": "ERROR"}.get(doctor.get("overall"), "ERROR"),
            "overall": doctor.get("overall"),
            "summary": doctor.get("summary"),
            "sections": doctor.get("sections", {}),
        }

        # Audit lines are structured as timestamp | EVENT | detail.  Only
        # explicit error events count; user/assistant text containing the word
        # "hata" must never become a false-positive diagnostic error.
        recent = self.audit.recent(120)
        error_events = {"TOOL_ERROR", "ERROR", "EXCEPTION", "FAIL", "FAILED", "BAŞARISIZ", "HATA", "RUNTIME_ERROR"}
        error_lines = []
        for line in recent:
            parts = [x.strip() for x in line.split("|", 2)]
            event = parts[1].upper() if len(parts) >= 2 else ""
            if event in error_events or any(event.endswith("_ERROR") for _ in [0]):
                error_lines.append(line)
        components["recent_errors"] = {
            "status": "WARN" if error_lines else "OK",
            "count": len(error_lines),
            "latest": error_lines[:5],
        }

        cpu = stats.get("cpu_percent") if isinstance(stats, dict) else None
        ram = stats.get("ram_percent") if isinstance(stats, dict) else None
        disk = stats.get("disk_percent") if isinstance(stats, dict) else None
        # Current resource pressure is a warning; historical spikes belong to
        # telemetry/history and should not make the current system unhealthy.
        hw_status = "WARN" if any(v is not None and v >= 95 for v in (cpu, ram, disk)) else "OK"
        components["hardware"] = {
            "status": hw_status, "cpu_percent": cpu, "ram_percent": ram,
            "disk_percent": disk, "gpus": stats.get("gpus", []) if isinstance(stats, dict) else [],
        }

        statuses = [v.get("status") for v in components.values()]
        overall = "ERROR" if "ERROR" in statuses else ("WARN" if "WARN" in statuses else "OK")
        warnings = [k for k, v in components.items() if v.get("status") == "WARN"]
        errors = [k for k, v in components.items() if v.get("status") == "ERROR"]
        headline = {
            "OK": "Tüm çekirdek kontroller geçti.",
            "WARN": "Sistem çalışıyor; bazı bölümlerde uyarı var.",
            "ERROR": "Kritik hata tespit edildi.",
        }[overall]
        report = (
            f"Boss, tam öz-teşhis tamamlandı. Genel durum: {overall}. {headline} "
            f"CPU {cpu if cpu is not None else 'N/A'}%, RAM {ram if ram is not None else 'N/A'}%, "
            f"Disk {disk if disk is not None else 'N/A'}. Neural ses: {'OK' if checks['neural_voice'] else 'WARN'}. "
            f"Ollama: {'OK' if ollama.get('connected') else 'WARN'}. "
            f"Son gerçek hata taraması: {len(error_lines)}. "
            f"Uyarılar: {', '.join(warnings) if warnings else 'yok'}. "
            f"Hatalar: {', '.join(errors) if errors else 'yok'}."
        )
        result = {"ok": overall != "ERROR", "overall": overall, "report": report,
                  "ts": time.time(), "components": components, "doctor": doctor, "system": stats}
        self.audit.write("SELF_DIAGNOSTIC", f"overall={overall} warnings={len(warnings)} errors={len(errors)}")
        return result

    def _register_tools(self):
        reg=self.registry
        reg.register('system_status',system_status,'Gerçek sistem telemetrisi getirir.')
        reg.register('self_diagnostic',self.self_diagnostic,'ULTRON\'un tüm çekirdek modüllerini salt-okunur biçimde teşhis eder.')
        reg.register('open_application',open_application,'Windows uygulaması açar.',{'type':'object','properties':{'name':{'type':'string'}},'required':['name']})
        reg.register('open_url',open_url,'URLyi Chrome ile açar.',{'type':'object','properties':{'url':{'type':'string'}},'required':['url']})
        reg.register('search_web',search_web,'Web araması açar.',{'type':'object','properties':{'query':{'type':'string'}},'required':['query']})
        reg.register('calculate',calculate,'Güvenli matematik hesaplar.',{'type':'object','properties':{'text':{'type':'string'}},'required':['text']})
        reg.register('list_directory',list_directory,'Klasör içeriğini listeler.',{'type':'object','properties':{'root':{'type':'string'}},'required':['root']})
        reg.register('find_files',find_files,'Dosya arar.',{'type':'object','properties':{'root':{'type':'string'},'pattern':{'type':'string'}},'required':['root','pattern']})
        reg.register('find_project',find_project,'Ultron projesini bulur.',{'type':'object','properties':{'start':{'type':'string'},'name_hint':{'type':'string'}},'required':[]})
        reg.register('read_text',read_text,'Metin/kod dosyası okur.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('capture_screen',capture_screen,'Gerçek ekran görüntüsü alır.')
        reg.register('screen_ocr',screen_ocr,'Ekran OCR yapar.',{'type':'object','properties':{'path':{'type':'string'}},'required':[]})
        reg.register('analyze_screen',lambda path,prompt='Ekranda ne görüyorsun?':self.vision_llm.analyze(path,prompt),'Vision LLM ile ekranı analiz eder.',{'type':'object','properties':{'path':{'type':'string'},'prompt':{'type':'string'}},'required':['path']})
        reg.register('run_diagnostic',run_diagnostic,'Salt-okunur Windows tanılama çalıştırır.',{'type':'object','properties':{'command':{'type':'string'}},'required':['command']})
        reg.register('ollama_status',lambda:ollama_status(self.brain.base_url,self.brain.model),'Ollama bağlantısını kontrol eder.')
        reg.register('make_plan',lambda goal:self.planner.make_plan(goal),'Karmaşık görev için yapılandırılmış plan üretir.',{'type':'object','properties':{'goal':{'type':'string'}},'required':['goal']})
        reg.register('analyze_project',lambda paths=None:self.code_agent.analyze(paths),'Kod tabanını analiz eder.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}}},'required':[]})
        reg.register('propose_code_patch',lambda paths=None:self.code_agent.propose_patch(paths),'Kod değişikliği önerir ama uygulamaz.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}}},'required':[]})
        # INTEGRATION ADAPTATION: apply=True is only honoured when the current
        # request carries explicit approval (runtime.approved, set by the bridge).
        reg.register('self_repair',lambda paths=None,apply=False:self.code_agent.self_repair(paths,apply=apply and self.approved),'Kodu analiz eder, patch önerir ve istenirse onaylı olarak uygular.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}},'apply':{'type':'boolean'}},'required':[]},dangerous=True)
        reg.register('write_text',write_text,'Metin dosyası yazar; onay gerekir.',{'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},'required':['path','content']},dangerous=True)
        reg.register('gui_click',self.gui.click,'Ekranda koordinata tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_type',self.gui.type_text,'Klavye ile metin yazar; onay gerekir.',{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},dangerous=True)
        reg.register('gui_press',self.gui.press,'Klavye tuşuna basar; onay gerekir.',{'type':'object','properties':{'key':{'type':'string'}},'required':['key']},dangerous=True)
        reg.register('gui_hotkey',self.gui.hotkey,'Klavye kısayolu çalıştırır; onay gerekir.',{'type':'object','properties':{'keys':{'type':'array','items':{'type':'string'}}},'required':['keys']},dangerous=True)
        reg.register('gui_locate_and_click',self.gui.locate_and_click,'Ekran görüntüsü şablonunu bulup tıklar; onay gerekir.',{'type':'object','properties':{'image':{'type':'string'},'confidence':{'type':'number'}},'required':['image']},dangerous=True)
        reg.register('apply_code_patch',lambda patch:self.code_agent.apply_patch(patch),'Önerilen kod patchini uygular; onay gerekir.',{'type':'object','properties':{'patch':{'type':'object'}},'required':['patch']},dangerous=True)
        reg.register('code_intel_analyze',lambda target='all':self.code_intel.analyze(target),'Projeyi statik analiz eder (bug/güvenlik/duplicate/unused/TODO).',{'type':'object','properties':{'target':{'type':'string'}},'required':[]})
        reg.register('code_intel_explain',lambda path:self.code_intel.explain_file(path),'Bir dosyanın ne yaptığını özetler.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('gui_double_click',self.gui.double_click,'Çift tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_right_click',self.gui.right_click,'Sağ tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_scroll',self.gui.scroll,'Kaydırır; onay gerekir.',{'type':'object','properties':{'amount':{'type':'integer'},'x':{'type':'integer'},'y':{'type':'integer'}},'required':['amount']},dangerous=True)
        reg.register('find_window',self.gui.find_window,'Başlığa göre pencere arar.',{'type':'object','properties':{'title':{'type':'string'}},'required':['title']})
        reg.register('focus_window',self.gui.focus_window,'Pencereyi öne getirir.',{'type':'object','properties':{'title':{'type':'string'}},'required':['title']})
        reg.register('close_application',close_application,'Uygulamayı kapatır; onay gerekir.',{'type':'object','properties':{'name':{'type':'string'}},'required':['name']},dangerous=True)
        reg.register('open_file',open_file,'Dosyayı varsayılan uygulamayla açar.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('open_folder',open_folder,'Klasörü açar.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        # Keep the registry and PermissionManager in sync even when settings add a new protected action.
        protected = set(self.settings.get('security', {}).get('require_confirmation_for', []))
        for name in protected:
            if name in reg._tools:
                reg._tools[name]['dangerous'] = True

    def _split_batch_requests(self,text):
        raw=text.replace('\r\n','\n').replace('\r','\n').strip()
        if not raw:return []
        lines=[ln.strip() for ln in raw.split('\n') if ln.strip()]
        line_re=re.compile(r'^\s*(\d{1,4})\s*[\.\):\-]\s*(.+?)\s*$'); items=[]
        for line in lines:
            m=line_re.match(line)
            if m:
                body=re.sub(r'^(?:\*\*)?(?:Sen|Ultron)(?:\*\*)?\s*:\s*','',m.group(2).strip(),flags=re.I);items.append(body)
        if len(items)>=2:return items
        flat=re.sub(r'\s+',' ',raw); fr=re.compile(r'(?<!\d)(\d{1,4})\s*[\.\):\-]\s*(?:\*\*)?(?:Ultron|Sen)(?:\*\*)?\s*:\s*',re.I); ms=list(fr.finditer(flat))
        if len(ms)>=2:
            out=[]
            for i,m in enumerate(ms):
                body=flat[m.end():(ms[i+1].start() if i+1<len(ms) else len(flat))].strip()
                if body:out.append(body)
            return out
        return [raw]
    def ask(self,text,approved=False):
        self.approved=bool(approved)
        req=self._split_batch_requests(text)
        if len(req)<=1:return self.agent.handle(text,approved=approved)
        results=[]
        for i,r in enumerate(req,1):
            try:a=self.agent.handle(r,approved=approved)
            except Exception as e:a=f'HATA — {e}'
            results.append(f'{i}. Soru: {r}\nUltron: {a}')
        return f'Toplam {len(req)} soru işlendi.\n\n'+'\n\n'.join(results)
    def reload_config(self):
        """Hot-reload settings.json without restart (persona mode, thresholds)."""
        self.settings=json.loads(Path(self.settings_path).read_text(encoding='utf-8'))
        self.agent.persona.mode=self.settings.get('persona_guard_mode','reframe')
        pm=self.settings.get('proactive',{})
        self.proactive.ram_limit=float(pm.get('ram_warning_percent',90))
        self.proactive.cpu_limit=float(pm.get('cpu_warning_percent',95))
        self.proactive.disk_limit=float(pm.get('disk_warning_percent',95))
        return self.settings
    def start_proactive(self):self.proactive.start()
    def stop_proactive(self):self.proactive.stop()
    def start_live_voice(self):
        # V2 stream+VAD path when deps exist; else legacy fixed window
        try:
            import sounddevice, faster_whisper  # noqa: F401
            from app.voice.voice_stack_v2 import LiveVoiceV2, VoiceStackV2
            stack=getattr(self,"live_stack",None) or VoiceStackV2()
            self.live_stack=stack
            self.live_voice_v2=LiveVoiceV2(self.agent,self.tts,self.settings,stack)
            import threading
            threading.Thread(target=self.live_voice_v2.run,daemon=True).start()
        except Exception:
            self.live_voice.start()
    def stop_live_voice(self):
        v2=getattr(self,"live_voice_v2",None)
        if v2: v2.stop()
        self.live_voice.stop()
    def _proactive_event(self,message):self.memory.add('proactive',message);self.audit.write('PROACTIVE',message);print(f'[ULTRON] {message}')

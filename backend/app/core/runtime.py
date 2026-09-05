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
from app.tools.file_tools import find_files, read_text, write_text, list_directory, find_project, copy_path, move_path, rename_path, create_folder, delete_path
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
from app.core.self_awareness import SelfAwareness

class UltronRuntime:
    def __init__(self, settings_path='config/settings.json'):
        self.root=Path.cwd().resolve()
        self.settings_path=settings_path
        self.settings=json.loads(Path(settings_path).read_text(encoding='utf-8'))
        self.approved=False
        from app.security.vault import CredentialVault
        from app.security.sandbox import FilesystemSandbox, sandboxed_read_text, sandboxed_write_text, sandboxed_list_directory, sandboxed_find_files
        self.vault=CredentialVault(audit=None)
        self._browser_agent=None
        try:
            from app.voice.wake import WakeWordManager
            self.wake_manager=WakeWordManager(self.settings,vault=self.vault)
        except Exception:
            self.wake_manager=None
        self.sandbox=FilesystemSandbox(self.settings, workspace_root=str(Path.cwd()))
        self.memory=Memory(redact_fn=self.vault.redact); self.semantic_memory=SemanticMemory(self.memory); self.audit=AuditLog(extra_values_fn=self.vault.all_values); self.permissions=PermissionManager(self.settings)
        self.registry=ToolRegistry(); self.brain=Brain(self.settings); self.tts=TextToSpeech()
        from app.core.model_router import ModelRouter
        self._models_cache={"ts":0.0,"models":[]}
        self.router=ModelRouter(self.brain,self.settings,self._cached_models)
        self.vision_llm=VisionLLM(self.brain,self.settings); self.gui=GUIAutomation(); self.code_agent=CodeAgent(self.brain,self.root); self.code_intel=CodeIntel(str(self.root)); self._register_tools()
        self.executor=Executor(self.registry,self.permissions,self.audit)
        if getattr(self,'skills',None) is not None: self.skills.executor=self.executor
        self.planner=Planner(self.brain,self.registry,semantic_memory=self.semantic_memory)
        from app.agent.adaptive_persona import AdaptivePersona
        from app.emotion.emotion_engine import EmotionLog
        from app.memory.semantic_memory_v2 import SemanticMemoryV2
        self.adaptive=AdaptivePersona(); self.emotion_log=EmotionLog(); self.semv2=SemanticMemoryV2(self.memory)
        self.world_context_fn=None
        self.agent=Agent(self.brain,self.executor,self.memory,self.registry,self.settings,self.audit,self.tts,semantic_memory=self.semantic_memory,planner=self.planner,vision_llm=self.vision_llm,adaptive=self.adaptive,router=self.router,world_fn=lambda:self.world_context_fn,redact_fn=self.vault.redact)
        self.self_awareness=SelfAwareness(registry=self.registry,doctor=doctor_mod,tts=self.tts,brain=self.brain)
        self.live_voice=LiveVoice(self.agent,self.tts,self.settings)
        self.proactive=ProactiveMonitor(self.settings,self._proactive_event)

    def _cached_models(self):
        import time as _t
        now=_t.time()
        if now-self._models_cache["ts"]>30:
            try: self._models_cache["models"]=(ollama_status(self.brain.base_url,self.brain.model) or {}).get("models") or []
            except Exception: self._models_cache["models"]=[]
            self._models_cache["ts"]=now
        return self._models_cache["models"]

    def self_awareness_report(self):
        return self.self_awareness.report()

    def self_diagnostic(self):
        import time
        try: stats=get_system_stats()
        except Exception as exc: stats={"error":str(exc)}
        data_dir=self.root/'data'; db_paths=[str(x) for x in data_dir.rglob('*.db')] if data_dir.exists() else []
        doctor=doctor_mod.run_doctor({'ollama_host':self.brain.base_url,'db_paths':db_paths,'rules_path':str(self.root/'config/security/master_rules.json'),'ports':(8000,5173,5174),'allow_busy_ports':True},persona=self.agent.persona)
        try: ollama=ollama_status(self.brain.base_url,self.brain.model)
        except Exception as exc: ollama={'connected':False,'error':str(exc),'models':[]}
        voice_backend=self.tts.backend()
        checks={'agent':self.agent is not None,'tools':bool(self.registry.names()),'memory':self.memory is not None,'semantic_memory':self.semantic_memory is not None,'planner':self.planner is not None,'model_router':self.router is not None,'local_voice':voice_backend in ('piper-local','espeak-ng-local'),'vision':self.vision_llm is not None,'proactive':self.proactive is not None}
        components={k:{'status':'OK' if v else 'WARN'} for k,v in checks.items()}
        components['local_voice']['backend']=voice_backend
        components['ollama']={'status':'OK' if ollama.get('connected') else 'WARN','model':self.brain.model,'installed':bool(ollama.get('model_installed')),'models':ollama.get('models') or []}
        components['doctor']={'status':{'PASS':'OK','WARN':'WARN','FAIL':'ERROR'}.get(doctor.get('overall'),'ERROR'),'overall':doctor.get('overall'),'summary':doctor.get('summary'),'sections':doctor.get('sections',{})}
        recent=self.audit.recent(120); error_events={'TOOL_ERROR','ERROR','EXCEPTION','FAIL','FAILED','BAŞARISIZ','HATA','RUNTIME_ERROR'}; error_lines=[]
        for line in recent:
            parts=[x.strip() for x in line.split('|',2)]; event=parts[1].upper() if len(parts)>=2 else ''
            if event in error_events or event.endswith('_ERROR'): error_lines.append(line)
        components['recent_errors']={'status':'WARN' if error_lines else 'OK','count':len(error_lines),'latest':error_lines[:5]}
        cpu=stats.get('cpu_percent') if isinstance(stats,dict) else None; ram=stats.get('ram_percent') if isinstance(stats,dict) else None; disk=stats.get('disk_percent') if isinstance(stats,dict) else None
        components['hardware']={'status':'WARN' if any(v is not None and v>=95 for v in (cpu,ram,disk)) else 'OK','cpu_percent':cpu,'ram_percent':ram,'disk_percent':disk,'gpus':stats.get('gpus',[]) if isinstance(stats,dict) else []}
        statuses=[v.get('status') for v in components.values()]; overall='ERROR' if 'ERROR' in statuses else ('WARN' if 'WARN' in statuses else 'OK'); warnings=[k for k,v in components.items() if v.get('status')=='WARN']; errors=[k for k,v in components.items() if v.get('status')=='ERROR']
        headline={'OK':'Tüm çekirdek kontroller geçti.','WARN':'Sistem çalışıyor; bazı bölümlerde uyarı var.','ERROR':'Kritik hata tespit edildi.'}[overall]
        report=f"Boss, tam öz-teşhis tamamlandı. Genel durum: {overall}. {headline} CPU {cpu if cpu is not None else 'N/A'}%, RAM {ram if ram is not None else 'N/A'}%, Disk {disk if disk is not None else 'N/A'}. Lokal ses: {voice_backend or 'UNAVAILABLE'}. Ollama: {'OK' if ollama.get('connected') else 'WARN'}. Son gerçek hata taraması: {len(error_lines)}. Uyarılar: {', '.join(warnings) if warnings else 'yok'}. Hatalar: {', '.join(errors) if errors else 'yok'}."
        result={'ok':overall!='ERROR','overall':overall,'report':report,'ts':time.time(),'components':components,'doctor':doctor,'system':stats}; self.audit.write('SELF_DIAGNOSTIC',f'overall={overall} warnings={len(warnings)} errors={len(errors)}'); return result

    def _register_tools(self):
        reg=self.registry
        reg.register('system_status',system_status,'Gerçek sistem telemetrisi getirir.')
        reg.register('self_diagnostic',self.self_diagnostic,"ULTRON'un tüm çekirdek modüllerini salt-okunur biçimde teşhis eder.")
        reg.register('self_awareness',self.self_awareness_report,"ULTRON'un gerçek yeteneklerini ve mevcut yerel durumunu salt-okunur bildirir.")
        reg.register('open_application',open_application,'Windows uygulaması açar.',{'type':'object','properties':{'name':{'type':'string'}},'required':['name']})
        reg.register('open_url',open_url,'URLyi Chrome ile açar.',{'type':'object','properties':{'url':{'type':'string'}},'required':['url']})
        reg.register('search_web',search_web,'Web araması açar.',{'type':'object','properties':{'query':{'type':'string'}},'required':['query']})
        reg.register('calculate',calculate,'Güvenli matematik hesaplar.',{'type':'object','properties':{'text':{'type':'string'}},'required':['text']})
        reg.register('list_directory',lambda root:sandboxed_list_directory(self.sandbox,root),'Klasör içeriğini listeler (sandbox içinde).',{'type':'object','properties':{'root':{'type':'string'}},'required':['root']})
        reg.register('find_files',lambda root,pattern:sandboxed_find_files(self.sandbox,root,pattern),'Dosya arar (sandbox içinde).',{'type':'object','properties':{'root':{'type':'string'},'pattern':{'type':'string'}},'required':['root','pattern']})
        reg.register('read_text',lambda path:sandboxed_read_text(self.sandbox,path),'Metin/kod dosyası okur (sandbox içinde).',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('write_text',lambda path,content:sandboxed_write_text(self.sandbox,path,content),'Metin dosyası yazar (sandbox + onay gerekir).',{'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},'required':['path','content']},dangerous=True)
        reg.register('copy_path',copy_path,'Dosya/klasör kopyalar; onay gerekir.',{'type':'object','properties':{'source':{'type':'string'},'destination':{'type':'string'}},'required':['source','destination']},dangerous=True)
        reg.register('move_path',move_path,'Dosya/klasör taşır; onay gerekir.',{'type':'object','properties':{'source':{'type':'string'},'destination':{'type':'string'}},'required':['source','destination']},dangerous=True)
        reg.register('rename_path',rename_path,'Dosya/klasör yeniden adlandırır; onay gerekir.',{'type':'object','properties':{'path':{'type':'string'},'new_name':{'type':'string'}},'required':['path','new_name']},dangerous=True)
        reg.register('create_folder',create_folder,'Klasör oluşturur; onay gerekir.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']},dangerous=True)
        reg.register('delete_path',delete_path,'Dosya/klasör siler; onay gerekir.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']},dangerous=True)
        br=lambda:self._get_browser()
        reg.register('browser_navigate',lambda url:br().navigate(url),'URL adresine gider.',{'type':'object','properties':{'url':{'type':'string'}},'required':['url']})
        reg.register('browser_read',lambda selector=None,limit=5000:br().read_text(selector,limit),'Açık sayfanın metnini okur.',{'type':'object','properties':{'selector':{'type':'string'},'limit':{'type':'number'}},'required':[]})
        reg.register('browser_find',lambda selector=None,text=None,limit=10:br().find_elements(selector,text,limit),'Sayfada element bulur.',{'type':'object','properties':{'selector':{'type':'string'},'text':{'type':'string'},'limit':{'type':'number'}},'required':[]})
        reg.register('browser_screenshot',lambda path=None:br().screenshot(path),'Sayfa ekran görüntüsü alır.',{'type':'object','properties':{'path':{'type':'string'}},'required':[]})
        reg.register('browser_verify',lambda url_contains=None,title_contains=None,selector_exists=None,text_contains=None:br().verify(url_contains,title_contains,selector_exists,text_contains),'Sayfayı doğrular.',{'type':'object','properties':{'url_contains':{'type':'string'},'title_contains':{'type':'string'},'selector_exists':{'type':'string'},'text_contains':{'type':'string'}},'required':[]})
        reg.register('browser_click',lambda selector:br().click(selector),'Elemente tıklar; onay gerekir.',{'type':'object','properties':{'selector':{'type':'string'}},'required':['selector']},dangerous=True)
        reg.register('browser_type',lambda selector,text,press_enter=False:br().type(selector,text,press_enter),'Alanı doldurur; onay gerekir.',{'type':'object','properties':{'selector':{'type':'string'},'text':{'type':'string'},'press_enter':{'type':'boolean'}},'required':['selector','text']},dangerous=True)
        reg.register('browser_select',lambda selector,value:br().select(selector,value),'Dropdown seçer; onay gerekir.',{'type':'object','properties':{'selector':{'type':'string'},'value':{'type':'string'}},'required':['selector']},dangerous=True)
        from app.tools.system_tools import process_list,process_info,process_kill,system_settings_view
        reg.register('process_list',lambda sort='cpu',limit=30,name=None:process_list(sort,limit,name),'Süreç listeler.')
        reg.register('process_info',lambda pid:process_info(pid),'Süreç detayı.',{'type':'object','properties':{'pid':{'type':'number'}},'required':['pid']})
        reg.register('process_kill',lambda pid:process_kill(pid),'Süreci öldürür; onay gerekir.',{'type':'object','properties':{'pid':{'type':'number'}},'required':['pid']},dangerous=True)
        reg.register('system_settings_view',lambda:system_settings_view(),'Sistem ayarlarını salt-okunur listeler.')
        from app.connectors import WeatherConnector,CalendarConnector
        self._weather=WeatherConnector(vault=self.vault); self._calendar=CalendarConnector(vault=self.vault)
        from app.connectors.email import EmailConnector
        self._email=EmailConnector(vault=self.vault,settings=self.settings)
        reg.register('weather_current',lambda city='Mersin':self._weather.current(city),'Güncel hava durumu.',{'type':'object','properties':{'city':{'type':'string'}},'required':['city']})
        reg.register('weather_forecast',lambda city='Mersin':self._weather.forecast(city),'Yarın hava tahmini.',{'type':'object','properties':{'city':{'type':'string'}},'required':['city']})
        reg.register('calendar_events',lambda days=7:self._calendar.events(days),'Yaklaşan takvim etkinlikleri.',{'type':'object','properties':{'days':{'type':'number'}},'required':[]})
        reg.register('email_inbox',lambda limit=10:self._email.inbox_read(limit),'Gelen kutusunu okur.',{'type':'object','properties':{'limit':{'type':'number'}},'required':[]})
        reg.register('email_search',lambda query,limit=10:self._email.search(query,limit=limit),'E-posta arar.',{'type':'object','properties':{'query':{'type':'string'}},'required':['query']})
        reg.register('email_draft',lambda to,subject,body:self._email.draft(to,subject,body),'E-posta taslağı oluşturur.',{'type':'object','properties':{'to':{'type':'string'},'subject':{'type':'string'},'body':{'type':'string'}},'required':['to','subject','body']})
        reg.register('email_send',lambda to,subject,body,attachments=None:self._email.send(to,subject,body,attachments),'E-posta gönderir; onay gerekir.',{'type':'object','properties':{'to':{'type':'string'},'subject':{'type':'string'},'body':{'type':'string'},'attachments':{'type':'array','items':{'type':'string'}}},'required':['to','subject','body']},dangerous=True)
        from app.skills import SkillRunner
        self.skills=SkillRunner(reg,executor=None,audit=self.audit,builtin_dir=str(Path(__file__).resolve().parents[2]/'config'/'skills'),user_dir='data/skills')
        reg.register('skill_run',lambda name,params=None:self.skills.run(name,params or {},approved=False,allow_dangerous=False),'Güvenli skill çalıştırır.',{'type':'object','properties':{'name':{'type':'string'},'params':{'type':'object'}},'required':['name']})
        reg.register('skill_run_dangerous',lambda name,params=None,approved=False:self.skills.run(name,params or {},approved=approved,allow_dangerous=True),'Dangerous skill çalıştırır; onay gerekir.',{'type':'object','properties':{'name':{'type':'string'},'params':{'type':'object'},'approved':{'type':'boolean'}},'required':['name']},dangerous=True)
        reg.register('skills_list',lambda:self.skills.list(),'Yüklü skilleri listeler.')
        reg.register('find_project',find_project,'Ultron projesini bulur.')
        reg.register('capture_screen',capture_screen,'Gerçek ekran görüntüsü alır.')
        reg.register('screen_ocr',screen_ocr,'Ekran OCR yapar.',{'type':'object','properties':{'path':{'type':'string'}},'required':[]})
        reg.register('analyze_screen',lambda path,prompt='Ekranda ne görüyorsun?':self.vision_llm.analyze(path,prompt),'Vision LLM ile ekranı analiz eder.',{'type':'object','properties':{'path':{'type':'string'},'prompt':{'type':'string'}},'required':['path']})
        from app.vision.analyze import analyze_image,diff_images,ocr_elements as _ocr_el
        reg.register('image_analyze',lambda path:analyze_image(path),'Görüntüyü analiz eder.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('image_diff',lambda path_a,path_b:diff_images(path_a,path_b),'İki görüntüyü karşılaştırır.',{'type':'object','properties':{'path_a':{'type':'string'},'path_b':{'type':'string'}},'required':['path_a','path_b']})
        reg.register('ocr_elements',lambda path:_ocr_el(path),'OCR metin ve koordinatları döner.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('run_diagnostic',run_diagnostic,'Salt-okunur Windows tanılama.',{'type':'object','properties':{'command':{'type':'string'}},'required':['command']})
        reg.register('ollama_status',lambda:ollama_status(self.brain.base_url,self.brain.model),'Ollama bağlantısını kontrol eder.')
        reg.register('make_plan',lambda goal:self.planner.make_plan(goal),'Karmaşık görev için plan üretir.',{'type':'object','properties':{'goal':{'type':'string'}},'required':['goal']})
        reg.register('analyze_project',lambda paths=None:self.code_agent.analyze(paths),'Kod tabanını analiz eder.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}}},'required':[]})
        reg.register('propose_code_patch',lambda paths=None:self.code_agent.propose_patch(paths),'Kod değişikliği önerir.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}}},'required':[]})
        reg.register('self_repair',lambda paths=None,apply=False:self.code_agent.self_repair(paths,apply=apply and self.approved),'Kodu analiz eder ve onaylı patch uygular.',{'type':'object','properties':{'paths':{'type':'array','items':{'type':'string'}},'apply':{'type':'boolean'}},'required':[]},dangerous=True)
        reg.register('gui_click',self.gui.click,'Koordinata tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_type',self.gui.type_text,'Metin yazar; onay gerekir.',{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},dangerous=True)
        reg.register('gui_press',self.gui.press,'Tuşa basar; onay gerekir.',{'type':'object','properties':{'key':{'type':'string'}},'required':['key']},dangerous=True)
        reg.register('gui_hotkey',self.gui.hotkey,'Kısayol çalıştırır; onay gerekir.',{'type':'object','properties':{'keys':{'type':'array','items':{'type':'string'}}},'required':['keys']},dangerous=True)
        reg.register('gui_locate_and_click',self.gui.locate_and_click,'Şablon bulup tıklar; onay gerekir.',{'type':'object','properties':{'image':{'type':'string'},'confidence':{'type':'number'}},'required':['image']},dangerous=True)
        reg.register('read_screen_elements',lambda:self.gui.read_screen_elements(),'Ekranı OCR ile okur.')
        reg.register('click_text',lambda text:self.gui.click_text(text),'OCR metnine tıklar; onay gerekir.',{'type':'object','properties':{'text':{'type':'string'}},'required':['text']},dangerous=True)
        reg.register('apply_code_patch',lambda patch:self.code_agent.apply_patch(patch),'Kod patchi uygular; onay gerekir.',{'type':'object','properties':{'patch':{'type':'object'}},'required':['patch']},dangerous=True)
        reg.register('code_intel_analyze',lambda target='all':self.code_intel.analyze(target),'Statik kod analizi.',{'type':'object','properties':{'target':{'type':'string'}},'required':[]})
        reg.register('code_intel_explain',lambda path:self.code_intel.explain_file(path),'Dosyayı açıklar.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('gui_double_click',self.gui.double_click,'Çift tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_right_click',self.gui.right_click,'Sağ tıklar; onay gerekir.',{'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y']},dangerous=True)
        reg.register('gui_scroll',self.gui.scroll,'Kaydırır; onay gerekir.',{'type':'object','properties':{'amount':{'type':'integer'},'x':{'type':'integer'},'y':{'type':'integer'}},'required':['amount']},dangerous=True)
        reg.register('find_window',self.gui.find_window,'Pencere arar.',{'type':'object','properties':{'title':{'type':'string'}},'required':['title']})
        reg.register('focus_window',self.gui.focus_window,'Pencereyi öne getirir.',{'type':'object','properties':{'title':{'type':'string'}},'required':['title']})
        reg.register('close_application',close_application,'Uygulamayı kapatır; onay gerekir.',{'type':'object','properties':{'name':{'type':'string'}},'required':['name']},dangerous=True)
        reg.register('open_file',open_file,'Dosyayı varsayılan uygulamayla açar.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        reg.register('open_folder',open_folder,'Klasörü açar.',{'type':'object','properties':{'path':{'type':'string'}},'required':['path']})
        protected=set(self.settings.get('security',{}).get('require_confirmation_for',[]))
        for name in protected:
            if name in reg._tools: reg._tools[name]['dangerous']=True

    def _split_batch_requests(self,text):
        raw=text.replace('\r\n','\n').replace('\r','\n').strip()
        if not raw:return []
        lines=[ln.strip() for ln in raw.split('\n') if ln.strip()]; line_re=re.compile(r'^\s*(\d{1,4})\s*[\.\):\-]\s*(.+?)\s*$'); items=[]
        for line in lines:
            m=line_re.match(line)
            if m:
                body=re.sub(r'^(?:\*\*)?(?:Sen|Ultron)(?:\*\*)?\s*:\s*','',m.group(2).strip(),flags=re.I); items.append(body)
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
        self.approved=bool(approved); req=self._split_batch_requests(text)
        if len(req)<=1:return self.agent.handle(text,approved=approved)
        results=[]
        for i,r in enumerate(req,1):
            try:a=self.agent.handle(r,approved=approved)
            except Exception as e:a=f'HATA — {e}'
            results.append(f'{i}. Soru: {r}\nUltron: {a}')
        return f'Toplam {len(req)} soru işlendi.\n\n'+'\n\n'.join(results)
    def reload_config(self):self.settings=json.loads(Path(self.settings_path).read_text(encoding='utf-8')); self.agent.persona.mode=self.settings.get('persona_guard_mode','reframe'); pm=self.settings.get('proactive',{}); self.proactive.ram_limit=float(pm.get('ram_warning_percent',90)); self.proactive.cpu_limit=float(pm.get('cpu_warning_percent',95)); self.proactive.disk_limit=float(pm.get('disk_warning_percent',95)); return self.settings
    def start_proactive(self):self.proactive.start()
    def _proactive_event(self,message):self.memory.add('proactive',message);self.audit.write('PROACTIVE',message);print(f'[ULTRON] {message}')
    def _get_browser(self):
        if self._browser_agent is None:
            from app.browser.agent import BrowserAgent
            self._browser_agent=BrowserAgent(self.settings,audit=self.audit)
        return self._browser_agent
    def shutdown(self):
        try:
            if getattr(self,'_browser_agent',None) is not None:self._browser_agent.close(); self._browser_agent=None
        except Exception:pass
        try:self.stop_live_voice()
        except Exception:pass
    def stop_proactive(self):self.proactive.stop()
    def start_live_voice(self):
        try:
            import sounddevice,faster_whisper
            from app.voice.voice_stack_v2 import LiveVoiceV2,VoiceStackV2
            stack=getattr(self,'live_stack',None) or VoiceStackV2(); self.live_stack=stack; self.live_voice_v2=LiveVoiceV2(self.agent,self.tts,self.settings,stack)
            import threading; threading.Thread(target=self.live_voice_v2.run,daemon=True).start()
        except Exception as exc:
            # Never fall back to the legacy LiveVoice (app.voice.live_voice) here: it
            # detects "wake word" by substring-matching the *already transcribed*
            # text instead of a real acoustic wake-word engine, which is an explicit
            # fake-success anti-pattern. Report the real reason honestly instead.
            self.live_voice_error=str(exc)[:300]
    def stop_live_voice(self):
        v2=getattr(self,'live_voice_v2',None)
        if v2:v2.stop()
        self.live_voice.stop()

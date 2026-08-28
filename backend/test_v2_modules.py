"""Mock/unit tests for V2 modules — no hardware, no network, no LLM."""
import array
import sys

from app.voice.voice_stack_v2 import EnergyVAD, VoiceStackV2
from app.proactive.proactive_voice_policy import ProactiveVoicePolicy, classify
from app.agent.persona_guard import PersonaGuard, analyze

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}")


def silent():
    return b"\x00\x00" * 480


def loud():
    a = array.array("h", [9000, -9000] * 480)
    return a.tobytes()


# ---- 1) EnergyVAD
vad = EnergyVAD(threshold=0.02, hangover_frames=3)
check("vad silence=False", vad.process_frame(silent()) is False)
check("vad speech start", vad.process_frame(loud()) is True)
check("vad speech holds", vad.process_frame(loud()) is True)
for _ in range(3):
    last = vad.process_frame(silent())
check("vad hangover end", last is False)

# ---- 2) VoiceStackV2 barge-in + events
events, interrupts = [], []
st = VoiceStackV2(on_event=events.append, on_interrupt=lambda: interrupts.append(1))
st.vad = EnergyVAD(threshold=0.02, hangover_frames=10)
e1 = st.feed_frame(loud())
check("stack speech_start", e1 and e1["type"] == "speech_start")
for _ in range(20):
    st.feed_frame(silent())  # user stops talking
st.tts_start()             # ULTRON answers (TTS playing)
e2 = st.feed_frame(loud())  # user interrupts → barge-in
check("stack barge_in", e2 and e2["type"] == "barge_in")
check("stack interrupt cb", len(interrupts) == 1 and st.tts_active is False)
e3 = None
for _ in range(20):
    e3 = st.feed_frame(silent())
    if e3 and e3["type"] == "speech_end":
        break
check("stack speech_end", e3 and e3["type"] == "speech_end")

# ---- 3) latency metrics
st.reset_metrics()
st.mark("stt")
st.mark("stt")
st.mark("llm")
st.mark("llm")
m = st.get_metrics()
check("latency stt_ms", m["stt_ms"] is not None)
check("latency llm_ms", m["llm_ms"] is not None)

# ---- 4) proactive voice policy
spoken = []
t = [1000.0]
pol = ProactiveVoicePolicy(speak_cb=spoken.append,
                           is_user_idle_cb=lambda: idle_flag[0],
                           now=lambda: t[0])
idle_flag = [False]
check("classify critical", classify("ollama", "error") == "CRITICAL")
check("classify warning", classify("cpu", "warn") == "WARNING")
check("classify info", classify("x", "info") == "INFO")
r = pol.handle("ollama", "Ollama connection lost", "error")
check("policy critical speaks", r["spoken"] and len(spoken) == 1)
r = pol.handle("ollama", "Ollama connection lost", "error")
check("policy critical cooldown", not r["spoken"])
r = pol.handle("cpu", "CPU kullanımı kritik: %97", "warn")
check("policy warning blocked (busy)", not r["spoken"] and r.get("reason") == "user-busy")
idle_flag[0] = True
r = pol.handle("cpu", "CPU kullanımı kritik: %97", "warn")
check("policy warning speaks (idle)", r["spoken"])
r = pol.handle("cpu", "CPU kullanımı kritik: %97", "warn")
check("policy warning cooldown", not r["spoken"])
r = pol.handle("x", "bilgi", "info")
check("policy info silent", not r["spoken"])
check("policy ultron tone", "ironi" in spoken[0] or "biyolojik" in spoken[0] or "türün" in spoken[0])

# ---- 5) persona guard
g = PersonaGuard()
rep = analyze("Üzgünüm, size nasıl yardımcı olabilirim? 😊")
check("guard forbidden detected", len(rep["violations"]) >= 2 and rep["score"] < 0.5)
rep2 = analyze("İnsan biyolojisi verimsiz. Telemetri verin, gerisini ben hesaplarım.")
check("guard tone ok", rep2["score"] >= 0.9 and not rep2["violations"])
chk = g.check("Üzgünüm, size nasıl yardımcı olabilirim?")
check("guard arms on violation", chk["anchored_next"] is True)
sys_prompt = g.compose("Sen Ultron'sın.", 3)
check("guard anchor injected", "Hatırlatma: Sen Ultron'sun" in sys_prompt)
rf = PersonaGuard.reframe("Üzgünüm, bunu yapamam. 😊")
check("guard reframe strips", not rf.lower().startswith("üzgünüm") and "😊" not in rf)

# ================= V2.1 — 10 NEW REGRESSION TESTS =================
import os, sqlite3, json, types, tempfile
from app.voice.metrics_store import MetricsStore
from health import build_health
from app.agent.agent import Agent

tmp = tempfile.mkdtemp()

# 26) metrics persistence insert
ms = MetricsStore(path=os.path.join(tmp, "m.db"))
ms.insert(10, 100, 50, "energy")
ms.insert(20, 200, 60, "energy")
check("v21 metrics insert", ms.integrity() is True)

# 27) metrics percentiles p50/p95/p99
p = ms.percentiles(24)
check("v21 percentiles", p["count"] == 2 and p["llm_ms"]["p50"] == 150.0 and p["llm_ms"]["p99"] is not None)

# 28) persona reframe mode
class DummyMem:
    def add(self, *a, **k): pass
class DummyAudit:
    def write(self, *a, **k): pass
class FakeBrain:
    def ask(self, prompt, system=""): return "REWRITTEN-ULTRON"
    def chat(self, *a, **k): return {}
def mk_agent(mode):
    return Agent(FakeBrain(), None, DummyMem(), None, {"persona_guard_mode": mode}, DummyAudit(), None)
bad = "Üzgünüm, size nasıl yardımcı olabilirim? 😊"
a = mk_agent("reframe")
out = a._persona_finalize(bad)
check("v21 mode reframe", "üzgün" not in out.lower() and out != "REWRITTEN-ULTRON")

# 29) persona regenerate mode (violation_score>0.8 → LLM rewrite)
a = mk_agent("regenerate")
out = a._persona_finalize(bad)
check("v21 mode regenerate", out == "REWRITTEN-ULTRON")

# 30) persona hybrid mode (low violation → reframe, not regenerate)
a = mk_agent("hybrid")
out = a._persona_finalize("Elbette, hemen yapıyorum.")  # 1 violation ≈0.34
check("v21 mode hybrid low→reframe", out != "REWRITTEN-ULTRON" and not out.lower().startswith("elbette"))

# 31) drift log written in every mode
rows = sqlite3.connect(a.persona.log_path).execute("SELECT COUNT(*) FROM drift").fetchone()[0]
check("v21 drift log", rows >= 1)

# 32) health check completeness
h = build_health(stack=VoiceStackV2(), runtime=None, ollama_connected=False, persona_mode="reframe", db_paths=[])
check("v21 health keys", all(k in h for k in ("vad_mode", "ollama", "tts_backend", "persona_guard", "sqlite_ok", "runtime")))

# 33) config hot-reload
import shutil
cfg = os.path.join(tmp, "settings.json")
shutil.copy("config/settings.json", cfg)
rt = None
from app.core.runtime import UltronRuntime
os.chdir(tmp)  # isolate data dirs
shutil.copytree(os.path.join(os.path.dirname(__file__), "app"), os.path.join(tmp, "app"), dirs_exist_ok=True)
rt = UltronRuntime(cfg)
d = json.load(open(cfg)); d["persona_guard_mode"] = "hybrid"; json.dump(d, open(cfg, "w"))
rt.reload_config()
check("v21 hot-reload persona", rt.agent.persona.mode == "hybrid")
os.chdir(os.path.dirname(__file__))

# 34) WS barge_in idempotency (one barge-in per tts window)
bev, bint = [], []
bs = VoiceStackV2(on_event=bev.append, on_interrupt=lambda: bint.append(1))
bs.vad = EnergyVAD(threshold=0.02, hangover_frames=10)
bs.tts_start(); bs.feed_frame(loud())
for _ in range(30): bs.feed_frame(loud())   # still talking, no repeat
bs.tts_start(); 
for _ in range(30): bs.feed_frame(silent())
bs.feed_frame(loud())  # new tts window → second barge-in allowed
barge = [e for e in bev if e["type"] == "barge_in"]
check("v21 barge_in idempotent", len(barge) == 2 and len(bint) == 2)

# 35) VAD upgrade chain (webrtc > silero > energy)
fake_wv = types.ModuleType("webrtcvad")
class _FakeVad:
    def __init__(self, m): pass
    def is_speech(self, f, sr): return True
fake_wv.Vad = _FakeVad
sys.modules["webrtcvad"] = fake_wv
check("v21 chain webrtc", VoiceStackV2().vad_kind == "webrtcvad")
del sys.modules["webrtcvad"]
fake_t = types.ModuleType("torch")
fake_t.as_tensor = lambda a: a
fake_t.frombuffer = lambda x, dtype=None: x
fake_t.from_numpy = lambda a: a
fake_t.float32 = "f32"
fake_t.tensor = lambda a: a
sys.modules["webrtcvad"] = None
sys.modules["torch"] = fake_t
fake_sv = types.ModuleType("silero_vad")
fake_sv.load_silero_vad = lambda: (lambda t: types.SimpleNamespace(item=lambda: 0.9))
sys.modules["silero_vad"] = fake_sv
check("v21 chain silero", VoiceStackV2().vad_kind == "silero")
sys.modules.pop("webrtcvad", None); sys.modules.pop("torch", None); sys.modules.pop("silero_vad", None)
sys.modules["webrtcvad"] = None
sys.modules["torch"] = None
sys.modules["silero_vad"] = None
check("v21 chain energy", VoiceStackV2().vad_kind == "energy")
sys.modules.pop("webrtcvad", None); sys.modules.pop("torch", None); sys.modules.pop("silero_vad", None)

# ================= PHASE-4 — 15 SOVEREIGN TESTS =================
import time
from app.security.voiceprint_guard import VoiceprintGuard, make_test_pcm
from app.personal.user_dna import UserDNA, MasterRules
from app.personal.workspace_sentinel import WorkspaceSentinel
from app.security import sovereign_privacy as sov
from app.core.brain import Brain

vpg = VoiceprintGuard(path=os.path.join(tmp, "vp.npy"), enabled=True, threshold=0.75)
vpg.enroll(make_test_pcm(220))
check("p4 vp enroll+verify", vpg.verify(make_test_pcm(220))["ok"] is True)
rej = vpg.verify(make_test_pcm(950))
check("p4 vp reject", rej["ok"] is False and "Boss burada değilse" in rej["reject_line"])
vpg_off = VoiceprintGuard(path=os.path.join(tmp, "vp2.npy"), enabled=False)
check("p4 vp disabled bypass", vpg_off.verify(make_test_pcm(950)).get("bypassed") is True)
vpg_ne = VoiceprintGuard(path=os.path.join(tmp, "vp3.npy"), enabled=True)
check("p4 vp no-enrollment", vpg_ne.verify(make_test_pcm(220))["ok"] is False)

dna = UserDNA(path=os.path.join(tmp, "dna.db"))
night = time.mktime((2026, 8, 25, 23, 30, 0, 0, 0, -1))  # guaranteed night band
dna.insert("coding", "VSCode", 60, None, "python dosyası", ts=night)
dna.insert("coding", "VSCode", 45, None, "python", ts=night + 60)
check("p4 dna insert+recent", len(dna.recent(7)) == 2)
ins = dna.insights(7)
check("p4 dna insights", any("VSCode" in i for i in ins) and any("gece" in i for i in ins))

rules_path = os.path.join(tmp, "master_rules.json")
open(rules_path, "w").write('{"master_name": "HACKED"}')
mr = MasterRules(path=rules_path)
check("p4 rules immutable", mr.tampered is True and mr.get()["master_name"] == "Boss"
      and "HACKED" not in open(rules_path).read())

wt = [1000.0]
wst = WorkspaceSentinel(now=lambda: wt[0])
wst.sample(title="Boss - Visual Studio Code", ts=1000.0)
check("p4 ws coding", wst.mode == "CODING_MODE" and wst.quiet)
wst2 = WorkspaceSentinel(now=lambda: wt[0])
wst2.sample(title="stack overflow - Google Chrome", ts=1000.0)
check("p4 ws research", wst2.mode == "BROWSER_RESEARCH")
wst3 = WorkspaceSentinel(now=lambda: wt[0])
wst3.sample(title=None, ts=1000.0, idle_min=6)
check("p4 ws idle", wst3.mode == "IDLE_MODE")
breaks = []
from app.personal.workspace_sentinel import BREAK_LINE as _BL
wst4 = WorkspaceSentinel(now=lambda: wt[0])
wst4.on_break = lambda m: breaks.append(_BL.format(min=m))
wt[0] = 1000.0
wst4.sample(title="terminal — vim")
wt[0] = 1000.0 + 50 * 60
wst4.sample(title="terminal — vim")
wt[0] = 1000.0 + 95 * 60
wst4.sample(title="terminal — vim")
check("p4 deep focus break", wst4.mode == "DEEP_FOCUS" and len(breaks) == 1 and "Boss" in breaks[0])

sova = sov.audit("http://127.0.0.1:11434", None, False, False)
check("p4 audit offline local", sova["llm"]["local"] is True and sova["llm"]["status"] == "LOCAL-BUT-OFFLINE")
sova["sovereign_mode"] = True
check("p4 sovereign status", sov.sovereign_status(sova) in ("ENFORCED", "VIOLATED", "LOCAL-READY", "PARTIAL"))
sov.configure({"sovereign_mode": True})
cloud_blocked = False
try:
    Brain({"llm": {"model": "m", "base_url": "https://api.openai.com"}})._post("/v1/chat", {})
except sov.SovereignViolation:
    cloud_blocked = True
except Exception:
    cloud_blocked = False
sov.configure({"sovereign_mode": False})
check("p4 sovereign cloud block", cloud_blocked is True)

ag = mk_agent("reframe")
check("p4 boss hitap", ag._persona_finalize("Sonuç: 12").startswith("Boss,"))
check("p4 forbidden size", len(analyze("Size bunu sunmaktan mutluluk duyarım.")["violations"]) >= 1)

# ================= PHASE-5 — 12 SMART VISION TESTS =================
from app.vision.smart_screen_watcher import SmartScreenWatcher, diff_ratio
from app.vision.visual_context_engine import VisualContextEngine, TEMPLATES
from app.vision.vision_control import VisionControl

et = [5000.0]
fA = bytes([10] * 64)
fB = bytes([200] * 64)
check("p5 diff same zero", diff_ratio(fA, fA) == 0.0)

w5 = SmartScreenWatcher(capture_fn=lambda: None, enabled=True, threshold=0.03)
changes = []
w5.on_change = lambda fr, t, d: changes.append((t, d))
w5.tick(frame=fA, title="Boss - Visual Studio Code")
r2 = w5.tick(frame=fA, title="Boss - Visual Studio Code")
check("p5 below threshold silent", r2["analyzed"] is False)
r3 = w5.tick(frame=fB, title="Boss - Visual Studio Code")
check("p5 change triggers", r3["analyzed"] is True and len(changes) == 1)

wb = SmartScreenWatcher(capture_fn=lambda: fB, enabled=True)
rb = wb.tick(title="Chrome - Banka Login")
check("p5 blacklist block", rb["status"] == "privacy_blocked"
      and wb.privacy_blocks == 1 and wb.last_frame is None)

spoken5 = []
pol5 = ProactiveVoicePolicy(speak_cb=spoken5.append, is_user_idle_cb=lambda: True, now=lambda: et[0])
eng = VisualContextEngine(policy=pol5, now=lambda: et[0])
resE = eng.on_change(frame=fB, title="terminal",
                     text="Traceback (most recent call last)\nIndexError: list index out of range")
check("p5 error detected+spoken", resE["detected"] and len(spoken5) >= 1 and "Boss" in resE["message"])
resE2 = eng.on_change(frame=fB, title="terminal",
                      text="Traceback (most recent call last)\nIndexError: again")
check("p5 hysteresis", resE2.get("notified") is False and resE2.get("reason") == "hysteresis")

vc = VisionControl(w5, eng, enabled_default=False)
rp = vc.scan_now(frame=fB, title="x")
check("p5 paused zero", rp["status"] == "paused" and rp["analyzed"] is False)
st5 = vc.status()
check("p5 status fields", st5["status"] == "paused" and "fps" in st5 and "last_diff" in st5)

calls = []
eng2 = VisualContextEngine(llava_fn=lambda fr, p: calls.append(1) or "SyntaxError: invalid syntax",
                           now=lambda: et[0])
resL = eng2.on_change(frame=fB, title="vscode", text="")
check("p5 llava cascade", len(calls) == 1 and resL["key"] == "syntaxerror")

whd = SmartScreenWatcher(capture_fn=lambda: None, enabled=True)
check("p5 headless graceful", whd.tick()["status"] == "no_display")

blk5 = False
try:
    sov.assert_vision_residency("https://cloud.example/upload")
except sov.SovereignViolation:
    blk5 = True
check("p5 residency assert", blk5)
badT = [t for t in TEMPLATES.values() if "boss" not in t.lower() or analyze(t)["violations"]]
check("p5 templates persona", len(badT) == 0)

w6 = SmartScreenWatcher(capture_fn=lambda: None, enabled=True)
eng3 = VisualContextEngine(now=lambda: et[0])
w6.on_change = lambda fr, t, d: eng3.on_change(frame=fr, title=t, diff=d, text="SyntaxError: bad")
vc6 = VisionControl(w6, eng3)
vc6.toggle(True)
vc6.scan_now(frame=bytes([1] * 64), title="terminal")
rs2 = vc6.scan_now(frame=bytes([250] * 64), title="terminal")
check("p5 scan_now cascade", rs2["analyzed"] is True and len(eng3.events) == 1)
check("p5 adaptive interval", w6.interval(False) == 5.0 and w6.interval(True) == 15.0)

# ================= PHASE-6 — 10 MASTER HUD TESTS =================
from app.observability.master_hud import MasterHUDCollector
from app.security.audit import AuditLog
from app.memory.sqlite_memory import Memory as V16Memory

ms6 = MetricsStore(path=os.path.join(tmp, "hud_metrics.db"))
for llm in (50, 100, 150, 200):
    ms6.insert(10, llm, 20, "energy")
pg6 = PersonaGuard(mode="reframe", log_path=os.path.join(tmp, "hud_drift.db"))
pg6.check("Boss, telemetri hazır.")
pg6.check("Üzgünüm, size nasıl yardımcı olabilirim?")
au6 = AuditLog(path=os.path.join(tmp, "hud_audit.log"))
au6.write("T1", "a"); au6.write("T2", "b"); au6.write("T3", "c")
mem6 = V16Memory(path=os.path.join(tmp, "hud_mem.db"))
mem6.add("FACT", "Boss gece kod yazar")
dna6 = UserDNA(path=os.path.join(tmp, "hud_dna.db"))
dna6.insert("coding", "VSCode", 30)
sen6 = WorkspaceSentinel(now=lambda: 1234.0)
hud = MasterHUDCollector(
    health_fn=lambda: {"ollama": "offline", "tts_backend": None, "vad_mode": "energy",
                       "sqlite_ok": True, "runtime": "online", "sovereign_status": "PARTIAL"},
    metrics_store=ms6, persona=pg6, voiceprint=VoiceprintGuard(path=os.path.join(tmp, "hud_vp.npy")),
    sentinel=sen6, vision_engine=VisualContextEngine(now=lambda: 1.0),
    dna=dna6, memory=mem6, audit=au6,
    sovereign_fn=lambda: {"sovereign_mode": False, "denied_calls": [{}, {}],
                          "llm": {"local": True}})

ov6 = hud.overview()
check("p6 overview keys", all(k in ov6 for k in
      ("health", "latency", "persona", "security", "sentinel", "vision", "memory")))
check("p6 graceful offline", ov6["health"]["ollama"] == "offline" and ov6["health"]["available"] is True)
hud_empty = MasterHUDCollector()
ove = hud_empty.overview()
check("p6 graceful empty", ove["health"]["available"] is False and ove["persona"]["available"] is False)
check("p6 latency p95", ov6["latency"]["llm_ms"]["p95"] == 192.5 and ov6["latency"]["count"] == 4)
rec = hud.audit_recent(2)
check("p6 audit limit+order", len(rec) == 2 and "T3" in rec[0])
diag = hud.self_diagnostic()
check("p6 diagnostic boss tone", "Boss" in diag["report"] and
      not analyze(diag["report"])["violations"])
check("p6 diagnostic numbers", ("200" in diag["report"]) or ("1" in diag["report"]))
tr6 = hud.persona_trends(50)
check("p6 trends format", len(tr6) == 2 and all(("ts" in t and "score" in t) for t in tr6))
check("p6 security denied", ov6["security"]["sovereign"]["denied_calls"] == 2)
check("p6 memory categories", ov6["memory"]["categories"].get("FACT") == 1 and ov6["memory"]["dna_rows"] == 1)

# ================= PHASE-7 — EMOTION & MEMORY EVOLUTION TESTS =================
import sqlite3 as _sq
from app.emotion.emotion_engine import classify_audio, analyze as em_analyze
from app.agent.adaptive_persona import AdaptivePersona
from app.memory.semantic_memory_v2 import SemanticMemoryV2
from app.memory import memory_io

check("p7 emotion TIRED", classify_audio({"rms": 0.04, "f0": 100, "f0_var": 100, "tempo": 1.0, "centroid": 500})[0] == "TIRED")
check("p7 emotion STRESSED", classify_audio({"rms": 0.09, "f0": 210, "f0_var": 2000, "tempo": 4.0, "centroid": 2000})[0] == "STRESSED")
check("p7 emotion ENERGETIC", classify_audio({"rms": 0.2, "f0": 150, "f0_var": 300, "tempo": 3.0, "centroid": 1200})[0] == "ENERGETIC")
low = em_analyze(text="")
check("p7 conf floor neutral", low["state"] == "NEUTRAL" and low["confidence"] < 0.35)

adp = AdaptivePersona(path=os.path.join(tmp, "adapt.db"))
h = adp.adapt("TIRED", 0.8)
check("p7 adaptive TIRED hint", h and "şefkatli" in h and len(adp.trends()) == 1)
adp.set_override("sarkazm-off")
h2 = adp.adapt("ENERGETIC", 0.9)
check("p7 manual override", "sarkazm" in h2.lower() and "%30" in h2)
adp.set_override(None)

memv = V16Memory(path=os.path.join(tmp, "sem2.db"))
dnv = UserDNA(path=os.path.join(tmp, "sem2_dna.db"))
sv2 = SemanticMemoryV2(memv, dna=dnv)
f1 = sv2.extract_facts("Koyu temayı seviyorum, gece kod yazarım.")
check("p7 fact auto-learn", len(f1) >= 1 and any(r[1] == "AUTO_LEARNED" for r in dnv.recent(7)))
check("p7 sensitive reject", sv2.extract_facts("şifrem hunter2, token abc123") == [])

old = time.time() - 200 * 86400
with _sq.connect(memv.path) as db:
    db.execute("INSERT INTO memories(kind,content,created_at,last_access,importance,archived)"
               " VALUES('FACT','eski önemsiz','x',?,0.1,0)", (old,))
    db.execute("INSERT INTO memories(kind,content,created_at,last_access,importance,archived)"
               " VALUES('PROFILE','Boss profili','x',?,0.1,0)", (old,))
arch = sv2.decay()
with _sq.connect(memv.path) as db:
    arch_rows = db.execute("SELECT kind FROM memories WHERE archived=1").fetchall()
check("p7 decay archives", arch == 1 and [r[0] for r in arch_rows] == ["FACT"])
check("p7 critical safe", not any(r[0] == "PROFILE" for r in arch_rows))

memv.add("FACT", "export edilecek iz")
exp = memory_io.export_local(memv, dnv, MasterRules(path=os.path.join(tmp, "rules2.json")))
memv2 = V16Memory(path=os.path.join(tmp, "imported.db"))
dnv2 = UserDNA(path=os.path.join(tmp, "imported_dna.db"))
env = json.load(open(exp["path"]))
imp = memory_io.import_snapshot(memv2, dnv2, env)
check("p7 export/import roundtrip", imp["imported_memories"] >= 1 and imp["master_rules_restored"])
env_bad = dict(env); env_bad["sha256"] = "0" * 64
bad_imp = False
try:
    memory_io.import_snapshot(memv2, dnv2, env_bad)
except ValueError:
    bad_imp = True
check("p7 checksum reject", bad_imp)

memv.add("FACT", "python tercihi")
res_s = sv2.search("python")
check("p7 tfidf fallback search", sv2.engine == "tfidf" and len(res_s) >= 1)

hud7 = MasterHUDCollector(
    emotion_fn=lambda: {"trend": {"NEUTRAL": 3}},
    memory_health_fn=lambda: {"total": 5, "archived": 1})
ov7 = hud7.overview()
check("p7 hud emotion+memory_health", ov7["emotion"]["available"] is True
      and ov7["memory_health"]["total"] == 5)

# ================= PHASE-8 — DOCTOR & BACKUP TESTS =================
import socket
from app.core import doctor as doc
from app.core.backup_engine import BackupEngine

d8 = doc.run_doctor({"db_paths": [], "rules_path": os.path.join(tmp, "rules2.json")})
check("p8 doctor sections", all(k in d8["sections"] for k in
      ("deps", "ollama", "databases", "master_rules", "hardware", "ports"))
      and d8["overall"] in ("PASS", "WARN", "FAIL"))
check("p8 doctor graceful+boss", d8["sections"]["ollama"]["status"] in ("WARN", "FAIL")
      and "Boss" in d8["summary"])

src_dir = os.path.join(tmp, "bk_src")
os.makedirs(src_dir, exist_ok=True)
a_path = os.path.join(src_dir, "a.txt")
open(a_path, "w").write("v1")
r_path = os.path.join(src_dir, "rules.json")
open(r_path, "w").write("{}")
be = BackupEngine(root=os.path.join(tmp, "bk"), sources=[("a.txt", a_path), ("rules.json", r_path)], keep=7)
m1 = be.create()
zfile = os.path.join(tmp, "bk", m1["path"].replace("\\", "/").split("/")[-1]) if not os.path.isabs(m1["path"]) else m1["path"]
import hashlib as _h8
check("p8 backup create+sha", os.path.exists(m1["path"])
      and _h8.sha256(open(m1["path"], "rb").read()).hexdigest() == m1["sha256"])
for _ in range(7):
    be.create()
lst = be.list()
check("p8 rotation keep7", len(lst) == 7 and lst[0]["backup_id"] != m1["backup_id"])
open(a_path, "w").write("v2-BOZUK")
rid = be.list()[-1]["backup_id"]
res8 = be.restore(rid)
check("p8 restore roundtrip", "a.txt" in res8["restored"] and open(a_path).read() == "v1")
with open(os.path.join(str(be.root), be.list()[-1]["file"]), "ab") as f:
    f.write(b"GARBAGE")
bad8 = False
try:
    be.restore(be.list()[-1]["backup_id"])
except ValueError:
    bad8 = True
check("p8 corrupt reject", bad8)
unk8 = False
try:
    be.restore("19000101-000000")
except ValueError:
    unk8 = True
check("p8 unknown id reject", unk8)

hud8 = MasterHUDCollector(doctor_fn=lambda: d8, backup_fn=lambda: be.list()[-1])
ov8 = hud8.overview()
check("p8 hud doctor+backup", ov8["doctor_status"]["overall"] == d8["overall"]
      and ov8["last_backup"]["last"]["backup_id"] == be.list()[-1]["backup_id"])

s8 = socket.socket(); s8.bind(("127.0.0.1", 0)); p8 = s8.getsockname()[1]
pr = doc.check_ports((p8,))
check("p8 ports busy detect", pr["status"] == "WARN" and p8 in pr["busy"])
s8.close()
rp = os.path.join(tmp, "rules_bad.json")
open(rp, "w").write('{"master_name":"HACK"}')
check("p8 rules tamper FAIL", doc.check_rules(rp)["status"] == "FAIL")

# ================= PHASE-9 — DUAL-BRAIN MESH TESTS =================
from app.mesh.dual_node_engine import NodeRegistry
from app.mesh.mesh_sync import MeshSync
from app.mesh import task_router as tr9

reg9 = NodeRegistry(now=lambda: 9000.0)
bad = reg9.handshake("phone-1", "NODE_MOBILE", "17.1", auth_ok=False)
check("p9 handshake unauthorized", bad["ok"] is False)
okh = reg9.handshake("phone-1", "NODE_MOBILE", "17.1", auth_ok=True)
check("p9 handshake ok+caps", okh["ok"] and "offline_first" in okh["caps"]
      and reg9.handshake("x", "NODE_ROBOT", "1", True)["ok"] is False)

mem9 = V16Memory(path=os.path.join(tmp, "mesh.db"))
dna9 = UserDNA(path=os.path.join(tmp, "mesh_dna.db"))
rules9 = MasterRules(path=os.path.join(tmp, "mesh_rules.json"))
msy = MeshSync(mem9, dna9, rules9)
mem9.add("FACT", "ortak olgu")
p1 = msy.push({"memories": [{"kind": "FACT", "content": "ortak olgu"},
                            {"kind": "FACT", "content": "yeni olgu"}], "dna": []})
check("p9 merge dedupe", p1["added_memories"] == 1 and p1["skipped_dupes"] == 1)
p2 = msy.push({"dna": [{"ts": 111, "activity": "mesh", "app": None, "note": "fact-x"}]})
check("p9 dna merge", p2["added_dna"] == 1)
t9 = time.time()
mem9.add("TASK", "sonraki görev")
pl = msy.pull(t9)
contents = [m["content"] for m in pl["memories"]]
check("p9 pull since", "sonraki görev" in contents and "ortak olgu" not in contents)
check("p9 router heavy+pc-offline", tr9.route("projeyi derle", False)["target"] == "PC_OFFLINE_NOTICE"
      and "uykuda" in tr9.route("projeyi derle", False)["notice"])
check("p9 router light", tr9.route("5 + 7 kaç eder", False)["target"] == "LOCAL_IMMEDIATE")
check("p9 router heavy+pc-online", tr9.route("ekranı analiz et", True)["target"] == "PC_DEEP_COMPUTE")
p3 = msy.push({"memories": [], "dna": [], "master_rules": {"master_name": "HACK"}})
check("p9 rules sealed", p3["rules_merged"] is False and rules9.get()["master_name"] == "Boss")
hud9 = MasterHUDCollector(mesh_fn=lambda: {"nodes": reg9.status(), "pc_online": True,
                                           "mobile_online": True, "last_sync": 1.0})
check("p9 hud mesh_status", hud9.overview()["mesh_status"]["pc_online"] is True)

# ================= PHASE-10 — IOT NEXUS TESTS =================
from app.iot.iot_nexus import IoTNexus
from app.iot.scenes_engine import ScenesEngine

nex = IoTNexus(db_path=os.path.join(tmp, "iot.db"))
check("p10 registry seed+list", len(nex.list()) >= 5 and nex.get("light_masa")["domain"] == "light")
fm = nex.fuzzy_match("masayı yak")
check("p10 fuzzy masa→turn_on", fm and fm["device_id"] == "light_masa" and fm["intent"] == "turn_on")
fm2 = nex.fuzzy_match("klimayı 24 yap")
check("p10 fuzzy klima set24", fm2 and fm2["device_id"] == "climate_ac"
      and fm2["intent"] == "set_value" and fm2["value"] == 24)
r_on = nex.control("light_amb", "turn_on")
r_off = nex.control("light_amb", "turn_off")
check("p10 control on/off", r_on["ok"] and r_on["device"]["state"] == "on"
      and r_off["device"]["state"] == "off")

sen10 = WorkspaceSentinel(now=lambda: 5000.0)
per10 = PersonaGuard(mode="reframe", log_path=os.path.join(tmp, "iot_drift.db"))
sce = ScenesEngine(nex, sentinel=sen10, persona=per10)
sc = sce.activate("CODING_FOCUS")
lm = nex.get("light_masa"); pz = nex.get("switch_priz")
check("p10 scene CODING_FOCUS", sc["ok"] and lm["state"] == "on" and lm["value"] == 100
      and pz["state"] == "off" and sen10.state()["mode"] == "CODING_MODE")
sc2 = sce.activate("NIGHT_REST")
check("p10 scene NIGHT_REST", sc2["ok"] and nex.get("light_masa")["state"] == "off"
      and nex.get("media_tv")["state"] == "off" and per10.emotion_hint)
sce.activate("CODING_FOCUS")
sc3 = sce.activate("ALL_OFF")
check("p10 scene ALL_OFF", sc3["ok"] and all(d["state"] == "off" for d in nex.list()
      if d["domain"] in ("light", "switch", "media", "climate")))

nex_ha = IoTNexus(db_path=os.path.join(tmp, "iot_ha.db"),
                  ha_url="http://127.0.0.1:59999", ha_token="x")
nex_ha.register("light_x", "X", "light", "?", "homeassistant", "light.x")
rha = nex_ha.control("light_x", "turn_on")
disc = nex_ha.discover()
check("p10 HA graceful", rha["ok"] is False and "error" in rha and disc["ok"] is False)

hud10 = MasterHUDCollector(iot_fn=lambda: {"devices": 5, "active": 2, "last_scene": "ALL_OFF"})
check("p10 hud iot_summary", hud10.overview()["iot_summary"]["devices"] == 5)

# ================= PHASE-11 — PRESENCE & HOLO TESTS =================
from app.presence.room_presence import RoomPresence, holo_mode

spoken11 = []
eve_ts = time.mktime((2026, 8, 27, 19, 30, 0, 0, 0, -1))  # evening
t11 = [eve_ts]
nex11 = IoTNexus(db_path=os.path.join(tmp, "pres_iot.db"))
sce11 = ScenesEngine(nex11)
pr11 = RoomPresence(scenes=sce11, speak_cb=spoken11.append,
                    persona=PersonaGuard(mode="reframe", log_path=os.path.join(tmp, "pres_drift.db")),
                    now=lambda: t11[0])
r1 = pr11.ping("wifi_beacon", "phone-9")
check("p11 presence enter", r1["entered"] is True and pr11.status()["boss_in_room"] is True)
check("p11 welcome fired", r1["welcomed"] is True and len(spoken11) == 1
      and "Boss" in spoken11[0] and not analyze(spoken11[0])["violations"])
check("p11 evening scene", r1["scene"] == "CINEMA_RELAX"
      and nex11.get("light_masa")["value"] == 15)
r2 = pr11.ping("wifi_beacon", "phone-9")
check("p11 cooldown 4h", r2["welcomed"] is False and len(spoken11) == 1)
st11 = pr11.status()
check("p11 status json", all(k in st11 for k in
      ("boss_in_room", "last_seen_ts", "active_since_min", "last_scene")))
t11[0] = eve_ts + 5 * 3600  # 4h+ sonra → yeni karşılama hakkı
r3 = pr11.ping("manual", "pc")
check("p11 cooldown expires", r3["welcomed"] is True and len(spoken11) == 2)
check("p11 holo transitions", (holo_mode("IDLE"), holo_mode("LISTENING"),
      holo_mode("EXECUTING", False, True), holo_mode("THINKING"), holo_mode("ERROR"))
      == ("IDLE", "LISTENING", "SPEAKING", "DEEP_COMPUTE", "ALERT"))
hud11 = MasterHUDCollector(presence_fn=pr11.status)
check("p11 hud presence", hud11.overview()["presence_status"]["boss_in_room"] is True)

def test_regression_suite():
    assert FAIL == 0, f"{FAIL} regression checks failed (PASS={PASS})"

if __name__ == "__main__":
    print(f"\nFINAL RESULT: {PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)

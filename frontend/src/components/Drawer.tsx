import { Play, ShieldCheck, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  analyzeCode,
  applyPatch,
  taskApprove,
  taskReject,
  hudOverview,
  hudTrends,
  iotDevices,
  iotControl,
  iotScene,
  getAudit,
  memoryV16,
  memoryV16Add,
  memoryV16Clear,
  memoryV16Delete,
  memoryV16Search,
  rejectPatch,
  runTests,
} from "../lib/api";
import { setState, useApp } from "../lib/store";
import type { MemoryRow } from "../lib/types";

const TABS = ["hud", "iot", "code", "memory", "automation", "approvals", "audit", "voice"] as const;

const sevColor: Record<string, string> = {
  high: "var(--red)",
  error: "var(--red)",
  warn: "var(--amber)",
  info: "var(--dim)",
};

function IotTab() {
  const [devs, setDevs] = useState<any[]>([]);
  const [fb, setFb] = useState("");
  const load = () => iotDevices().then((d: any) => setDevs(Array.isArray(d) ? d : [])).catch(() => undefined);
  useEffect(() => { load(); }, []);
  const ctl = async (id: string, action: string, value?: number) => {
    const r: any = await iotControl({ device_id: id, action, value }).catch((e) => ({ error: String(e) }));
    setFb(r.ok ? `${r.device?.name} → ${r.device?.state}` : `hata: ${r.error}`);
    load();
  };
  const scene = async (name: string) => {
    const r: any = await iotScene({ scene_name: name }).catch((e) => ({ error: String(e) }));
    setFb(r.note ?? r.error ?? "");
    load();
  };
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {devs.map((d) => (
          <div className="row" key={d.device_id}>
            <span style={{ fontSize: 11 }}>{d.name} <span className="dim">({d.room})</span></span>
            <span style={{ display: "flex", gap: 4 }}>
              <span className={"badge " + (d.state === "on" ? "g" : "gray")}>{d.state}{d.value != null ? ` ${d.value}` : ""}</span>
              <button className="badge c" onClick={() => ctl(d.device_id, "toggle")}>⟳</button>
            </span>
          </div>
        ))}
      </div>
      <div style={{ flex: 1, borderLeft: "1px solid rgba(160,180,205,.1)", paddingLeft: 10 }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>SAHNELER</div>
        {["CODING_FOCUS", "NIGHT_REST", "CINEMA_RELAX", "ALL_OFF"].map((s) => (
          <button key={s} className="btn gray" style={{ marginBottom: 6 }} onClick={() => scene(s)}>{s}</button>
        ))}
        {fb && <div className="mono" style={{ fontSize: 10, color: "var(--cyan)" }}>{fb}</div>}
      </div>
    </div>
  );
}

function HudTab() {
  const [ov, setOv] = useState<any>(null);
  const [tr, setTr] = useState<any[]>([]);
  useEffect(() => {
    hudOverview().then(setOv).catch(() => undefined);
    hudTrends().then(setTr).catch(() => undefined);
  }, []);
  if (!ov) return <div className="dim mono" style={{ fontSize: 10 }}>HUD yükleniyor…</div>;
  const h = ov.health ?? {};
  const lat = ov.latency ?? {};
  const per = ov.persona ?? {};
  const sec = ov.security ?? {};
  const sen = ov.sentinel ?? {};
  const mem = ov.memory ?? {};
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: "auto" }}>
        <div className="row"><span className="dim">RUNTIME</span><span className="badge g">{h.runtime ?? "—"}</span></div>
        <div className="row"><span className="dim">OLLAMA</span><span className={"badge " + (h.ollama === "connected" ? "c" : "r")}>{h.ollama ?? "—"}</span></div>
        <div className="row"><span className="dim">VAD / TTS</span><span className="mono" style={{ fontSize: 10 }}>{h.vad ?? "—"} / {h.tts ?? "none"}</span></div>
        <div className="row"><span className="dim">SQLITE</span><span className={"badge " + (h.sqlite_ok ? "g" : "r")}>{h.sqlite_ok ? "OK" : "FAIL"}</span></div>
        <div className="row"><span className="dim">SOVEREIGN</span><span className="badge c">{h.sovereign_status ?? "—"}</span></div>
        <div className="row"><span className="dim">LLM p95</span><span className="mono" style={{ fontSize: 10 }}>{lat?.llm_ms?.p95 ?? "—"} ms ({lat?.count ?? 0} int.)</span></div>
        <div className="row"><span className="dim">DRIFT avg</span><span className="mono" style={{ fontSize: 10 }}>{per?.avg_score ?? "—"} · mode {per?.mode ?? "—"}</span></div>
        <div className="row"><span className="dim">SENTINEL</span><span className="mono" style={{ fontSize: 10 }}>{sen?.mode ?? "—"} · {sen?.focus_min ?? 0}dk</span></div>
        <div className="row"><span className="dim">VISION ERR</span><span className="mono" style={{ fontSize: 10 }}>{ov?.vision?.errors_detected ?? 0}</span></div>
        <div className="row"><span className="dim">MEMORY</span><span className="mono" style={{ fontSize: 10 }}>{mem?.total ?? 0} iz</span></div>
        <div className="row"><span className="dim">MESH</span>
          <span className="mono" style={{ fontSize: 10 }}>
            🖥PC {String((ov as any)?.mesh_status?.pc_online ?? true).toUpperCase()} · 📱MOB {String((ov as any)?.mesh_status?.mobile_online ?? false).toUpperCase()}
          </span></div>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="badge a">BOSS · {sec?.voiceprint?.enabled ? "VOICEPRINT LOCKED" : "SINGLE-MASTER"}</span>
        </div>
      </div>
      <div style={{ flex: 1, borderLeft: "1px solid rgba(160,180,205,.1)", paddingLeft: 10, overflowY: "auto" }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>PERSONA DRIFT (son {tr.length})</div>
        {tr.length === 0 && <div className="dim mono" style={{ fontSize: 10 }}>henüz drift verisi yok</div>}
        <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 70 }}>
          {tr.map((t, i) => (
            <div key={i} title={String(t.score)} style={{
              width: 5, height: Math.max(4, (t.score ?? 0) * 70),
              background: (t.score ?? 0) >= 0.8 ? "var(--green)" : (t.score ?? 0) >= 0.6 ? "var(--amber)" : "var(--red)",
            }} />
          ))}
        </div>
        <div className="dim mono" style={{ fontSize: 9, marginTop: 6 }}>yeşil ≥0.8 · amber ≥0.6 · kırmızı drift</div>
      </div>
    </div>
  );
}

function CodeTab() {
  const testEvents = useApp((s) => s.testEvents);
  const [report, setReport] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (target: string) => {
    setBusy(true);
    try {
      setReport(await analyzeCode(target));
    } catch {
      setReport(null);
    }
    setBusy(false);
  };
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1.2, display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn" style={{ width: "auto" }} disabled={busy} onClick={() => run("all")}>Analyze All</button>
          <button className="btn" style={{ width: "auto" }} disabled={busy} onClick={() => run("frontend")}>Frontend</button>
          <button className="btn" style={{ width: "auto" }} disabled={busy} onClick={() => run("backend")}>Backend</button>
          <button className="btn" style={{ width: "auto" }} onClick={() => void runTests(true)}><Play size={11} /> Run Tests</button>
        </div>
        {report && (
          <div className="mono dim" style={{ fontSize: 9.5 }}>
            {report.summary?.files} files · {report.summary?.functions} fn · {report.summary?.classes} cls ·{" "}
            {report.issues?.length} issues · {report.todo?.length} TODO · {report.duplicates?.length} dup
          </div>
        )}
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {report?.issues?.map((i: any, k: number) => (
            <div key={k} className="list-row" style={{ alignItems: "baseline" }}>
              <span className="mono" style={{ fontSize: 8.5, color: sevColor[i.severity] ?? "var(--dim)", flex: "none", width: 38 }}>{i.severity}</span>
              <span className="mono dim" style={{ fontSize: 8.5, flex: "none", width: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {String(i.file).split("/").slice(-2).join("/")}:{i.line}
              </span>
              <span className="list-text" style={{ whiteSpace: "normal" }}>[{i.kind}] {i.message}</span>
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, borderLeft: "1px solid rgba(160,180,205,.1)", paddingLeft: 10, overflowY: "auto", minHeight: 0 }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>TEST RESULTS</div>
        {testEvents.length === 0 && <div className="dim mono" style={{ fontSize: 9.5 }}>No test events yet.</div>}
        {[...testEvents].reverse().map((t, i) => (
          <div className="list-row" key={i}>
            <span className={"state-chip " + (t.status === "SUCCESS" ? "DONE" : t.status === "ERROR" ? "ERROR" : "EXECUTING")}>{t.status}</span>
            <span className="mono" style={{ fontSize: 10 }}>{t.name}</span>
            <span className="list-text dim" style={{ fontSize: 9 }}>{t.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MemoryTab() {
  const [rows, setRows] = useState<MemoryRow[]>([]);
  const [kinds, setKinds] = useState<Record<string, number>>({});
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ score: number; kind: string; content: string }[] | null>(null);
  const [kind, setKind] = useState("FACT");
  const [text, setText] = useState("");
  const load = useCallback(() => {
    memoryV16().then((d) => { setRows(d.rows); setKinds(d.kinds); }).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
        <div style={{ display: "flex", gap: 6 }}>
          <input className="cmd-input" style={{ flex: 1 }} placeholder="Semantic search…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={async (e) => { if (e.key === "Enter" && q.trim()) setHits(await memoryV16Search(q).catch(() => [])); }} />
          <button className="btn" style={{ width: "auto" }} onClick={async () => { await memoryV16Clear(); setHits(null); load(); }}>
            <Trash2 size={11} /> Clear
          </button>
        </div>
        <div className="mono dim" style={{ fontSize: 9 }}>
          {Object.entries(kinds).map(([k, c]) => `${k}:${c}`).join(" · ") || "empty"}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <select className="cmd-input" style={{ width: 120 }} value={kind} onChange={(e) => setKind(e.target.value)}>
            {["PROFILE", "PREFERENCE", "PROJECT", "DEVICE", "TASK", "FACT", "IMPORTANT"].map((k) => <option key={k}>{k}</option>)}
          </select>
          <input className="cmd-input" style={{ flex: 1 }} placeholder="Add memory…" value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={async (e) => { if (e.key === "Enter" && text.trim()) { await memoryV16Add(kind, text); setText(""); setHits(null); load(); } }} />
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {(hits ?? rows.map((r) => ({ score: -1, kind: r.kind, content: r.content, id: r.id })))?.map((r: any, i) => (
            <div className="list-row" key={i} style={{ alignItems: "baseline" }}>
              <span className="state-chip THINKING" style={{ flex: "none" }}>{r.kind}</span>
              {r.score >= 0 && <span className="mono dim" style={{ fontSize: 8.5 }}>{r.score}</span>}
              <span className="list-text" style={{ whiteSpace: "normal" }}>{r.content}</span>
              {r.id != null && (
                <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={async () => { await memoryV16Delete(r.id); setHits(null); load(); }}>
                  <X size={10} />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function AutomationTab() {
  const tools = useApp((s) => s.tools);
  const [audit, setAudit] = useState<string[]>([]);
  useEffect(() => { getAudit().then(setAudit).catch(() => undefined); }, []);
  const gui = tools.filter((t) => t.id.startsWith("gui") || t.id === "screen");
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {gui.map((t) => (
          <div className="list-row" key={t.id}>
            <span className={"dot " + (t.status === "READY" ? "green" : t.status === "RUNNING" ? "amber pulse" : "gray")} style={{ alignSelf: "center" }} />
            <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.1em" }}>{t.label.toUpperCase()}</span>
            <span className="mono dim" style={{ fontSize: 9 }}>{t.status} · {t.detail}</span>
          </div>
        ))}
        <div className="dim mono" style={{ fontSize: 9, marginTop: 8, lineHeight: 1.6 }}>
          Mouse/keyboard automation requires Windows + pyautogui. Destructive actions always require confirmation and are audited.
        </div>
      </div>
      <div style={{ flex: 1, borderLeft: "1px solid rgba(160,180,205,.1)", paddingLeft: 10, overflowY: "auto" }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>TOOL AUDIT</div>
        {audit.filter((l) => /TOOL|GUI|gui/.test(l)).slice(0, 25).map((l, i) => (
          <div className="mono" key={i} style={{ fontSize: 8.5, color: "#9aa4b1", padding: "2px 0", wordBreak: "break-word" }}>{l}</div>
        ))}
      </div>
    </div>
  );
}

function ApprovalsTab() {
  const pendingPatch = useApp((s) => s.pendingPatch);
  const pendingTask = useApp((s) => s.pendingTask);
  const [showDiff, setShowDiff] = useState(true);
  const [audit, setAudit] = useState<string[]>([]);
  useEffect(() => { getAudit().then(setAudit).catch(() => undefined); }, [pendingPatch, pendingTask]);
  return (
    <div style={{ display: "flex", gap: 12, height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1.4, display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
        {pendingTask && (
          <div style={{ border: "1px solid rgba(255,180,84,.5)", borderRadius: 6, padding: 8 }}>
            <div className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>
              TASK {pendingTask.id} · WAITING_APPROVAL · RISK: {pendingTask.risks.join(", ")}
            </div>
            <div style={{ fontSize: 11, color: "#c3ccd8", margin: "4px 0" }}>{pendingTask.text}</div>
            {pendingTask.steps.map((s, i) => (
              <div className="mono" key={i} style={{ fontSize: 9.5, color: s.dangerous ? "var(--red)" : "#9aa4b1" }}>
                {i + 1}. {s.label} [{s.tool}]
              </div>
            ))}
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button className="btn ok-flash" style={{ width: "auto" }} onClick={() => void taskApprove(pendingTask.id)}>Approve & Run</button>
              <button className="btn danger-flash" style={{ width: "auto" }} onClick={() => void taskReject(pendingTask.id)}>Reject</button>
            </div>
          </div>
        )}
        {!pendingPatch && !pendingTask && <div className="dim mono" style={{ fontSize: 10 }}>No patch/task waiting for approval. Ask: “Yeni bir tool oluştur.”</div>}
        {pendingPatch && (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <ShieldCheck size={14} style={{ color: "var(--amber)" }} />
              <span className="mono" style={{ fontSize: 10.5, color: "var(--amber)" }}>
                {pendingPatch.id} · {pendingPatch.source} · WAITING_APPROVAL
              </span>
            </div>
            <div style={{ fontSize: 11, color: "#c3ccd8" }}>{pendingPatch.goal}</div>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn ok-flash" style={{ width: "auto" }} onClick={async () => { await applyPatch(pendingPatch.id); }}>Approve & Apply</button>
              <button className="btn danger-flash" style={{ width: "auto" }} onClick={async () => { await rejectPatch(pendingPatch.id); }}>Reject</button>
              <button className="btn" style={{ width: "auto" }} onClick={() => setShowDiff((v) => !v)}>View Diff</button>
            </div>
            <div className="mono dim" style={{ fontSize: 9 }}>TEST PLAN: {pendingPatch.test_plan.join(" → ")}</div>
            {showDiff && (
              <pre style={{ flex: 1, minHeight: 0, overflow: "auto", margin: 0, fontSize: 9, lineHeight: 1.5, color: "#9fe8c5", background: "rgba(0,0,0,.4)", border: "1px solid rgba(160,180,205,.12)", borderRadius: 4, padding: 8 }}>
                {pendingPatch.files.map((f) => `--- ${f.path}\n${f.diff || "(new file)"}\n`).join("\n")}
              </pre>
            )}
          </>
        )}
      </div>
      <div style={{ flex: 1, borderLeft: "1px solid rgba(160,180,205,.1)", paddingLeft: 10, overflowY: "auto" }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>CODEGEN AUDIT</div>
        {audit.filter((l) => /CODEGEN/.test(l)).slice(0, 20).map((l, i) => (
          <div className="mono" key={i} style={{ fontSize: 8.5, color: "#9aa4b1", padding: "2px 0", wordBreak: "break-word" }}>{l}</div>
        ))}
      </div>
    </div>
  );
}

function AuditTab() {
  const [rows, setRows] = useState<string[]>([]);
  useEffect(() => { getAudit().then(setRows).catch(() => undefined); }, []);
  return (
    <div style={{ overflowY: "auto", height: "100%" }}>
      {rows.map((r, i) => (
        <div className="mono" key={i} style={{ fontSize: 9, color: "#b6bfcb", padding: "2.5px 0", borderBottom: "1px solid rgba(160,180,205,.06)", wordBreak: "break-word" }}>{r}</div>
      ))}
    </div>
  );
}

function VoiceTab({ onArm, onDisarm }: { onArm: () => void; onDisarm: () => void }) {
  const ttsMode = useApp((s) => s.ttsMode);
  const ttsSpeaking = useApp((s) => s.ttsSpeaking);
  const ttsRate = useApp((s) => s.ttsRate);
  const ttsVolume = useApp((s) => s.ttsVolume);
  const voiceArmed = useApp((s) => s.voiceArmed);
  const mic = useApp((s) => s.mic);
  return (
    <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
      <div style={{ width: 260 }}>
        <div className="panel-t" style={{ marginBottom: 6 }}>TTS ENGINE</div>
        <div className="mono" style={{ fontSize: 11, color: "var(--cyan)" }}>
          {ttsSpeaking ? "SPEAKING" : ttsMode.toUpperCase()}
        </div>
        <div className="dim mono" style={{ fontSize: 8.5, marginTop: 4, lineHeight: 1.6 }}>
          Engine: Microsoft Edge Neural TTS. Amplitude drives avatar lip-sync.
        </div>
        <label className="dim mono" style={{ fontSize: 9, display: "block", marginTop: 10 }}>SPEED {ttsRate.toFixed(2)}x</label>
        <input type="range" min={0.7} max={1.4} step={0.05} value={ttsRate} style={{ width: "100%", accentColor: "var(--red)" }}
          onChange={(e) => setState({ ttsRate: Number(e.target.value) })} />
        <label className="dim mono" style={{ fontSize: 9, display: "block" }}>VOLUME {Math.round(ttsVolume * 100)}%</label>
        <input type="range" min={0} max={1} step={0.05} value={ttsVolume} style={{ width: "100%", accentColor: "var(--red)" }}
          onChange={(e) => setState({ ttsVolume: Number(e.target.value) })} />
      </div>
      <div>
        <div className="panel-t" style={{ marginBottom: 6 }}>CONTINUOUS VOICE</div>
        <button className={"btn " + (voiceArmed ? "ok-flash" : "")} style={{ width: "auto" }} onClick={() => (voiceArmed ? onDisarm() : onArm())}>
          {voiceArmed ? "Disarm wake word" : "Arm wake word (ULTRON)"}
        </button>
        <div className="dim mono" style={{ fontSize: 8.5, marginTop: 6, lineHeight: 1.7 }}>
          MIC: {mic.toUpperCase()}<br />
          Disabled by default — no listening until armed. After “ULTRON”, the next utterance is a command; loop returns to wake monitoring.
        </div>
      </div>
    </div>
  );
}

export function Drawer({ onArm, onDisarm }: { onArm: () => void; onDisarm: () => void }) {
  const drawer = useApp((s) => s.drawer);
  const pendingPatch = useApp((s) => s.pendingPatch);
  if (!drawer) return null;
  return (
    <div
      style={{
        position: "fixed", left: 0, right: 0, bottom: 0, height: "44%", zIndex: 70,
        background: "rgba(7,10,15,0.97)", borderTop: "1px solid rgba(255,36,56,.45)",
        boxShadow: "0 -18px 50px rgba(0,0,0,.6)", backdropFilter: "blur(12px)",
        display: "flex", flexDirection: "column", padding: "8px 14px 12px",
      }}
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        {TABS.map((t) => (
          <button
            key={t}
            className="btn"
            style={{ width: "auto", padding: "3px 10px", ...(drawer === t ? { borderColor: "var(--red)", color: "#fff", background: "rgba(255,36,56,.14)" } : {}) }}
            onClick={() => setState({ drawer: t })}
          >
            {t.toUpperCase()}
            {t === "approvals" && pendingPatch && <i className="dot amber pulse" style={{ marginLeft: 6 }} />}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="icon-btn" onClick={() => setState({ drawer: null })}><X size={14} /></button>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        {drawer === "hud" && <HudTab />}
        {drawer === "iot" && <IotTab />}
        {drawer === "code" && <CodeTab />}
        {drawer === "memory" && <MemoryTab />}
        {drawer === "automation" && <AutomationTab />}
        {drawer === "approvals" && <ApprovalsTab />}
        {drawer === "audit" && <AuditTab />}
        {drawer === "voice" && <VoiceTab onArm={onArm} onDisarm={onDisarm} />}
      </div>
    </div>
  );
}

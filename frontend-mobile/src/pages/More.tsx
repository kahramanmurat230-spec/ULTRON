import { useCallback, useEffect, useState } from "react";
import { api, handshake, post, taskApprove, taskReject } from "../lib/api";
import { setState, useM } from "../lib/store";
import { SystemPage } from "./System";

type Sub = "approvals" | "system" | "memory" | "tools" | "activity" | "settings";

const SUBS: Sub[] = ["approvals", "system", "memory", "tools", "activity", "settings"];

export function More() {
  const [sub, setSub] = useState<Sub>("approvals");
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 12 }}>
        {SUBS.map((s) => (
          <button key={s} className={"btn " + (sub === s ? "" : "gray")} style={{ padding: 9, fontSize: 10 }} onClick={() => setSub(s)}>
            {s.toUpperCase()}
          </button>
        ))}
      </div>
      {sub === "approvals" && <Approvals />}
      {sub === "system" && <SystemPage />}
      {sub === "memory" && <Memory />}
      {sub === "tools" && <Tools />}
      {sub === "activity" && <Activity />}
      {sub === "settings" && <Settings />}
    </>
  );
}

function Approvals() {
  const task = useM((s) => s.task);
  const patch = useM((s) => s.patch);
  return (
    <>
      {task && (
        <div className="card" style={{ borderColor: "rgba(255,180,84,.55)" }}>
          <h3>TASK {task.id} · RISK: {(task.risks ?? []).join(", ")}</h3>
          <div style={{ fontSize: 13 }}>{task.text}</div>
          {(task.steps ?? []).map((s: any, i: number) => (
            <div className="ev" key={i} style={{ color: s.dangerous ? "var(--red)" : "#9aa4b1" }}>
              {i + 1}. {s.label} [{s.tool}]
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn green" onClick={() => void taskApprove(task.id).catch(() => undefined)}>APPROVE</button>
            <button className="btn" onClick={() => void taskReject(task.id).catch(() => undefined)}>REJECT</button>
          </div>
        </div>
      )}
      {patch && (
        <div className="card" style={{ borderColor: "rgba(255,180,84,.55)" }}>
          <h3>PATCH {patch.id}</h3>
          <div style={{ fontSize: 13 }}>{patch.goal}</div>
          <pre className="diff">{(patch.files ?? []).map((f: any) => `--- ${f.path}\n${f.diff || "(new)"}\n`).join("\n")}</pre>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn green" onClick={() => void post("/api/codegen/apply", { id: patch.id }).catch(() => undefined)}>APPROVE</button>
            <button className="btn" onClick={() => void post("/api/codegen/reject", { id: patch.id }).catch(() => undefined)}>REJECT</button>
          </div>
        </div>
      )}
      {!task && !patch && (
        <div className="card">
          <h3>APPROVALS</h3>
          <div className="dim mono" style={{ fontSize: 11 }}>Bekleyen onay yok. Riskli görev/patch gelince burada APPROVE/REJECT edilir.</div>
        </div>
      )}
    </>
  );
}

function Memory() {
  const [rows, setRows] = useState<any[]>([]);
  const [kinds, setKinds] = useState<Record<string, number>>({});
  const [kind, setKind] = useState("FACT");
  const [text, setText] = useState("");
  const load = useCallback(() => {
    api("/api/memory/v16").then((d) => { setRows(d.rows ?? []); setKinds(d.kinds ?? {}); }).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  return (
    <div className="card">
      <h3>SHARED MEMORY · SQLITE</h3>
      <div className="dim mono" style={{ fontSize: 10, marginBottom: 8 }}>
        {Object.entries(kinds).map(([k, c]) => `${k}:${c}`).join(" · ") || "boş"} — laptoptakiyle AYNI veritabanı
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 110 }}>
          {["PROFILE", "PREFERENCE", "PROJECT", "DEVICE", "TASK", "FACT", "IMPORTANT"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input placeholder="memory ekle…" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <button className="btn green" style={{ marginTop: 8 }} onClick={async () => {
        if (!text.trim()) return;
        await post("/api/memory/v16/add", { kind, text }).catch(() => undefined);
        setText(""); load();
      }}>ADD</button>
      <div style={{ marginTop: 10 }}>
        {rows.slice(0, 20).map((r) => (
          <div className="row" key={r.id} style={{ alignItems: "baseline" }}>
            <span className="badge c" style={{ flex: "none" }}>{r.kind}</span>
            <span style={{ fontSize: 12.5, flex: 1 }}>{r.content}</span>
            <button className="badge r" onClick={async () => { await post("/api/memory/v16/delete", { id: r.id }).catch(() => undefined); load(); }}>DEL</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function Tools() {
  const tools = useM((s) => s.tools);
  const patch = useM((s) => s.patch);
  const [showDiff, setShowDiff] = useState(true);
  return (
    <>
      {patch && (
        <div className="card" style={{ borderColor: "rgba(255,180,84,.55)" }}>
          <h3>WAITING_APPROVAL · {patch.id}</h3>
          <div style={{ fontSize: 13 }}>{patch.goal}</div>
          <div className="dim mono" style={{ fontSize: 10, margin: "6px 0" }}>TEST PLAN: {patch.test_plan?.join(" → ")}</div>
          {showDiff && <pre className="diff">{patch.files?.map((f: any) => `--- ${f.path}\n${f.diff || "(new file)"}\n`).join("\n")}</pre>}
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn green" onClick={async () => { await post("/api/codegen/apply", { id: patch.id }).catch(() => undefined); }}>APPROVE</button>
            <button className="btn" onClick={async () => { await post("/api/codegen/reject", { id: patch.id }).catch(() => undefined); }}>REJECT</button>
          </div>
        </div>
      )}
      <div className="card">
        <h3>TOOLS (GERÇEK DURUM)</h3>
        {tools.map((t) => (
          <div className="row" key={t.id}>
            <span>{t.label}</span>
            <span className={"badge " + (t.status === "READY" || t.status === "SUCCESS" ? "g" : t.status === "RUNNING" || t.status === "CHECKING" ? "a" : t.status === "UNAVAILABLE" ? "r" : "r")}>
              {t.status}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function Activity() {
  const events = useM((s) => s.agentEvents);
  const agentState = useM((s) => s.agentState);
  return (
    <div className="card">
      <h3>AGENT STATE · {agentState}</h3>
      {events.length === 0 && <div className="dim mono" style={{ fontSize: 11 }}>henüz event yok</div>}
      {[...events].reverse().slice(0, 40).map((e, i) => (
        <div className="ev" key={i}>
          <span className="st">[{e.state}]</span> {e.message ?? ""}
        </div>
      ))}
    </div>
  );
}

function Settings() {
  const connected = useM((s) => s.connected);
  const deviceName = useM((s) => s.deviceName);
  const deviceId = useM((s) => s.deviceId);
  const token = useM((s) => s.token);
  const tts = useM((s) => s.tts);
  const [name, setName] = useState(deviceName);
  return (
    <>
      <div className="card">
        <h3>CONNECTION</h3>
        <div className="row"><span className="dim">Backend</span><span className="mono" style={{ fontSize: 11 }}>{location.host} (V17.1 brain)</span></div>
        <div className="row"><span className="dim">WebSocket</span><span className={"badge " + (connected ? "g" : "r")}>{connected ? "LIVE" : "DOWN"}</span></div>
        <div className="row"><span className="dim">Device token</span><span className="mono" style={{ fontSize: 10 }}>{token ? token.slice(0, 10) + "…" : "yok (auth kapalı)"}</span></div>
      </div>
      <div className="card">
        <h3>DEVICE IDENTITY</h3>
        <div className="row"><span className="dim">Device ID</span><span className="mono" style={{ fontSize: 10 }}>{deviceId.slice(0, 8)}…</span></div>
        <input value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn gray" style={{ marginTop: 8 }} onClick={async () => {
          try { localStorage.setItem("ultron_device", JSON.stringify({ id: deviceId, name })); } catch { /* ignore */ }
          setState({ deviceName: name });
          await handshake();
        }}>SAVE + RE-HANDSHAKE</button>
      </div>
      <div className="card">
        <h3>VOICE</h3>
        <div className="row">
          <span className="dim">Cevapları sesli oku (browser TTS)</span>
          <button className={"badge " + (tts ? "g" : "r")} onClick={() => setState({ tts: !tts })}>{tts ? "ON" : "OFF"}</button>
        </div>
      </div>
    </>
  );
}

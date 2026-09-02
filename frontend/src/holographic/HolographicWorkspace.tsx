import { useEffect, useMemo, useState } from "react";
import { Activity, BrainCircuit, Camera, Cpu, Database, FileStack, Network, Shield, Terminal, Volume2 } from "lucide-react";
import { HoloHead3D } from "../components/HoloHead3D";
import { useApp } from "../lib/store";
import { readHolographicSnapshot } from "./bridge/HolographicBridge";

const panelStyle: React.CSSProperties = {
  background: "linear-gradient(135deg, rgba(5,12,20,.88), rgba(4,8,14,.66))",
  border: "1px solid rgba(56,225,255,.32)",
  boxShadow: "0 0 26px rgba(56,225,255,.08), inset 0 0 22px rgba(56,225,255,.035)",
  backdropFilter: "blur(12px)",
  borderRadius: 12,
};

function Panel({ title, icon, children, className = "" }: { title: string; icon: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={className} style={{ ...panelStyle, padding: 14, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#ff5369", fontSize: 11, fontWeight: 800, letterSpacing: ".12em", textTransform: "uppercase", marginBottom: 10 }}>
        {icon}<span>{title}</span><span style={{ marginLeft: "auto", color: "#38e1ff", opacity: .7 }}>●</span>
      </div>
      {children}
    </section>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  return <div style={{ marginBottom: 8 }}>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#b8c5d1", marginBottom: 4 }}><span>{label}</span><span>{Math.round(value)}%</span></div>
    <div style={{ height: 3, background: "rgba(255,255,255,.08)", borderRadius: 99, overflow: "hidden" }}><div style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: "100%", background: "linear-gradient(90deg,#38e1ff,#ff1e42)", boxShadow: "0 0 10px rgba(255,30,66,.65)" }} /></div>
  </div>;
}

export function HolographicWorkspace() {
  const system = useApp((s) => s.system);
  const agentState = useApp((s) => s.agentState);
  const connected = useApp((s) => s.connected);
  const activity = useApp((s) => s.activity);
  const notifications = useApp((s) => s.notifications);
  const ttsSpeaking = useApp((s) => s.ttsSpeaking);
  const [cameraOn, setCameraOn] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const snapshot = useMemo(() => readHolographicSnapshot(), [system, agentState, connected, activity.length, notifications.length, ttsSpeaking]);

  useEffect(() => {
    if (!cameraOn) return;
    const video = document.getElementById("ultron-holo-camera") as HTMLVideoElement | null;
    if (!video || !navigator.mediaDevices?.getUserMedia) {
      setCameraError("Camera API unavailable");
      return;
    }
    let stream: MediaStream | null = null;
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 360 } }, audio: false })
      .then((s) => { stream = s; video.srcObject = s; return video.play(); })
      .catch(() => setCameraError("Camera permission denied"));
    return () => { stream?.getTracks().forEach((track) => track.stop()); video.srcObject = null; };
  }, [cameraOn]);

  const cpu = system?.cpu.percent ?? 0;
  const gpu = system?.gpu?.util ?? 0;
  const ram = system?.ram.percent ?? 0;
  const disk = system?.disk.percent ?? 0;
  const stateLabel = agentState.replaceAll("_", " ");

  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", color: "#e9f5ff", fontFamily: "Inter, system-ui, sans-serif", background: "radial-gradient(circle at 50% 48%, rgba(26,65,84,.18), transparent 35%), #02060b" }}>
      <div style={{ position: "absolute", inset: 0, opacity: .22, backgroundImage: "linear-gradient(rgba(56,225,255,.08) 1px, transparent 1px), linear-gradient(90deg, rgba(56,225,255,.08) 1px, transparent 1px)", backgroundSize: "48px 48px", transform: "perspective(600px) rotateX(58deg) scale(1.8) translateY(35%)", transformOrigin: "center bottom", pointerEvents: "none" }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,.72) 100%)", pointerEvents: "none" }} />

      <header style={{ position: "absolute", top: 16, left: 22, right: 22, display: "flex", alignItems: "center", zIndex: 5 }}>
        <div style={{ fontSize: 13, letterSpacing: ".14em", color: "#91a8b8" }}>ULTRON OS <b style={{ color: "#38e1ff" }}>v17.1</b></div>
        <div style={{ margin: "0 auto", textAlign: "center" }}><div style={{ fontSize: 24, letterSpacing: ".22em", color: "#ff5267", textShadow: "0 0 20px rgba(255,30,66,.45)" }}>ULTRON</div><div style={{ fontSize: 9, letterSpacing: ".42em", color: "#38e1ff" }}>HOLOGRAPHIC INTERFACE</div></div>
        <div style={{ fontSize: 11, color: connected ? "#41f2a1" : "#ff5369", letterSpacing: ".1em" }}>{connected ? "● ONLINE" : "● OFFLINE"}</div>
      </header>

      <div style={{ position: "absolute", top: 76, left: 20, bottom: 78, width: "23%", minWidth: 220, maxWidth: 340, display: "flex", flexDirection: "column", gap: 12, zIndex: 4 }}>
        <Panel title="Live Camera" icon={<Camera size={13} />}>
          <div style={{ position: "relative", aspectRatio: "16 / 9", background: "#010307", borderRadius: 7, overflow: "hidden", border: "1px solid rgba(56,225,255,.18)" }}>
            <video id="ultron-holo-camera" muted playsInline style={{ width: "100%", height: "100%", objectFit: "cover", opacity: cameraOn ? .9 : .12, transform: "scaleX(-1)" }} />
            {!cameraOn && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#66808e", fontSize: 10 }}>CAMERA STANDBY</div>}
            {cameraOn && !cameraError && <div style={{ position: "absolute", right: 8, bottom: 7, fontSize: 9, color: "#41f2a1" }}>◉ TRACKING INPUT</div>}
          </div>
          <button onClick={() => { setCameraError(null); setCameraOn((v) => !v); }} style={{ marginTop: 8, width: "100%", border: "1px solid rgba(56,225,255,.25)", background: "rgba(56,225,255,.06)", color: "#9bdff0", borderRadius: 6, padding: "7px 10px", cursor: "pointer", fontSize: 10 }}>{cameraOn ? "DISABLE CAMERA" : "ENABLE CAMERA"}</button>
          {cameraError && <div style={{ marginTop: 6, color: "#ff5369", fontSize: 9 }}>{cameraError}</div>}
        </Panel>

        <Panel title="Agent Network" icon={<Network size={13} />}>
          {[["SUPERVISOR", "ONLINE"], ["ORCHESTRATOR", "ONLINE"], ["PLANNER", agentState === "PLANNING" ? "ACTIVE" : "READY"], ["EXECUTOR", agentState === "EXECUTING" ? "ACTIVE" : "READY"], ["FILE AGENT 2.0", "ISOLATED"]].map(([name, status]) => <div key={name} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,.05)", fontSize: 10 }}><span>{name}</span><span style={{ color: status === "ACTIVE" ? "#ffb84a" : status === "ISOLATED" ? "#38e1ff" : "#41f2a1" }}>{status}</span></div>)}
        </Panel>

        <Panel title="Current Tasks" icon={<Activity size={13} />}>
          {activity.slice(0, 5).map((item, i) => <div key={`${item.ts}-${i}`} style={{ display: "flex", gap: 8, padding: "5px 0", fontSize: 10 }}><span style={{ color: "#ff5369" }}>{i + 1}</span><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.text}</span></div>)}
          {!activity.length && <div style={{ color: "#617684", fontSize: 10 }}>No active activity</div>}
        </Panel>
      </div>

      <main style={{ position: "absolute", left: "24%", right: "24%", top: 68, bottom: 62, minWidth: 360, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2 }}>
        <div style={{ position: "absolute", width: "min(34vw, 520px)", aspectRatio: 1, borderRadius: "50%", border: "1px solid rgba(56,225,255,.18)", boxShadow: "0 0 70px rgba(56,225,255,.08), inset 0 0 60px rgba(255,30,66,.05)" }} />
        <div style={{ position: "absolute", width: "min(25vw, 390px)", aspectRatio: 1, borderRadius: "50%", border: "1px dashed rgba(255,30,66,.28)", animation: "ultron-spin 18s linear infinite" }} />
        <div style={{ width: "100%", height: "100%", maxHeight: 650 }}><HoloHead3D /></div>
        <div style={{ position: "absolute", bottom: 16, textAlign: "center", pointerEvents: "none" }}><div style={{ color: "#38e1ff", fontSize: 9, letterSpacing: ".25em" }}>HOLOGRAPHIC CORE</div><div style={{ color: "#ff5369", fontSize: 12, letterSpacing: ".16em", marginTop: 3 }}>{stateLabel}</div></div>
      </main>

      <div style={{ position: "absolute", top: 76, right: 20, bottom: 78, width: "23%", minWidth: 220, maxWidth: 340, display: "flex", flexDirection: "column", gap: 12, zIndex: 4 }}>
        <Panel title="System Telemetry" icon={<Cpu size={13} />}>
          <Meter label="CPU" value={cpu} /><Meter label="GPU" value={gpu} /><Meter label="RAM" value={ram} /><Meter label="DISK" value={disk} />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 10, fontSize: 9, color: "#8299a8" }}><span>NET ↓ {system?.net.down_mbps.toFixed(1) ?? "0.0"} Mbps</span><span>NET ↑ {system?.net.up_mbps.toFixed(1) ?? "0.0"} Mbps</span></div>
        </Panel>
        <Panel title="System Map" icon={<BrainCircuit size={13} />}>
          <div style={{ height: 150, position: "relative", display: "grid", placeItems: "center" }}>
            <div style={{ width: 72, height: 72, borderRadius: "50%", border: "1px solid #ff5369", display: "grid", placeItems: "center", fontSize: 9, color: "#ff5369", boxShadow: "0 0 24px rgba(255,30,66,.25)" }}>ULTRON<br />CORE</div>
            {[["AI", "12% 12%"], ["MEMORY", "72% 15%"], ["TOOLS", "75% 70%"], ["TASKS", "12% 74%"], ["NET", "50% 88%"]].map(([name, pos]) => <div key={name} style={{ position: "absolute", left: pos.split(" ")[0], top: pos.split(" ")[1], transform: "translate(-50%,-50%)", fontSize: 8, color: "#38e1ff", textAlign: "center" }}><span style={{ display: "block", width: 8, height: 8, margin: "0 auto 3px", border: "1px solid #38e1ff", borderRadius: "50%", boxShadow: "0 0 8px rgba(56,225,255,.5)" }} />{name}</div>)}
          </div>
        </Panel>
        <Panel title="Command Console" icon={<Terminal size={13} />}>
          <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 9, lineHeight: 1.8, color: "#71d9ee" }}>&gt; holographic.interface<br />&gt; agent.state ........ <span style={{ color: "#41f2a1" }}>{agentState}</span><br />&gt; telemetry .......... <span style={{ color: "#41f2a1" }}>LIVE</span><br />&gt; bridge ............. <span style={{ color: "#41f2a1" }}>CONNECTED</span></div>
        </Panel>
      </div>

      <footer style={{ position: "absolute", left: 22, right: 22, bottom: 14, height: 40, display: "flex", alignItems: "center", gap: 8, zIndex: 5 }}>
        {[<FileStack key="f" />, <Activity key="t" />, <Database key="m" />, <Shield key="s" />, <Volume2 key="v" />].map((icon, i) => <button key={i} title={["FILES", "TASKS", "MEMORY", "SECURITY", "VOICE"][i]} style={{ ...panelStyle, width: 42, height: 34, padding: 0, display: "grid", placeItems: "center", color: i === 0 ? "#38e1ff" : "#92a7b5", cursor: "pointer" }}>{icon}</button>)}
        <div style={{ flex: 1, height: 3, borderRadius: 99, background: "linear-gradient(90deg, transparent, rgba(56,225,255,.55), transparent)", position: "relative" }}><div style={{ position: "absolute", left: "45%", top: -6, width: 12, height: 12, borderRadius: "50%", background: ttsSpeaking ? "#ff5369" : "#38e1ff", boxShadow: `0 0 18px ${ttsSpeaking ? "rgba(255,83,105,.8)" : "rgba(56,225,255,.7)"}` }} /></div>
        <div style={{ fontSize: 9, letterSpacing: ".1em", color: ttsSpeaking ? "#ff5369" : "#41f2a1", minWidth: 100, textAlign: "right" }}>{ttsSpeaking ? "VOICE ACTIVE" : "VOICE READY"}</div>
        <div style={{ fontSize: 9, color: "#647a89", minWidth: 70, textAlign: "right" }}>FPS {Math.round(snapshot.system ? 60 : 0)}</div>
      </footer>

      <style>{`@keyframes ultron-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } } @media (max-width: 900px) { .holo-hide-mobile { display:none!important; } }`}</style>
    </div>
  );
}

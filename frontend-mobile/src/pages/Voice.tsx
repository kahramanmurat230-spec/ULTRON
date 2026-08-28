import { useEffect, useRef, useState } from "react";
import { api, sendCommand, speak as speakNeural } from "../lib/api";
import { setState, useM } from "../lib/store";

type VState = "IDLE" | "LISTENING" | "PROCESSING" | "SPEAKING";

export function Voice() {
  const agentState = useM((s) => s.agentState);
  const connected = useM((s) => s.connected);
  const [vs, setVs] = useState<VState>("IDLE");
  const [armed, setArmed] = useState(false);
  const [lastText, setLastText] = useState("");
  const recRef = useRef<any>(null);
  const armRef = useRef<any>(null);

  // PROCESSING follows the shared-brain agent state
  useEffect(() => {
    if (agentState === "THINKING" || agentState === "PLANNING" || agentState === "EXECUTING" ||
        agentState === "VERIFYING" || agentState === "WAITING_APPROVAL") {
      setVs("PROCESSING");
    } else if (agentState === "IDLE" || agentState === "DONE" || agentState === "ERROR") {
      setVs((v) => (v === "PROCESSING" ? "IDLE" : v));
    }
  }, [agentState]);

  const speak = (text: string) => {
    // NEURAL ONLY — SAPI5 permanently disabled
    setVs("SPEAKING");
    speakNeural(text).finally(() => setVs(armed ? "LISTENING" : "IDLE"));
  };

  // TTS the final agent answer (Voice 2.0: answer goes to UI AND speaker)
  const chat = useM((s) => s.chat);
  useEffect(() => {
    const last = chat[chat.length - 1];
    if (last && last.role === "ultron") {
      setLastText(last.text);
      speak(last.text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.length]);

  const dispatch = async (text: string) => {
    if (!text.trim() || !connected) return;
    setVs("PROCESSING");
    try { await sendCommand(text.trim()); } catch { setVs("IDLE"); }
  };

  const stopAll = () => {
    try { recRef.current?.stop?.(); } catch { /* ignore */ }
    try { armRef.current?.stop?.(); } catch { /* ignore */ }
    recRef.current = null; armRef.current = null;
    setArmed(false);
    setVs("IDLE");
  };

  const pushToTalk = () => {
    if (vs === "LISTENING" && recRef.current) { recRef.current.stop(); recRef.current = null; setVs("IDLE"); return; }
    const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Ctor) { setLastText("Bu tarayıcıda Web Speech API yok — CHAT sayfasından yazın."); return; }
    const rec = new Ctor();
    recRef.current = rec;
    rec.lang = "tr-TR";
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const t = e.results[e.results.length - 1][0].transcript;
      setLastText("» " + t);
      void dispatch(t);
    };
    rec.onend = () => { recRef.current = null; setVs((v) => (v === "LISTENING" ? "IDLE" : v)); setState({ mic: "idle" }); };
    rec.onerror = () => { recRef.current = null; setVs("IDLE"); setState({ mic: "idle" }); };
    rec.start();
    setState({ mic: "listening" });
    setVs("LISTENING");
  };

  const toggleArm = () => {
    if (armed) { stopAll(); return; }
    const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Ctor) { setLastText("Wake-word için Web Speech API gerekli."); return; }
    const rec = new Ctor();
    armRef.current = rec;
    rec.lang = "tr-TR";
    rec.continuous = true;
    rec.interimResults = true;
    let inCmd = false;
    rec.onresult = (e: any) => {
      const last = e.results[e.results.length - 1];
      const t = String(last[0].transcript).toLowerCase();
      if (!inCmd && t.includes("ultron")) { inCmd = true; setVs("LISTENING"); }
      else if (inCmd && last.isFinal) {
        const cmd = String(last[0].transcript).replace(/ultron/gi, "").trim();
        setLastText("» " + cmd);
        inCmd = false;
        void dispatch(cmd);
      }
    };
    rec.onend = () => { if (armed) { try { rec.start(); } catch { stopAll(); } } };
    rec.onerror = () => stopAll();
    rec.start();
    setArmed(true);
    setVs("IDLE");
  };

  const color = vs === "SPEAKING" ? "var(--green)" : vs === "LISTENING" ? "var(--cyan)" : vs === "PROCESSING" ? "var(--amber)" : "var(--dim)";

  return (
    <>
      <div className="card" style={{ textAlign: "center" }}>
        <h3>VOICE · MIC → STT → AGENT → TTS</h3>
        <div className="mono" style={{ fontSize: 14, letterSpacing: ".3em", color, padding: "10px 0" }}>{vs}</div>
        <div className="dim mono" style={{ fontSize: 10, marginBottom: 10 }}>
          STT: telefon Web Speech · Agent+tools: Windows backend · TTS: telefon hoparlör
        </div>
        <button className="micbtn" style={{ width: 74, height: 74, margin: "0 auto" }} onClick={pushToTalk} aria-label="mic">
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="9" y="3" width="6" height="10" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
          </svg>
        </button>
        <div style={{ marginTop: 12 }}>
          <button className={"btn " + (armed ? "green" : "gray")} onClick={toggleArm}>
            {armed ? "WAKE-WORD ARMED — DURDUR" : "WAKE-WORD ARM (ULTRON)"}
          </button>
          <div className="dim mono" style={{ fontSize: 9.5, marginTop: 6 }}>
            Arm = mikrofon açık kalır, "Ultron" denince komut dinlenir. Varsayılan kapalı (mahremiyet).
          </div>
        </div>
      </div>
      {lastText && (
        <div className="card">
          <h3>SON</h3>
          <div style={{ fontSize: 13.5, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{lastText}</div>
        </div>
      )}
      <MetricsWidget />
    </>
  );
}

function MetricsWidget() {
  const [m, setM] = useState<any>(null);
  useEffect(() => {
    api("/api/voice/metrics/history?hours=24").then(setM).catch(() => undefined);
  }, []);
  if (!m) return null;
  return (
    <div className="card">
      <h3>LATENCY (24h) · {m.count} interaction</h3>
      {m.count === 0 && <div className="dim mono" style={{ fontSize: 10 }}>henüz voice interaction yok</div>}
      {m.count > 0 && (
        <>
          {(["stt_ms", "llm_ms", "tts_ms"] as const).map((k) => (
            <div className="row" key={k}>
              <span className="dim mono" style={{ fontSize: 10 }}>{k}</span>
              <span className="mono" style={{ fontSize: 10 }}>
                p50 {m[k]?.p50 ?? "—"} · p95 {m[k]?.p95 ?? "—"} · p99 {m[k]?.p99 ?? "—"}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

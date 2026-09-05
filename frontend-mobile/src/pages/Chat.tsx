import { useEffect, useRef, useState } from "react";
import { smartCommand } from "../lib/mesh_client";
import { getState, setState, useM } from "../lib/store";

export function Chat() {
  const chat = useM((s) => s.chat);
  const connected = useM((s) => s.connected);
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const recRef = useRef<any>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat]);

  const send = async (t: string) => {
    const clean = t.trim();
    if (!clean) return; // Saha Modu: PC offline olsa da komut asla reddedilmez
    setState({ chat: [...getState().chat, { role: "user" as const, text: clean, ts: Date.now() / 1000 }].slice(-60) });
    setText("");
    try {
      const reply = await smartCommand(clean);
      if (reply !== "→ PC brain") {
        setState({ chat: [...getState().chat, { role: "ultron" as const, text: reply, ts: Date.now() / 1000 }].slice(-60) });
      }
    } catch { /* agent event shows error */ }
  };

  const toggleMic = () => {
    if (listening) { recRef.current?.stop?.(); recRef.current = null; setListening(false); return; }
    const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Ctor) return;
    const rec = new Ctor();
    recRef.current = rec;
    rec.lang = "tr-TR";
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const t = e.results[e.results.length - 1][0].transcript;
      void send(t);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    rec.start();
    setListening(true);
  };

  return (
    <>
      <div className="card" style={{ minHeight: "60%" }}>
        <h3>CHAT · SAME BRAIN</h3>
        <div className="msgs" ref={boxRef} style={{ maxHeight: "52vh", overflowY: "auto" }}>
          {chat.length === 0 && <div className="dim mono" style={{ fontSize: 11 }}>Komut yaz — Windows'taki ULTRON Agent çalıştırır.</div>}
          {chat.map((m, i) => (
            <div key={i} className={"msg " + m.role}>{m.text}</div>
          ))}
        </div>
        <div className="chatbar">
          <input
            placeholder="Google'ı aç · CPU'yu söyle · Ekranımı analiz et…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void send(text); }}
          />
          <button className={"micbtn" + (listening ? " live" : "")} onClick={toggleMic} aria-label="voice">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
              <rect x="9" y="3" width="6" height="10" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          </button>
        </div>
      </div>
    </>
  );
}

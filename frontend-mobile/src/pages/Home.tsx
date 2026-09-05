import { useEffect, useState } from "react";
import type { Page } from "../App";
import { HoloCoreCanvas } from "../components/HoloCoreCanvas";
import { api, speak as speakNeural } from "../lib/api";
import { controlAnywhere, tryIoT } from "../lib/iot_client";
import { getQueueCount } from "../lib/mesh_client";
import { useM } from "../lib/store";

type HoloMode = "IDLE" | "LISTENING" | "SPEAKING" | "ALERT";

export function Home({ go }: { go: (p: Page) => void }) {
  const pcOnline = useM((s) => s.pcOnline);
  const fieldMode = useM((s) => s.fieldMode);
  const speaking = useM((s) => s.speaking);
  const mic = useM((s) => s.mic);
  const agentState = useM((s) => s.agentState);
  const chat = useM((s) => s.chat);
  const [devs, setDevs] = useState<any[]>([]);
  const [queue, setQueue] = useState(0);
  const [fb, setFb] = useState("");

  useEffect(() => {
    const loadDevs = () => {
      if (pcOnline) api("/api/iot/devices").then((d: any) => setDevs(Array.isArray(d) ? d : [])).catch(() => undefined);
      else setDevs([
        { device_id: "light_masa", name: "Masa Lambası", state: "on", value: 80 },
        { device_id: "switch_priz", name: "Priz", state: "on", value: null },
      ]);
      setQueue(getQueueCount());
    };
    loadDevs();
    const iv = setInterval(loadDevs, 8000);
    return () => clearInterval(iv);
  }, [pcOnline]);

  const mode: HoloMode = (fieldMode && agentState === "ERROR") || agentState === "ERROR" ? "ALERT"
    : speaking ? "SPEAKING" : mic === "listening" ? "LISTENING" : "IDLE";
  const lastMsg = chat.length ? chat[chat.length - 1] : null;

  const diag = async () => {
    try {
      const d: any = await api("/api/hud/self_diagnostic", { method: "POST" });
      setFb(d.report?.slice(0, 140) ?? "");
      speakNeural(d.report ?? ""); // NEURAL ONLY — SAPI5 disabled
    } catch {
      setFb("Saha modu: PC'ye ulaşılamadı — yerel sistemler yeşil.");
    }
  };

  return (
    <>
      {/* ÜST BAR */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", padding: "4px 0 8px" }}>
        <span className="badge a">BOSS</span>
        <span className={"badge " + (pcOnline ? "g" : "r")}>🖥 PC {pcOnline ? "ON" : "OFF"}</span>
        <span className="badge g">📱 MOB ON</span>
        {fieldMode && <span className="badge a">SAHA MODU · ACTIVE</span>}
        <span className="badge c">QUEUE: {queue}</span>
      </div>

      {/* MERKEZ: HoloCore + altyazı */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "6px 0" }}>
        <HoloCoreCanvas mode={mode} theme="CRIMSON" alert={mode === "ALERT"}
          amp={speaking ? 0.6 : mic === "listening" ? 0.4 : 0} size={210} />
        <div className="mono" style={{ minHeight: 30, fontSize: 10.5, color: "var(--cyan)", textAlign: "center", padding: "4px 8px" }}>
          {lastMsg ? `▸ ${lastMsg.text.slice(0, 90)}` : "…"}
        </div>
      </div>

      {/* ALT PANEL: tek-el IoT + sahneler */}
      <div className="card">
        <h3>SAHA KONTROL · TEK EL</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          {devs.slice(0, 4).map((d) => (
            <button key={d.device_id} className="btn gray"
              onClick={async () => setFb(await controlAnywhere(d.device_id, "toggle"))}>
              {d.device_id.includes("light") ? "💡" : "🔌"} {String(d.name).split(" ")[0]} · {d.state}
            </button>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
          <button className="btn gray" onClick={async () => setFb((await tryIoT("ışığı aç")) || "")}>💡 IŞIK</button>
          <button className="btn gray" onClick={async () => setFb((await tryIoT("kodlama moduna geç")) || "")}>💻 CODING</button>
          <button className="btn gray" onClick={async () => setFb((await tryIoT("sinema modu")) || "")}>🎬 CINEMA</button>
          <button className="btn gray" onClick={async () => setFb((await tryIoT("her şeyi kapat")) || "")}>⛔ ALL OFF</button>
        </div>
        <button className="btn" style={{ marginTop: 6 }} onClick={diag}> SELF-DIAGNOSTIC (SESLİ)</button>
        {fb && <div className="mono" style={{ fontSize: 10, marginTop: 6, color: "var(--cyan)" }}>{fb}</div>}
      </div>

      <div style={{ display: "flex", gap: 6, paddingBottom: 8 }}>
        <button className="btn gray" onClick={() => go("voice")}>🎙 VOICE</button>
        <button className="btn gray" onClick={() => go("chat")}>💬 CHAT</button>
        <button className="btn gray" onClick={() => go("more")}>⚙ MORE</button>
      </div>
    </>
  );
}

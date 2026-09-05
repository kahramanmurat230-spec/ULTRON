import { useEffect, useState } from "react";
import { api, sendCommand } from "../lib/api";
import { useM } from "../lib/store";

export function Vision() {
  const agentEvents = useM((s) => s.agentEvents);
  const agentState = useM((s) => s.agentState);
  const [path, setPath] = useState("");
  const [last, setLast] = useState<{ exists: boolean; name?: string }>({ exists: false });

  const visionMsgs = agentEvents.filter((e) =>
    (e.state === "DONE" || e.state === "ERROR") &&
    e.message && /EKRAN ANALİZİ|VISION|SCREENSHOT/i.test(e.message));
  const lastMsg = visionMsgs[visionMsgs.length - 1];

  useEffect(() => {
    api("/api/vision/last").then(setLast).catch(() => setLast({ exists: false }));
  }, [agentState]);

  return (
    <>
      <div className="card">
        <h3>PC VISION · LLaVA</h3>
        <div className="dim mono" style={{ fontSize: 10.5, marginBottom: 10 }}>
          Telefon kamerası DEĞİL — Windows capture_screen → gerçek PNG → llava:7b.
        </div>
        <button className="btn" onClick={() => void sendCommand("Ekranımı analiz et ve ekranda ne gördüğünü anlat.")}>
          EKRANIMI ANALİZ ET
        </button>
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <input placeholder="Bu ekran görüntüsünü analiz et: C:\…\x.png" value={path} onChange={(e) => setPath(e.target.value)} />
        </div>
        {path.trim() && (
          <button className="btn gray" style={{ marginTop: 8 }} onClick={() => void sendCommand("Bu ekran görüntüsünü analiz et: " + path.trim())}>
            ANALYZ THIS PNG
          </button>
        )}
      </div>

      {lastMsg && (
        <div className="card">
          <h3>ANALİZ SONUCU · {lastMsg.state}</h3>
          <div style={{ fontSize: 13.5, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{lastMsg.message}</div>
        </div>
      )}

      <div className="card">
        <h3>SON SCREENSHOT</h3>
        {last.exists ? (
          <img
            src={"/api/vision/preview?name=" + encodeURIComponent(last.name!)}
            alt="screen"
            style={{ width: "100%", borderRadius: 10, border: "1px solid var(--edge)" }}
          />
        ) : (
          <div className="dim mono" style={{ fontSize: 11 }}>henüz screenshot yok</div>
        )}
      </div>
    </>
  );
}

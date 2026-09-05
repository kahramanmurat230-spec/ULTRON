import { setState, useApp } from "../lib/store";
import { Panel } from "./Panel";

function Row({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "3px 0" }}>
      <span className="dim" style={{ fontSize: 9.5, letterSpacing: "0.14em", fontFamily: "var(--font-mono)" }}>{k}</span>
      <span className="mono" style={{ fontSize: 10, color: tone ?? "#c3ccd8", textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {v}
      </span>
    </div>
  );
}

export function AIStatus() {
  const ai = useApp((s) => s.ai);
  return (
    <Panel
      title="AI Status"
      right={
        <button className="btn" style={{ width: "auto", padding: "2px 8px", fontSize: 8.5 }} onClick={() => setState({ settingsOpen: true })}>
          Change
        </button>
      }
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0 6px" }}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={ai?.connected ? "var(--cyan)" : "#566070"} strokeWidth="1.6" aria-hidden>
          <rect x="7" y="7" width="10" height="10" rx="2" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.5 4.5l2 2M17.5 17.5l2 2M19.5 4.5l-2 2M6.5 17.5l-2 2" />
        </svg>
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.16em" }}>OLLAMA</div>
          <div className="mono" style={{ fontSize: 9.5, color: ai?.connected ? "var(--green)" : "var(--red)" }}>
            {ai === null ? "PROBING…" : ai.connected ? "CONNECTED" : "OFFLINE"}
          </div>
        </div>
      </div>
      <div style={{ borderTop: "1px solid rgba(160,180,205,.09)", paddingTop: 4 }}>
        <Row k="PRIMARY" v={ai?.primary ?? "N/A"} tone={ai?.primary ? "var(--cyan)" : undefined} />
        <Row k="VISION" v={ai?.vision ?? "UNAVAILABLE"} tone={ai?.vision ? "var(--cyan)" : "var(--dim)"} />
        <Row k="EMBEDDING" v={ai?.embedding ?? "N/A"} tone={ai?.embedding ? "var(--cyan)" : undefined} />
        <Row k="HOST" v={ai?.host ?? "—"} />
        <Row k="MODE" v="INTELLIGENT" tone="var(--green)" />
      </div>
    </Panel>
  );
}

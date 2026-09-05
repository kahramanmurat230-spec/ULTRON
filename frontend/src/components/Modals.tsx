import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getAudit } from "../lib/api";
import { setState, useApp } from "../lib/store";
import { fmtTime, toolDot, toolTone } from "./hudHelpers";

function Shell({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="overlay" onClick={onClose}>
      <motion.div
        className="modal"
        initial={{ opacity: 0, scale: 0.94, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 8 }}
        transition={{ duration: 0.18 }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="panel-h">
          <span className="panel-t">{title}</span>
          <button className="icon-btn" onClick={onClose}>
            <X size={14} />
          </button>
        </header>
        <div className="panel-b">{children}</div>
        <i className="hud c-tl" />
        <i className="hud c-tr" />
        <i className="hud c-bl" />
        <i className="hud c-br" />
      </motion.div>
    </div>
  );
}

const KV = ({ k, v }: { k: string; v: string }) => (
  <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 2px", borderBottom: "1px solid rgba(160,180,205,.07)" }}>
    <span className="dim mono" style={{ fontSize: 9.5, letterSpacing: "0.14em" }}>{k}</span>
    <span className="mono" style={{ fontSize: 10.5, color: "#c3ccd8", textAlign: "right" }}>{v}</span>
  </div>
);

function AuditModal() {
  const [rows, setRows] = useState<string[] | null>(null);
  useEffect(() => {
    getAudit()
      .then(setRows)
      .catch(() => setRows([]));
  }, []);
  return (
    <Shell title="Audit Log (V16)" onClose={() => setState({ listModal: null })}>
      {rows === null && <div className="dim mono" style={{ fontSize: 10 }}>LOADING…</div>}
      {rows !== null && rows.length === 0 && (
        <div className="dim mono" style={{ fontSize: 10 }}>NO AUDIT ENTRIES</div>
      )}
      {rows?.map((r, i) => (
        <div className="mono" key={i} style={{ fontSize: 9.5, color: "#b6bfcb", padding: "2.5px 0", borderBottom: "1px solid rgba(160,180,205,.06)", lineHeight: 1.5, wordBreak: "break-word" }}>
          {r}
        </div>
      ))}
    </Shell>
  );
}

export function Modals() {
  const settingsOpen = useApp((s) => s.settingsOpen);
  const listModal = useApp((s) => s.listModal);
  const config = useApp((s) => s.config);
  const activity = useApp((s) => s.activity);
  const notifications = useApp((s) => s.notifications);
  const tools = useApp((s) => s.tools);

  return (
    <AnimatePresence>
      {settingsOpen && (
        <Shell title="Settings · Configuration" onClose={() => setState({ settingsOpen: false })}>
          <KV k="VERSION" v={`ULTRON V${config?.version ?? "15.0.0"}`} />
          <KV k="OLLAMA HOST" v={config?.ollama_host ?? "—"} />
          <KV k="PRIMARY MODEL" v={config?.primary_model ?? "N/A (offline)"} />
          <KV k="PERSISTENT MEMORY" v={config?.persistent_memory ? "ENABLED" : "DISABLED"} />
          <KV k="WORKSPACE" v={config?.workspace ?? "—"} />
          <div className="dim mono" style={{ fontSize: 9, marginTop: 10, lineHeight: 1.6 }}>
            Configuration is environment-driven on the backend (ULTRON_OLLAMA_HOST, ULTRON_MODEL,
            ULTRON_PERSISTENT_MEMORY). No secrets are stored in the frontend.
          </div>
        </Shell>
      )}
      {listModal === "activity" && (
        <Shell title="Full Activity Log" onClose={() => setState({ listModal: null })}>
          {activity.length === 0 && <div className="dim mono" style={{ fontSize: 10 }}>EMPTY</div>}
          {activity.map((a, i) => (
            <div className="list-row" key={i}>
              <span className="list-time">{fmtTime(a.ts)}</span>
              <span className="list-text" style={{ whiteSpace: "normal" }}>{a.text}</span>
            </div>
          ))}
        </Shell>
      )}
      {listModal === "notifications" && (
        <Shell title="All Notifications" onClose={() => setState({ listModal: null })}>
          {notifications.length === 0 && <div className="dim mono" style={{ fontSize: 10 }}>EMPTY</div>}
          {notifications.map((n, i) => (
            <div className="list-row" key={i}>
              <span className="list-time">{fmtTime(n.ts)}</span>
              <span className="list-text" style={{ whiteSpace: "normal" }}>[{n.level}] {n.text}</span>
            </div>
          ))}
        </Shell>
      )}
      {listModal === "audit" && <AuditModal />}
      {listModal === "tools" && (
        <Shell title="Tool Registry · Live Status" onClose={() => setState({ listModal: null })}>
          {tools.map((t) => (
            <div key={t.id} style={{ padding: "6px 2px", borderBottom: "1px solid rgba(160,180,205,.07)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.14em" }}>{t.label.toUpperCase()}</span>
                <span className="mono" style={{ fontSize: 9.5, color: toolTone(t.status) }}>
                  <i className={"dot " + toolDot(t.status)} style={{ marginRight: 6, width: 5, height: 5 }} />
                  {t.status}
                </span>
              </div>
              <div className="mono dim" style={{ fontSize: 9, marginTop: 2 }}>{t.detail || "—"}</div>
            </div>
          ))}
        </Shell>
      )}
    </AnimatePresence>
  );
}

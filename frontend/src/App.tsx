import { Layers, Settings } from "lucide-react";
import { useEffect } from "react";
import { useVoice } from "./hooks/useVoice";
import { connectWS } from "./lib/api";
import { setState, useApp } from "./lib/store";
import { applyTheme, saveTheme } from "./styles/themes";
import { ParticleSphere } from "./components/ParticleSphere";
import { TerminalChat } from "./components/TerminalChat";
import { PTTBar } from "./components/PTTBar";
import { Drawer } from "./components/Drawer";
import { Modals } from "./components/Modals";
import { JarvisHUD } from "./components/JarvisHUD";

/**
 * ULTRON — Void Core UI.
 * JARVIS capability cockpit is layered on top of the existing 3D/voice/runtime
 * stack; it does not replace the established controls or safety boundaries.
 */
export default function App() {
  const minimized = useApp((s) => s.minimized);
  const closed = useApp((s) => s.closed);
  const theme = useApp((s) => s.theme);
  const agentState = useApp((s) => s.agentState);
  const voice = useVoice();

  useEffect(() => {
    applyTheme(theme, agentState === "ERROR");
    fetch("/api/ui/theme", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: theme }) }).catch(() => undefined);
  }, [theme, agentState]);

  useEffect(() => {
    if (window.location.pathname !== "/cockpit") window.history.replaceState(null, "", "/cockpit");
    try {
      if (!localStorage.getItem("ultron_theme")) saveTheme("CRIMSON");
      localStorage.setItem("ultron_view", "cockpit");
    } catch { /* private mode */ }
  }, []);

  useEffect(() => {
    connectWS();
    const unlockOnce = () => {
      void import("./lib/tts").then((m) => m.unlock());
      window.removeEventListener("pointerdown", unlockOnce);
      window.removeEventListener("keydown", unlockOnce);
    };
    window.addEventListener("pointerdown", unlockOnce);
    window.addEventListener("keydown", unlockOnce);
    return () => {
      window.removeEventListener("pointerdown", unlockOnce);
      window.removeEventListener("keydown", unlockOnce);
    };
  }, []);

  if (closed) {
    return (
      <div className="overlay">
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: "0.4em", color: "var(--red)", textShadow: "0 0 20px rgba(255,26,26,.6)" }}>SESSION TERMINATED</div>
          <div className="dim mono" style={{ fontSize: 10, margin: "10px 0 18px", letterSpacing: "0.14em" }}>ULTRON backend link remains live · UI session closed by operator</div>
          <button className="btn" style={{ width: "auto", margin: "0 auto" }} onClick={() => location.reload()}>Restart Session</button>
        </div>
      </div>
    );
  }

  return (
    <div className="ultron-root">
      {!minimized && (
        <>
          <TerminalChat />
          <div className="stage-wrap">
            <ParticleSphere />
            <PTTBar onVoice={voice.toggle} />
          </div>
          <JarvisHUD />
        </>
      )}
      <div className="void-corner">
        <button className="ghost" title="console" onClick={() => setState({ drawer: "hud" })}><Layers size={13} /></button>
        <button className="ghost" title="settings" onClick={() => setState({ settingsOpen: true })}><Settings size={13} /></button>
      </div>
      <Drawer onArm={() => void voice.arm()} onDisarm={voice.disarm} />
      <Modals />
      {minimized && <button className="min-pill" onClick={() => setState({ minimized: false })}>◈ ULTRON · RESTORE</button>}
    </div>
  );
}

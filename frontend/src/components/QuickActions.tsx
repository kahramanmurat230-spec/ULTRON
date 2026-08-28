import { Camera, Cog, Globe, Mic, Trash2 } from "lucide-react";
import { useState } from "react";
import { captureScreen, clearMemory, openBrowser, systemCheck } from "../lib/api";
import { Panel } from "./Panel";

type Flash = "idle" | "busy" | "ok" | "err";

export function QuickActions({ onVoice }: { onVoice: () => void }) {
  const [flash, setFlash] = useState<Record<string, Flash>>({});
  const set = (id: string, v: Flash) => setFlash((f) => ({ ...f, [id]: v }));

  const run = async (id: string, fn: () => Promise<{ ok: boolean }>) => {
    if (flash[id] === "busy") return;
    set(id, "busy");
    try {
      const r = await fn();
      set(id, r.ok ? "ok" : "err");
    } catch {
      set(id, "err");
    }
    setTimeout(() => set(id, "idle"), 1800);
  };

  const cls = (id: string) =>
    "btn" + (flash[id] === "ok" ? " ok-flash" : flash[id] === "err" ? " danger-flash" : "");

  return (
    <Panel title="Quick Actions" className="flex-1">
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        <button className={cls("shot")} disabled={flash.shot === "busy"} onClick={() => run("shot", captureScreen)}>
          <Camera size={13} /> Take Screenshot
        </button>
        <button className={cls("browser")} disabled={flash.browser === "busy"} onClick={() => run("browser", openBrowser)}>
          <Globe size={13} /> Open Browser
        </button>
        <button className={cls("check")} disabled={flash.check === "busy"} onClick={() => run("check", systemCheck)}>
          <Cog size={13} /> System Check
        </button>
        <button className={cls("mem")} disabled={flash.mem === "busy"} onClick={() => run("mem", clearMemory)}>
          <Trash2 size={13} /> Clear Memory
        </button>
        <button className="btn" onClick={onVoice}>
          <Mic size={13} /> Voice Command
        </button>
      </div>
    </Panel>
  );
}

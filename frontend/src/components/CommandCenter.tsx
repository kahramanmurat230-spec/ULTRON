import { Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { executeCommand } from "../lib/api";
import { audioLevel, useApp } from "../lib/store";

/** Thin neon reactive waveform (mic amplitude when listening). */
function Waveform() {
  const mic = useApp((s) => s.mic);
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const w = (cv.width = cv.offsetWidth * 2);
      const h = (cv.height = cv.offsetHeight * 2);
      ctx.clearRect(0, 0, w, h);
      const mid = h / 2;
      ctx.strokeStyle = "rgba(255,36,56,.25)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();
      const n = 110;
      for (let i = 0; i < n; i++) {
        const v = mic === "listening" ? audioLevel.current * (0.4 + 0.6 * Math.abs(Math.sin(i * 0.7 + performance.now() / 90))) : 0;
        const x = (i / (n - 1)) * w;
        const bh = Math.max(1, v * (h - 6));
        ctx.strokeStyle = `rgba(255,36,56,${0.25 + v * 0.75})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x, mid - bh);
        ctx.lineTo(x, mid + bh);
        ctx.stroke();
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [mic]);
  return <canvas ref={ref} className="wave" />;
}

/** Cinematic void command bar: waveform + film subtitle + slim input. */
export function CommandCenter({ onVoice, className }: { onVoice: () => void; className?: string }) {
  const events = useApp((s) => s.agentEvents);
  const mic = useApp((s) => s.mic);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const last = [...events].reverse().find((e) => (e.state === "DONE" || e.state === "ERROR") && e.message)?.message;

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    setErr(null);
    setText("");
    try {
      const r = await executeCommand(t);
      if (!r.ok) setErr(r.error ?? "command rejected");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "backend unreachable");
    }
  };

  return (
    <div className={"void-cmd" + (className ? " " + className : "")}>
      <Waveform />
      <div className="subtitle">{last ?? "…"}</div>
      <div className="void-row">
        <input
          ref={inputRef}
          className="void-input"
          placeholder="komut fısılda…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
        />
        <button
          className={"mic-btn" + (mic === "listening" ? " live" : mic === "unavailable" ? " dead" : "")}
          title="voice"
          onClick={onVoice}
        >
          <Mic size={15} />
        </button>
      </div>
      {err && <div className="mono void-err">⚠ {err}</div>}
    </div>
  );
}

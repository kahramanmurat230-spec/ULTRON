import { useEffect, useRef } from "react";

/** Canonical mirror of backend room_presence.holo_mode */
export function holoMode(agentState: string, listening: boolean, speaking: boolean): string {
  if (speaking) return "SPEAKING";
  if (listening || agentState === "LISTENING") return "LISTENING";
  if (agentState === "ERROR") return "ALERT";
  if (["THINKING", "PLANNING", "EXECUTING", "VERIFYING"].includes(agentState)) return "DEEP_COMPUTE";
  return "IDLE";
}

/** Lightweight canvas holographic core — no heavy 3D libs. */
export function HoloCore({ agentState, listening = false, speaking = false,
                           ampRef, size = 120 }: {
  agentState: string; listening?: boolean; speaking?: boolean;
  ampRef?: { current: number }; size?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const st = useRef({ mode: "IDLE" });
  st.current.mode = holoMode(agentState, listening, speaking);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    const draw = (tms: number) => {
      raf = requestAnimationFrame(draw);
      const t = tms / 1000;
      const S = cv.width;
      const c = S / 2;
      ctx.clearRect(0, 0, S, S);
      const mode = st.current.mode;
      const amp = ampRef?.current ?? (mode === "SPEAKING" ? 0.5 + 0.3 * Math.sin(t * 9) : 0);
      const red = "255,36,56", cyan = "56,225,255";
      // core
      const pulse = mode === "IDLE" ? 0.16 * Math.sin(t * 1.4)
        : mode === "ALERT" ? 0.3 * Math.sin(t * 14) : 0.22 * Math.sin(t * (mode === "SPEAKING" ? 9 : 4));
      const r0 = S * (0.16 + 0.02 * pulse + 0.05 * amp);
      const g = ctx.createRadialGradient(c, c, 0, c, c, r0 * 2.2);
      g.addColorStop(0, `rgba(${red},0.9)`);
      g.addColorStop(0.5, `rgba(${red},0.25)`);
      g.addColorStop(1, `rgba(${red},0)`);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(c, c, r0 * 2.2, 0, 7); ctx.fill();
      // rings
      const rings = mode === "LISTENING" ? 4 : mode === "DEEP_COMPUTE" ? 5 : 3;
      for (let i = 0; i < rings; i++) {
        const speed = mode === "DEEP_COMPUTE" ? 3.2 : mode === "ALERT" ? 5 : mode === "SPEAKING" ? 2.4 : 0.7;
        const rr = mode === "LISTENING"
          ? (S * 0.2 + ((t * 30 + i * 40) % (S * 0.3)))
          : S * (0.26 + i * 0.07) + (mode === "SPEAKING" ? amp * 8 * Math.sin(t * 10 + i) : 0);
        ctx.strokeStyle = i % 2 ? `rgba(${cyan},0.5)` : `rgba(${red},0.6)`;
        ctx.lineWidth = mode === "ALERT" ? 2.4 : 1.2;
        ctx.beginPath();
        ctx.arc(c, c, rr, t * speed + i, t * speed + i + Math.PI * (mode === "DEEP_COMPUTE" ? 1.2 : 1.7));
        ctx.stroke();
      }
      // particles for deep compute
      if (mode === "DEEP_COMPUTE" || mode === "ALERT") {
        for (let i = 0; i < 24; i++) {
          const a = t * (mode === "ALERT" ? 6 : 2.6) + (i * Math.PI * 2) / 24;
          const rr = S * (0.34 + 0.06 * Math.sin(t * 5 + i));
          ctx.fillStyle = i % 3 ? `rgba(${red},0.8)` : `rgba(${cyan},0.8)`;
          ctx.fillRect(c + Math.cos(a) * rr, c + Math.sin(a) * rr, 2, 2);
        }
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [ampRef]);

  return <canvas ref={ref} width={size} height={size}
    style={{ width: size, height: size, display: "block" }} />;
}

import { useEffect, useRef } from "react";
import { resolveTheme } from "../lib/themes";

/**
 * HoloCoreCanvas — battery-friendly HTML5 canvas voice core.
 * IDLE breathing ring · LISTENING mic ripples · SPEAKING reactive waves ·
 * ALERT/SAHA red flasher. Theme colors from mesh sync or local.
 */
export function HoloCoreCanvas({ mode, amp = 0, theme = "CRIMSON", alert = false, size = 220 }: {
  mode: "IDLE" | "LISTENING" | "SPEAKING" | "ALERT";
  amp?: number; theme?: string; alert?: boolean; size?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const st = useRef({ mode, amp, theme, alert });
  st.current = { mode, amp, theme, alert };

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    const draw = (tms: number) => {
      raf = requestAnimationFrame(draw);
      const t = tms / 1000;
      const { mode, amp, theme, alert } = st.current;
      const th = resolveTheme(theme as any, alert || mode === "ALERT");
      const S = cv.width, c = S / 2;
      ctx.clearRect(0, 0, S, S);
      const acc = th.red, sec = th.cyan;
      const flash = mode === "ALERT" ? (Math.sin(t * 12) > 0 ? 0.9 : 0.25) : 0;
      // plasma core
      const breathe = mode === "IDLE" ? 0.06 * Math.sin(t * 1.2) : 0;
      const r0 = S * (0.17 + breathe + amp * 0.05);
      const g = ctx.createRadialGradient(c, c, 0, c, c, r0 * 2.4);
      g.addColorStop(0, hexA(acc, 0.85 + flash * 0.15));
      g.addColorStop(0.45, hexA(acc, 0.25));
      g.addColorStop(1, hexA(acc, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(c, c, r0 * 2.4, 0, 7); ctx.fill();
      // inner ring
      ctx.strokeStyle = hexA(acc, 0.9);
      ctx.lineWidth = 2 + amp * 3;
      ctx.beginPath(); ctx.arc(c, c, r0, 0, 7); ctx.stroke();
      // state rings
      if (mode === "LISTENING") {
        for (let i = 0; i < 3; i++) {
          const rr = S * 0.2 + ((t * 40 + i * 45) % (S * 0.28));
          ctx.strokeStyle = hexA(sec, Math.max(0, 0.7 - rr / (S * 0.5)));
          ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.arc(c, c, rr, 0, 7); ctx.stroke();
        }
      } else if (mode === "SPEAKING") {
        for (let i = 0; i < 40; i++) {
          const a = (i / 40) * Math.PI * 2;
          const wob = 0.5 + amp * Math.sin(t * 10 + i * 1.7);
          const rr = S * (0.24 + 0.05 * wob);
          ctx.strokeStyle = hexA(i % 2 ? sec : acc, 0.8);
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(c + Math.cos(a) * rr, c + Math.sin(a) * rr);
          ctx.lineTo(c + Math.cos(a) * (rr + 6 + amp * 14 * Math.abs(Math.sin(t * 9 + i))),
                     c + Math.sin(a) * (rr + 6 + amp * 14 * Math.abs(Math.sin(t * 9 + i))));
          ctx.stroke();
        }
      } else {
        // IDLE / ALERT outer rings
        const jitter = mode === "ALERT" ? Math.sin(t * 30) * 3 : 0;
        for (let i = 0; i < 2; i++) {
          ctx.strokeStyle = hexA(i ? sec : acc, 0.5 + flash * 0.4);
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(c, c, S * (0.3 + i * 0.08) + jitter, t * (0.4 + i * 0.3), t * (0.4 + i * 0.3) + Math.PI * 1.6);
          ctx.stroke();
        }
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} width={size} height={size}
    style={{ width: size, height: size, display: "block", transition: "filter .3s" }} />;
}

function hexA(hex: string, a: number) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a))})`;
}

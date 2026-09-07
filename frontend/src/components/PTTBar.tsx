import { Mic } from "lucide-react";
import { useApp } from "../lib/store";

export type PhaseLabel = "IDLE" | "LISTENING" | "THINKING" | "SPEAKING";

/** Ajanın anlık faz etiketini store'dan türet (küre animasyonuyla aynı mantık). */
export function derivePhase(): PhaseLabel {
  const s = { mic: useApp((x) => x.mic), agent: useApp((x) => x.agentState), tts: useApp((x) => x.ttsSpeaking) };
  if (s.tts) return "SPEAKING";
  if (s.mic === "listening" || s.agent === "LISTENING") return "LISTENING";
  if (["THINKING", "PLANNING", "EXECUTING", "VERIFYING"].includes(s.agent)) return "THINKING";
  return "IDLE";
}

/**
 * PTTBar — alt bar: minimal Push-To-Talk butonu.
 *
 * Mouse/parmak basılı → PTT turu başlar (mevcut /api/voice/ptt akışı,
 * Whisper STT). onVoice, App'teki useVoice().toggle'a bağlıdır; Web Speech
 * API KULLANILMAZ, backend PTT mantığı korunur. Sağda faz göstergesi.
 */
export function PTTBar({ onVoice }: { onVoice: () => void }) {
  const phase = derivePhase();
  const live = phase === "LISTENING";

  const start = () => {
    // basılı tutunca / dokununca yerel PTT turunu tetikle
    if (phase !== "LISTENING") onVoice();
  };
  const stop = () => {
    // aktifken bırakınca turu erken sonlandır (mic dinliyorsa)
    if (phase === "LISTENING") onVoice();
  };

  return (
    <div className="ptt-bar">
      <button
        className={"ptt-btn" + (live ? " live" : "")}
        title="Push-To-Talk — bas ve konuş"
        onPointerDown={start}
        onPointerUp={stop}
        onPointerLeave={() => { if (live) stop(); }}
      >
        <Mic size={18} />
      </button>
      <div className="ptt-meta">
        <div className="ptt-label">PUSH-TO-TALK</div>
        <div className={"ptt-phase p-" + phase.toLowerCase()}>{phase}</div>
      </div>
    </div>
  );
}

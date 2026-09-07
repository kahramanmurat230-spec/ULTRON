import { useCallback, useEffect, useRef } from "react";
import { pushToTalk, sendMicState, sendVoiceCommand, sendVoiceReport } from "../lib/api";
import { cancelSpeech } from "../lib/tts";
import { audioLevel, getState, setState } from "../lib/store";

interface SRAlternative {
  new (): any;
}

export function speechRecognitionCtor(): SRAlternative | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function useVoice(): { toggle: () => void; supported: boolean; arm: () => void; disarm: () => void } {
  const recRef = useRef<any>(null);
  const audioRef = useRef<{ ctx: AudioContext; stream: MediaStream; raf: number } | null>(null);
  const wakeRef = useRef<{ rec: any; stream: MediaStream } | null>(null);
  const cmdMode = useRef(false);
  const supported = true; // Electron/Windows PTT uses the local backend, not Web Speech.

  // Report real client capability to the backend once.
  useEffect(() => {
    sendVoiceReport(true, "local push-to-talk backend");
  }, []);

  const stopAudio = useCallback(() => {
    const a = audioRef.current;
    if (a) {
      cancelAnimationFrame(a.raf);
      a.stream.getTracks().forEach((t) => t.stop());
      a.ctx.close().catch(() => undefined);
      audioRef.current = null;
    }
    audioLevel.current = 0;
  }, []);

  const stop = useCallback(() => {
    recRef.current?.stop?.();
    recRef.current = null;
    stopAudio();
    sendMicState(false);
    setState({ mic: "idle" });
  }, [stopAudio]);

  const start = useCallback(async () => {
    const Ctor = speechRecognitionCtor();
    if (!Ctor) {
      setState({ mic: "unavailable" });
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        audioLevel.current = Math.min(1, Math.sqrt(sum / buf.length) * 3.2);
        if (getState().ttsSpeaking && audioLevel.current > 0.15) cancelSpeech();
        if (audioRef.current) audioRef.current.raf = requestAnimationFrame(tick);
      };
      audioRef.current = { ctx, stream, raf: requestAnimationFrame(tick) };
    } catch {
      setState({ mic: "error" });
      return;
    }

    const rec = new Ctor();
    recRef.current = rec;
    rec.lang = "tr-TR";
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const last = e.results[e.results.length - 1];
      if (last?.isFinal) {
        const text = String(last[0].transcript).trim();
        if (text) sendVoiceCommand(text).catch(() => setState({ mic: "error" }));
      }
    };
    rec.onerror = () => {
      stop();
      setState({ mic: "error" });
    };
    rec.onend = () => {
      if (getState().mic === "listening") {
        try {
          rec.start();
        } catch {
          stop();
        }
      }
    };
    try {
      rec.start();
      sendMicState(true);
      setState({ mic: "listening" });
    } catch {
      stop();
      setState({ mic: "error" });
    }
  }, [stop]);

  // Primary microphone action: true local push-to-talk turn.
  const toggle = useCallback(() => {
    if (getState().mic === "listening") {
      stop();
      return;
    }
    setState({ mic: "listening" });
    void pushToTalk(6)
      .then((result) => {
        if (!result.ok) throw new Error(result.error ?? "PTT failed");
      })
      .catch((e) => {
        setState({ mic: "error" });
        console.error("ULTRON local PTT:", e);
      })
      .finally(() => {
        setState({ mic: "idle" });
        sendMicState(false);
      });
  }, [stop]);

  const disarm = useCallback(() => {
    const w = wakeRef.current;
    if (w) {
      try {
        w.rec.stop();
      } catch {
        /* already stopped */
      }
      w.stream.getTracks().forEach((t) => t.stop());
      wakeRef.current = null;
    }
    cmdMode.current = false;
    sendMicState(false);
    setState({ voiceArmed: false, mic: "idle" });
  }, []);

  const arm = useCallback(async () => {
    const Ctor = speechRecognitionCtor();
    if (!Ctor) {
      setState({ mic: "unavailable" });
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new Ctor();
      rec.lang = "tr-TR";
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (e: any) => {
        const last = e.results[e.results.length - 1];
        if (!cmdMode.current) {
          const txt = String(last[0].transcript).toLowerCase();
          if (txt.includes("ultron")) {
            cmdMode.current = true;
            sendMicState(true);
            setState({ mic: "listening" });
          }
        } else if (last.isFinal) {
          const command = String(last[0].transcript).trim();
          if (command && !command.toLowerCase().startsWith("ultron")) {
            sendVoiceCommand(command).catch(() => undefined);
          }
          setTimeout(() => {
            if (cmdMode.current && getState().voiceArmed) {
              cmdMode.current = false;
              sendMicState(false);
              setState({ mic: "armed" });
            }
          }, 2500);
        }
      };
      rec.onerror = () => {
        disarm();
        setState({ mic: "error" });
      };
      rec.onend = () => {
        if (getState().voiceArmed) {
          try {
            rec.start();
          } catch {
            disarm();
          }
        }
      };
      rec.start();
      wakeRef.current = { rec, stream };
      setState({ voiceArmed: true, mic: "armed" });
    } catch {
      setState({ mic: "error" });
    }
  }, [disarm]);

  useEffect(() => stop, [stop]);
  useEffect(() => disarm, [disarm]);

  return { toggle, supported, arm, disarm };
}

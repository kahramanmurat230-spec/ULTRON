/**
 * ULTRON client TTS — local backend stream.
 *
 * speak(text) → POST /api/tts/speak (local Piper WAV / local eSpeak WAV)
 *             → AudioContext.decodeAudioData → speakers
 *             → REAL amplitude (AnalyserNode RMS) → ttsLevel
 *             → HoloHead jaw/eye lip-sync + waveform reactivity.
 *
 * Local-only playback. If the local backend is unavailable, ULTRON stays silent
 * rather than falling back to cloud or browser synthesis.
 *
 * window.speechSynthesis is PERMANENTLY DISABLED — zero calls in this file.
 */
import { getState, setState } from "./store";

export const ttsLevel = { current: 0 };

let ctx: AudioContext | null = null;
let analyser: AnalyserNode | null = null;
let gain: GainNode | null = null;
let timeBuf: Uint8Array | null = null;
let src: AudioBufferSourceNode | null = null;
let speaking = false;

/** Must be called from a user gesture once (autoplay policy). */
export function unlock(): void {
  if (!ctx) {
    const AC: typeof AudioContext | undefined =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    gain = ctx.createGain();
    analyser.connect(gain);
    gain.connect(ctx.destination);
    timeBuf = new Uint8Array(analyser.fftSize);
  }
  if (gain) gain.gain.value = getState().ttsVolume;
  if (ctx.state === "suspended") ctx.resume().catch(() => undefined);
}

/** RMS amplitude loop → ttsLevel (lip-sync + waveform). */
function levelLoop(): void {
  const tick = () => {
    if (!speaking) {
      ttsLevel.current = 0;
      return;
    }
    if (analyser && timeBuf) {
      analyser.getByteTimeDomainData(timeBuf as Uint8Array<ArrayBuffer>);
      let sum = 0;
      for (let i = 0; i < timeBuf.length; i++) {
        const v = (timeBuf[i] - 128) / 128;
        sum += v * v;
      }
      ttsLevel.current = Math.min(1, Math.sqrt(sum / timeBuf.length) * 3.4);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/** Decode & play local audio through the analyser chain. */
async function playBytes(bytes: ArrayBuffer): Promise<boolean> {
  if (!ctx || !analyser) return false;
  try {
    const audio = await ctx.decodeAudioData(bytes);
    try {
      src?.stop();
    } catch {
      /* already stopped */
    }
    src = ctx.createBufferSource();
    src.buffer = audio;
    src.playbackRate.value = getState().ttsRate;
    src.connect(analyser);
    speaking = true;
    setState({ ttsSpeaking: true });
    src.onended = () => {
      speaking = false;
      setState({ ttsSpeaking: false });
    };
    src.start();
    levelLoop();
    return true;
  } catch {
    return false;
  }
}

function b64ToBytes(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr.buffer as ArrayBuffer;
}

/** Local TTS only. Cloud/browser synthesis is never used. */
export async function speak(text: string): Promise<void> {
  if (!getState().ttsEnabled) return;
  unlock();
  try {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    const r = await fetch("/api/tts/speak", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    window.clearTimeout(timeout);
    if (r.ok) {
      const d = await r.json();
      if (d.audio_b64 && (await playBytes(b64ToBytes(d.audio_b64)))) return;
    }
  } catch {
    /* local backend unavailable → silence */
  }
  setState({ ttsSpeaking: false });
}

/** V2 barge-in: immediately cut TTS output when the Boss starts speaking. */
export function cancelSpeech(): void {
  try {
    src?.stop();
  } catch {
    /* already stopped */
  }
  speaking = false;
  setState({ ttsSpeaking: false });
}

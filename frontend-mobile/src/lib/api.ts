import { getState, setState } from "./store";

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const t = getState().token;
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

export async function api<T = any>(path: string, init?: RequestInit, timeoutMs = 8000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, { ...init, headers: headers(), signal: ctl.signal });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const b = await res.json(); if (b?.error) detail = b.error; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export const post = (path: string, body: unknown, timeoutMs?: number) =>
  api(path, { method: "POST", body: JSON.stringify(body) }, timeoutMs);

export const sendCommand = (text: string) => post("/api/agent/command", { text });
export const taskApprove = (id: string) => post("/api/task/approve", { id });
export const taskReject = (id: string) => post("/api/task/reject", { id });

export async function handshake(): Promise<void> {
  const s = getState();
  try {
    const r = await post("/api/auth/handshake", { device: `${s.deviceName} (${s.deviceId.slice(0, 6)})` });
    setState({ token: r.token ?? null });
  } catch {
    setState({ token: null });
  }
}

let ws: WebSocket | null = null;
let retry = 1000;

export function connectWS(): void {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const token = getState().token;
  ws = new WebSocket(proto + location.host + "/ws" + (token ? `?token=${encodeURIComponent(token)}` : ""));
  ws.onopen = () => { retry = 1000; setState({ connected: true }); };
  ws.onclose = () => { setState({ connected: false }); setTimeout(connectWS, retry); retry = Math.min(retry * 1.6, 6000); };
  ws.onerror = () => ws?.close();
  ws.onmessage = (ev) => {
    try { handle(JSON.parse(ev.data as string)); } catch { /* ignore */ }
  };
}

export async function speak(text: string) {
  if (!getState().tts) return;
  // Local Piper/eSpeak audio from the ULTRON backend; no cloud/browser fallback.
  try {
    const r = await fetch("/api/tts/speak", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ text: text.slice(0, 300) }),
    });
    if (r.ok) {
      const d = await r.json();
      if (d.audio_b64) {
        const bin = atob(d.audio_b64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const AC = window.AudioContext || (window as any).webkitAudioContext;
        if (AC) {
          const ctx = new AC();
          const audio = await ctx.decodeAudioData(arr.buffer as ArrayBuffer);
          const src = ctx.createBufferSource();
          src.buffer = audio;
          setState({ speaking: true });
          src.onended = () => setState({ speaking: false });
          src.connect(ctx.destination);
          src.start();
          return;
        }
      }
    }
  } catch {
    /* local backend unavailable → silence */
  }
  setState({ speaking: false });
}

function handle(m: any) {
  const s = getState();
  switch (m.type) {
    case "hello":
      setState({
        connected: true,
        system: m.system, ai: m.ai, tools: m.tools,
        notifications: m.notifications, activity: m.activity,
        agentState: m.agent?.state ?? "IDLE",
      });
      break;
    case "system": setState({ system: m.data }); break;
    case "ai": setState({ ai: m.data }); break;
    case "tools": setState({ tools: m.data }); break;
    case "notifications": setState({ notifications: m.data }); break;
    case "activity": setState({ activity: m.data }); break;
    case "patch": setState({ patch: m.data ?? null }); break;
    case "task": setState({ task: m.data ?? null }); break;
    case "barge_in":
      break;
    case "proactive_speech":
      speak(String(m.text ?? ""));
      break;
    case "memory": break;
    case "tests": break;
    case "agent": {
      const ev = { ts: m.ts, state: m.state, message: m.message };
      const chat = [...s.chat];
      if ((m.state === "DONE" || m.state === "ERROR") && m.message) {
        chat.push({ role: "ultron", text: m.message, ts: m.ts });
        speak(m.message);
      }
      setState({ agentState: m.state, agentEvents: [...s.agentEvents, ev].slice(-80), chat: chat.slice(-60) });
      break;
    }
  }
}

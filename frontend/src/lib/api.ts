import { getState, setState } from "./store";
import { cancelSpeech, speak } from "./tts";
import type { MemoryRow, PatchProposal, TestEvent } from "./types";
import type {
  ActivityItem,
  AgentState,
  AIStatus,
  Config,
  MemoryStatus,
  NotificationItem,
  SystemSnapshot,
  ToolStatus,
} from "./types";

// Browser/Vite uses same-origin API paths; Electron production runs from file://.
const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8000" : "";

async function req<T>(path: string, init?: RequestInit, timeoutMs = 6000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...init, signal: ctl.signal });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body?.error) detail = body.error;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

const post = (path: string, body?: unknown) =>
  req<{ ok: boolean; error?: string; output?: unknown }>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

// ---------- service layer (spec names) ----------
export const getSystemStatus = () => req<SystemSnapshot>("/api/system");
export const getAIStatus = () => req<AIStatus>("/api/ai");
export const getAgentStatus = () => req<{ state: AgentState }>("/api/agent");
export const getTools = () => req<ToolStatus[]>("/api/tools");
export const getMemoryStatus = () => req<MemoryStatus>("/api/memory");
export const getRecentActivity = () => req<ActivityItem[]>("/api/activity");
export const getNotifications = () => req<NotificationItem[]>("/api/notifications");
export const getConfig = () => req<Config>("/api/config");

export const executeCommand = (text: string, approved = false) =>
  post("/api/agent/command", { text, approved });
export const getAudit = () => req<string[]>("/api/audit");

// ---- code intelligence / codegen / tests ----
export const analyzeCode = (target: string) =>
  req<Record<string, unknown>>("/api/codeintel/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  }, 120000);
export const proposeCode = (goal: string) =>
  post("/api/codegen/propose", { goal });
export const applyPatch = (id: string) => post("/api/codegen/apply", { id });
export const rejectPatch = (id: string) => post("/api/codegen/reject", { id });
export const pendingPatches = () => req<PatchProposal[]>("/api/codegen/pending");
export const taskApprove = (id: string) => post("/api/task/approve", { id });
export const taskReject = (id: string) => post("/api/task/reject", { id });
export const hudOverview = () => req<Record<string, unknown>>("/api/hud/overview");
export const hudTrends = () => req<{ ts: number; score: number }[]>("/api/hud/persona/trends");
export const iotDevices = () => req<Record<string, unknown>[]>("/api/iot/devices");
export const iotControl = (b: { device_id: string; action: string; value?: number }) =>
  post("/api/iot/device/control", b);
export const iotScene = (b: { scene_name: string }) => post("/api/iot/scene/activate", b);
export const runTests = (quick: boolean) =>
  post("/api/tests/run", { quick });

// ---- v16 memory management ----
export const memoryV16 = () =>
  req<{ rows: MemoryRow[]; kinds: Record<string, number }>("/api/memory/v16?limit=60");
export const memoryV16Add = (kind: string, text: string) => post("/api/memory/v16/add", { kind, text });
export const memoryV16Delete = (id: number) => post("/api/memory/v16/delete", { id });
export const memoryV16Clear = () => post("/api/memory/v16/clear");
export const memoryV16Search = (q: string) =>
  req<{ score: number; kind: string; content: string }[]>(`/api/memory/v16/search?q=${encodeURIComponent(q)}`);
export const sendVoiceCommand = (text: string) => post("/api/agent/command", { text });
export const captureScreen = () => post("/api/actions/screenshot");
export const openBrowser = () => post("/api/actions/browser");
export const systemCheck = () => post("/api/actions/system-check");
export const clearMemory = () => post("/api/actions/clear-memory");
export const reportVoiceCapability = (available: boolean, detail: string) =>
  post("/api/tools/voice", { available, detail });

// ---------- websocket stream ----------
let ws: WebSocket | null = null;
let retryDelay = 1000;
let closedByUser = false;

interface WireMessage {
  type: string;
  [key: string]: unknown;
}

function handle(msg: WireMessage): void {
  const s = getState();
  switch (msg.type) {
    case "hello":
      setState({
        connected: true,
        system: msg.system as SystemSnapshot,
        ai: msg.ai as AIStatus,
        tools: msg.tools as ToolStatus[],
        memory: msg.memory as MemoryStatus,
        activity: msg.activity as ActivityItem[],
        notifications: msg.notifications as NotificationItem[],
        agentState: (msg.agent as { state: AgentState }).state,
        config: msg.config as Config,
      });
      break;
    case "system":
      setState({ system: msg.data as SystemSnapshot });
      break;
    case "ai":
      setState({ ai: msg.data as AIStatus });
      break;
    case "tools":
      setState({ tools: msg.data as ToolStatus[] });
      break;
    case "memory":
      setState({ memory: msg.data as MemoryStatus });
      break;
    case "activity":
      setState({ activity: msg.data as ActivityItem[] });
      break;
    case "notifications":
      setState({ notifications: msg.data as NotificationItem[] });
      break;
    case "agent": {
      const ev = { ts: msg.ts as number, state: msg.state as AgentState, message: msg.message as string | undefined };
      setState({ agentState: ev.state, agentEvents: [...s.agentEvents, ev].slice(-60) });
      if ((ev.state === "DONE" || ev.state === "ERROR") && ev.message) void speak(ev.message);
      break;
    }
    case "patch": {
      const p = (msg.data ?? null) as PatchProposal | null;
      setState({ pendingPatch: p, drawer: p ? "approvals" : getState().drawer });
      break;
    }
    case "task": {
      const t = (msg.data ?? null) as import("./types").TaskProposal | null;
      setState({ pendingTask: t, drawer: t ? "approvals" : getState().drawer });
      break;
    }
    case "tests": {
      const t = msg.data as TestEvent;
      setState({ testEvents: [...getState().testEvents, t].slice(-40) });
      break;
    }
    case "barge_in":
      cancelSpeech();
      break;
    case "proactive_speech":
      speak(String(msg.text ?? ""));
      break;
    default:
      break;
  }
}

export function connectWS(): void {
  if (closedByUser) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const host = location.protocol === "file:" ? "127.0.0.1:8000" : location.host;
  try {
    ws = new WebSocket(proto + host + "/ws");
  } catch {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    retryDelay = 1000;
    setState({ connected: true });
  };
  ws.onmessage = (ev) => {
    try {
      handle(JSON.parse(ev.data as string) as WireMessage);
    } catch {
      /* malformed frame */
    }
  };
  ws.onclose = () => {
    setState({ connected: false });
    scheduleReconnect();
  };
  ws.onerror = () => ws?.close();
}

function scheduleReconnect(): void {
  if (closedByUser) return;
  setTimeout(connectWS, retryDelay);
  retryDelay = Math.min(retryDelay * 1.6, 6000);
}

export function sendVoiceReport(available: boolean, detail: string): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "voice_report", available, detail }));
  } else {
    reportVoiceCapability(available, detail).catch(() => undefined);
  }
}

export function sendMicState(active: boolean): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "mic", active }));
  }
}

import { useSyncExternalStore } from "react";

export interface ChatMsg { role: "user" | "ultron"; text: string; ts: number }

export interface MState {
  connected: boolean;
  system: any;
  ai: any;
  tools: any[];
  notifications: any[];
  activity: any[];
  agentState: string;
  agentEvents: any[];
  patch: any | null;
  task: any | null;
  pcOnline: boolean;
  fieldMode: boolean;
  speaking: boolean;
  mic: "idle" | "listening" | "error";
  memoryRows: any[];
  memoryKinds: Record<string, number>;
  chat: ChatMsg[];
  deviceId: string;
  deviceName: string;
  token: string | null;
  tts: boolean;
  visionLast: { exists: boolean; name?: string; size?: number; ts?: number };
}

function loadDevice(): { id: string; name: string } {
  try {
    const raw = localStorage.getItem("ultron_device");
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  const id = (crypto as any).randomUUID ? (crypto as any).randomUUID() : String(Date.now());
  const name = "Phone-" + Math.floor(1000 + Math.random() * 9000);
  const dev = { id, name };
  try { localStorage.setItem("ultron_device", JSON.stringify(dev)); } catch { /* ignore */ }
  return dev;
}

const dev = loadDevice();

let state: MState = {
  connected: false,
  system: null,
  ai: null,
  tools: [],
  notifications: [],
  activity: [],
  agentState: "IDLE",
  agentEvents: [],
  patch: null,
  task: null,
  pcOnline: true,
  fieldMode: false,
  speaking: false,
  mic: "idle",
  memoryRows: [],
  memoryKinds: {},
  chat: [],
  deviceId: dev.id,
  deviceName: dev.name,
  token: null,
  tts: true,
  visionLast: { exists: false },
};

const listeners = new Set<() => void>();
export const getState = () => state;
export function setState(patch: Partial<MState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}
function subscribe(l: () => void) {
  listeners.add(l);
  return () => { listeners.delete(l); };
}
export function useM<T>(sel: (s: MState) => T): T {
  return useSyncExternalStore(subscribe, () => sel(state));
}

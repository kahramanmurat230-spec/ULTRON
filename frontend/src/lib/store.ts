import { useSyncExternalStore } from "react";
import { loadTheme } from "../styles/themes";
import type {
  ActivityItem,
  AgentEvent,
  AgentState,
  AIStatus,
  Config,
  MemoryStatus,
  NotificationItem,
  SystemSnapshot,
  ToolStatus,
} from "./types";

export type MicState = "idle" | "armed" | "listening" | "unavailable" | "error";
export type TtsMode = "neural" | "off";

/** A single terminal/chat line rendered in the left console panel. */
export interface ChatLine {
  role: "user" | "ultron";
  text: string;
  ts: number;
}

export interface AppState {
  connected: boolean;
  system: SystemSnapshot | null;
  ai: AIStatus | null;
  tools: ToolStatus[];
  memory: MemoryStatus | null;
  activity: ActivityItem[];
  notifications: NotificationItem[];
  agentState: AgentState;
  agentEvents: AgentEvent[];
  chat: ChatLine[];
  coreFps: number;
  mic: MicState;
  ttsEnabled: boolean;
  ttsMode: TtsMode;
  ttsSpeaking: boolean;
  ttsRate: number;
  ttsVolume: number;
  pendingPatch: import("./types").PatchProposal | null;
  pendingTask: (import("./types").TaskProposal | null);
  testEvents: import("./types").TestEvent[];
  drawer: "hud" | "iot" | "code" | "memory" | "automation" | "approvals" | "audit" | "voice" | null;
  theme: import("../styles/themes").ThemeName;
  voiceArmed: boolean;
  settingsOpen: boolean;
  listModal: "activity" | "notifications" | "tools" | "audit" | null;
  minimized: boolean;
  closed: boolean;
  config: Config | null;
}

let state: AppState = {
  connected: false,
  system: null,
  ai: null,
  tools: [],
  memory: null,
  activity: [],
  notifications: [],
  agentState: "IDLE",
  agentEvents: [],
  chat: [],
  coreFps: 0,
  mic: "idle",
  ttsEnabled: true,
  ttsMode: "neural",
  ttsSpeaking: false,
  ttsRate: 1,
  ttsVolume: 1,
  pendingPatch: null,
  pendingTask: null,
  testEvents: [],
  drawer: null,
  voiceArmed: false,
  settingsOpen: false,
  listModal: null,
  minimized: false,
  closed: false,
  config: null,
  theme: loadTheme(),
};

const listeners = new Set<() => void>();

export function getState(): AppState {
  return state;
}

export function setState(patch: Partial<AppState>): void {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useApp<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state));
}

/** Live microphone amplitude (0..1), written by the voice hook, read by the waveform canvas. */
export const audioLevel = { current: 0 };

/**
 * Append a line to the terminal/chat console (left panel).
 * De-dupes identical consecutive lines so a spoken command that is also
 * echoed back over the activity stream is not shown twice.
 */
export function pushChat(role: ChatLine["role"], text: string): void {
  const clean = (text ?? "").trim();
  if (!clean) return;
  const prev = state.chat[state.chat.length - 1];
  if (prev && prev.role === role && prev.text === clean) return;
  setState({ chat: [...state.chat, { role, text: clean, ts: Date.now() / 1000 }].slice(-120) });
}

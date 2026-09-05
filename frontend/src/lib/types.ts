export interface SystemSnapshot {
  ts: number;
  uptime_s: number;
  cpu: { percent: number; freq_mhz: number | null };
  ram: { percent: number; used_gb: number; total_gb: number };
  disk: { percent: number; used_gb: number; total_gb: number };
  gpu: { util: number; mem_used_mb: number; mem_total_mb: number; temp_c: number; name: string } | null;
  net: { down_mbps: number; up_mbps: number };
  temps: Record<string, number>;
}

export interface AIStatus {
  connected: boolean;
  host: string;
  models: string[];
  primary: string | null;
  vision: string | null;
  embedding: string | null;
  checked_at: number;
}

export type ToolState = "READY" | "RUNNING" | "SUCCESS" | "ERROR" | "UNAVAILABLE" | "CHECKING";

export interface ToolStatus {
  id: string;
  label: string;
  detail: string;
  status: ToolState;
}

export interface MemoryStatus {
  session_count: number;
  persistent_enabled: boolean;
  persistent_count: number;
  usage_bytes: number;
  quota_bytes: number;
  recent_session: { ts: number; kind: string; text: string }[];
  recent_persistent: { ts: number; text: string }[];
  v16_total?: number | null;
}

export interface ActivityItem {
  ts: number;
  text: string;
  kind: string;
}

export interface NotificationItem {
  ts: number;
  text: string;
  level: string;
}

export type AgentState =
  | "IDLE"
  | "LISTENING"
  | "THINKING"
  | "PLANNING"
  | "EXECUTING"
  | "VERIFYING"
  | "WAITING_APPROVAL"
  | "DONE"
  | "ERROR";

export interface PatchFile {
  path: string;
  content: string;
  diff: string;
  note?: string;
}

export interface PatchProposal {
  id: string;
  goal: string;
  source: string;
  files: PatchFile[];
  status: string;
  test_plan: string[];
}

export interface TestEvent {
  name: string;
  status: string;
  detail: string;
}

export interface TaskProposal {
  id: string;
  text: string;
  created: number;
  risks: string[];
  steps: { label: string; tool: string; dangerous: boolean }[];
  coords?: number[];
}

export interface MemoryRow {
  id: number;
  kind: string;
  content: string;
  created_at: string;
}

export interface AgentEvent {
  ts: number;
  state: AgentState;
  message?: string;
}

export interface Config {
  version: string;
  ollama_host: string;
  primary_model: string | null;
  persistent_memory: boolean;
  workspace: string;
}

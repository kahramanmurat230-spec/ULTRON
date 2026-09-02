import { getState } from "../../lib/store";
import type { AgentState, SystemSnapshot } from "../../lib/types";

export interface HolographicSnapshot {
  connected: boolean;
  agentState: AgentState;
  system: SystemSnapshot | null;
  taskCount: number;
  activityCount: number;
  notificationCount: number;
  memoryUsagePercent: number;
  ttsSpeaking: boolean;
}

/** Read-only adapter from the existing ULTRON store into holographic presentation data.
 * It deliberately does not own WebSocket connections or mutate agent/File Agent state.
 */
export function readHolographicSnapshot(): HolographicSnapshot {
  const state = getState();
  const memoryUsagePercent = state.memory && state.memory.quota_bytes > 0
    ? Math.min(100, (state.memory.usage_bytes / state.memory.quota_bytes) * 100)
    : 0;

  return {
    connected: state.connected,
    agentState: state.agentState,
    system: state.system,
    taskCount: state.pendingTask ? 1 : 0,
    activityCount: state.activity.length,
    notificationCount: state.notifications.length,
    memoryUsagePercent,
    ttsSpeaking: state.ttsSpeaking,
  };
}

export interface HolographicFileProvider {
  list(path: string): Promise<unknown[]>;
  inspect(path: string): Promise<unknown>;
  open(path: string): Promise<void>;
}

/** Integration seam for a future File Agent 2.0 adapter. No File Agent code is imported here. */
export interface HolographicProviderRegistry {
  files?: HolographicFileProvider;
}

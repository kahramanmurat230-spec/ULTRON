import type { ToolState } from "../lib/types";

export const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });

export const toolDot = (s: ToolState) =>
  s === "READY" ? "green" : s === "RUNNING" || s === "CHECKING" ? "amber pulse" : s === "SUCCESS" ? "green" : s === "ERROR" ? "red" : "gray";

export const toolTone = (s: ToolState) =>
  s === "READY" || s === "SUCCESS" ? "var(--green)" : s === "RUNNING" || s === "CHECKING" ? "var(--amber)" : s === "ERROR" ? "var(--red)" : "var(--dim)";

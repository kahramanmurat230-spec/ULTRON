/**
 * Mesh client — Dual-Brain adapter for the mobile node.
 * PC unreachable => "Bağımsız Saha Modu": commands are never rejected;
 * light ones resolve locally, heavy ones get the honest sleep notice and are
 * queued for sync when the PC brain wakes. master_rules never leave sealed.
 */
import { api, sendCommand } from "./api";
import { getState, setState } from "./store";
import { tryIoT } from "./iot_client";

const LS = "ultron_field_state";
const HEAVY = ["ekran", "derle", "compile", "kod", "analiz", "patch", "vision", "build"];
const SLEEP_LINE =
  "Boss, bu cerrahi işlem için masaüstü bedenin uyanık olmalı. Laptop şu an uykuda — kuyruğa aldım, uyanınca senkronize oluruz.";

interface FieldState { memory: { ts: number; text: string }[]; queue: { ts: number; text: string }[] }

function load(): FieldState {
  try {
    const raw = localStorage.getItem(LS);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { memory: [], queue: [] };
}
function save(f: FieldState) {
  try { localStorage.setItem(LS, JSON.stringify(f)); } catch { /* ignore */ }
}

let watch: ReturnType<typeof setInterval> | null = null;

export function startMeshWatch() {
  if (watch) return;
  const tick = async () => {
    try {
      const n = await api("/api/mesh/nodes");
      setState({ pcOnline: !!n.pc_online, fieldMode: false });
      void flushQueue();
    } catch {
      setState({ pcOnline: false, fieldMode: true });
    }
  };
  void tick();
  watch = setInterval(tick, 10000);
}

export async function flushQueue() {
  const f = load();
  if (!f.queue.length || !getState().pcOnline) return;
  try {
    await api("/api/mesh/sync/push", {
      method: "POST",
      body: JSON.stringify({
        memories: f.queue.map((q) => ({ kind: "TASK", content: q.text, created_at: new Date(q.ts).toISOString() })),
        dna: [],
      }),
    });
    f.queue = [];
    save(f);
  } catch { /* keep queued */ }
}

function localMath(text: string): string | null {
  const m = text.match(/(-?\d+(?:[.,]\d+)?)\s*([+\-*/x])\s*(-?\d+(?:[.,]\d+)?)/);
  if (!m) return null;
  const a = parseFloat(m[1].replace(",", "."));
  const b = parseFloat(m[3].replace(",", "."));
  const op = m[2] === "x" ? "*" : m[2];
  const r = op === "+" ? a + b : op === "-" ? a - b : op === "*" ? a * b : b !== 0 ? a / b : NaN;
  return `Saha modu hesabı: ${a} ${op} ${b} = ${r}`;
}

export async function smartCommand(text: string): Promise<string> {
  const iot = await tryIoT(text); // IoT asla laptop'a ihtiyaç duymaz
  if (iot) return iot;
  if (/yaklaşıyorum|eve gel|yoldayım| hazırlık/.test(text.toLowerCase())) {
    const f = load();
    f.queue.push({ ts: Date.now(), text });
    save(f);
    if (getState().pcOnline) {
      try {
        await api("/api/presence/ping", { method: "POST", body: JSON.stringify({ source: "manual", device_id: "mobile" }) });
      } catch { /* pc vanished */ }
    }
    await tryIoT("masa ışığını aç");
    await tryIoT("klimayı 22 yap");
    return "Boss, eve yaklaştığını hissettim: ışık %80, iklim 22°C hizalandı. Eşikten girdiğin an sahne mühürlenir.";
  }
  if (getState().pcOnline) {
    await sendCommand(text);
    return "→ PC brain";
  }
  const t = text.toLowerCase();
  const math = localMath(text);
  if (math) return math;
  if (t.includes("not al") || t.includes("hatırlat")) {
    const f = load();
    f.memory.push({ ts: Date.now(), text });
    f.queue.push({ ts: Date.now(), text });
    save(f);
    return "Saha modu: not yerel hafızaya mühürlendi, PC uyanınca senkronlanacak.";
  }
  if (HEAVY.some((k) => t.includes(k))) {
    const f = load();
    f.queue.push({ ts: Date.now(), text });
    save(f);
    return SLEEP_LINE;
  }
  return "Saha modu: hafif komutlar bende. (not/hesap destekli)";
}

export function getQueueCount(): number {
  return load().queue.length;
}

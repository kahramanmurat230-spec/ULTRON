/**
 * IoT client — works WITH the PC brain OR alone on local Wi-Fi (no laptop).
 * Config: Home Assistant base+token, or demo driver (virtual devices in
 * localStorage) so "ışığı aç" never dies even fully offline.
 */
import { api } from "./api";
import { getState } from "./store";

const LS = "ultron_iot";

interface Cfg {
  ha_url?: string;
  ha_token?: string;
  demo: Record<string, { state: string; value: number | null }>;
}

function load(): Cfg {
  try {
    const raw = localStorage.getItem(LS);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { demo: {
    light_masa: { state: "on", value: 80 },
    light_amb: { state: "off", value: 0 },
    climate_ac: { state: "off", value: 22 },
    switch_priz: { state: "on", value: null },
    media_tv: { state: "off", value: null },
  } };
}
function save(c: Cfg) { try { localStorage.setItem(LS, JSON.stringify(c)); } catch { /* ignore */ } }

const SCENE_KEYS: [RegExp, string][] = [
  [/kodlama modu|coding focus|kod modu/, "CODING_FOCUS"],
  [/gece modu|night rest|uyku modu/, "NIGHT_REST"],
  [/sinema|cinema/, "CINEMA_RELAX"],
  [/her şeyi kapat|tümünü kapat|all off|hepsini kapat/, "ALL_OFF"],
];

export function parseIoT(text: string):
  | { kind: "scene"; scene: string }
  | { kind: "device"; device: string; action: string; value?: number }
  | null {
  const t = text.toLowerCase();
  for (const [rx, scene] of SCENE_KEYS) if (rx.test(t)) return { kind: "scene", scene };
  const dev =
    /klima|serin|ısın/.test(t) ? "climate_ac" :
    /priz/.test(t) ? "switch_priz" :
    /tv|televizyon/.test(t) ? "media_tv" :
    /ambiyans/.test(t) ? "light_amb" :
    /ışık|lamba|masa|aydın/.test(t) ? "light_masa" : null;
  if (!dev) return null;
  const mv = t.match(/(\d{1,3})\s*(?:derece|°|yap|ayarla|yüzde)/);
  const value = mv ? parseFloat(mv[1]) : undefined;
  const action = value !== undefined ? "set_value"
    : /kapat|söndür/.test(t) ? "turn_off"
    : /aç|yak|başlat/.test(t) ? "turn_on" : "toggle";
  return { kind: "device", device: dev, action, value };
}

async function demoExec(desc: NonNullable<ReturnType<typeof parseIoT>>): Promise<string> {
  const c = load();
  if (desc.kind === "scene") {
    if (desc.scene === "ALL_OFF" || desc.scene === "NIGHT_REST")
      Object.values(c.demo).forEach((d) => { d.state = "off"; });
    if (desc.scene === "CODING_FOCUS") { c.demo.light_masa = { state: "on", value: 100 }; c.demo.switch_priz.state = "off"; }
    if (desc.scene === "CINEMA_RELAX") { c.demo.light_masa = { state: "on", value: 15 }; c.demo.light_amb = { state: "on", value: 15 }; }
    save(c);
    return `Saha-IoT: ${desc.scene} sahnesi yerel olarak uygulandı, Boss.`;
  }
  const d = c.demo[desc.device] ?? { state: "off", value: null };
  d.state = desc.action === "turn_on" ? "on" : desc.action === "turn_off" ? "off" : d.state === "on" ? "off" : "on";
  if (desc.action === "set_value" && desc.value !== undefined) { d.value = desc.value; d.state = "on"; }
  c.demo[desc.device] = d;
  save(c);
  return `Saha-IoT: ${desc.device} → ${d.state}${d.value !== null ? " @" + d.value : ""} (yerel demo sürücü).`;
}

async function haExec(desc: NonNullable<ReturnType<typeof parseIoT>>, cfg: Cfg): Promise<string | null> {
  if (!cfg.ha_url) return null;
  try {
    if (desc.kind === "device") {
      await fetch(cfg.ha_url.replace(/\/$/, "") + `/api/services/light/${desc.action === "set_value" ? "turn_on" : desc.action}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${cfg.ha_token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: desc.device }),
      });
    }
    return `HA-IoT: komut iletildi (${desc.kind === "scene" ? desc.scene : desc.device}).`;
  } catch {
    return null; // fall through to demo
  }
}

/** Called from smartCommand BEFORE any PC routing — IoT never needs the laptop. */
export async function tryIoT(text: string): Promise<string | null> {
  const desc = parseIoT(text);
  if (!desc) return null;
  const cfg = load();
  const ha = await haExec(desc, cfg);
  if (ha) return ha;
  if (getState().pcOnline) {
    try {
      if (desc.kind === "scene") {
        const r = await api("/api/iot/scene/activate", { method: "POST", body: JSON.stringify({ scene_name: desc.scene }) });
        return (r as any).note ?? "Sahne uygulandı.";
      }
      const r = await api("/api/iot/device/control", { method: "POST", body: JSON.stringify({ device_id: desc.device, action: desc.action, value: desc.value }) });
      return (r as any).ok ? `IoT: ${(r as any).device?.name ?? desc.device} → ${(r as any).device?.state}` : `IoT hata: ${(r as any).error}`;
    } catch { /* pc vanished mid-call → local */ }
  }
  return demoExec(desc);
}

export async function controlAnywhere(device: string, action: string, value?: number): Promise<string> {
  return tryIoT(`${device === "light_masa" ? "masa ışığı" : device} ${action === "turn_on" ? "aç" : action === "turn_off" ? "kapat" : "değiştir"}${value !== undefined ? " " + value : ""}`) as Promise<string>;
}

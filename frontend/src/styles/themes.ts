/** Live theme manager — 4 themes, CSS-var driven, localStorage persisted. */
export type ThemeName = "CRIMSON" | "CYAN" | "PURPLE" | "HYBRID";

export const THEMES: Record<Exclude<ThemeName, "HYBRID">, { red: string; cyan: string }> = {
  CRIMSON: { red: "#ff1a1a", cyan: "#38e1ff" },   // Ultron klasik: titanyum + neon kızıl
  CYAN: { red: "#00f0ff", cyan: "#4d7cff" },      // Stark Jarvis: lacivert + elektrik cyan
  PURPLE: { red: "#bc13fe", cyan: "#7a5cff" },    // Void cyberpunk: obsidyen + plazma moru
};

export function resolveTheme(name: ThemeName, alert: boolean) {
  if (name === "HYBRID") return alert ? THEMES.CRIMSON : THEMES.CYAN;
  return THEMES[name];
}

export function applyTheme(name: ThemeName, alert: boolean) {
  const t = resolveTheme(name, alert);
  const r = document.documentElement.style;
  r.setProperty("--red", t.red);
  r.setProperty("--cyan", t.cyan);
}

export function loadTheme(): ThemeName {
  try {
    const v = localStorage.getItem("ultron_theme");
    if (v && ["CRIMSON", "CYAN", "PURPLE", "HYBRID"].includes(v)) return v as ThemeName;
  } catch { /* ignore */ }
  return "CRIMSON";
}

export function saveTheme(name: ThemeName) {
  try { localStorage.setItem("ultron_theme", name); } catch { /* ignore */ }
}

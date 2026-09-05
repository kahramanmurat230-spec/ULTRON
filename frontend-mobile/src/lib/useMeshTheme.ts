import { useEffect } from "react";
import { getState } from "./store";
import { applyTheme, loadTheme, saveTheme } from "./themes";
import type { ThemeName } from "./themes";

/** PC theme sync over the brain relay; offline → last local theme. */
export function useMeshTheme() {
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      if (getState().pcOnline) {
        try {
          const r = await fetch("/api/ui/theme");
          const d = await r.json();
          if (d?.name && ["CRIMSON", "CYAN", "PURPLE", "HYBRID"].includes(d.name)) {
            applyTheme(d.name as ThemeName, false);
            saveTheme(d.name as ThemeName);
            return;
          }
        } catch { /* pc vanished mid-call */ }
      }
      applyTheme(loadTheme(), false);
    };
    void tick();
    const iv = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
}

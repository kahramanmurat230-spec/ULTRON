import { useM } from "../lib/store";

function Meter({ label, value, sub }: { label: string; value: number | null; sub?: string }) {
  return (
    <div className="meter">
      <span className="dim mono" style={{ width: 44, fontSize: 11 }}>{label}</span>
      <div className="bar"><i style={{ width: (value ?? 0) + "%" }} /></div>
      <span className="mono" style={{ width: 84, textAlign: "right", fontSize: 11 }}>
        {value === null ? "N/A" : value + "%"}{sub ? <span className="dim"> {sub}</span> : null}
      </span>
    </div>
  );
}

export function SystemPage() {
  const s = useM((st) => st.system);
  return (
    <>
      <div className="card">
        <h3>WINDOWS TELEMETRY (GERÇEK)</h3>
        <Meter label="CPU" value={s ? s.cpu.percent : null} sub={s?.cpu.freq_mhz ? (s.cpu.freq_mhz / 1000).toFixed(1) + "G" : undefined} />
        <Meter label="RAM" value={s ? s.ram.percent : null} sub={s ? s.ram.used_gb + "/" + s.ram.total_gb + "G" : undefined} />
        <Meter label="GPU" value={s?.gpu ? s.gpu.util : null} />
        <Meter label="DISK" value={s ? s.disk.percent : null} sub={s ? s.disk.used_gb + "/" + s.disk.total_gb + "G" : undefined} />
        <div className="row" style={{ marginTop: 6 }}>
          <span className="dim">NET ↓/↑</span>
          <span className="mono" style={{ fontSize: 11 }}>{s ? s.net.down_mbps + " / " + s.net.up_mbps + " Mbps" : "—"}</span>
        </div>
      </div>
      <div className="card">
        <h3>SENSORS</h3>
        {s && Object.keys(s.temps ?? {}).length === 0 && <div className="dim mono" style={{ fontSize: 11 }}>NO THERMAL SENSORS — N/A</div>}
        {s && Object.entries((s.temps ?? {}) as Record<string, number>).map(([k, v]) => (
          <div className="row" key={k}><span className="dim">{k}</span><span className="mono">{v}°C</span></div>
        ))}
      </div>
    </>
  );
}

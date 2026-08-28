const R = 19;
const CIRC = 2 * Math.PI * R;

export function RingMeter({
  value,
  color,
  label,
  sub,
}: {
  value: number | null; // 0..100, null => N/A
  color: string;
  label: string;
  sub: string;
}) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value));
  const offset = CIRC * (1 - pct / 100);
  return (
    <div className="meter-row">
      <div className="ring-wrap" style={{ color }}>
        <svg width="44" height="44" viewBox="0 0 44 44">
          <circle className="ring-bg" cx="22" cy="22" r={R} fill="none" strokeWidth="3" />
          <circle
            className="ring-fg"
            cx="22"
            cy="22"
            r={R}
            fill="none"
            strokeWidth="3"
            strokeLinecap="round"
            stroke={value === null ? "#39424f" : color}
            strokeDasharray={CIRC}
            strokeDashoffset={value === null ? CIRC : offset}
          />
        </svg>
        <span className="ring-val" style={{ color: value === null ? "#5b6572" : undefined }}>
          {value === null ? "N/A" : `${Math.round(pct)}%`}
        </span>
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="meter-label">{label}</div>
        <div className="meter-sub">{value === null ? "UNAVAILABLE" : sub}</div>
      </div>
    </div>
  );
}

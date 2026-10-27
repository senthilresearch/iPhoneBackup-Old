import { KIND_LABEL, localDay } from "../format.js";

export function KindFilter({ kinds, value, onChange }) {
  const toggle = (k) => onChange(value.includes(k) ? value.filter((x) => x !== k) : [...value, k]);
  return (
    <div className="filter-group" role="group" aria-label="Type">
      {kinds.map((k) => (
        <label key={k} className="check">
          <input type="checkbox" checked={value.includes(k)} onChange={() => toggle(k)} /> {KIND_LABEL[k]}
        </label>
      ))}
    </div>
  );
}

const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localDay(d);
};

export function DateRange({ from, to, onChange }) {
  const today = localDay(new Date());
  const monthStart = today.slice(0, 8) + "01";
  return (
    <div className="filter-group">
      <label>
        From <input type="date" value={from} max={to || undefined} onChange={(e) => onChange(e.target.value, to)} />
      </label>
      <label>
        To <input type="date" value={to} min={from || undefined} onChange={(e) => onChange(from, e.target.value)} />
      </label>
      <span className="presets">
        <button onClick={() => onChange(daysAgo(7), today)}>7 days</button>
        <button onClick={() => onChange(daysAgo(30), today)}>30 days</button>
        <button onClick={() => onChange(monthStart, today)}>This month</button>
        <button onClick={() => onChange("", "")} disabled={!from && !to}>
          All time
        </button>
      </span>
    </div>
  );
}

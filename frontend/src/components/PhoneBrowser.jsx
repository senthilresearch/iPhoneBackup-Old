import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, phoneThumb } from "../api.js";
import { bytes } from "../format.js";
import { DateRange, KindFilter } from "./Filters.jsx";
import FolderPicker from "./FolderPicker.jsx";
import ThumbImage from "./ThumbImage.jsx";

const PAGE = 60;
const EMPTY_SEL = () => ({ all: false, picked: new Map(), excluded: new Map() });

export default function PhoneBrowser({ live, destination, onDestination }) {
  const { device, catalog, import: importState } = live;
  const [kinds, setKinds] = useState(["photos", "videos"]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [hideImported, setHideImported] = useState(true);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalBytes, setTotalBytes] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState(EMPTY_SEL);
  const [error, setError] = useState("");
  const seq = useRef(0);

  const filter = useMemo(
    () => ({ kind: kinds, date_from: from, date_to: to, hide_imported: hideImported }),
    [kinds, from, to, hideImported]
  );
  const filterKey = JSON.stringify(filter);
  const finished = ["done", "cancelled", "error"].includes(importState.state) ? importState.state : "";

  const load = useCallback(
    async (offset) => {
      const mine = ++seq.current;
      setLoading(true);
      try {
        const res = await api.phoneFiles(filter, offset, PAGE);
        if (mine !== seq.current) return; // a newer request superseded this one
        setTotal(res.total);
        setTotalBytes(res.total_bytes);
        setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]));
        setError("");
      } catch (e) {
        if (mine === seq.current) setError(e.message);
      } finally {
        if (mine === seq.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filterKey]
  );

  // Selection belongs to a filter: changing the filter starts a new selection.
  useEffect(() => setSel(EMPTY_SEL()), [filterKey]);
  // Reload when the filter changes, a scan completes, or an import finishes.
  useEffect(() => {
    load(0);
  }, [load, catalog.scanned_at, finished]);

  const isSelected = (it) => (sel.all ? !sel.excluded.has(it.path) : sel.picked.has(it.path));
  const toggle = (it) =>
    setSel((s) => {
      const key = s.all ? "excluded" : "picked";
      const next = new Map(s[key]);
      next.has(it.path) ? next.delete(it.path) : next.set(it.path, it.size);
      return { ...s, [key]: next };
    });

  const sum = (m) => [...m.values()].reduce((a, b) => a + b, 0);
  const count = sel.all ? total - sel.excluded.size : sel.picked.size;
  const selBytes = sel.all ? totalBytes - sum(sel.excluded) : sum(sel.picked);
  const busy = ["scanning", "importing", "paused"].includes(importState.state);

  const start = async () => {
    setError("");
    const body = sel.all
      ? {
          filter: { kinds, date_from: from || null, date_to: to || null, hide_imported: hideImported },
          exclude: [...sel.excluded.keys()],
        }
      : { paths: [...sel.picked.keys()] };
    try {
      await api.startImport({ ...body, destination: destination || null });
      setSel(EMPTY_SEL());
    } catch (e) {
      setError(e.message);
    }
  };

  const scanning = catalog.scanning;

  return (
    <section className="card">
      <div className="toolbar">
        <KindFilter kinds={["photos", "videos", "files"]} value={kinds} onChange={setKinds} />
        <DateRange from={from} to={to} onChange={(f, t) => { setFrom(f); setTo(t); }} />
        <label className="check">
          <input type="checkbox" checked={hideImported} onChange={(e) => setHideImported(e.target.checked)} /> Hide
          already imported
        </label>
        <button onClick={() => api.rescan().catch((e) => setError(e.message))} disabled={!device.connected || scanning}>
          {scanning ? "Reading phone…" : "Rescan"}
        </button>
      </div>

      <div className="selectbar">
        <span>
          <strong>{total.toLocaleString()}</strong> match{total === 1 ? "es" : ""} · {bytes(totalBytes)}
          {catalog.stale && <span className="sub"> · from the last scan, refreshing…</span>}
        </span>
        <button onClick={() => setSel({ all: true, picked: new Map(), excluded: new Map() })} disabled={!total}>
          Select all {total ? total.toLocaleString() : ""}
        </button>
        <button onClick={() => setSel(EMPTY_SEL())} disabled={!count}>
          Clear
        </button>
      </div>

      {error && <p className="err">{error}</p>}
      {!device.connected && items.length === 0 && <p className="sub">Connect your iPhone to see what's on it.</p>}
      {device.connected && scanning && items.length === 0 && (
        <p className="sub">Reading the list of photos and videos on the iPhone. This takes about 30 seconds…</p>
      )}
      {device.connected && !scanning && !loading && items.length === 0 && !error && (
        <p className="sub">Nothing matches these filters.</p>
      )}

      <div className="grid">
        {items.map((it) => (
          <PhoneTile key={it.path} it={it} selected={isSelected(it)} onToggle={() => toggle(it)} />
        ))}
      </div>

      {items.length < total && (
        <div className="actions">
          <button onClick={() => load(items.length)} disabled={loading}>
            {loading ? "Loading…" : `Load more (${(total - items.length).toLocaleString()} left)`}
          </button>
        </div>
      )}

      <div className="importbar">
        <FolderPicker value={destination} onChange={onDestination} />
        <span className="grow sub">
          {count > 0
            ? `${count.toLocaleString()} selected · ${bytes(selBytes)} → ${destination || "Year / Month folders"}`
            : "Pick items, or Select all, then choose where to save them."}
        </span>
        <button className="primary" onClick={start} disabled={!device.connected || busy || count <= 0}>
          {busy ? "Import running…" : `Import ${count > 0 ? count.toLocaleString() : ""}`}
        </button>
      </div>
    </section>
  );
}

function PhoneTile({ it, selected, onToggle }) {
  const [broken, setBroken] = useState(false);
  const ext = it.name.split(".").pop().toUpperCase();
  return (
    <button
      className={`tile ${selected ? "selected" : ""}`}
      onClick={onToggle}
      title={`${it.name} · ${new Date(it.taken_at).toLocaleString()} · ${bytes(it.size)}`}
      aria-pressed={selected}
    >
      {it.kind === "photos" && !broken ? (
        <ThumbImage src={phoneThumb(it.path)} alt={it.name} onError={() => setBroken(true)} />
      ) : (
        <div className="video">
          {it.kind === "videos" ? "▶" : it.kind === "files" ? "📄" : "🖼"}
          <span>{it.kind === "files" ? it.name : ext}</span>
        </div>
      )}
      <span className="tick">{selected ? "✓" : ""}</span>
      {it.imported && <span className="badge imported">Imported</span>}
      <span className="date">{new Date(it.taken_at).toLocaleDateString()}</span>
    </button>
  );
}

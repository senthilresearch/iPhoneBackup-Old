import { useEffect, useState } from "react";
import { api } from "../api.js";
import { bytes, duration } from "../format.js";

const TITLE = {
  scanning: "Preparing…",
  importing: "Importing",
  paused: "Paused",
  done: "Import finished",
  cancelled: "Import cancelled",
  error: "Import failed",
};

export default function ProgressPanel({ progress: p }) {
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState("");

  // A new run (or one in progress after reload) should always be visible.
  useEffect(() => {
    if (p.state === "scanning" || p.state === "importing") setHidden(false);
  }, [p.state]);

  if (!p.state || p.state === "idle" || hidden) return null;

  const active = ["scanning", "importing", "paused"].includes(p.state);
  const pct = p.bytes_total ? Math.min(100, Math.round((p.bytes_done / p.bytes_total) * 100)) : p.state === "done" ? 100 : 0;
  const filePct = p.current_size ? Math.min(100, Math.round((p.current_bytes / p.current_size) * 100)) : 0;
  const completed = p.copied + p.duplicates;

  const act = (fn) => async () => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    }
  };
  const retry = act(() => api.startImport({ paths: p.failed_paths, destination: p.destination || null }));

  return (
    <section className="card progress-panel">
      <div className="row">
        <h2 className="grow">
          {TITLE[p.state]}
          {p.state === "importing" && ` · file ${Math.min(p.done + 1, p.total).toLocaleString()} of ${p.total.toLocaleString()}`}
          {p.destination && <span className="sub"> → {p.destination}</span>}
        </h2>
        {p.state === "importing" && <button onClick={act(api.pause)}>Pause</button>}
        {p.state === "paused" && <button className="primary" onClick={act(api.resume)}>Resume</button>}
        {active && p.state !== "scanning" && <button onClick={act(api.cancel)}>Cancel</button>}
        {!active && <button onClick={() => setHidden(true)}>Dismiss</button>}
      </div>

      {p.state === "scanning" && <p className="sub">Reading the iPhone's file list…</p>}

      {p.total > 0 && (
        <>
          <div className="bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${pct}%` }} />
          </div>
          <div className="stats-line">
            <strong>{pct}%</strong>
            <span>
              {p.done.toLocaleString()} of {p.total.toLocaleString()} files processed
            </span>
            <span>
              {bytes(p.bytes_done)} of {bytes(p.bytes_total)}
            </span>
            {active && p.speed > 0 && <span>{bytes(p.speed)}/s</span>}
            {p.state === "importing" && p.eta > 0 && <span>about {duration(p.eta)} left</span>}
          </div>
          <div className="stats-line sub">
            <span className="ok">{completed.toLocaleString()} completed</span>
            {p.duplicates > 0 && <span>({p.duplicates} duplicates not stored again)</span>}
            {p.skipped > 0 && <span>{p.skipped.toLocaleString()} already imported</span>}
            {p.failed > 0 && <span className="err">{p.failed} failed</span>}
          </div>
        </>
      )}

      {active && p.current && (
        <div className="current">
          <div className="sub ellipsis">{p.current}</div>
          <div className="bar thin"><div style={{ width: `${filePct}%` }} /></div>
        </div>
      )}

      {p.recent?.length > 0 && active && <div className="sub ellipsis">Done: {p.recent.join(", ")}</div>}
      {p.state === "error" && <p className="err">{p.error}</p>}
      {p.failed > 0 && !active && (
        <details open>
          <summary className="err">{p.failed} file(s) failed.</summary>
          <pre>{p.errors.slice(0, 20).join("\n")}</pre>
          <button onClick={retry}>Retry failed</button>
        </details>
      )}
      {error && <p className="err">{error}</p>}
    </section>
  );
}

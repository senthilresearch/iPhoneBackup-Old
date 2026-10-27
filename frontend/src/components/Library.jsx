import { useCallback, useEffect, useMemo, useState } from "react";
import { api, mediaUrl, thumbUrl } from "../api.js";
import { bytes } from "../format.js";
import { DateRange, KindFilter } from "./Filters.jsx";
import FolderPicker from "./FolderPicker.jsx";
import ThumbImage from "./ThumbImage.jsx";
import VideoThumb from "./VideoThumb.jsx";

const PAGE = 60;

export default function Library({ importState }) {
  const [kinds, setKinds] = useState(["photos", "videos", "files"]);
  const [folder, setFolder] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(null);

  const filter = useMemo(
    () => ({ kind: kinds, folder, date_from: from, date_to: to }),
    [kinds, folder, from, to]
  );
  const key = JSON.stringify(filter);
  const finished = importState === "done" || importState === "cancelled";

  const load = useCallback(
    async (offset) => {
      setLoading(true);
      try {
        const res = await api.library(filter, offset, PAGE);
        setTotal(res.total);
        setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]));
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key]
  );

  useEffect(() => {
    load(0);
  }, [load, finished]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <section className="card">
      <div className="toolbar">
        <KindFilter kinds={["photos", "videos", "files"]} value={kinds} onChange={setKinds} />
        <DateRange from={from} to={to} onChange={(f, t) => { setFrom(f); setTo(t); }} />
        <FolderPicker value={folder} onChange={setFolder} includeAuto allowAutomatic={false} label="Folder" />
      </div>
      <div className="selectbar">
        <strong>{total.toLocaleString()}</strong> in library
      </div>

      {items.length === 0 && !loading && <p className="sub">Nothing here yet.</p>}

      <div className="grid">
        {items.map((it) => (
          <button key={`${it.kind}${it.id}`} className="tile" onClick={() => setOpen(it)} title={it.name}>
            {it.kind === "photos" ? (
              <ThumbImage src={thumbUrl(it.id)} alt={it.name} />
            ) : it.kind === "videos" ? (
              <VideoThumb src={mediaUrl("videos", it.id)} name={it.name} />
            ) : (
              <div className="video">
                📄
                <span>{it.name}</span>
              </div>
            )}
          </button>
        ))}
      </div>

      {items.length < total && (
        <div className="actions">
          <button onClick={() => load(items.length)} disabled={loading}>
            {loading ? "Loading…" : `Load more (${(total - items.length).toLocaleString()} left)`}
          </button>
        </div>
      )}

      {open && (
        <div className="lightbox" onClick={() => setOpen(null)}>
          <div onClick={(e) => e.stopPropagation()}>
            {open.kind === "videos" && <video src={mediaUrl("videos", open.id)} controls autoPlay />}
            {open.kind === "photos" && <img src={thumbUrl(open.id, 1600)} alt={open.name} />}
            {open.kind === "files" && (
              <p>
                <a href={mediaUrl("files", open.id)} download={open.name}>
                  Download {open.name}
                </a>
              </p>
            )}
            <div className="caption">
              {open.name} · {new Date(open.taken_at).toLocaleString()} · {bytes(open.size)}
              <br />
              {open.stored_path}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

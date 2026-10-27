import { useEffect, useRef, useState } from "react";

// Browsers allow ~6 connections per host. Loading dozens of thumbnails at once would queue
// filter/import requests behind them, so thumbnails load a few at a time and only when visible.
const MAX_ACTIVE = 3;
let active = 0;
const queue = [];

function pump() {
  while (active < MAX_ACTIVE && queue.length) {
    const job = queue.shift();
    if (job.cancelled) continue;
    active++;
    const probe = new Image();
    const finish = (ok) => {
      active--;
      if (!job.cancelled) job.done(ok);
      pump();
    };
    probe.onload = () => finish(true);
    probe.onerror = () => finish(false);
    probe.src = job.url;
  }
}

export default function ThumbImage({ src, alt, onError }) {
  const holder = useRef(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(false);
    let job = null;
    const el = holder.current;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries[0].isIntersecting || job) return;
        job = { url: src, cancelled: false, done: (ok) => (ok ? setReady(true) : onError?.()) };
        queue.push(job);
        pump();
        observer.disconnect();
      },
      { rootMargin: "300px" }
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (job) job.cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  return <img ref={holder} src={ready ? src : undefined} alt={alt} style={{ opacity: ready ? 1 : 0 }} />;
}

import { useEffect, useRef, useState } from "react";

// Shows a video's first frame as its tile. Only tiles near the viewport get a <video>, and
// preload="metadata" fetches just enough (a Range request) to paint that frame.
export default function VideoThumb({ src, name }) {
  const holder = useRef(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "300px" }
    );
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="video-thumb" ref={holder}>
      {visible && !failed && (
        <video
          src={`${src}${src.includes("#") ? "" : "#t=0.1"}`}
          preload="metadata"
          muted
          playsInline
          onError={() => setFailed(true)}
        />
      )}
      <span className="play">▶</span>
      {failed && <span className="name">{name}</span>}
    </div>
  );
}

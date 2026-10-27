import { useEffect, useState } from "react";
import { api, withToken } from "../api.js";

const EMPTY = {
  online: false,
  device: { connected: false, reason: "BackendUnavailable" },
  import: { state: "idle" },
  catalog: { scanning: false, count: 0, scanned_at: null },
};

/** Live backend state (device, import progress, phone catalog) pushed over Server-Sent Events. */
export default function useLive() {
  const [live, setLive] = useState(EMPTY);

  useEffect(() => {
    let es;
    let stopped = false;
    // A first normal request asks for the pairing token if one is needed (EventSource cannot show that).
    api.device().catch(() => {}).finally(() => {
      if (stopped) return;
      es = new EventSource(withToken("/api/events"));
      es.onmessage = (e) => setLive({ online: true, ...JSON.parse(e.data) });
      es.onerror = () => setLive((prev) => ({ ...prev, online: false, device: { connected: false, reason: "BackendUnavailable" } }));
    });
    return () => {
      stopped = true;
      es?.close();
    };
  }, []);

  return live;
}

import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api } from "../api.js";

export default function PairingDialog({ onClose }) {
  const [info, setInfo] = useState(null);
  const [qr, setQr] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .pairing()
      .then(async (p) => {
        setInfo(p);
        setQr(await QRCode.toDataURL(p.upload_url, { margin: 1, width: 220 }));
      })
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="lightbox" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h2>Send from your iPhone over Wi-Fi</h2>
        {error && <p className="err">{error}</p>}
        {info && (
          <>
            <p>
              On the iPhone (same Wi-Fi as this PC), scan this code with the Camera app and tap the link. Then use
              “Add to Home Screen” to keep it like an app.
            </p>
            {qr && <img className="qr" src={qr} alt="QR code for the upload page" />}
            <p className="sub break">{info.upload_url}</p>
            <p className="sub">
              The backend must be reachable from the phone (<code>start.ps1 -Lan</code>, or Docker) and Windows
              Firewall must allow its port on private networks. In Docker, open this page via the PC's Wi-Fi address
              (or set <code>PUBLIC_URL</code>) so the link points at the right place. Keep this link private: it
              contains the pairing token.
            </p>
          </>
        )}
        <div className="actions">
          {info && <button onClick={() => navigator.clipboard?.writeText(info.upload_url)}>Copy link</button>}
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

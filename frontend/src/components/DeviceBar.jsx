import { useState } from "react";
import { api } from "../api.js";
import PairingDialog from "./PairingDialog.jsx";

const HINTS = {
  BackendUnavailable: "The backend isn't running. Start it with start.ps1.",
  NoAppleService: "Apple's USB service isn't running. Install or open the Apple Devices app.",
  NoDevice: "No iPhone found. Plug it in with the cable, unlock it, or enable Wi-Fi sync in Apple Devices.",
  Locked: "Unlock your iPhone.",
  NotTrusted: "Tap “Trust” on your iPhone and enter the passcode.",
};

const DOCKER_HINT =
  "Library view. New photos arrive from the Mac agent (python -m mac_agent) or the Safari upload page — " +
  "use “Pair a Mac or iPhone” for the address and token.";

export default function DeviceBar({ live }) {
  const { device, online, catalog } = live;
  const [pairing, setPairing] = useState(false);
  const [auto, setAuto] = useState(null);
  const autoImport = auto ?? device.auto_import ?? false;
  const state = !online ? "off" : device.connected ? "ok" : "wait";

  const toggleAuto = async (e) => {
    const value = e.target.checked;
    setAuto(value);
    try {
      await api.setAutoImport(value);
    } catch {
      setAuto(!value);
    }
  };

  return (
    <header className={`devicebar ${state}`}>
      <span className="dot" />
      <div className="grow">
        <strong>iPhone Backup</strong>
        <div className="sub">
          {device.connected ? (
            <>
              {device.name} · iOS {device.ios}{" "}
              <span className="badge">{device.transport === "wifi" ? "Wi-Fi" : "USB"}</span>
              {catalog.scanning && " · reading photos…"}
            </>
          ) : (
            (device.container && DOCKER_HINT) || HINTS[device.reason] || `Not connected (${device.reason}).`
          )}
        </div>
      </div>
      {!device.container && (
        <label className="check" title="Import new photos and videos automatically when the iPhone connects">
          <input type="checkbox" checked={autoImport} onChange={toggleAuto} /> Auto-import on connect
        </label>
      )}
      <button onClick={() => setPairing(true)}>{device.container ? "Pair a Mac or iPhone" : "Connect from iPhone"}</button>
      {pairing && <PairingDialog onClose={() => setPairing(false)} />}
    </header>
  );
}

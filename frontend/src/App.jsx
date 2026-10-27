import { useState } from "react";
import useLive from "./hooks/useLive.js";
import DeviceBar from "./components/DeviceBar.jsx";
import ProgressPanel from "./components/ProgressPanel.jsx";
import PhoneBrowser from "./components/PhoneBrowser.jsx";
import Library from "./components/Library.jsx";

export default function App() {
  const live = useLive();
  const [tab, setTab] = useState("phone");
  const [destination, setDestination] = useState(() => {
    try {
      return localStorage.getItem("destination") || "";
    } catch {
      return "";
    }
  });

  const changeDestination = (value) => {
    setDestination(value);
    try {
      localStorage.setItem("destination", value);
    } catch {
      /* storage unavailable: remembering the folder is optional */
    }
  };

  // In Docker the phone is unreachable (phones arrive via the Mac agent or the Safari page), so only the library is shown.
  const docker = Boolean(live.device.container);

  return (
    <div className="app">
      <DeviceBar live={live} />
      {docker ? (
        <Library importState={live.import.state} />
      ) : (
        <>
      <ProgressPanel progress={live.import} destination={destination} />
      <nav className="tabs main-tabs">
        <button className={tab === "phone" ? "active" : ""} onClick={() => setTab("phone")}>
          On the iPhone
        </button>
        <button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>
          Library on this PC
        </button>
      </nav>
      {tab === "phone" ? (
        <PhoneBrowser live={live} destination={destination} onDestination={changeDestination} />
      ) : (
        <Library importState={live.import.state} />
      )}
        </>
      )}
    </div>
  );
}

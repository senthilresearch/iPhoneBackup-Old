import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

/** Choose where imported items go: automatic Year/Month, an existing folder, or a new one. */
export default function FolderPicker({ value, onChange, includeAuto = false, allowAutomatic = true, label = "Save to" }) {
  const [folders, setFolders] = useState([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.folders(includeAuto).then((r) => setFolders(r.folders)).catch(() => {});
  }, [includeAuto]);
  useEffect(load, [load]);

  const create = async () => {
    setError("");
    try {
      const { folder } = await api.createFolder(name);
      await load();
      onChange(folder);
      setCreating(false);
      setName("");
    } catch (e) {
      setError(e.message);
    }
  };

  const options = value && !folders.includes(value) ? [value, ...folders] : folders;

  return (
    <div className="folder-picker">
      <label>
        {label}{" "}
        <select value={value} onChange={(e) => (e.target.value === "__new" ? setCreating(true) : onChange(e.target.value))}>
          {allowAutomatic && <option value="">Automatic (Year / Month)</option>}
          {!allowAutomatic && <option value="">All folders</option>}
          {options.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
          {allowAutomatic && <option value="__new">+ New folder…</option>}
        </select>
      </label>
      {creating && (
        <span className="new-folder">
          <input
            autoFocus
            placeholder="e.g. Trips/Goa 2026"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && name && create()}
          />
          <button className="primary" onClick={create} disabled={!name.trim()}>
            Create
          </button>
          <button onClick={() => { setCreating(false); setError(""); }}>Cancel</button>
        </span>
      )}
      {error && <span className="err"> {error}</span>}
    </div>
  );
}

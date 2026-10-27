// Only needed when the backend does not see this browser as the PC itself (e.g. behind Docker).
const store = {
  get: () => { try { return localStorage.getItem("token") || ""; } catch { return ""; } },
  set: (t) => { try { localStorage.setItem("token", t); } catch { /* ignore */ } },
};
export const withToken = (url) => (store.get() ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(store.get())}` : url);

// Many requests fail at once on page load: share one prompt, and skip it if the token changed meanwhile.
let pendingPrompt = null;
function askToken(used) {
  if (store.get() && store.get() !== used) return Promise.resolve(true);
  pendingPrompt ??= Promise.resolve()
    .then(() => {
      const ask = used
        ? "That pairing token was rejected by the backend. It may belong to a different backend (each one has its own). Paste the token from this backend's settings.json:"
        : "Pairing token required. Paste the token from the backend's settings.json:";
      const t = window.prompt(ask);
      if (t?.trim()) store.set(t.trim());
      return Boolean(t?.trim());
    })
    .finally(() => { pendingPrompt = null; });
  return pendingPrompt;
}

async function request(path, options = {}, retry = true) {
  const token = store.get();
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(token ? { "X-Token": token } : {}), ...(options.headers || {}) },
  });
  if (res.status === 401 && retry && (await askToken(token))) return request(path, options, false);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

const post = (path, body) => request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else q.append(k, v === true ? "true" : v);
  }
  return q.toString();
}

export const api = {
  device: () => request("/device"),
  setAutoImport: (auto_import) => request("/settings", { method: "PUT", body: JSON.stringify({ auto_import }) }),
  pairing: () => request("/pairing"),
  rescan: () => post("/device/rescan"),
  phoneFiles: (filter, offset = 0, limit = 60) =>
    request(`/device/files?${query({ ...filter, offset, limit })}`),
  startImport: (body) => post("/import", body),
  pause: () => post("/import/pause"),
  resume: () => post("/import/resume"),
  cancel: () => post("/import/cancel"),
  folders: (includeAuto = false) => request(`/folders?${query({ include_auto: includeAuto })}`),
  createFolder: (path) => post("/folders", { path }),
  library: (filter, offset = 0, limit = 60) => request(`/library?${query({ ...filter, offset, limit })}`),
};

export const phoneThumb = (path, size = 240) => withToken(`/api/device/thumb?${query({ path, size })}`);
export const thumbUrl = (id, size = 360) => withToken(`/api/thumb/photos/${id}?size=${size}`);
export const mediaUrl = (kind, id) => withToken(`/api/media/${kind}/${id}`);

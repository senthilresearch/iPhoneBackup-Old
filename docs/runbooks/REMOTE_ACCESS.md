# Remote access: using the Docker backend from another network

The backend (and the library) run in Docker on the **Windows PC** at home. This page lists the ways to reach them
from the **MacBook** when it is on a different network, and exactly which steps belong on which machine, so you can
log into each machine, open Claude Code there, and say: *"Follow section X, steps for the Windows PC in runbooks/REMOTE_ACCESS.md"*.

Facts used below (change them if they change):

| Thing | Value |
|---|---|
| PC home-network address | `192.168.0.115` (web UI `:8081`, API `:8765`) |
| Library folder on the PC | `C:\Senthil Krishna\iPhoneLibrary` |
| Repo folder | `.../iPhoneBackup` (same repo on both machines; `git pull` first) |
| DuckDNS name | `beammedia.duckdns.org` (must point at the PC's current public IP) |
| Pairing token | Windows PC: `config/settings.json` in the repo (never commit it, never paste it into chat) |

## Which option do I need?

| I want to… | Option | Public exposure |
|---|---|---|
| Browse/download the library and also upload from the MacBook, anywhere | **A. Tailscale (recommended)** | none |
| Only send phone files from the MacBook to the PC, anywhere | **B. DuckDNS HTTPS, upload only** (built, not yet deployed); optional **mutual TLS** second lock | upload API only, token (+ client certificate) required |
| Open the web UI from any browser without installing anything | **C. DuckDNS HTTPS, full UI** (not built yet) | web UI + API, token + extra password |

A and B can be used together. Start with A; it needs no router changes, certificates or open ports.

---

## Option A: Tailscale (private network between your devices)

### On the Windows PC

1. Install Tailscale: `winget install Tailscale.Tailscale` (or download from tailscale.com/download). Sign in with your account (this opens a browser; do it yourself).
2. Note the PC's Tailscale address and name: `tailscale ip -4` (looks like `100.x.y.z`) and `tailscale status`.
3. Allow Tailscale traffic to the app's ports in Windows Firewall (PowerShell as Administrator):
   ```powershell
   New-NetFirewallRule -DisplayName "iPhoneBackup via Tailscale" -Direction Inbound -Protocol TCP -LocalPort 8081,8765 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
4. Make sure the stack is running: `docker compose -f deployment/docker-compose.yml --env-file .env ps` in the repo folder shows `backend` and `frontend` as `Up`. If not, `docker compose -f deployment/docker-compose.yml --env-file .env up -d --build`.
5. Check from the PC itself: `curl.exe http://localhost:8081` returns HTML.
6. Optional: in the Tailscale admin console (login.tailscale.com) turn on **Disable key expiry** for the PC, so it never drops off the network.

### On the MacBook

1. Install Tailscale: `brew install --cask tailscale` or the installer from tailscale.com/download. macOS 12 is old: if the newest app will not install or open, use the standalone package from pkgs.tailscale.com (an older release). Sign in with **the same account** as the PC.
2. Check: `tailscale status` (or the menu-bar icon) lists the PC.
3. Test the backend: `curl http://<PC Tailscale address>:8081` and `curl -H "X-Token: <token>" http://<PC Tailscale address>:8765/api/device`.
4. Browse the library: open `http://<PC Tailscale address>:8081` in a browser (asks for the token once; it is stored in that browser).
5. Send phone files from the MacBook: `cd backend && .venv/bin/python -m mac_agent --ui`, then in **Server connection** use `http://<PC Tailscale address>:8765` and the token, or run the CLI with `--server http://<PC Tailscale address>:8765 --token <token>`.
6. Optional: use the PC's Tailscale name (MagicDNS), for example `http://pc-name:8081`, instead of the 100.x address.

### Is Tailscale safe?

Yes, and much safer than opening a router port.

- Traffic between your devices is end-to-end encrypted (WireGuard). Tailscale's servers only help the devices find each
  other; if no direct path exists, relays carry traffic that is already encrypted. They cannot read your photos.
- Nothing is exposed to the internet: no open router port, so scanners and bots cannot reach the PC. Only devices signed in
  to your Tailscale account can. The pairing token still applies on top, and plain HTTP inside the tunnel is encrypted anyway.
- Tailscale's coordination service sees metadata (account, device names, IP addresses, when they connect), not traffic contents.
  The core client is open source and has had independent security audits.
- **The real risk is your Tailscale account.** Whoever signs in can add a device to your network and reach the PC. So:
  sign in through an identity provider with 2FA (e.g. your Google account); do not share the PC or MacBook with others
  (use Tailscale's "share node" to give someone one machine only); review the machines list at login.tailscale.com now and then
  and remove devices you do not recognise; optionally add an access rule (ACL) so only the MacBook can reach ports 8081 and 8765.
- The firewall rule above allows only Tailscale's own range (`100.64.0.0/10`), so nothing else about the PC's firewall changes.
- "Disable key expiry" on the PC is convenient, but a lost or stolen PC then stays trusted until you remove it from the machines list.
- Self-hosting the coordination server (Headscale) is possible but not worth it for photo backups.

### Troubleshooting A
- Times out: PC asleep, Docker not running, or the firewall rule from step 3 is missing.
- `tailscale status` shows the PC offline: sign in again on the PC; set "disable key expiry".
- Works at home but not away: you used `192.168.0.115` instead of the Tailscale address.

---

## Option B: DuckDNS + HTTPS, upload API only (already in the repo)

Publishes only `/api/upload*` and `/api/device` on **`https://beammedia.duckdns.org`** (standard ports 443, with 80 redirecting to
HTTPS). Everything else (web UI, library browsing, settings, pairing) answers 404 from outside, and every call still needs the
token. Off until you create `.env` on the PC. (`deployment/docker-compose.yml` service `caddy`, `deployment/Dockerfile.caddy`, `deployment/docker/Caddyfile`;
not yet tested on the real PC.) The DuckDNS name is **beammedia** (two m's); `beanmedia` does not exist.

### On the Windows PC

1. `git pull` in the repo folder.
2. Make sure nothing else on the PC uses ports 80/443 (IIS, another web server): `netstat -ano | findstr ":80 :443"`. If something does,
   set `CADDY_HTTP_PORT=8080` and `CADDY_HTTPS_PORT=8443` in `.env` (step 4) and forward the router to those ports instead; the
   public address then needs the port: `https://beammedia.duckdns.org:8443`.
3. Router: forward external TCP **443 and 80** to the PC (`192.168.0.115`; give the PC a fixed LAN address / DHCP reservation).
   Windows Firewall (PowerShell as Administrator):
   ```powershell
   New-NetFirewallRule -DisplayName "iPhoneBackup HTTPS" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow
   ```
4. Create `.env` in the repo folder (copy `.env.example`; gitignored):
   ```
   COMPOSE_PROFILES=public
   DUCKDNS_TOKEN=<your DuckDNS token, from duckdns.org>
   PUBLIC_HOST=beammedia.duckdns.org
   PUBLIC_URL=https://beammedia.duckdns.org
   ```
5. Keep DuckDNS pointing at the PC's public IP: use the DuckDNS update client on the PC, or your router's DuckDNS setting. Check:
   `nslookup beammedia.duckdns.org` equals `curl.exe https://api.ipify.org` run on the PC.
6. Deploy: `docker compose -f deployment/docker-compose.yml --env-file .env up -d --build` (or wait for `deploy-watch`). The first build compiles Caddy with the DuckDNS plugin (a few minutes).
7. Check: `docker compose -f deployment/docker-compose.yml --env-file .env logs caddy` shows the certificate was obtained, with no `error` lines. If the build or certificate fails, give Claude that log.

### On the MacBook (on a different network than the PC)

1. Run the automated check (it proves DNS, the router forward, a valid certificate, that only the upload API is published, that the
   token is required, and that port 80 redirects):
   ```bash
   cd backend
   PUBLIC_URL=https://beammedia.duckdns.org PUBLIC_TOKEN=<pairing token> .venv/bin/python -m pytest tests/test_public_access.py -v
   ```
   (Without `PUBLIC_URL` the live tests are skipped; one offline test always runs. Add the port to `PUBLIC_URL` if you used 8443.)
   Failure guide: `Network is unreachable`/timeout = router forward, firewall, DuckDNS IP or Caddy not running; certificate error = see
   `docker compose -f deployment/docker-compose.yml --env-file .env logs caddy`; a 404 where 401 is expected means the request did not reach the backend.
2. Preview without sending: `cd backend && .venv/bin/python -m mac_agent --server https://beammedia.duckdns.org --token <token> --dry-run`.
3. Or in `python -m mac_agent --ui` (or the Mac app) put `https://beammedia.duckdns.org` in **Server connection**.
4. If a network blocks it, try a phone hotspot to tell a network block from a setup problem.

### Troubleshooting B
- Cannot connect: router forward or firewall rule missing, the ISP blocks inbound ports or uses CGNAT (the router's WAN IP differs from
  `api.ipify.org`), DuckDNS has a stale IP, or Caddy is not running (`docker compose -f deployment/docker-compose.yml --env-file .env ps`).
- Certificate error: the DuckDNS token is wrong or the container cannot reach duckdns.org; see `docker compose -f deployment/docker-compose.yml --env-file .env logs caddy`.
- Works from outside but not from home: some routers lack NAT loopback; use the LAN address at home.

### What is Caddy? Is it third party? Does it cost anything?
- Caddy is a well-known **open-source web server / reverse proxy** (Apache-2.0 licence, caddyserver.com). It is third-party software, but
  widely used and actively maintained, and **free**. Let's Encrypt certificates are free too, and so is DuckDNS.
- It runs in its own Docker container and only forwards the allowed paths to the backend. The DuckDNS certificate plugin
  (`github.com/caddy-dns/duckdns`) is a community module compiled into the image by `deployment/Dockerfile.caddy`.
- Alternatives if you prefer: nginx (already used for the web UI; needs a certbot setup) or Cloudflare Tunnel (no open ports, but needs
  a Cloudflare account and a domain on Cloudflare). Tailscale (Option A) needs no proxy at all.
- You do need a proxy in front: the backend (uvicorn) is not meant to face the internet directly, has no TLS, and plain `http://`
  would send the pairing token and every photo in the clear.

### How is the data encrypted?
- **Outside (Mac to proxy): HTTPS, TLS 1.2/1.3 for every call**: photos, videos, files, the token and all API traffic. HSTS is on.
- **Inside (proxy to backend):** plain HTTP over Docker's private network on the PC; that traffic never leaves the machine.
- **Second, independent credential: mutual TLS (built, optional, see the next section).** Besides the token, the proxy can require a
  client certificate that only your Mac holds; without it the TLS handshake is refused, so strangers never even reach the API.
- **End-to-end payload encryption** of photos/videos with a Mac-only key is not built and not recommended: the server could no
  longer make thumbnails, previews or dedupe by checksum, and the web UI could not show the library.

### Checklist: Option B with mutual TLS, in order
| # | Machine | Step |
|---|---|---|
| 1 | Windows PC | `git pull`; confirm nothing else uses ports 80/443 |
| 2 | Router + PC | forward 80 and 443 to the PC; allow 80/443 in Windows Firewall |
| 3 | Windows PC | create `.env` (`COMPOSE_PROFILES=public`, `DUCKDNS_TOKEN`, `PUBLIC_HOST`, `PUBLIC_URL`) |
| 4 | Windows PC | `docker compose -f deployment/docker-compose.yml --env-file .env up -d --build`; `docker compose -f deployment/docker-compose.yml --env-file .env logs caddy` shows a certificate obtained |
| 5 | MacBook | `git pull`; run the live test without mutual TLS first (see Option B) |
| 6 | MacBook | `./deployment/make_client_cert.sh` (creates `~/.iphone-backup-mtls`) |
| 7 | MacBook to PC | copy `~/.iphone-backup-mtls/ca.crt` to `<repo>\config\mtls\ca.crt` on the PC (never copy `ca.key`) |
| 8 | Windows PC | add `MTLS_SNIPPET=/etc/caddy/mtls.conf` to `.env`; `docker compose -f deployment/docker-compose.yml --env-file .env up -d --build` |
| 9 | MacBook | run the live test with `PUBLIC_MTLS=1`; then `python -m mac_agent --ui` with server `https://beammedia.duckdns.org` |

### Mutual TLS (client certificate), optional second lock for Option B
The Mac agent, the screen and the Mac app present the certificate automatically (https servers only) when
`~/.iphone-backup-mtls/client.crt` and `client.key` exist (override the folder with `IPHONE_MTLS_DIR`). Without those files nothing changes.
The Safari upload page and the iPhone cannot use it (they have no such certificate) and are not published anyway.

**On the MacBook (once):**
1. `cd` into the repo and run `./deployment/make_client_cert.sh`. It creates a private CA and a client certificate in `~/.iphone-backup-mtls`
   and prints what to do next. `ca.key` never leaves the Mac. Only `ca.crt` (public) goes to the PC.
2. Copy `~/.iphone-backup-mtls/ca.crt` to the PC as `<repo>\config\mtls\ca.crt` (USB stick, AirDrop to a shared folder, scp).

**On the Windows PC:**
1. Check `config\mtls\ca.crt` exists.
2. Add to `.env`: `MTLS_SNIPPET=/etc/caddy/mtls.conf`
3. `docker compose -f deployment/docker-compose.yml --env-file .env up -d --build` (recreates Caddy). If `ca.crt` is missing or invalid, Caddy refuses to start, which is deliberate:
   it never silently falls back to "no client certificate needed". `docker compose -f deployment/docker-compose.yml --env-file .env logs caddy` shows why.

**Then on the MacBook:** `PUBLIC_URL=https://beammedia.duckdns.org PUBLIC_TOKEN=<token> PUBLIC_MTLS=1 .venv/bin/python -m pytest tests/test_public_access.py -v`.
The mTLS test proves a client without the certificate is refused and one with it is accepted.

**Turn it off:** remove `MTLS_SNIPPET` from `.env` and redeploy. **Renew:** the client certificate lasts 2 years and the CA 5; run the
script again into a new folder, replace `ca.crt` on the PC and the files in `~/.iphone-backup-mtls`. **If the Mac is lost:**
delete `config\mtls\ca.crt` on the PC (or switch the CA) and redeploy; the old certificate stops working.
If the agent says "The server may require a client certificate", the certificate files are missing on that Mac; "refused this Mac's
client certificate" means the PC has a different `ca.crt`.

---

## Option C: full web UI through DuckDNS (not built yet)

Adds the web UI and the browse/download calls to the public address, so any browser can open the library.
Because that makes the whole library depend on one token on the public internet, it should be built with:
a failed-attempt rate limit, an extra password layer in Caddy (basic auth), and the UI only served from `https`.

To build it, on the **Windows PC or MacBook** (wherever the repo is checked out) ask Claude:
*"Implement Option C in runbooks/REMOTE_ACCESS.md: publish the web UI and library browse/download through Caddy with basic auth and rate limiting."*
Then follow the Windows PC steps of Option B (they are the same: router forward, firewall, `.env`, deploy) plus the new password in `.env`.

---

## Safety notes
- Never commit or paste into chat: the pairing token (`config/settings.json`), the DuckDNS token (`.env`), or Tailscale auth keys.
- If the pairing token leaks: on the PC delete `config/settings.json` and run `docker compose -f deployment/docker-compose.yml --env-file .env restart backend`; a new token is generated (then re-enter it on the MacBook and in the browser).
- The library files are in `C:\Senthil Krishna\iPhoneLibrary`; the catalogs are in the Docker volume `iphone-data`. Deleting files by hand leaves stale catalog entries.

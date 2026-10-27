# Contract: browsing the stored library from the phone app

Status: **draft for review, nothing built.** Written 2026-10-20 (Mac). Implements item 5 of the "backup box" in `archive/ROADMAP.md`
and the idea in `archive/BROWSE_LIBRARY.md`. It is part of the one iPhone app (a new tab), not a separate app.

## Goals and limits
- Show the folders and files the home agent has stored (`Photos/2026/October`, `Trips/Goa`, ...), with thumbnails, and open a photo or video.
- The agent only ever calls out; the relay (AWS) is untrusted and must not be able to read names, folders, thumbnails or files.
- Read-only: nothing here can change, delete or upload to the library.
- The crypto format does **not** change. The same `IBE1` blob (X25519 + HKDF + ChaCha20-Poly1305 chunks, see `relay/crypto.py`)
  is used in both directions; only who holds which key changes. Swift already has the opener and the sealer.

## Keys
| Who | Key | Held where |
|---|---|---|
| Agent | existing X25519 key (`agent_keys`) | Pi/PC, backed up by the owner |
| Phone | **new** X25519 key, one per phone install | iOS Keychain (this device only), never leaves the phone |

The phone registers its public key with the relay: `PUT /api/phone/key {public_key}` (user token; the relay stores it per token).
The agent reads the list with `GET /api/relay/phones` (agent token): `[{phone_id, public_key, auth, approved_hint}]`.

### Why the agent must not just trust that list
The relay could add its own "phone" key and the agent would seal the whole library to it. So the agent only seals to a phone key it has
**authenticated**, in one of two ways:
1. **Invite QR (auto-approved).** The invite QR carries a second, random value `auth_secret` that the phone keeps and the relay never
   sees (new field `&auth=<base64url>` in `iphonebackup://enroll?...`; the agent keeps it next to the invite). When the phone registers its key it sends
   `auth = HMAC-SHA256(auth_secret, phone_public_key)`; the agent recomputes it and approves the key if it matches.
2. **Pair QR / email sign-in (manual approval).** There is no shared secret, so the agent lists the new phone with the key's fingerprint
   (`python -m home_agent --phones`, later a button on the PC web page). The owner compares it with the code shown in the phone's Account tab
   and approves (`--approve <phone_id>`). Until approved, the agent answers nothing and the app says "waiting for approval on your PC".
Revoking a phone: `--revoke <phone_id>` on the agent, plus removing the phone's token on the relay.

## Messages (all bodies are IBE1 blobs, base64 in JSON or raw bytes in PUT bodies)
**Request** (phone -> agent), sealed to the **agent** key. Sealed metadata (JSON) is the request:
```
{"v":1,"id":"<random 128-bit hex>","ts":"<ISO time>","phone":"<phone_id>","op":"list|thumb|file","path":"Photos/2026/October",
 "name":"IMG_1234.HEIC","cursor":null,"offset":0}
```
- `list`: folder listing of `path` (empty = root), paged by `cursor`.
- `thumb`: JPEG thumbnail (longest side 256 px, at most 64 KB) of `path/name`.
- `file`: the original, from byte `offset` (resumable; returned in 4 MiB pieces).

**Response** (agent -> phone), sealed to the **phone** key (the agent looks it up from `phone`), same blob format:
- `list`: `{"path":..., "folders":[{"name"}], "files":[{"name","size","taken_at","kind"}], "next_cursor":null|"..."}` (at most 256 KB plaintext per page).
- `thumb`: the JPEG bytes.
- `file`: one piece, metadata carries `{"offset":N,"total":SIZE,"sha256":"<of the whole file, on the last piece>"}`.
- errors: `{"error":"not_found|not_allowed|busy|too_large"}` (no paths echoed back).

## Relay endpoints (new; the relay stores sealed bytes only, for a short time)
| Call | Who | Meaning |
|---|---|---|
| `PUT /api/phone/key` | phone | register this phone's public key and its `auth` value |
| `GET /api/relay/phones` | agent | phone keys to authenticate |
| `POST /api/browse/requests` `{id, blob}` | phone | queue a request (max 4 KB blob, 30 per minute per account) |
| `GET /api/relay/browse/pending?wait=25` | agent | long poll: requests for this account, oldest first |
| `PUT /api/relay/browse/{id}/response?piece=N&of=M` | agent | upload one response piece (max 4 MiB + overhead) |
| `GET /api/browse/requests/{id}?wait=25` | phone | long poll: `{state: waiting|ready, pieces}` |
| `GET /api/browse/requests/{id}/pieces/{n}` | phone | download piece `n` |
| `DELETE /api/browse/requests/{id}` | phone | done; the relay also deletes by TTL |
Rules: requests and responses expire after 10 minutes; a per-account cap on stored browse bytes (e.g. 64 MB) so it cannot fill the disk;
the phone token may only read its own phone's responses; the agent token only its own account's requests. `GET /api/account` reports the
agent's `last_seen` already, so the app can say "your box is offline" without sending a request.

## What the relay (and an eavesdropper) still learns
That a phone asked something, when, how often, the size of each answer and that an agent answered. Not which folder, which file, or any content.
Mitigation later if wanted: pad answers to fixed sizes.

## Agent-side rules
- Open the request, check `ts` within 5 minutes and `id` not seen before (replay), check the phone is authenticated and not revoked.
- Resolve `path`/`name` with the existing `safe_folder` / `safe_name`; reject absolute paths, `..`, symlinks that leave the library; read only below
  the library roots (`Photos`, `Videos`, `Files`, and user folders).
- Thumbnails are generated and cached on the agent (so the phone never downloads originals to browse); reuse `thumbs.py`.
- Never log names or paths; log only counts and error kinds.

## Phone (app) behaviour
- New **Library** tab (browse the PC), next to **Sent** (the phone's own record). Folder list, thumbnail grid, photo/video viewer.
- Cache decrypted thumbnails in memory/disk cache inside the app sandbox only; "Remove this phone" wipes the cache and the phone key.
- Say so when the agent is offline or the phone is not approved yet instead of showing an empty folder.
- Opening a video downloads and decrypts to a temp file in 4 MiB pieces (no streaming playback in v1).

## Tests to write with it
- Python and Swift: seal a request with one, open with the other (add to `tools/make_test_vectors`, regenerate the Swift vectors).
- Relay: request/response round trip, expiry, isolation between accounts, size and rate limits.
- Agent: path traversal attempts, replay, unauthenticated phone, revoked phone, oversized answers.
- Swift: key creation in the Keychain, HMAC auth value matches Python's, paging.

## Open questions for the human
1. Is **manual approval on the PC** acceptable for email/pair sign-in phones (invite-QR phones are auto-approved)?
2. Should video playback stream, or is "download then open" enough for the first version?
3. Is a 10-minute relay hold for answers acceptable, given the Pi may be slow or offline?

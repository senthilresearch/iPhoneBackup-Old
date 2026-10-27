# Browsing your stored photos from the phone

Status: **idea, not built.** Decided 2026-10-20. See also `ROADMAP.md` ("the backup box").

## What you want

Open the phone app, see the same folders that are on the Raspberry Pi
(`Photos/2026/October`, `Trips/Goa`, ...), and tap a folder to see thumbnails and open a photo.
Not just "what did this phone send", but "what is stored at home".

## Why it is not trivial

```
 iPhone ──── internet ────► AWS RELAY ◄──── internet ──── Raspberry Pi (home agent + SSD)
                         (a mailbox)        the Pi only CALLS OUT;
                                            nothing can call IN to it
```

- The photos live on the **Pi's SSD**.
- The Pi sits behind your home router and **never accepts incoming connections**. That is what makes
  it safe and easy to set up, but it also means the phone cannot just open a connection to the Pi.
- The relay (in AWS) is reachable by everybody, but it **does not keep the photos**. It only holds
  them until the Pi collects them, and it must not be able to read them.

So the phone and the Pi cannot talk to each other directly. They need the relay as a go-between.

## How it would work (like leaving notes in a mailbox)

1. You open a folder in the app. The phone sends a **request** to the relay:
   "please list `Photos/2026/October`".
2. The Pi is always holding a call open to the relay ("anything for me?"). It receives the request.
3. The Pi looks at its SSD and sends the **answer** (folder names, file names, small thumbnails) back
   through the relay.
4. The phone shows it.
5. Tap a photo: same thing, but the Pi sends the full-size file (slower, because it goes over your
   home internet upload speed).

## Keeping it private

The Pi **locks every answer so only your phone can open it** (it uses the phone's own key). The relay
just passes along sealed envelopes it cannot read. Today the phone locks files for the Pi; this
adds the opposite direction, so the phone needs its own key pair.

## What has to be built

| Part | Work |
|---|---|
| Phone | Create and store its own key; screens for folders, thumbnails and a photo viewer; ask for and decrypt answers |
| Relay | A request/answer channel that passes sealed messages between phone and Pi (new API) |
| Home agent (Pi) | Hold the call open, answer "list folder / thumbnail / file" requests, seal the answers |
| Contract | Written down first in `SYNC_NOTES.md`; crypto test vectors regenerated so Swift and Python agree |

## What it will feel like

- Folder lists and thumbnails: quick (small).
- Opening a full photo or video: depends on your home upload speed; it may take a few seconds to a minute.
- If the Pi is off or offline, the app says so instead of showing an empty folder.
- At home, a direct phone-to-Pi connection could later make this faster. That is an optional extra.

## Alternative that is much simpler (but less convenient)

Show the library only in a **web page served by the Pi on your home Wi-Fi**
(the existing web UI already does folders and thumbnails). It works only at home and
needs no new relay features, but it is not inside the iPhone app. We could do this first and
add the in-app version later.

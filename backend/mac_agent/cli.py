import argparse
import asyncio

import httpx

from app.device import device_state, open_afc

from . import scancache
from .tls import explain as tls_hint, new_client
from .ui import remember
from .agent import AuthError, day_after, day_start, extension_counts, normalize_exts, parse_pairing, preflight, sync

REASONS = {
    "NoAppleService": "usbmuxd is not reachable (is this a Mac with the phone plugged in?)",
    "NoDevice": "Plug in the iPhone with a cable.",
    "Locked": "Unlock the iPhone.",
    "NotTrusted": "Tap Trust on the iPhone.",
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="mac_agent", description="Send an iPhone's media to the backend.")
    p.add_argument("--ui", action="store_true", help="open the browser screen instead of running from the command line")
    p.add_argument("--port", type=int, default=8770, help="port for --ui (default 8770)")
    p.add_argument("--pair", help="pairing link from 'Connect from iPhone' (sets --server and --token)")
    p.add_argument("--server", help="e.g. http://192.168.1.20:8765")
    p.add_argument("--token", help="pairing token (Settings > pairing in the web UI)")
    p.add_argument("--dest", help='destination folder, e.g. "Trips/Goa"')
    p.add_argument("--kinds", default="", help="comma list of photos,videos,files (default: all)")
    p.add_argument("--exts", default="",
                   help="comma list of file extensions to copy, e.g. heic,jpg,mov or none (default: all). "
                        "--dry-run lists the extensions found.")
    p.add_argument("--since", help="only files modified on/after YYYY-MM-DD (this Mac's local time)")
    p.add_argument("--until", help="only files modified on/before YYYY-MM-DD, that whole day included")
    p.add_argument("--dry-run", action="store_true", help="list what would be considered; upload nothing")
    p.add_argument("--force", action="store_true",
                   help="upload again even if the server already has the file, replacing the stored copy")
    p.add_argument("--rescan", action="store_true",
                   help="ask the phone about every file again instead of only the new ones")
    p.add_argument("--wait", type=int, default=120, help="seconds to wait for a locked/untrusted phone")
    args = p.parse_args(argv)
    if args.pair:
        try:
            args.server, args.token = parse_pairing(args.pair)
        except ValueError as e:
            p.error(str(e))
    if not args.ui and not (args.server and args.token):
        p.error("--server and --token are required (or use --ui)")
    return args


async def wait_for_phone(timeout: int) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while True:
        state = await device_state()
        if state["connected"]:
            return state
        msg = REASONS.get(state["reason"], state["reason"])
        if msg != last:
            print(f"Waiting for the phone: {msg}")
            last = msg
        if asyncio.get_event_loop().time() >= deadline:
            raise SystemExit(f"No usable phone: {msg}")
        await asyncio.sleep(3)


async def run(args) -> int:
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()} or None
    since = day_start(args.since) if args.since else None
    until = day_after(args.until) if args.until else None
    exts = normalize_exts(args.exts) or None
    with new_client(args.server, args.token) as client:
        try:
            preflight(client)
            remember(args.server, args.token)  # so --ui opens with them filled in
        except AuthError as e:
            print(e)
            return 2
        except httpx.HTTPError as e:
            print(f"Cannot reach {args.server}: {e}{tls_hint(e)}")
            return 2
        await wait_for_phone(args.wait)
        async with open_afc() as (udid, afc):
            known = {} if args.rescan else scancache.load(udid)
            try:
                s = await sync(client, afc, udid, kinds=kinds, since=since, until=until, exts=exts,
                               destination=args.dest, dry_run=args.dry_run, force=args.force, known=known)
                scancache.save(udid, known)
            except AuthError as e:
                print(e)
                return 2
    if args.dry_run:
        print("File types found:", ", ".join(f"{e['ext']} {e['count']}" for e in extension_counts(s.planned)) or "none")
        for f in s.planned:
            print(f"{f.kind:7} {f.size:>12}  {f.path}")
        return 0
    print(f"Done: {s.uploaded} uploaded, {s.duplicates} duplicates, {s.skipped} already there, "
          f"{len(s.failed)} failed, {s.bytes_sent / 1e6:.1f} MB sent.")
    for path, why in s.failed:
        print(f"  failed: {path}: {why}")
    return 1 if s.failed else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.ui:
        from .ui import serve

        serve(args.port, defaults={"server": args.server, "token": args.token})
        return 0
    return asyncio.run(run(args))

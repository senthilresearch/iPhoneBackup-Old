"""Phase 1 spike: detect the iPhone over USB and list the camera roll (/DCIM)."""
import asyncio

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService


async def main():
    lockdown = await create_using_usbmux()
    print("Device:", lockdown.display_name, "| iOS", lockdown.product_version)
    async with AfcService(lockdown) as afc:
        for folder in await afc.listdir("/DCIM"):
            if not await afc.isdir(f"/DCIM/{folder}"):
                print(f"/DCIM/{folder}: (file, skipped)")
                continue
            files = await afc.listdir(f"/DCIM/{folder}")
            print(f"/DCIM/{folder}: {len(files)} items, e.g. {files[:3]}")


asyncio.run(main())

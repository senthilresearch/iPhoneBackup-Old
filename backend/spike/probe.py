import asyncio
from pymobiledevice3 import usbmux
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService

async def main():
    for d in await usbmux.list_devices():
        print("mux:", d.serial, d.connection_type)
    ld = await create_using_usbmux()
    async with AfcService(ld) as afc:
        for top in await afc.listdir("/"):
            try:
                isd = await afc.isdir("/" + top)
                n = len(await afc.listdir("/" + top)) if isd else "-"
            except Exception as e:
                n = f"err {e}"
            print("/" + top, n)
asyncio.run(main())

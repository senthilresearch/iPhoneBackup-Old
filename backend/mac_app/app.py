"""iPhone Backup for Mac: window + menu-bar item around the agent screen. `python -m mac_app`."""
import socket
import sys
import threading
import time

import uvicorn

from mac_agent import ui as agent_ui

from . import login
from .awake import KeepAwake


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int):
    agent_ui.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    app = agent_ui.create_app()
    app.state.job._log("Screen ready. Preview the phone, then copy.")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):  # wait until it accepts connections
        if server.started:
            break
        time.sleep(0.05)
    return server, app


def status_title(state: dict, running: bool) -> str:
    if running and state.get("total"):
        return f"📱 {int(100 * state.get('index', 0) / state['total'])}%"
    return "📱 …" if running else "📱"


def main() -> int:
    import webview
    from AppKit import NSAlert, NSMenu, NSMenuItem, NSStatusBar
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    port = free_port()
    server, app = start_server(port)
    job = app.state.job
    awake = KeepAwake(lambda: job.running)
    awake.start()
    quitting = threading.Event()

    window = webview.create_window("iPhone Backup", f"http://127.0.0.1:{port}/", width=1280, height=860, min_size=(900, 600))

    def on_closing():
        if quitting.is_set():
            return True
        window.hide()  # keep running in the menu bar so a copy carries on
        return False

    window.events.closing += on_closing

    class Menu(NSObject):
        def show_(self, _):
            window.show()

        def toggleLogin_(self, item):
            login.set_enabled(not login.enabled())
            item.setState_(1 if login.enabled() else 0)

        def quit_(self, _):
            if job.running:
                a = NSAlert.alloc().init()
                a.setMessageText_("A copy is still running")
                a.setInformativeText_("Quit anyway? Files already sent are kept, and the next run carries on where this stopped.")
                a.addButtonWithTitle_("Keep running")
                a.addButtonWithTitle_("Quit")
                if a.runModal() != 1001:
                    return
            quitting.set()
            awake.stop()
            server.should_exit = True
            for w in list(webview.windows):
                w.destroy()

    menu_target = Menu.alloc().init()
    holder = {}

    def build_menu():
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        item.button().setTitle_("📱")
        menu = NSMenu.alloc().init()
        for title, sel, key in (("Open iPhone Backup", "show:", ""), ("Start at login", "toggleLogin:", ""), ("Quit", "quit:", "q")):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key)
            mi.setTarget_(menu_target)
            if sel == "toggleLogin:":
                mi.setState_(1 if login.enabled() else 0)
            menu.addItem_(mi)
        item.setMenu_(menu)
        holder["item"] = item  # keep a reference or the item disappears

    def tick():
        while not quitting.is_set():
            title = status_title(job.state, job.running)
            AppHelper.callAfter(lambda t=title: holder["item"].button().setTitle_(t))
            time.sleep(1)

    def after_start():
        AppHelper.callAfter(build_menu)
        time.sleep(0.5)
        threading.Thread(target=tick, daemon=True).start()

    webview.start(after_start)
    awake.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

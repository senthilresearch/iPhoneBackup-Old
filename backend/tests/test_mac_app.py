import time

from mac_app import login
from mac_app.app import status_title
from mac_app.awake import KeepAwake


def test_status_title():
    assert status_title({}, False) == "📱"
    assert status_title({}, True) == "📱 …"
    assert status_title({"index": 25, "total": 100}, True) == "📱 25%"


def test_keep_awake_follows_the_job(monkeypatch):
    started = []

    class P:
        def __init__(self, cmd):
            started.append(cmd)
            self.dead = False

        def poll(self):
            return 0 if self.dead else None

        def terminate(self):
            self.dead = True

    monkeypatch.setattr("mac_app.awake.subprocess.Popen", P)
    busy = {"v": False}
    k = KeepAwake(lambda: busy["v"], interval=0.01)
    k.start()
    time.sleep(0.05)
    assert not k.active
    busy["v"] = True
    time.sleep(0.1)
    assert k.active and started == [["caffeinate", "-dims"]]  # started once, not on every poll
    busy["v"] = False
    time.sleep(0.1)
    assert not k.active
    k.stop()


def test_login_item(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "PLIST", tmp_path / "LaunchAgents" / "x.plist")
    assert not login.enabled()
    login.set_enabled(True)
    assert login.enabled()
    import plistlib
    data = plistlib.loads(login.PLIST.read_bytes())
    assert data["RunAtLoad"] and data["ProgramArguments"][-2:] == ["-m", "mac_app"]
    login.set_enabled(False)
    assert not login.enabled()

"""Keep the Mac awake while a scan or copy runs (sleep pauses USB transfers). Released as soon as the job ends."""
import subprocess
import threading
import time


class KeepAwake:
    def __init__(self, is_busy, interval: float = 2.0):
        self._is_busy = is_busy
        self._interval = interval
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def active(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = bool(self._is_busy())
            except Exception:
                busy = False
            if busy and not self.active:
                try:  # -d display, -i idle, -m disk, -s system sleep
                    self._proc = subprocess.Popen(["caffeinate", "-dims"])
                except OSError:
                    self._proc = None
            elif not busy and self.active:
                self.release()
            time.sleep(self._interval)

    def release(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    def stop(self) -> None:
        self._stop.set()
        self.release()

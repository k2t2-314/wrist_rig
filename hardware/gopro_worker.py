import time
import urllib.error
import urllib.request

from config import GOPRO_TIMEOUT


class GoProWorker:
    """HTTP control wrapper for a USB-wired GoPro camera."""

    def __init__(self):
        self.base_url = ""
        self.connected = False
        self.recording = False
        self.last_error = ""

    def _normalize_base_url(self, base_url: str) -> str:
        base = str(base_url or "").strip()
        if not base:
            raise ValueError("GoPro base URL is required.")
        return base.rstrip("/")

    def _build_url(self, path: str) -> str:
        if not self.base_url:
            raise RuntimeError("GoPro base URL is not configured.")
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{suffix}"

    def _get(self, path: str, timeout: float | None = None):
        req = urllib.request.Request(
            self._build_url(path),
            headers={"User-Agent": "wrist-rig/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout or GOPRO_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return getattr(response, "status", response.getcode()), body

    def _setup(self):
        self._get("/gopro/camera/control/wired_usb?p=1")
        self._get("/gopro/camera/analytics/set_client_info")
        self._get("/gopro/camera/control/set_ui_controller?p=2")
        self._get("/gopro/camera/presets/set_group?id=1000")
        self._get("/gopro/camera/keep_alive")
        time.sleep(1.0)

    def connect(self, base_url: str, on_success=None, on_error=None):
        normalized = ""
        try:
            normalized = self._normalize_base_url(base_url)
            self.base_url = normalized
            self._get("/gopro/camera/state")
            self._setup()
            self.connected = True
            self.recording = False
            self.last_error = ""
            if on_success:
                on_success()
        except Exception as exc:
            self.connected = False
            self.recording = False
            if not self.base_url:
                self.base_url = normalized
            self.last_error = str(exc)
            if on_error:
                on_error(self.last_error)

    def start_recording(self, on_error=None):
        if not self.connected:
            return
        if self.recording:
            return
        try:
            self._get("/gopro/camera/keep_alive")
            self._get("/gopro/camera/shutter/start")
            self.recording = True
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            if on_error:
                on_error(self.last_error)

    def stop_recording(self, on_error=None):
        if not self.connected:
            return
        if not self.recording:
            return
        try:
            self._get("/gopro/camera/keep_alive")
            self._get("/gopro/camera/shutter/stop")
            self.recording = False
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            if on_error:
                on_error(self.last_error)

    def disconnect(self):
        self.connected = False
        self.recording = False
        self.last_error = ""
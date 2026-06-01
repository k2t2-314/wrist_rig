from pathlib import Path
import time

from config import OBS_TIMEOUT


class OBSWorker:
    """Thin wrapper around obs-websocket for experiment recording control."""

    def __init__(self):
        self.client = None
        self.connected = False
        self.recording = False
        self.last_error = ""
        self._pending_suffix = ""

    def _rename_with_retry(self, src: Path, dst: Path, timeout_s: float = 2.0, interval_s: float = 0.2):
        deadline = time.time() + timeout_s
        last_exc = None
        while time.time() < deadline:
            try:
                src.rename(dst)
                return
            except PermissionError as exc:
                last_exc = exc
                time.sleep(interval_s)
            except FileNotFoundError:
                return
        if last_exc is not None:
            raise last_exc

    def connect(self, host: str, port: int, password: str = "", on_success=None, on_error=None):
        if self.connected:
            return
        try:
            import obsws_python as obs
        except Exception as exc:
            self.last_error = (
                "obsws-python is not installed. Run: python -m pip install obsws-python"
            )
            if on_error:
                on_error(self.last_error)
            return

        try:
            self.client = obs.ReqClient(
                host=host,
                port=int(port),
                password=password or "",
                timeout=OBS_TIMEOUT,
            )
            self.connected = True
            self.recording = False
            self.last_error = ""
            if on_success:
                on_success()
        except Exception as exc:
            self.client = None
            self.connected = False
            self.recording = False
            self.last_error = str(exc)
            if on_error:
                on_error(self.last_error)

    def start_recording(self, suffix: str = "", on_error=None):
        if not self.connected or not self.client:
            return
        try:
            self._pending_suffix = str(suffix or "").strip()
            self.client.start_record()
            self.recording = True
            self.last_error = ""
        except Exception as exc:
            self.recording = False
            self.last_error = str(exc)
            if on_error:
                on_error(self.last_error)

    def stop_recording(self, on_error=None):
        if not self.connected or not self.client:
            return
        if not self.recording:
            return
        try:
            response = self.client.stop_record()
            output_path = getattr(response, "output_path", None)
            if output_path and self._pending_suffix:
                src = Path(output_path)
                dst = src.with_name(f"{src.stem}_{self._pending_suffix}{src.suffix}")
                if dst != src:
                    if dst.exists():
                        counter = 1
                        while True:
                            candidate = src.with_name(
                                f"{src.stem}_{self._pending_suffix}_{counter}{src.suffix}"
                            )
                            if not candidate.exists():
                                dst = candidate
                                break
                            counter += 1
                    self._rename_with_retry(src, dst)
            self.last_error = ""
            self.recording = False
            self._pending_suffix = ""
        except Exception as exc:
            # Ignore the common case where OBS is already stopped,
            # but still surface other errors if the caller wants them.
            self.last_error = str(exc)
            self.recording = False
            if on_error:
                on_error(self.last_error)

    def disconnect(self):
        self.connected = False
        self.recording = False
        self.last_error = ""
        self.client = None

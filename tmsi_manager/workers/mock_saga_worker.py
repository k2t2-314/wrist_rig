"""
workers/mock_saga_worker.py — drop-in replacement for SagaWorker when no device is present

Matches the SagaWorker public API exactly so gui/app.py needs no conditional logic.
Recording "files" are not actually written; operations are logged to stdout.
"""

import threading
import time


class MockSagaWorker:
    """Simulates a connected SAGA device for development and testing."""

    def __init__(self, label: str = "MockSAGA"):
        self.label = label
        self._connected = False
        self._streaming = False
        self._recording = False
        self._lock = threading.Lock()
        self._stream_name = self.label.replace(" ", "").replace("#", "")
        self._channel_names = [f"Ch {i+1:02d}" for i in range(32)]
        self._recording_path = ""
        self._recording_started_s = 0.0
        self._recording_start_errors = 0
        self._recording_stop_errors = 0
        self._recording_last_error = ""

    # ── Public API (mirrors SagaWorker) ──────────────────────────────────────

    def connect(self, device_index: int = 0, on_success=None, on_error=None):
        def _run():
            time.sleep(0.3)  # simulate discovery delay
            with self._lock:
                self._connected = True
                self._streaming = False
            print(f"[{self.label}] Mock connected (device_index={device_index}).")
            if on_success:
                on_success()

        threading.Thread(target=_run, daemon=True).start()

    def disconnect(self):
        with self._lock:
            self._streaming = False
            self._recording = False
            self._connected = False
        print(f"[{self.label}] Mock disconnected.")

    def start_streaming(self, on_success=None, on_error=None):
        with self._lock:
            if not self._connected:
                msg = f"[{self.label}] Not connected (mock)."
                if callable(on_error):
                    on_error(msg)
                return
            self._streaming = True
        print(f"[{self.label}] Mock LSL stream started.")
        if on_success:
            on_success()

    def stop_streaming(self):
        with self._lock:
            self._streaming = False
        print(f"[{self.label}] Mock LSL stream stopped.")

    def start_recording(self, filepath: str, on_error=None):
        with self._lock:
            if not self._connected:
                msg = f"[{self.label}] Not connected (mock)."
                print(msg)
                self._recording_start_errors += 1
                self._recording_last_error = msg
                if callable(on_error):
                    on_error(msg)
                return
            self._recording = True
            self._recording_path = filepath + ".mock"
            self._recording_started_s = time.perf_counter()
            self._recording_last_error = ""
        print(f"[{self.label}] Mock recording started -> {filepath}")

    def stop_recording(self):
        with self._lock:
            self._recording = False
        print(f"[{self.label}] Mock recording stopped.")

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def get_recording_health(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "path": self._recording_path,
                "exists": bool(self._recording_path),
                "size_bytes": 0,
                "size_delta_bytes": 0,
                "elapsed_s": max(0.0, time.perf_counter() - self._recording_started_s)
                if self._recording and self._recording_started_s
                else 0.0,
                "start_errors": self._recording_start_errors,
                "stop_errors": self._recording_stop_errors,
                "last_error": self._recording_last_error,
            }

    def is_streaming(self) -> bool:
        with self._lock:
            return self._streaming

    def get_num_channels(self) -> int:
        return 32  # simulate SAGA32

    def get_channel_names(self, active_only: bool = False):
        return list(self._channel_names)

    def get_lsl_stream_name(self) -> str:
        return self._stream_name

    def is_live_monitor_available(self) -> bool:
        with self._lock:
            return self._streaming

    def get_live_monitor_status(self) -> str:
        with self._lock:
            if self._streaming:
                return f"LSL stream '{self._stream_name}' ready."
            if self._connected:
                return "Connect complete. Click Start Stream to enable live monitor."
        return "Connect a device, then choose one channel to monitor."

    def get_device(self):
        return None

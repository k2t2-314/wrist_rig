"""
workers/saga_worker.py — thin wrapper around a single SagaDevice

Responsibilities:
    - discover / connect / disconnect
    - start / stop LSL streaming on demand
    - start / stop file recording (poly5 or xdf)
    - expose device state for the GUI

Thread safety: connect/disconnect are called from the GUI thread.
Recording runs on the SDK's own internal thread.
"""

import os
import threading
import time

import config  # ensures TMSI_SDK_PATH is inserted into sys.path

from TMSiSDK.tmsi_sdk import TMSiSDK, DeviceType, DeviceInterfaceType, DeviceState
from TMSiSDK.tmsi_errors.error import TMSiError
from TMSiSDK.device.tmsi_device_enums import MeasurementType
from TMSiFileFormats.file_writer import FileWriter, FileFormat


def _interface(name: str) -> DeviceInterfaceType:
    return DeviceInterfaceType[name]


class SagaWorker:
    """Controls one SAGA device: discover → open → record → close.

    LSL stream lifecycle:
        connect() → open device only
        start_streaming() → _start_lsl_stream() → LSL outlet + start_measurement
        stop_streaming() → close LSL outlet and stop measurement only if no recording remains
        start/stop_recording → open/close poly5 FileWriter and manage measurement as needed
        disconnect() → stop recording + stop stream + close device
    """

    def __init__(self, label: str = "SAGA"):
        """
        Args:
            label: human-readable name shown in the GUI (e.g. "SAGA #1")
        """
        self.label = label
        self._dev = None
        self._file_writer = None
        self._lsl_writer = None          # FileWriter(FileFormat.lsl, ...)
        self._measurement_running = False
        self._lock = threading.Lock()
        self._stream_name = self.label.replace(" ", "").replace("#", "")
        self._lsl_error = ""
        self._recording_path = ""
        self._recording_started_s = 0.0
        self._recording_start_errors = 0
        self._recording_stop_errors = 0
        self._recording_last_error = ""
        self._recording_last_size = None
        self._recording_last_size_delta = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def connect(self, device_index: int = 0, on_success=None, on_error=None):
        """
        Discover SAGA devices and open connection to device at device_index.

        Args:
            device_index: index into the discovered device list (0 for first device)
            on_success: callable() called on success
            on_error:   callable(str) called with error message on failure
        """
        def _run():
            # Guard: skip if already connected
            with self._lock:
                if self._dev is not None:
                    print(f"[{self.label}] Already connected, ignoring duplicate connect.")
                    if on_success:
                        on_success()
                    return

            max_attempts = 8
            retry_delay = 1.5  # seconds between attempts
            last_exc = None

            for attempt in range(1, max_attempts + 1):
                try:
                    # num_retries=1: fail fast so our outer loop handles delays
                    TMSiSDK().discover(
                        dev_type=DeviceType.saga,
                        dr_interface=_interface(config.DR_INTERFACE),
                        ds_interface=_interface(config.DS_INTERFACE),
                        num_retries=1,
                    )
                    device_list = TMSiSDK().get_device_list(DeviceType.saga)

                    if not device_list:
                        raise RuntimeError("No SAGA devices found.")
                    if device_index >= len(device_list):
                        raise RuntimeError(
                            f"Device index {device_index} out of range "
                            f"({len(device_list)} device(s) found)."
                        )

                    dev = device_list[device_index]
                    time.sleep(0.5)  # give DS time to prepare before open()
                    dev.open()

                    with self._lock:
                        self._dev = dev
                        self._lsl_error = ""

                    print(f"[{self.label}] Connected: {dev}")
                    if on_success:
                        on_success()
                    return  # success — exit retry loop

                except (TMSiError, RuntimeError, Exception) as exc:
                    last_exc = exc
                    print(f"[{self.label}] Connect attempt {attempt}/{max_attempts} failed: {exc}")
                    if attempt < max_attempts:
                        time.sleep(retry_delay)

            # all attempts exhausted
            print(f"[{self.label}] Connect failed after {max_attempts} attempts.")
            if callable(on_error):
                on_error(str(last_exc))

        threading.Thread(target=_run, daemon=True).start()

    def disconnect(self):
        """Stop any active recording, close LSL stream, and close device connection."""
        with self._lock:
            self._stop_recording_internal()
            self._stop_lsl_stream()
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception as exc:
                    print(f"[{self.label}] Disconnect error: {exc}")
                finally:
                    self._dev = None
        print(f"[{self.label}] Disconnected.")

    def start_streaming(self, on_success=None, on_error=None):
        """Start LSL streaming without coupling it to connect()."""
        def _run():
            with self._lock:
                if self._dev is None:
                    msg = f"[{self.label}] Not connected."
                    if callable(on_error):
                        on_error(msg)
                    return
                if self._lsl_writer is not None:
                    if on_success:
                        on_success()
                    return

            self._start_lsl_stream()

            with self._lock:
                ok = self._lsl_writer is not None
                err = self._lsl_error or f"[{self.label}] Unable to start LSL stream."

            if ok:
                if on_success:
                    on_success()
            else:
                if callable(on_error):
                    on_error(err)

        threading.Thread(target=_run, daemon=True).start()

    def stop_streaming(self):
        """Stop LSL streaming while keeping recording alive if needed."""
        with self._lock:
            self._stop_lsl_stream()

    def start_recording(self, filepath: str, on_error=None):
        """
        Begin writing data to file.

        Args:
            filepath: full path to output file (without extension;
                      extension is added based on config.FILE_FORMAT)
            on_error: callable(str) called if recording fails to start
        """
        with self._lock:
            if self._dev is None:
                msg = f"[{self.label}] Not connected."
                print(msg)
                if callable(on_error):
                    on_error(msg)
                return
            if self._file_writer is not None:
                print(f"[{self.label}] Already recording.")
                return

            try:
                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                fmt = config.FILE_FORMAT.lower()
                if fmt == "poly5":
                    recording_path = filepath + ".poly5"
                    self._file_writer = FileWriter(
                        FileFormat.poly5, recording_path
                    )
                elif fmt == "xdf":
                    recording_path = filepath + ".xdf"
                    self._file_writer = FileWriter(
                        FileFormat.xdf, recording_path, add_ch_locs=False
                    )
                else:
                    raise ValueError(f"Unsupported FILE_FORMAT: {config.FILE_FORMAT}")

                self._file_writer.open(self._dev)
                # start_measurement only if LSL stream hasn't started it yet
                if not self._measurement_running:
                    self._dev.start_measurement(MeasurementType.SAGA_SIGNAL)
                    self._measurement_running = True
                self._recording_path = recording_path
                self._recording_started_s = time.perf_counter()
                self._recording_last_error = ""
                self._recording_last_size = None
                self._recording_last_size_delta = 0
                print(f"[{self.label}] Recording started -> {recording_path}")

            except Exception as exc:
                writer = self._file_writer
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass
                self._file_writer = None
                self._recording_start_errors += 1
                self._recording_last_error = str(exc)
                print(f"[{self.label}] start_recording error: {exc}")
                if callable(on_error):
                    on_error(str(exc))

    def stop_recording(self):
        """Stop writing data to file."""
        with self._lock:
            self._stop_recording_internal()

    def is_connected(self) -> bool:
        with self._lock:
            if self._dev is None:
                return False
            state = self._dev.get_device_state()
            # sampling counts as connected (LSL stream is running)
            return state in (DeviceState.connected, DeviceState.sampling)

    def is_recording(self) -> bool:
        with self._lock:
            return self._file_writer is not None

    def get_recording_health(self) -> dict:
        with self._lock:
            path = self._recording_path
            recording = self._file_writer is not None
            started_s = self._recording_started_s
            start_errors = self._recording_start_errors
            stop_errors = self._recording_stop_errors
            last_error = self._recording_last_error
            previous_size = self._recording_last_size
        exists = bool(path and os.path.exists(path))
        size_bytes = os.path.getsize(path) if exists else 0
        size_delta = 0 if previous_size is None else int(size_bytes - previous_size)
        with self._lock:
            if path == self._recording_path:
                self._recording_last_size = size_bytes
                self._recording_last_size_delta = size_delta
        return {
            "recording": recording,
            "path": path,
            "exists": exists,
            "size_bytes": size_bytes,
            "size_delta_bytes": size_delta,
            "elapsed_s": max(0.0, time.perf_counter() - started_s) if recording and started_s else 0.0,
            "start_errors": start_errors,
            "stop_errors": stop_errors,
            "last_error": last_error,
        }

    def is_streaming(self) -> bool:
        with self._lock:
            return self._lsl_writer is not None

    def get_num_channels(self) -> int:
        with self._lock:
            if self._dev is None:
                return 0
            return self._dev.get_num_channels()

    def get_channel_names(self, active_only: bool = False):
        with self._lock:
            dev = self._dev

        if dev is None:
            return []

        channels = None
        getter_names = (
            ("get_device_active_channels", "get_device_channels")
            if active_only
            else ("get_device_channels", "get_device_active_channels")
        )

        for getter_name in getter_names:
            getter = getattr(dev, getter_name, None)
            if getter is None:
                continue
            try:
                channels = getter()
            except Exception:
                channels = None
            if channels:
                break

        if channels:
            names = []
            for idx, ch in enumerate(channels):
                name = None

                get_name = getattr(ch, "get_channel_name", None)
                if callable(get_name):
                    try:
                        name = get_name()
                    except Exception:
                        name = None

                if not name or str(name).strip() in ("", "-"):
                    name = getattr(ch, "_alt_name", None)
                if not name or str(name).strip() in ("", "-"):
                    name = getattr(ch, "_def_name", None)

                names.append(str(name) if name and str(name).strip() not in ("", "-") else f"Ch {idx+1:02d}")

            if names:
                return names

        count = 0
        try:
            count = dev.get_num_channels()
        except Exception:
            count = 0
        return [f"Ch {i+1:02d}" for i in range(count)]

    def get_lsl_stream_name(self) -> str:
        return self._stream_name

    def is_live_monitor_available(self) -> bool:
        with self._lock:
            return self._lsl_writer is not None

    def get_live_monitor_status(self) -> str:
        with self._lock:
            if self._lsl_writer is not None:
                return f"LSL stream '{self._stream_name}' ready."
            if self._lsl_error:
                return f"Live monitor unavailable: {self._lsl_error}"
            if self._dev is None:
                return "Connect a device, then choose one channel to monitor."
            return "Connect complete. Click Start Stream to enable live monitor."

    def get_device(self):
        """Return the raw SagaDevice handle (for advanced SDK use)."""
        with self._lock:
            return self._dev

    # ── Internal ──────────────────────────────────────────────────────────────

    def _stop_recording_internal(self):
        """Close poly5/xdf writer and stop measurement if nothing else needs it.
        Must be called with self._lock held."""
        if self._file_writer is not None:
            try:
                self._file_writer.close()
                print(f"[{self.label}] Recording stopped.")
            except Exception as exc:
                self._recording_stop_errors += 1
                self._recording_last_error = str(exc)
                print(f"[{self.label}] stop_recording error: {exc}")
            finally:
                self._file_writer = None

        if self._measurement_running and self._lsl_writer is None:
            try:
                if self._dev is not None:
                    self._dev.stop_measurement()
            except Exception as exc:
                print(f"[{self.label}] stop_measurement after recording error: {exc}")
            finally:
                self._measurement_running = False

    def _stop_lsl_stream(self):
        """Close LSL writer and stop hardware measurement only if no recording remains.
        Must be called with self._lock held."""
        if self._lsl_writer is not None:
            try:
                self._lsl_writer.close()
                print(f"[{self.label}] LSL stream closed.")
            except Exception as exc:
                print(f"[{self.label}] LSL writer close error: {exc}")
            finally:
                self._lsl_writer = None

        self._lsl_error = ""

        if self._measurement_running and self._file_writer is None:
            try:
                if self._dev is not None:
                    self._dev.stop_measurement()
            except Exception as exc:
                print(f"[{self.label}] stop_measurement error (ignored): {exc}")
                try:
                    if self._dev is not None:
                        self._dev.close()
                        self._dev = None
                except Exception:
                    pass
            finally:
                self._measurement_running = False

    def _start_measurement_if_needed(self):
        """Start hardware measurement once, even if LSL is unavailable."""
        with self._lock:
            dev = self._dev
            measurement_running = self._measurement_running

        if dev is None or measurement_running:
            return

        dev.start_measurement(MeasurementType.SAGA_SIGNAL)
        with self._lock:
            self._measurement_running = True

    def _start_lsl_stream(self):
        """Open LSL FileWriter and start hardware measurement.
        Called from connect() thread — NOT holding self._lock."""
        with self._lock:
            dev = self._dev
        if dev is None:
            return
        stream_name = self._stream_name  # "SAGA1"
        lsl_writer = None
        try:
            dev.set_device_triggers(True)  # ensure trigger docking station is enabled
            lsl_writer = FileWriter(FileFormat.lsl, stream_name)
            lsl_writer.open(dev)
            self._start_measurement_if_needed()
            with self._lock:
                self._lsl_writer = lsl_writer
                self._lsl_error = ""
            print(f"[{self.label}] LSL stream '{stream_name}' started.")
        except Exception as exc:
            error_text = str(exc)
            with self._lock:
                self._lsl_error = error_text
            print(f"[{self.label}] LSL stream start error: {error_text}")
            if lsl_writer is not None:
                try:
                    lsl_writer.close()
                except Exception:
                    pass
            try:
                self._start_measurement_if_needed()
                print(f"[{self.label}] Continuing without live monitor; measurement is still running.")
            except Exception as measurement_exc:
                print(f"[{self.label}] Measurement start error after LSL fallback: {measurement_exc}")

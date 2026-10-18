"""
gui/app.py — TMSi Manager operator GUI

Adds a larger operator layout and a single-channel live monitor to keep
plotting overhead low while a device is connected.
"""

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _PLOT_AVAILABLE = True
except Exception:
    FigureCanvasTkAgg = None
    Figure = None
    _PLOT_AVAILABLE = False

try:
    from pylsl import StreamInlet, resolve_byprop
    _LSL_AVAILABLE = True
except Exception:
    StreamInlet = None
    resolve_byprop = None
    _LSL_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config


_MONITOR_WINDOW_SECONDS = 10.0
_MONITOR_BUFFER_SAMPLES = 50000


class TMSiManagerGUI(tk.Tk):

    def __init__(self, worker1, worker2, default_geometry="1240x760"):
        super().__init__()
        self.title("TMSi Manager")
        self.geometry(default_geometry)
        self.minsize(1100, 680)
        self.resizable(True, True)

        self.workers = [worker1, worker2]
        self._device_ui = []
        self._recording_label_vars = []

        self._monitor_stop_event = None
        self._monitor_thread = None
        self._monitor_buffer = deque(maxlen=_MONITOR_BUFFER_SAMPLES)
        self._monitor_buffer_lock = threading.Lock()
        self._monitor_channel_lookup = {}
        self._monitor_active_device_idx = None
        self._monitor_active_channel_idx = None
        self._monitor_active_channel_name = ""

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll()
        self._refresh_monitor_controls()
        self._draw_monitor_plot()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=(10, 10, 6, 10))
        left.grid(row=0, column=0, sticky="ns")

        right = ttk.Frame(self, padding=(6, 10, 10, 10))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._device_frames = []
        for idx, label in enumerate(["SAGA #1", "SAGA #2"]):
            frm = ttk.LabelFrame(left, text=label, padding=(10, 8))
            frm.pack(fill="x", pady=(0, 8))
            self._build_device_panel(frm, idx)
            self._device_frames.append(frm)

        self._build_recording_panel(left)
        self._build_monitor_panel(right)

    def _build_recording_panel(self, parent):
        pad = {"padx": 10, "pady": 6}
        rec_frm = ttk.LabelFrame(parent, text="Recording", padding=(10, 8))
        rec_frm.pack(fill="x", pady=(4, 0))
        rec_frm.columnconfigure(1, weight=1)

        ttk.Label(rec_frm, text="Filename prefix:").grid(
            row=0, column=0, sticky="w", **pad)
        self._prefix_var = tk.StringVar(value="tmsi")
        ttk.Entry(rec_frm, textvariable=self._prefix_var, width=28).grid(
            row=0, column=1, sticky="w", **pad)

        self._recording_label_vars = [
            tk.StringVar(value="saga1"),
            tk.StringVar(value="saga2"),
        ]
        for idx, var in enumerate(self._recording_label_vars):
            ttk.Label(rec_frm, text=f"SAGA #{idx + 1} file tag:").grid(
                row=idx + 1, column=0, sticky="w", **pad)
            ttk.Entry(rec_frm, textvariable=var, width=28).grid(
                row=idx + 1, column=1, sticky="w", **pad)

        btn_row = ttk.Frame(rec_frm)
        btn_row.grid(row=3, column=0, columnspan=2, pady=(0, 6))
        self._btn_start_rec = ttk.Button(
            btn_row, text="▶  Start Recording", command=self._start_recording, width=20)
        self._btn_start_rec.pack(side="left", padx=6)
        self._btn_stop_rec = ttk.Button(
            btn_row, text="■  Stop Recording", command=self._stop_recording,
            width=20, state="disabled")
        self._btn_stop_rec.pack(side="left", padx=6)

    def _build_monitor_panel(self, parent):
        frm = ttk.LabelFrame(parent, text="Single-Channel Live Monitor", padding=(12, 10))
        frm.grid(row=0, column=0, sticky="nsew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(2, weight=1)
        frm.rowconfigure(4, weight=1)

        controls = ttk.Frame(frm)
        controls.grid(row=0, column=0, sticky="ew")

        ttk.Label(controls, text="Device:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        self._monitor_device_var = tk.StringVar()
        self._monitor_device_combo = ttk.Combobox(
            controls, textvariable=self._monitor_device_var, state="disabled", width=18)
        self._monitor_device_combo.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)
        self._monitor_device_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_monitor_channel_options())

        ttk.Label(controls, text="Channel:").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        self._monitor_channel_var = tk.StringVar()
        self._monitor_channel_combo = ttk.Combobox(
            controls, textvariable=self._monitor_channel_var, state="disabled", width=30)
        self._monitor_channel_combo.grid(row=0, column=3, sticky="w", padx=(0, 16), pady=4)

        self._btn_start_monitor = ttk.Button(
            controls, text="Start Monitor", command=self._start_monitoring, state="disabled", width=16)
        self._btn_start_monitor.grid(row=0, column=4, sticky="w", padx=(0, 8), pady=4)

        self._btn_stop_monitor = ttk.Button(
            controls, text="Stop Monitor", command=self._stop_monitoring, state="disabled", width=16)
        self._btn_stop_monitor.grid(row=0, column=5, sticky="w", pady=4)

        self._monitor_status_var = tk.StringVar(
            value="Connect a device, then choose one channel to monitor.")
        self._monitor_status_label = tk.Label(
            frm, textvariable=self._monitor_status_var, anchor="w", justify="left")
        self._monitor_status_label.grid(row=1, column=0, sticky="ew", pady=(6, 10))

        plot_holder = ttk.Frame(frm)
        plot_holder.grid(row=2, column=0, sticky="nsew")
        plot_holder.columnconfigure(0, weight=1)
        plot_holder.rowconfigure(0, weight=1)

        self._monitor_canvas = None
        self._monitor_ax = None
        if _PLOT_AVAILABLE:
            self._monitor_fig = Figure(figsize=(7.0, 4.8), dpi=100)
            self._monitor_ax = self._monitor_fig.add_subplot(111)
            self._monitor_canvas = FigureCanvasTkAgg(self._monitor_fig, master=plot_holder)
            self._monitor_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
            self._clear_monitor_plot("No live channel selected.")
        else:
            ttk.Label(
                plot_holder,
                text="matplotlib is not available, so live monitoring is disabled.",
            ).grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text="Connected channel names:").grid(
            row=3, column=0, sticky="w", pady=(12, 6))

        channel_list_holder = ttk.Frame(frm)
        channel_list_holder.grid(row=4, column=0, sticky="nsew")
        channel_list_holder.columnconfigure(0, weight=1)
        channel_list_holder.rowconfigure(0, weight=1)

        self._channel_names_var = tk.StringVar(value=())
        self._channel_name_list = tk.Listbox(
            channel_list_holder,
            listvariable=self._channel_names_var,
            height=12,
            exportselection=False,
        )
        self._channel_name_list.grid(row=0, column=0, sticky="nsew")

        channel_scroll = ttk.Scrollbar(
            channel_list_holder,
            orient="vertical",
            command=self._channel_name_list.yview,
        )
        channel_scroll.grid(row=0, column=1, sticky="ns")
        self._channel_name_list.configure(yscrollcommand=channel_scroll.set)

    def _build_device_panel(self, parent, idx):
        pad = {"padx": 6, "pady": 3}

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=0, column=0, columnspan=5, sticky="w", **pad)

        btn_connect = ttk.Button(
            btn_row, text="Connect", command=lambda i=idx: self._connect(i), width=10)
        btn_connect.pack(side="left", padx=(0, 4))

        btn_start_stream = ttk.Button(
            btn_row, text="Start Stream", command=lambda i=idx: self._start_streaming(i), width=12)
        btn_start_stream.pack(side="left", padx=(0, 4))

        btn_stop_stream = ttk.Button(
            btn_row, text="Stop Stream", command=lambda i=idx: self._stop_streaming(i), width=12)
        btn_stop_stream.pack(side="left", padx=(0, 4))

        btn_disconnect = ttk.Button(
            btn_row, text="Disconnect", command=lambda i=idx: self._disconnect(i), width=10)
        btn_disconnect.pack(side="left", padx=(0, 8))

        status_dot = tk.Label(btn_row, text="●", fg="gray", font=("", 14))
        status_dot.pack(side="left")

        info_var = tk.StringVar(value="Not connected")
        ttk.Label(parent, textvariable=info_var).grid(row=1, column=0, sticky="w", **pad)

        self._device_ui.append({
            "btn_connect": btn_connect,
            "btn_start_stream": btn_start_stream,
            "btn_stop_stream": btn_stop_stream,
            "btn_disconnect": btn_disconnect,
            "status_dot": status_dot,
            "info_var": info_var,
        })

    # ── Connect / Disconnect ──────────────────────────────────────────────────

    def _connect(self, idx: int):
        w = self.workers[idx]
        ui = self._device_ui[idx]

        if w.is_connected():
            return

        ui["info_var"].set("Connecting…")
        ui["status_dot"].config(fg="orange")

        def _ok():
            self.after(0, lambda: (
                self._refresh_device_ui(idx),
                self._update_monitor_device_options(),
                self._refresh_monitor_controls(),
            ))

        def _err(msg):
            self.after(0, lambda: (
                ui["info_var"].set(f"Error: {msg}"),
                ui["status_dot"].config(fg="red"),
                self._refresh_monitor_controls(),
            ))

        w.connect(device_index=idx, on_success=_ok, on_error=_err)

    def _disconnect(self, idx: int):
        if self._monitor_active_device_idx == idx:
            self._stop_monitoring(clear_status=False)
        self.workers[idx].disconnect()
        self._refresh_device_ui(idx)
        self._update_monitor_device_options()
        self._refresh_monitor_controls()

    def _start_streaming(self, idx: int):
        w = self.workers[idx]
        ui = self._device_ui[idx]

        if not w.is_connected() or w.is_streaming():
            return

        ui["info_var"].set("Starting stream…")
        ui["status_dot"].config(fg="orange")

        def _ok():
            self.after(0, lambda: (
                self._refresh_device_ui(idx),
                self._update_monitor_device_options(),
                self._refresh_monitor_controls(),
            ))

        def _err(msg):
            self.after(0, lambda: (
                ui["info_var"].set(f"Stream error: {msg}"),
                ui["status_dot"].config(fg="red"),
                self._refresh_monitor_controls(),
            ))

        w.start_streaming(on_success=_ok, on_error=_err)

    def _stop_streaming(self, idx: int):
        if self._monitor_active_device_idx == idx:
            self._stop_monitoring(clear_status=False)
        self.workers[idx].stop_streaming()
        self._refresh_device_ui(idx)
        self._update_monitor_device_options()
        self._refresh_monitor_controls()

    # ── Recording ─────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_filename_part(value: str, fallback: str) -> str:
        text = (value or "").strip() or fallback
        for ch in '<>:"/\\|?*':
            text = text.replace(ch, "_")
        return text.strip(" .") or fallback

    def _start_recording(self):
        connected = [w for w in self.workers if w.is_connected()]
        if not connected:
            messagebox.showerror("Error", "Connect at least one SAGA first.")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = self._sanitize_filename_part(self._prefix_var.get(), "tmsi")
        labels = [
            self._sanitize_filename_part(var.get(), f"saga{idx + 1}")
            for idx, var in enumerate(self._recording_label_vars)
        ]

        connected_labels = [labels[idx] for idx, w in enumerate(self.workers) if w.is_connected()]
        if len(connected_labels) != len(set(connected_labels)):
            messagebox.showerror("Error", "Connected SAGA file tags must be unique.")
            return

        for idx, w in enumerate(self.workers):
            if not w.is_connected():
                continue
            filepath = os.path.join(config.RECORDING_DIR, f"{prefix}_{ts}_{labels[idx]}")
            w.start_recording(
                filepath,
                on_error=lambda msg, i=idx: self.after(
                    0,
                    lambda: messagebox.showwarning(
                        f"SAGA #{i+1}", f"Recording error: {msg}"
                    ),
                ),
            )

        recording = [w for w in self.workers if w.is_recording()]
        if recording:
            self._btn_start_rec.config(state="disabled")
            self._btn_stop_rec.config(state="normal")
            if len(recording) != len(connected):
                messagebox.showwarning(
                    "Recording",
                    f"Only {len(recording)}/{len(connected)} connected SAGA device(s) started recording.",
                )
        else:
            self._btn_start_rec.config(state="normal")
            self._btn_stop_rec.config(state="disabled")

    def _stop_recording(self):
        for w in self.workers:
            if w.is_recording():
                w.stop_recording()

        self._btn_start_rec.config(state="normal")
        self._btn_stop_rec.config(state="disabled")

    # ── Live monitor ──────────────────────────────────────────────────────────

    def _update_monitor_device_options(self):
        connected_labels = [w.label for w in self.workers if w.is_connected()]
        current = self._monitor_device_var.get()
        self._monitor_device_combo["values"] = connected_labels

        if current not in connected_labels:
            self._monitor_device_var.set(connected_labels[0] if connected_labels else "")

        self._update_monitor_channel_options()

    def _update_monitor_channel_options(self):
        idx, worker = self._get_selected_monitor_worker()
        current = self._monitor_channel_var.get()
        self._monitor_channel_lookup = {}

        values = []
        if worker is not None and worker.is_connected():
            channel_names = worker.get_channel_names(active_only=True)
            values = [f"{i+1:02d}: {name}" for i, name in enumerate(channel_names)]
            self._monitor_channel_lookup = {label: i for i, label in enumerate(values)}

        self._monitor_channel_combo["values"] = values
        if current not in self._monitor_channel_lookup:
            self._monitor_channel_var.set(values[0] if values else "")

        self._refresh_channel_name_list(worker)

        self._refresh_monitor_controls()

    def _refresh_channel_name_list(self, worker):
        if worker is None or not worker.is_connected():
            self._channel_names_var.set(())
            return

        channel_names = worker.get_channel_names(active_only=True)
        if not channel_names:
            self._channel_names_var.set(("No channel names available.",))
            return

        display_names = tuple(
            f"{idx + 1:02d}: {name}" for idx, name in enumerate(channel_names)
        )
        self._channel_names_var.set(display_names)

    def _get_selected_monitor_worker(self):
        selected_label = self._monitor_device_var.get()
        for idx, worker in enumerate(self.workers):
            if worker.label == selected_label:
                return idx, worker
        return None, None

    def _start_monitoring(self):
        if not _LSL_AVAILABLE:
            messagebox.showerror("Live Monitor", "pylsl is not available in this environment.")
            return
        if not _PLOT_AVAILABLE:
            messagebox.showerror("Live Monitor", "matplotlib is not available in this environment.")
            return

        device_idx, worker = self._get_selected_monitor_worker()
        if worker is None or not worker.is_connected():
            messagebox.showerror("Live Monitor", "Select a connected SAGA device first.")
            return
        if hasattr(worker, "is_live_monitor_available") and not worker.is_live_monitor_available():
            status = worker.get_live_monitor_status() if hasattr(worker, "get_live_monitor_status") else "Live monitor unavailable."
            messagebox.showerror("Live Monitor", status)
            self._monitor_status_var.set(status)
            self._refresh_monitor_controls()
            return

        channel_label = self._monitor_channel_var.get()
        if channel_label not in self._monitor_channel_lookup:
            messagebox.showerror("Live Monitor", "Select one channel to monitor.")
            return

        channel_idx = self._monitor_channel_lookup[channel_label]
        channel_names = worker.get_channel_names(active_only=True)
        channel_name = channel_names[channel_idx] if channel_idx < len(channel_names) else channel_label
        stream_name = worker.get_lsl_stream_name()

        self._stop_monitoring(clear_status=False)
        with self._monitor_buffer_lock:
            self._monitor_buffer.clear()

        self._monitor_stop_event = threading.Event()
        self._monitor_active_device_idx = device_idx
        self._monitor_active_channel_idx = channel_idx
        self._monitor_active_channel_name = channel_name
        self._monitor_status_var.set(
            f"Resolving LSL stream '{stream_name}' for {worker.label}, channel {channel_idx + 1}: {channel_name}"
        )

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(worker.label, stream_name, channel_idx, channel_name, self._monitor_stop_event),
            daemon=True,
        )
        self._monitor_thread.start()
        self._refresh_monitor_controls()

    def _stop_monitoring(self, clear_status=True):
        if self._monitor_stop_event is not None:
            self._monitor_stop_event.set()

        self._monitor_stop_event = None
        self._monitor_thread = None
        self._monitor_active_device_idx = None
        self._monitor_active_channel_idx = None
        self._monitor_active_channel_name = ""

        with self._monitor_buffer_lock:
            self._monitor_buffer.clear()

        if clear_status:
            self._monitor_status_var.set("Live monitor stopped.")
        self._clear_monitor_plot("No live channel selected.")
        self._refresh_monitor_controls()

    def _monitor_loop(self, worker_label, stream_name, channel_idx, channel_name, stop_event):
        inlet = None
        try:
            streams = resolve_byprop("name", stream_name, timeout=2.0)
            if not streams:
                raise RuntimeError(f"Could not find LSL stream '{stream_name}'.")

            inlet = StreamInlet(streams[0], max_buflen=12, recover=True)
            info = inlet.info()
            stream_channel_count = info.channel_count()
            wait_start = time.monotonic()
            last_wait_status = 0.0
            first_samples_received = False

            self.after(
                0,
                lambda: self._monitor_status_var.set(
                    f"LSL stream resolved for {worker_label} ({stream_channel_count} channels); waiting for first samples on channel {channel_idx + 1}: {channel_name}"
                ),
            )

            while not stop_event.is_set():
                chunk, timestamps = inlet.pull_chunk(timeout=0.25, max_samples=64)
                if not timestamps:
                    now = time.monotonic()
                    if now - wait_start >= 2.0 and now - last_wait_status >= 1.0:
                        last_wait_status = now
                        self.after(
                            0,
                            lambda elapsed=now - wait_start: self._monitor_status_var.set(
                                f"LSL stream resolved, but no samples received for {elapsed:.1f}s. Recording is not required; this usually means the outlet is idle or not pushing data yet."
                            ),
                        )
                    continue

                if channel_idx >= len(chunk[0]):
                    raise RuntimeError(
                        f"Selected channel index {channel_idx + 1} exceeds incoming sample width {len(chunk[0])}."
                    )

                points = []
                for sample, ts in zip(chunk, timestamps):
                    if channel_idx < len(sample):
                        points.append((ts, float(sample[channel_idx])))

                if points:
                    if not first_samples_received:
                        first_samples_received = True
                        self.after(
                            0,
                            lambda: self._monitor_status_var.set(
                                f"Receiving samples from {worker_label} | channel {channel_idx + 1}: {channel_name}"
                            ),
                        )
                    with self._monitor_buffer_lock:
                        self._monitor_buffer.extend(points)

        except Exception as exc:
            self.after(0, lambda msg=str(exc): self._handle_monitor_error(msg))
        finally:
            if inlet is not None:
                try:
                    inlet.close_stream()
                except Exception:
                    pass

    def _handle_monitor_error(self, msg):
        self._monitor_status_var.set(f"Live monitor error: {msg}")
        self._monitor_stop_event = None
        self._monitor_thread = None
        self._refresh_monitor_controls()

    def _refresh_monitor_controls(self):
        connected_labels = [w.label for w in self.workers if w.is_connected()]
        has_device = bool(connected_labels)
        is_monitoring = self._monitor_thread is not None
        has_channel = self._monitor_channel_var.get() in self._monitor_channel_lookup
        live_supported = _LSL_AVAILABLE and _PLOT_AVAILABLE
        _, selected_worker = self._get_selected_monitor_worker()
        worker_live_ready = True

        if selected_worker is not None and hasattr(selected_worker, "is_live_monitor_available"):
            worker_live_ready = selected_worker.is_live_monitor_available()

        self._monitor_device_combo.config(state="readonly" if has_device else "disabled")
        self._monitor_channel_combo.config(
            state="readonly" if has_device and self._monitor_channel_lookup else "disabled"
        )

        can_start = live_supported and has_device and has_channel and worker_live_ready and not is_monitoring
        self._btn_start_monitor.config(state="normal" if can_start else "disabled")
        self._btn_stop_monitor.config(state="normal" if is_monitoring else "disabled")

        if not live_supported:
            if not _LSL_AVAILABLE:
                self._monitor_status_var.set("Live monitor unavailable: pylsl is missing.")
            elif not _PLOT_AVAILABLE:
                self._monitor_status_var.set("Live monitor unavailable: matplotlib is missing.")
        elif selected_worker is not None and not worker_live_ready and not is_monitoring:
            self._monitor_status_var.set(selected_worker.get_live_monitor_status())
        elif not has_device and self._monitor_thread is None:
            self._monitor_status_var.set("Connect a device, then choose one channel to monitor.")

    def _draw_monitor_plot(self):
        if self._monitor_canvas is not None and self._monitor_ax is not None:
            with self._monitor_buffer_lock:
                data = list(self._monitor_buffer)

            if data:
                window_seconds = _MONITOR_WINDOW_SECONDS
                last_ts = data[-1][0]
                window_start = last_ts - window_seconds
                visible = [(ts, value) for ts, value in data if ts >= window_start]

                first_ts = visible[0][0]
                x_vals = [ts - first_ts for ts, _ in visible]
                y_vals = [value for _, value in visible]
                duration = x_vals[-1] if x_vals else 0.0

                # While only a short history is available, zoom to the collected span
                # so the trace does not appear as a tiny segment on the left.
                if duration < 1.0:
                    x_limit = 1.0
                elif duration < window_seconds:
                    x_limit = duration
                else:
                    x_limit = window_seconds

                self._monitor_ax.clear()
                self._monitor_ax.plot(x_vals, y_vals, color="tab:blue", linewidth=1.0)
                self._monitor_ax.set_xlim(0.0, x_limit)
                self._monitor_ax.set_xlabel("Time in view (s)")
                self._monitor_ax.set_ylabel("Amplitude")
                title = self._monitor_active_channel_name or "Single-channel view"
                self._monitor_ax.set_title(title)
                self._monitor_ax.grid(True, linewidth=0.4, alpha=0.5)
            else:
                waiting_text = "Waiting for LSL samples..." if self._monitor_thread is not None else "No live channel selected."
                self._clear_monitor_plot(waiting_text)

            self._monitor_canvas.draw_idle()

        self.after(150, self._draw_monitor_plot)

    def _clear_monitor_plot(self, message):
        if self._monitor_ax is None:
            return

        self._monitor_ax.clear()
        self._monitor_ax.text(
            0.5, 0.5, message, ha="center", va="center", transform=self._monitor_ax.transAxes
        )
        self._monitor_ax.set_xticks([])
        self._monitor_ax.set_yticks([])
        self._monitor_ax.set_title("Single-channel view")

    # ── Polling / refresh ─────────────────────────────────────────────────────

    @staticmethod
    def _format_bytes(value) -> str:
        try:
            size = float(value)
        except Exception:
            size = 0.0
        units = ["B", "KB", "MB", "GB"]
        idx = 0
        while size >= 1024.0 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        return f"{size:.1f}{units[idx]}" if idx else f"{int(size)}B"

    def _recording_health_text(self, worker) -> tuple[str, bool]:
        if not hasattr(worker, "get_recording_health"):
            return "", False
        try:
            health = worker.get_recording_health()
        except Exception as exc:
            return f"RecHealth ERROR: {exc}", True
        errors = int(health.get("start_errors", 0) or 0) + int(health.get("stop_errors", 0) or 0)
        last_error = str(health.get("last_error", "") or "")
        if errors:
            return f"RecErrors={errors} {last_error}", True
        if not health.get("recording"):
            return "", False
        path = str(health.get("path", "") or "")
        name = os.path.basename(path) if path else "pending"
        exists = "exists" if health.get("exists") else "missing"
        size = self._format_bytes(health.get("size_bytes", 0))
        delta = self._format_bytes(health.get("size_delta_bytes", 0))
        return f"File: {name} {exists} size={size} delta={delta}/poll", False

    def _refresh_device_ui(self, idx: int):
        w = self.workers[idx]
        ui = self._device_ui[idx]

        if w.is_connected():
            n_ch = w.get_num_channels()
            stream = "ON" if w.is_streaming() else "OFF"
            rec = "YES" if w.is_recording() else "NO"
            health_text, health_error = self._recording_health_text(w)
            text = f"Channels: {n_ch}   Stream: {stream}   Recording: {rec}"
            if health_text:
                text += f"   {health_text}"
            ui["info_var"].set(text)
            ui["status_dot"].config(fg="red" if health_error else ("green" if w.is_streaming() else "orange"))
            ui["btn_connect"].config(state="disabled")
            ui["btn_start_stream"].config(state="normal" if not w.is_streaming() else "disabled")
            ui["btn_stop_stream"].config(state="normal" if w.is_streaming() else "disabled")
            ui["btn_disconnect"].config(state="normal")
        else:
            ui["info_var"].set("Not connected")
            ui["status_dot"].config(fg="gray")
            ui["btn_connect"].config(state="normal")
            ui["btn_start_stream"].config(state="disabled")
            ui["btn_stop_stream"].config(state="disabled")
            ui["btn_disconnect"].config(state="disabled")

    def _poll(self, interval_ms: int = 1000):
        for idx in range(len(self.workers)):
            self._refresh_device_ui(idx)

        if self._monitor_active_device_idx is not None:
            active_worker = self.workers[self._monitor_active_device_idx]
            if not active_worker.is_connected():
                self._stop_monitoring(clear_status=False)

        self._update_monitor_device_options()
        self._refresh_monitor_controls()
        self.after(interval_ms, self._poll)

    def _on_close(self):
        self._stop_monitoring(clear_status=False)
        for worker in self.workers:
            if worker.is_recording():
                worker.stop_recording()
            if worker.is_connected():
                worker.disconnect()
        self.destroy()

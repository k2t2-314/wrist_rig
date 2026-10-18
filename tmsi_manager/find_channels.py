"""
Standalone LSL EMG channel finder for two TMSi SAGA streams.

No serial device or TMSi SDK connection is used. Start the SAGA LSL outlets
first, then run:

    python find_channels.py

The 30-second protocol is:
    0-5 s rest, 5-10 s ramp up, 10-25 s MVC hold, 25-30 s ramp down.

For each of the 128 grid channels, the tool removes the median of the rest
period, applies a 20-500 Hz bandpass, and compares the net MVC RMS with the
rest RMS:

    signal_rms = sqrt(max(active_rms^2 - rest_rms^2, 0))
    snr_db = 20 * log10(signal_rms / rest_rms)
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

import numpy as np

try:
    from scipy import signal
except ImportError:
    signal = None

try:
    import pylsl
except ImportError:
    pylsl = None


STREAMS = (("saga1", "SAGA1"), ("saga2", "SAGA2"))
GRID_CHANNELS = 64
GRID_FIRST_SAMPLE_INDEX = 1
PROTOCOL_SECONDS = 30.0
REST_END_SECONDS = 5.0
HOLD_START_SECONDS = 10.0
HOLD_END_SECONDS = 25.0
LOW_HZ = 20.0
HIGH_HZ = 500.0
LIVE_RMS_SECONDS = 0.25
HEATMAP_COLORS = (
    "#17202a",
    "#1f4e79",
    "#2874a6",
    "#17a589",
    "#58d68d",
    "#f4d03f",
    "#f39c12",
    "#e74c3c",
)


@dataclass
class ChannelResult:
    stream_key: str
    channel_name: str
    channel_index: int
    snr_db: float
    rest_rms_uv: float
    active_rms_uv: float
    signal_rms_uv: float
    offset_uv: float
    sample_rate_hz: float


def channel_name(grid_index: int) -> str:
    row = grid_index // 8 + 1
    col = grid_index % 8 + 1
    return f"R{row}C{col}"


def estimate_sample_rate(timestamps: np.ndarray, nominal_rate: float) -> float:
    if timestamps.size > 2:
        diffs = np.diff(timestamps)
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size:
            measured = float(1.0 / np.median(diffs))
            if math.isfinite(measured) and measured > 1.0:
                return measured
    if math.isfinite(nominal_rate) and nominal_rate > 1.0:
        return float(nominal_rate)
    return 2000.0


def bandpass(values: np.ndarray, sample_rate: float) -> np.ndarray:
    centered = values - np.mean(values, axis=0, keepdims=True)
    high = min(HIGH_HZ, sample_rate * 0.475)
    if signal is None or high <= LOW_HZ or values.shape[0] < 32:
        freqs = np.fft.rfftfreq(centered.shape[0], d=1.0 / sample_rate)
        spectrum = np.fft.rfft(centered, axis=0)
        spectrum[(freqs < LOW_HZ) | (freqs > high), :] = 0
        return np.fft.irfft(spectrum, n=centered.shape[0], axis=0)

    sos = signal.butter(
        4, [LOW_HZ, high], btype="bandpass", fs=sample_rate, output="sos"
    )
    try:
        return signal.sosfiltfilt(sos, centered, axis=0)
    except ValueError:
        return signal.sosfilt(sos, centered, axis=0)


def analyze_stream(
    stream_key: str,
    timestamps: np.ndarray,
    samples: np.ndarray,
    nominal_rate: float,
) -> list[ChannelResult]:
    if timestamps.size < 3 or samples.shape[0] != timestamps.size:
        raise RuntimeError(f"{stream_key}: not enough valid samples")

    relative_time = timestamps - timestamps[0]
    rest_mask = (relative_time >= 0.25) & (relative_time <= 4.75)
    hold_mask = (relative_time >= HOLD_START_SECONDS) & (
        relative_time <= HOLD_END_SECONDS
    )
    if np.count_nonzero(rest_mask) < 10 or np.count_nonzero(hold_mask) < 10:
        raise RuntimeError(
            f"{stream_key}: incomplete protocol "
            f"(received {relative_time[-1]:.1f} s)"
        )

    offsets = np.median(samples[rest_mask], axis=0)
    centered = samples.astype(np.float64, copy=False) - offsets
    sample_rate = estimate_sample_rate(timestamps, nominal_rate)
    filtered = bandpass(centered, sample_rate)

    rest_rms = np.sqrt(np.mean(np.square(filtered[rest_mask]), axis=0))
    active_rms = np.sqrt(np.mean(np.square(filtered[hold_mask]), axis=0))
    signal_rms = np.sqrt(np.maximum(np.square(active_rms) - np.square(rest_rms), 0.0))
    floor = np.finfo(float).eps
    snr = 20.0 * np.log10(np.maximum(signal_rms, floor) / np.maximum(rest_rms, floor))

    results = []
    for grid_idx in range(GRID_CHANNELS):
        results.append(
            ChannelResult(
                stream_key=stream_key,
                channel_name=channel_name(grid_idx),
                channel_index=grid_idx + GRID_FIRST_SAMPLE_INDEX,
                snr_db=float(snr[grid_idx]),
                rest_rms_uv=float(rest_rms[grid_idx]),
                active_rms_uv=float(active_rms[grid_idx]),
                signal_rms_uv=float(signal_rms[grid_idx]),
                offset_uv=float(offsets[grid_idx]),
                sample_rate_hz=sample_rate,
            )
        )
    return results


class GridHeatmap(ttk.LabelFrame):
    def __init__(self, master, title: str):
        super().__init__(master, text=title, padding=8)
        self._cells: list[tk.Label] = []
        self._values = np.zeros(GRID_CHANNELS, dtype=float)
        self._top_channels: set[str] = set()

        for col in range(8):
            self.columnconfigure(col, weight=1, uniform="grid")
        for row in range(8):
            self.rowconfigure(row, weight=1, uniform="grid")

        for idx in range(GRID_CHANNELS):
            label = tk.Label(
                self,
                text=f"{channel_name(idx)}\n--",
                bg=HEATMAP_COLORS[0],
                fg="white",
                relief="flat",
                borderwidth=2,
                font=("Segoe UI", 8),
                width=8,
                height=3,
            )
            label.grid(
                row=idx // 8,
                column=idx % 8,
                sticky="nsew",
                padx=1,
                pady=1,
            )
            self._cells.append(label)

    def show_values(
        self,
        values: np.ndarray | list[float],
        unit: str,
        top_channels: set[str] | None = None,
    ) -> None:
        arr = np.asarray(values, dtype=float)
        if arr.size != GRID_CHANNELS:
            return
        self._values = arr
        self._top_channels = top_channels or set()
        finite = arr[np.isfinite(arr)]
        if finite.size:
            low = float(np.percentile(finite, 5))
            high = float(np.percentile(finite, 95))
        else:
            low, high = 0.0, 1.0
        if high <= low:
            high = low + 1.0

        for idx, cell in enumerate(self._cells):
            value = float(arr[idx])
            if math.isfinite(value):
                fraction = min(1.0, max(0.0, (value - low) / (high - low)))
                color_idx = min(
                    len(HEATMAP_COLORS) - 1,
                    int(fraction * len(HEATMAP_COLORS)),
                )
                text = f"{channel_name(idx)}\n{value:.1f} {unit}"
            else:
                color_idx = 0
                text = f"{channel_name(idx)}\n--"
            is_top = channel_name(idx) in self._top_channels
            cell.configure(
                text=text,
                bg=HEATMAP_COLORS[color_idx],
                relief="solid" if is_top else "flat",
                borderwidth=3 if is_top else 2,
                highlightthickness=2 if is_top else 0,
                highlightbackground="#ffffff",
            )

    def clear(self) -> None:
        self.show_values(np.full(GRID_CHANNELS, np.nan), "")


class ChannelFinderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TMSi MVC Channel Finder")
        self.geometry("1420x900")
        self.minsize(1120, 760)

        self._event_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._capture_started_at = 0.0
        self._results: list[ChannelResult] = []
        self._live_values = {
            "saga1": np.full(GRID_CHANNELS, np.nan),
            "saga2": np.full(GRID_CHANNELS, np.nan),
        }
        self._stream_status = {
            "saga1": tk.StringVar(value="Not scanned"),
            "saga2": tk.StringVar(value="Not scanned"),
        }
        self._top_n_var = tk.IntVar(value=8)
        self._phase_var = tk.StringVar(value="Ready")
        self._countdown_var = tk.StringVar(value="30.0 s")
        self._detail_var = tk.StringVar(
            value="Scan both LSL streams, then start one 30-second MVC."
        )

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_events)

    def _build_ui(self) -> None:
        controls = ttk.Frame(self, padding=(12, 10))
        controls.pack(fill="x")

        ttk.Button(controls, text="Scan LSL", command=self._scan_streams).grid(
            row=0, column=0, rowspan=2, sticky="ns", padx=(0, 12)
        )
        for idx, (key, display_name) in enumerate(STREAMS):
            ttk.Label(controls, text=f"{display_name}:").grid(
                row=idx, column=1, sticky="e", padx=(0, 5)
            )
            ttk.Label(
                controls, textvariable=self._stream_status[key], width=30
            ).grid(row=idx, column=2, sticky="w")

        ttk.Separator(controls, orient="vertical").grid(
            row=0, column=3, rowspan=2, sticky="ns", padx=14
        )
        ttk.Label(controls, text="Top N:").grid(row=0, column=4, sticky="e")
        top_n = ttk.Spinbox(
            controls,
            from_=1,
            to=128,
            textvariable=self._top_n_var,
            width=6,
            command=self._refresh_results,
        )
        top_n.grid(row=0, column=5, sticky="w", padx=(5, 14))
        top_n.bind("<Return>", lambda _event: self._refresh_results())
        top_n.bind("<FocusOut>", lambda _event: self._refresh_results())

        self._start_button = ttk.Button(
            controls, text="Start MVC", command=self._start_mvc
        )
        self._start_button.grid(row=0, column=6, rowspan=2, sticky="ns", padx=4)
        self._stop_button = ttk.Button(
            controls, text="Stop", command=self._stop_mvc, state="disabled"
        )
        self._stop_button.grid(row=0, column=7, rowspan=2, sticky="ns", padx=4)

        ttk.Label(
            controls,
            textvariable=self._phase_var,
            font=("Segoe UI", 18, "bold"),
            foreground="#087f5b",
        ).grid(row=0, column=8, sticky="e", padx=(24, 8))
        ttk.Label(
            controls,
            textvariable=self._countdown_var,
            font=("Consolas", 18, "bold"),
            width=8,
        ).grid(row=0, column=9, sticky="w")
        ttk.Label(controls, textvariable=self._detail_var).grid(
            row=1, column=4, columnspan=6, sticky="w"
        )
        controls.columnconfigure(8, weight=1)

        protocol = ttk.Frame(self, padding=(12, 0, 12, 8))
        protocol.pack(fill="x")
        phases = (
            ("REST", "0-5 s", "#74c0fc"),
            ("RAMP UP", "5-10 s", "#ffd43b"),
            ("MVC HOLD", "10-25 s", "#51cf66"),
            ("RAMP DOWN", "25-30 s", "#ffa94d"),
        )
        for idx, (name, timing, color) in enumerate(phases):
            panel = tk.Frame(protocol, bg=color, padx=10, pady=5)
            panel.grid(row=0, column=idx, sticky="ew", padx=2)
            tk.Label(
                panel, text=name, bg=color, font=("Segoe UI", 9, "bold")
            ).pack()
            tk.Label(panel, text=timing, bg=color).pack()
            protocol.columnconfigure(idx, weight=1)

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        heatmaps = ttk.Frame(body)
        heatmaps.columnconfigure(0, weight=1)
        heatmaps.columnconfigure(1, weight=1)
        heatmaps.rowconfigure(0, weight=1)
        self._heatmaps = {
            "saga1": GridHeatmap(heatmaps, "SAGA1 - live RMS"),
            "saga2": GridHeatmap(heatmaps, "SAGA2 - live RMS"),
        }
        self._heatmaps["saga1"].grid(
            row=0, column=0, sticky="nsew", padx=(0, 5)
        )
        self._heatmaps["saga2"].grid(
            row=0, column=1, sticky="nsew", padx=(5, 0)
        )
        body.add(heatmaps, weight=3)

        results_frame = ttk.LabelFrame(
            body,
            text=(
                "Recommended channels - SNR = 20 log10(net MVC RMS / rest RMS)"
            ),
            padding=8,
        )
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        columns = (
            "rank",
            "stream",
            "channel",
            "snr",
            "rest",
            "active",
            "signal",
            "offset",
            "rate",
        )
        self._result_tree = ttk.Treeview(
            results_frame, columns=columns, show="headings", height=9
        )
        headings = {
            "rank": "Rank",
            "stream": "Stream",
            "channel": "Channel",
            "snr": "SNR (dB)",
            "rest": "Rest RMS (uV)",
            "active": "MVC RMS (uV)",
            "signal": "Net signal (uV)",
            "offset": "Offset (uV)",
            "rate": "Rate (Hz)",
        }
        widths = {
            "rank": 55,
            "stream": 75,
            "channel": 75,
            "snr": 90,
            "rest": 115,
            "active": 115,
            "signal": 120,
            "offset": 115,
            "rate": 90,
        }
        for col in columns:
            self._result_tree.heading(col, text=headings[col])
            self._result_tree.column(
                col,
                width=widths[col],
                minwidth=50,
                anchor="center",
                stretch=col not in ("rank", "stream", "channel"),
            )
        self._result_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            results_frame, orient="vertical", command=self._result_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._result_tree.configure(yscrollcommand=scrollbar.set)

        ttk.Label(
            results_frame,
            text=(
                "Grid mapping: R1C1..R8C8 use LSL sample indices 1..64. "
                "Higher SNR is better; inspect signal quality and anatomy before final selection."
            ),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
        body.add(results_frame, weight=2)

    def _scan_streams(self) -> None:
        if pylsl is None:
            messagebox.showerror(
                "LSL unavailable", "pylsl is not installed in this Python environment."
            )
            return
        self._detail_var.set("Scanning for SAGA1 and SAGA2...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        for key, stream_name in STREAMS:
            try:
                found = pylsl.resolve_byprop("name", stream_name, timeout=1.0)
                if not found:
                    found = pylsl.resolve_byprop(
                        "name", stream_name.lower(), timeout=0.25
                    )
                if found:
                    info = found[0]
                    payload = (
                        key,
                        True,
                        f"Found {info.name()} | {info.channel_count()} ch | "
                        f"{info.nominal_srate():.1f} Hz",
                    )
                else:
                    payload = (key, False, f"{stream_name} not found")
            except Exception as exc:
                payload = (key, False, f"Scan error: {exc}")
            self._event_queue.put(("scan", payload))
        self._event_queue.put(("scan_done", None))

    def _resolve_stream(self, stream_name: str):
        found = pylsl.resolve_byprop("name", stream_name, timeout=2.0)
        if not found:
            found = pylsl.resolve_byprop("name", stream_name.lower(), timeout=0.5)
        if not found:
            raise RuntimeError(f"Could not find LSL stream {stream_name}")
        return found[0]

    def _start_mvc(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        if pylsl is None:
            messagebox.showerror(
                "LSL unavailable", "pylsl is not installed in this Python environment."
            )
            return
        if signal is None:
            proceed = messagebox.askyesno(
                "SciPy unavailable",
                "SciPy is not available. Use the slower FFT bandpass fallback?",
            )
            if not proceed:
                return

        self._results = []
        self._refresh_results()
        for heatmap in self._heatmaps.values():
            heatmap.clear()
        self._stop_event.clear()
        self._capture_started_at = time.monotonic()
        self._phase_var.set("Connecting")
        self._countdown_var.set("30.0 s")
        self._detail_var.set("Opening both LSL streams...")
        self._start_button.configure(state="disabled")
        self._stop_button.configure(state="normal")
        self._worker = threading.Thread(
            target=self._capture_worker, daemon=True, name="ChannelFinderCapture"
        )
        self._worker.start()
        self.after(50, self._update_protocol_clock)

    def _stop_mvc(self) -> None:
        self._stop_event.set()
        self._detail_var.set("Stopping capture...")

    def _capture_worker(self) -> None:
        inlets = {}
        chunks = {key: [] for key, _ in STREAMS}
        timestamps = {key: [] for key, _ in STREAMS}
        nominal_rates = {}
        try:
            for key, stream_name in STREAMS:
                info = self._resolve_stream(stream_name)
                if info.channel_count() < GRID_FIRST_SAMPLE_INDEX + GRID_CHANNELS:
                    raise RuntimeError(
                        f"{stream_name} has {info.channel_count()} channels; "
                        "at least 65 are required for the 8x8 grid"
                    )
                inlet = pylsl.StreamInlet(
                    info,
                    max_buflen=35,
                    max_chunklen=1024,
                    processing_flags=pylsl.proc_none,
                    recover=True,
                )
                inlet.open_stream(timeout=2.0)
                inlet.flush()
                inlets[key] = inlet
                nominal_rates[key] = float(info.nominal_srate())

            start_lsl = pylsl.local_clock()
            self._event_queue.put(("capture_ready", start_lsl))
            last_live_update = 0.0

            while not self._stop_event.is_set():
                now_lsl = pylsl.local_clock()
                if now_lsl - start_lsl >= PROTOCOL_SECONDS:
                    break
                received_any = False
                for key, _stream_name in STREAMS:
                    sample_chunk, ts_chunk = inlets[key].pull_chunk(
                        timeout=0.0, max_samples=1024
                    )
                    if not ts_chunk:
                        continue
                    received_any = True
                    arr = np.asarray(sample_chunk, dtype=np.float32)
                    grid = arr[
                        :,
                        GRID_FIRST_SAMPLE_INDEX : GRID_FIRST_SAMPLE_INDEX
                        + GRID_CHANNELS,
                    ]
                    chunks[key].append(grid)
                    timestamps[key].append(np.asarray(ts_chunk, dtype=np.float64))

                    if now_lsl - last_live_update >= 0.15:
                        window_samples = max(
                            1,
                            int(
                                max(nominal_rates[key], 100.0)
                                * LIVE_RMS_SECONDS
                            ),
                        )
                        recent = grid[-window_samples:]
                        recent = recent - np.median(recent, axis=0, keepdims=True)
                        live_rms = np.sqrt(np.mean(np.square(recent), axis=0))
                        self._event_queue.put(("live", (key, live_rms)))
                if now_lsl - last_live_update >= 0.15:
                    last_live_update = now_lsl
                if not received_any:
                    time.sleep(0.005)

            if self._stop_event.is_set():
                self._event_queue.put(("stopped", None))
                return

            self._event_queue.put(("analyzing", None))
            all_results = []
            for key, _stream_name in STREAMS:
                if not chunks[key]:
                    raise RuntimeError(f"{key}: no LSL samples received")
                sample_matrix = np.concatenate(chunks[key], axis=0)
                timestamp_vector = np.concatenate(timestamps[key])
                all_results.extend(
                    analyze_stream(
                        key,
                        timestamp_vector,
                        sample_matrix,
                        nominal_rates[key],
                    )
                )
            all_results.sort(key=lambda item: item.snr_db, reverse=True)
            self._event_queue.put(("results", all_results))
        except Exception as exc:
            self._event_queue.put(("error", str(exc)))
        finally:
            for inlet in inlets.values():
                try:
                    inlet.close_stream()
                except Exception:
                    pass

    def _update_protocol_clock(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        elapsed = time.monotonic() - self._capture_started_at
        remaining = max(0.0, PROTOCOL_SECONDS - elapsed)
        self._countdown_var.set(f"{remaining:04.1f} s")
        if elapsed < REST_END_SECONDS:
            phase = "REST"
        elif elapsed < HOLD_START_SECONDS:
            phase = "RAMP UP"
        elif elapsed < HOLD_END_SECONDS:
            phase = "MVC HOLD"
        else:
            phase = "RAMP DOWN"
        self._phase_var.set(phase)
        self.after(50, self._update_protocol_clock)

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._event_queue.get_nowait()
                if event == "scan":
                    key, found, text = payload
                    self._stream_status[key].set(text)
                elif event == "scan_done":
                    self._detail_var.set(
                        "Scan complete. Both streams are required to start MVC."
                    )
                elif event == "capture_ready":
                    self._capture_started_at = time.monotonic()
                    self._phase_var.set("REST")
                    self._detail_var.set(
                        "Capturing all 128 channels. Keep completely relaxed."
                    )
                elif event == "live":
                    key, values = payload
                    self._live_values[key] = values
                    self._heatmaps[key].configure(
                        text=f"{key.upper()} - live 250 ms RMS"
                    )
                    self._heatmaps[key].show_values(values, "uV")
                elif event == "analyzing":
                    self._phase_var.set("Analyzing")
                    self._countdown_var.set("0.0 s")
                    self._detail_var.set(
                        "Filtering 128 channels and calculating SNR..."
                    )
                elif event == "results":
                    self._results = payload
                    self._finish_capture()
                    self._refresh_results()
                    self._detail_var.set(
                        "Analysis complete. White borders mark the current Top N."
                    )
                elif event == "stopped":
                    self._finish_capture()
                    self._phase_var.set("Stopped")
                    self._detail_var.set("Capture stopped; no ranking was calculated.")
                elif event == "error":
                    self._finish_capture()
                    self._phase_var.set("Error")
                    self._detail_var.set(payload)
                    messagebox.showerror("Channel finder", payload)
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _finish_capture(self) -> None:
        self._start_button.configure(state="normal")
        self._stop_button.configure(state="disabled")
        self._countdown_var.set("0.0 s")

    def _top_n(self) -> int:
        try:
            return max(1, min(128, int(self._top_n_var.get())))
        except (TypeError, ValueError, tk.TclError):
            self._top_n_var.set(8)
            return 8

    def _refresh_results(self) -> None:
        for item in self._result_tree.get_children():
            self._result_tree.delete(item)
        if not self._results:
            return

        top_results = self._results[: self._top_n()]
        selected_by_stream = {"saga1": set(), "saga2": set()}
        for rank, result in enumerate(top_results, start=1):
            selected_by_stream[result.stream_key].add(result.channel_name)
            self._result_tree.insert(
                "",
                "end",
                values=(
                    rank,
                    result.stream_key,
                    result.channel_name,
                    f"{result.snr_db:.2f}",
                    f"{result.rest_rms_uv:.3f}",
                    f"{result.active_rms_uv:.3f}",
                    f"{result.signal_rms_uv:.3f}",
                    f"{result.offset_uv:.3f}",
                    f"{result.sample_rate_hz:.1f}",
                ),
            )

        for key, _stream_name in STREAMS:
            stream_results = [
                result for result in self._results if result.stream_key == key
            ]
            values = np.full(GRID_CHANNELS, np.nan)
            for result in stream_results:
                values[result.channel_index - GRID_FIRST_SAMPLE_INDEX] = result.snr_db
            self._heatmaps[key].configure(text=f"{key.upper()} - SNR (dB)")
            self._heatmaps[key].show_values(
                values, "dB", selected_by_stream[key]
            )

    def _on_close(self) -> None:
        self._stop_event.set()
        self.destroy()


def main() -> None:
    app = ChannelFinderGUI()
    app.mainloop()


if __name__ == "__main__":
    main()

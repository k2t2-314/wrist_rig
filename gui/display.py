"""
gui/display.py — Subject Display Window
"""

import math as _math
import time
import threading
import collections

import tkinter as tk
from tkinter import ttk

from config import WRIST_LIMIT_DEG, GEAR_RATIO


class DisplayWindow(tk.Toplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Wrist Rig — Subject Display")
        self.configure(bg="#0d1117")
        self.resizable(True, True)
        self.geometry("1920x1080")

        self._fx_history      = collections.deque(maxlen=300)
        self._fx_last_channel = None
        self._last_beep_t     = 0.0
        self._last_beep_sec   = -1

        self._build_ui()
        self._poll()

        self._counting_down = False

        self._rah_expanded = False

    # ══════════════════════════════════════════════════════════════════════════
    #  UI
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.columnconfigure(0, weight=2)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        # ── Top row: wrist angle + instruction ───────────────────────────────
        top = tk.Frame(self, bg="#0d1117")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
        top.columnconfigure(1, weight=1)

        angle_frm = tk.Frame(top, bg="#0d1117")
        angle_frm.grid(row=0, column=0, sticky="w", padx=(0, 30))
        tk.Label(angle_frm, text="Wrist Angle",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 14)).pack(anchor="w")
        self._angle_lbl = tk.Label(angle_frm, text="--- °",
                                   bg="#0d1117", fg="#ffffff",
                                   font=("Segoe UI", 48, "bold"))
        self._angle_lbl.pack(anchor="w")

        instr_frm = tk.Frame(top, bg="#0d1117")
        instr_frm.grid(row=0, column=1, sticky="nsew")
        tk.Label(instr_frm, text="Instruction",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 14)).pack(anchor="w")
        self._instr_lbl = tk.Label(instr_frm, text="—",
                                   bg="#0d1117", fg="#00e676",
                                   font=("Segoe UI", 36, "bold"),
                                   wraplength=600, justify="left",
                                   height=2, anchor="nw")
        self._instr_lbl.pack(anchor="w")
        self._timer_lbl = tk.Label(instr_frm, text="",
                                   bg="#0d1117", fg="#ffd700",
                                   font=("Segoe UI", 28, "bold"))
        self._timer_lbl.pack(anchor="w")

        self._force_alert_lbl = tk.Label(instr_frm, text="",
                                  bg="#0d1117", fg="#ff1744",
                                  font=("Segoe UI", 28, "bold"))
        self._force_alert_lbl.pack(side="right", anchor="ne", padx=20)

        # ── LC Force chart ────────────────────────────────────────────────────
        fx_frm = tk.Frame(self, bg="#0d1117")
        fx_frm.grid(row=1, column=0, sticky="nsew", padx=20, pady=8)
        fx_frm.columnconfigure(0, weight=1)
        fx_frm.rowconfigure(1, weight=1)
        tk.Label(fx_frm, text="Force / Torque",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 12)).grid(row=0, column=0, sticky="w")
        self._fx_canvas = tk.Canvas(fx_frm, bg="#0d1117",
                                    highlightthickness=1,
                                    highlightbackground="#1e2a3a")
        self._fx_canvas.grid(row=1, column=0, sticky="nsew")
        self._fx_live_lbl = tk.Label(fx_frm, text="---",
                                     bg="#0d1117", fg="#ff6b6b",
                                     font=("Segoe UI", 13, "bold"))
        self._fx_live_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))

        # ── Motion canvas (ball) ──────────────────────────────────────────────
        ball_frm = tk.Frame(self, bg="#0d1117")
        ball_frm.grid(row=2, column=0, sticky="nsew", padx=20, pady=(8, 20))
        ball_frm.columnconfigure(0, weight=1)
        ball_frm.rowconfigure(1, weight=1)
        tk.Label(ball_frm, text="Motion Guide",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 12)).grid(row=0, column=0, sticky="w")
        self._ball_canvas = tk.Canvas(ball_frm, bg="#1a1a2e",
                                      highlightthickness=1,
                                      highlightbackground="#444")
        self._ball_canvas.grid(row=1, column=0, sticky="nsew")
        self._info_lbl = tk.Label(ball_frm, text="",
                                  bg="#0d1117", fg="#555555",
                                  font=("Segoe UI", 10))
        self._info_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))

        self._draw_ball_idle()
        self._draw_fx_empty()

    # ══════════════════════════════════════════════════════════════════════════
    #  Poll loop
    # ══════════════════════════════════════════════════════════════════════════
    def _poll(self, interval_ms=100):
        if not self.winfo_exists():
            return
        try:
            self._update_angle()
            self._update_fx()
            self._update_ball()
            self._update_instruction()
        except Exception:
            pass
        self.after(interval_ms, self._poll)

    def _update_angle(self):
        p = self.parent
        sample, _ = p.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            wrist = (sample[6] - p.wrist_zero) / GEAR_RATIO
            self._angle_lbl.config(text=f"{wrist:+.1f} °")
        else:
            self._angle_lbl.config(text="--- °")

    def _update_instruction(self):
        if self._counting_down:
            return
        p    = self.parent
        mode = p.motion_runner.active_mode

        if mode is None:
            self._instr_lbl.config(text="—", fg="#888888")
            self._timer_lbl.config(text="")
            return

        name  = mode.name
        phase = mode.sub_phase

        instr  = ""
        timer  = ""
        color  = "#00e676"
        countdown_remaining = None

        # ── AAN ──────────────────────────────────────────────────────────────
        if name == "aan":
            if phase == "active":
                ae      = abs(mode.params.get("active_end", 30.0))
                timeout = float(mode.params.get("active_timeout_s", 5.0))
                remain  = max(0.0, timeout - mode.active_t)
                instr   = f"Move your wrist toward {ae:.0f}°"
                color   = "#00e676"
                if remain <= 10.0:
                    timer = f"{remain:.1f}s"
                    countdown_remaining = remain
            elif phase == "pre_passive":
                pause_dur = float(mode.params.get("pre_passive_pause_s", 0.0))
                remain    = max(0.0, pause_dur - mode.pause_t)
                instr     = "Hold still — servo engaging" if mode.timed_out else "Stay still — servo engaging"
                color     = "#ffd700"
                if pause_dur > 0:
                    timer = f"{remain:.1f}s"
                    countdown_remaining = remain
                color = "#ffd700"
            elif phase == "passive":
                instr = "Keep pushing — servo assisting your wrist"
                color = "#90caf9"
                self._check_force_alert(p)
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_time_s", 3.0))
                remain   = max(0.0, hold_dur - mode.hold_t)
                instr    = "Hold still"
                color    = "#90caf9"
                timer    = f"{remain:.1f}s"
                countdown_remaining = remain
                self._check_force_alert(p)
            elif phase == "return":
                return_dur = float(mode.params.get("return_time_s", 3.0))
                remain     = max(0.0, return_dur - mode.return_t)
                instr      = "Return your wrist to neutral (0°)"
                color      = "#ffeb3b"
                timer      = f"{remain:.1f}s"
                countdown_remaining = remain
            elif phase == "servo_back":
                instr = "Servo returning your wrist"
                color = "#ff9800"
            elif phase == "rest":
                rest_dur = float(mode.params.get("rest_time_s", 3.0))
                remain   = max(0.0, rest_dur - mode.rest_t)
                instr    = "Rest"
                color    = "#888888"
                timer    = f"{remain:.1f}s"
                countdown_remaining = remain

        # ── ROM ──────────────────────────────────────────────────────────────
        elif name == "rom_assessment":
            if phase == "active":
                instr = "Move your wrist as far as comfortable"
                color = "#00e676"
            elif phase == "passive":
                instr = "Relax — servo is moving your wrist"
                color = "#90caf9"
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_time_s", 3.0))
                remain   = max(0.0, hold_dur - mode.hold_t)
                instr    = "Hold still"
                color    = "#90caf9"
                timer    = f"{remain:.1f}s"
                countdown_remaining = remain
            elif phase == "servo_back":
                instr = "Relax — servo returning your wrist"
                color = "#ff9800"
            elif phase == "rest":
                rest_dur = float(mode.params.get("rest_time_s", 3.0))
                remain   = max(0.0, rest_dur - mode.rest_t)
                instr    = "Rest"
                color    = "#888888"
                timer    = f"{remain:.1f}s"
                countdown_remaining = remain
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        elif name == "calibration":
            if phase == "moving":
                target = abs(mode.params.get("start_angle_deg", 0.0))
                instr = f"Relax — servo moving to {target:.0f}°"
                color = "#90caf9"
            elif phase == "hold":
                instr = "Hold still"
                color = "#90caf9"
            elif phase == "return":
                instr = "Return your wrist to neutral (0°)"
                color = "#ffeb3b"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"
        
        elif name == "passive_movement":
            if phase == "go":
                instr = "Relax — servo moving your wrist"
                color = "#90caf9"
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_duration_s", 2.0))
                remain   = max(0.0, hold_dur - mode.hold_t)
                instr    = "Hold still"
                color    = "#90caf9"
                timer    = f"{remain:.1f}s"
            elif phase == "return":
                instr = "Relax — servo returning your wrist"
                color = "#90caf9"
            elif phase == "rest":
                rest_dur = float(mode.params.get("rest_time_s", 2.0))
                remain   = max(0.0, rest_dur - mode.rest_t)
                instr    = "Rest"
                color    = "#888888"
                timer    = f"{remain:.1f}s"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        elif name == "rest":
            if phase in ("moving", "hold"):
                instr = "Relax — stay still"
                color = "#90caf9"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"
        elif name == "active_movement":
            if phase == "go":
                d = int(round(mode.params.get("direction", 1)))
                dir_str = "extension" if d >= 0 else "flexion"
                instr = f"Move your wrist in {dir_str}"
                color = "#00e676"
            elif phase == "return":
                instr = "Return your wrist to neutral (0°)"
                color = "#ffeb3b"
            elif phase == "rest":
                rest_dur = float(mode.params.get("rest_time_s", 2.0))
                remain   = max(0.0, rest_dur - mode.rest_t)
                instr    = "Rest"
                color    = "#888888"
                timer    = f"{remain:.1f}s"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        if name != "aan" or phase not in ("passive", "hold"):
            self._force_alert_lbl.config(text="")

        self._instr_lbl.config(text=instr, fg=color)
        self._timer_lbl.config(text=timer)

        # # beep in last 3 seconds, once per second
        # if countdown_remaining is not None and 0 < countdown_remaining <= 3.0:
        #     sec = int(countdown_remaining)
        #     if sec != self._last_beep_sec:
        #         self._last_beep_sec = sec
        #         self._beep()
        # else:
        #     self._last_beep_sec = -1

    def _beep(self):
        def _do():
            try:
                import winsound
                winsound.Beep(880, 120)
            except Exception:
                try:
                    self.bell()
                except Exception:
                    pass
        threading.Thread(target=_do, daemon=True).start()

    def _update_fx(self):
        p = self.parent
        mode = p.motion_runner.active_mode
        if mode is not None and mode.name == "ramp_and_hold":
            self._draw_fx_empty()
            self._fx_live_lbl.config(text="— see RAH monitor below —")
            return
        channel = p.lc_channel_var.get()
        if channel != self._fx_last_channel:
            self._fx_history.clear()
            self._fx_last_channel = channel
        lc_sample, _ = p.lc_worker.ring.get_latest()
        if lc_sample and len(lc_sample) >= 6:
            val = self._lc_value(lc_sample, channel)
            self._fx_history.append((time.time(), val))
            _, _, unit = self._LC_CHANNELS.get(channel, (0, False, "N"))
            self._fx_live_lbl.config(text=f"{channel} = {val:.3f} {unit}")
            self._draw_fx_chart(channel)
        else:
            if not self._fx_history:
                self._draw_fx_empty()

    def _update_ball(self):
        p    = self.parent
        mode = p.motion_runner.active_mode
        if mode is None or mode.name != "ramp_and_hold":
            if self._rah_expanded:
                self._fx_canvas.master.grid()  # 恢复 LC chart frame
                self._ball_canvas.master.grid(row=2, column=0, sticky="nsew",
                                            rowspan=1, padx=20, pady=(8, 20))
                self._rah_expanded = False
        if mode is None:
            self._draw_ball_idle()
            self._info_lbl.config(text="")
            return
        sample, _ = p.serial_worker.ring.get_latest()
        wrist = (sample[6] - p.wrist_zero) / GEAR_RATIO \
                if (sample and len(sample) >= 7) else 0.0
        name = mode.name
        if name == "rom_assessment":
            d = int(round(mode.params.get("direction", 1)))
            self._draw_rom(wrist, mode.sub_phase,
                           mode.active_rom, mode.passive_rom, d, p)
        elif name == "aan":
            self._draw_aan(mode, wrist, p)
        elif name == "passive_movement":
            self._draw_pm(mode, wrist, p)
        elif name == "active_movement":
            self._draw_am(mode, wrist, p)
        # elif name == "back_and_forth":
        #     self._draw_bf(mode, wrist)
        elif name == "ramp_and_hold":
            if not self._rah_expanded:
                self._fx_canvas.master.grid_remove()
                self._ball_canvas.master.grid(row=1, column=0, sticky="nsew",
                                            rowspan=2, padx=20, pady=(8, 20))
                self._rah_expanded = True
            lc_sample, _ = p.lc_worker.ring.get_latest()
            t_elapsed = time.time() - p._rah_t0 if p._rah_t0 else 0.0
            self._draw_rah(lc_sample, t_elapsed, p)
        elif name == "calibration":
            self._draw_cali(wrist, mode, p)

    # ══════════════════════════════════════════════════════════════════════════
    #  LC helpers
    # ══════════════════════════════════════════════════════════════════════════
    _LC_CHANNELS = {
        "Fx":    (0, True,  "N"),  "Fy":    (1, True,  "N"),  "Fz":    (2, True,  "N"),
        "Tx":    (3, True,  "Nm"), "Ty":    (4, True,  "Nm"), "Tz":    (5, True,  "Nm"),
        "Fnorm": (None, False, "N"), "Tnorm": (None, False, "Nm"),
    }

    def _lc_value(self, sample, channel: str) -> float:
        if channel == "Fnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3)))
        if channel == "Tnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3, 6)))
        return sample[self._LC_CHANNELS[channel][0]]

    # ══════════════════════════════════════════════════════════════════════════
    #  Force chart
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_fx_empty(self):
        c = self._fx_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 160
        c.create_text(W // 2, H // 2, text="Waiting for LC data…",
                      fill="#334", font=("Segoe UI", 10))

    def _draw_fx_chart(self, channel: str):
        c = self._fx_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 160
        ML, MR, MT, MB = 54, 10, 12, 22
        PW, PH = W - ML - MR, H - MT - MB
        p = self.parent
        try:
            baseline = float(p.fx_max_var.get())
            if baseline <= 0:
                baseline = 10.0
        except Exception:
            baseline = 10.0
        _, signed, unit = self._LC_CHANNELS.get(channel, (0, False, "N"))
        y_min = -baseline * 1.4 if signed else 0.0
        y_max =  baseline * 1.4
        y_span = y_max - y_min or 1.0
        def _ypx(v):
            frac = (v - y_min) / y_span
            return int(MT + PH * (1.0 - max(0.0, min(1.0, frac))))
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill="#0d1117", outline="#1e2a3a")
        n_grid = 5 if signed else 4
        for i in range(n_grid + 1):
            val = y_min + (y_max - y_min) * i / n_grid
            y   = _ypx(val)
            c.create_line(ML, y, ML + PW, y, fill="#1a2535", width=1)
            c.create_text(ML - 4, y, text=f"{val:.1f}", anchor="e",
                          fill="#445566", font=("Segoe UI", 7))
        if signed:
            c.create_line(ML, _ypx(0), ML + PW, _ypx(0), fill="#2a3a4a", width=1)
        c.create_text(10, MT + PH // 2, text=f"{channel} {unit}",
                      fill="#445566", font=("Segoe UI", 8), angle=90)
        WINDOW_S = 15.0
        now     = time.time()
        t_start = now - WINDOW_S
        def _xpx(t):
            return int(ML + (t - t_start) / WINDOW_S * PW)
        for off in range(0, int(WINDOW_S) + 1, 5):
            x = _xpx(now - off)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill="#445566")
                c.create_text(x, MT + PH + 12,
                              text=f"-{off}s" if off else "now",
                              fill="#445566", font=("Segoe UI", 7))
        if signed:
            for sign, off in [(1, -7), (-1, 9)]:
                by = _ypx(sign * baseline)
                c.create_line(ML, by, ML + PW, by, fill="#ffd700", width=2, dash=(8, 4))
                c.create_text(ML + PW - 2, by + off,
                              text=f"{'+' if sign>0 else ''}{sign*baseline:.1f}{unit}",
                              anchor="e", fill="#ffd700", font=("Segoe UI", 8, "bold"))
        else:
            by = _ypx(baseline)
            c.create_line(ML, by, ML + PW, by, fill="#ffd700", width=2, dash=(8, 4))
            c.create_text(ML + PW - 2, by - 7, text=f"{baseline:.1f}{unit}",
                          anchor="e", fill="#ffd700", font=("Segoe UI", 8, "bold"))
        pts = [(t, v) for t, v in self._fx_history if t >= t_start - 0.5]
        if len(pts) >= 2:
            coords = []
            for t, v in pts:
                x = _xpx(t); y = _ypx(v)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_val = pts[-1][1]
                mode = p.motion_runner.active_mode
                is_aan = mode is not None and mode.name == "aan"
                over = abs(last_val) > baseline if signed else last_val > baseline
                lc_col = "#00e676" if (over if is_aan else not over) else "#ff4444"
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2]-5, coords[-1]-5,
                              coords[-2]+5, coords[-1]+5,
                              fill=lc_col, outline="white", width=1)
        c.create_line(ML + PW, MT, ML + PW, MT + PH, fill="#334455", width=1)

    # ══════════════════════════════════════════════════════════════════════════
    #  Ball canvas
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_ball_idle(self):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        c.create_text(W // 2, H // 2, text="Waiting for experiment…",
                      fill="#334", font=("Segoe UI", 12))

    def _axis(self, c, W, H, p):
        BOUND = WRIST_LIMIT_DEG; r_flex = -BOUND; span = 2 * BOUND
        cy = H // 2; margin = 40
        h  = 1 if p.handedness_var.get() == "Right" else -1
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        c.create_line(margin, cy, W - margin, cy, fill="#333", width=3)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#444", width=2)
            c.create_text(x, cy+24, text=label, fill="#444", font=("Segoe UI", 10))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-26, text="EXT",  fill="#00e676", font=("Segoe UI", 10, "bold"))
        c.create_text(flex_x, cy-26, text="FLEX", fill="#ff9800", font=("Segoe UI", 10, "bold"))
        return cy, margin, _x, h, BOUND, span

    def _draw_rom(self, wrist, sub_phase, active_rom, passive_rom, direction, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        phys = direction * h
        gx = _x(phys * BOUND)
        c.create_oval(gx-22, cy-22, gx+22, cy+22, fill="#00e676", outline="#00c853", width=3)
        if active_rom is not None:
            ax = _x(max(-BOUND, min(BOUND, phys * active_rom)))
            c.create_line(ax, cy-50, ax, cy+50, fill="#00bcd4", width=2, dash=(6,4))
            c.create_text(ax, cy-62, text=f"A={active_rom:.1f}°", fill="#00bcd4", font=("Segoe UI", 10))
        if passive_rom is not None:
            px = _x(max(-BOUND, min(BOUND, phys * passive_rom)))
            c.create_line(px, cy-50, px, cy+50, fill="#ff9800", width=2, dash=(6,4))
            c.create_text(px, cy-62, text=f"P={passive_rom:.1f}°", fill="#ff9800", font=("Segoe UI", 10))
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-18, cy-18, rx+18, cy+18, fill="#ff1744", outline="#d50000", width=3)
        colors = {"active": "#00e676", "passive": "#90caf9", "return": "#ffeb3b", "done": "#aaa"}
        col = colors.get(sub_phase, "#aaa")
        c.create_text(W//2, 18, text=sub_phase.upper(), fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_aan(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        direction = 1 if int(round(mode.params.get("direction", 1))) >= 0 else -1
        phys = direction * h
        ae = abs(mode.params.get("active_end",  30.0))
        pe = abs(mode.params.get("passive_end", 50.0))
        DOT_R = 18
        ax_raw = _x(max(-BOUND, min(BOUND, phys * ae)))
        ax = ax_raw + (DOT_R if phys >= 0 else -DOT_R)
        c.create_line(ax, cy-60, ax, cy+60, fill="#00bcd4", width=2, dash=(6,4))
        c.create_text(ax, cy-72, text=f"A={ae:.1f}°", fill="#00bcd4", font=("Segoe UI", 10))
        px = _x(max(-BOUND, min(BOUND, phys * pe)))
        c.create_line(px, cy-60, px, cy+60, fill="#ff9800", width=2, dash=(6,4))
        c.create_text(px, cy-72, text=f"P={pe:.1f}°", fill="#ff9800", font=("Segoe UI", 10))
        if mode.sub_phase in ("passive", "hold") and mode.drive_pos:
            sx = _x(max(-BOUND, min(BOUND, mode.drive_pos)))
            c.create_oval(sx-14, cy-14, sx+14, cy+14, fill="#ffd700", outline="#f9a825", width=2)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-18, cy-18, rx+18, cy+18, fill="#ff1744", outline="#d50000", width=3)
        colors = {"active": "#00e676", "passive": "#ffd700", "hold": "#90caf9", "return": "#ffeb3b"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_pm(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        direction = 1 if int(round(mode.params.get("direction", 1))) >= 0 else -1
        phys   = direction * h
        target = abs(mode.params.get("target_deg", 40.0))
        tx = _x(max(-BOUND, min(BOUND, phys * target)))
        c.create_line(tx, cy-60, tx, cy+60, fill="#ffd700", width=2, dash=(6, 4))
        c.create_text(tx, cy-72, text=f"T={target:.1f}°", fill="#ffd700", font=("Segoe UI", 10))
        if mode.sub_phase in ("go", "hold", "return") and mode.drive_pos is not None:
            sx = _x(max(-BOUND, min(BOUND, mode.drive_pos)))
            c.create_oval(sx-14, cy-14, sx+14, cy+14, fill="#ffd700", outline="#f9a825", width=2)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-18, cy-18, rx+18, cy+18, fill="#ff1744", outline="#d50000", width=3)
        colors = {"go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_am(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)

        if mode.sub_phase == "go":
            direction = 1 if int(round(mode.params.get("direction", 1))) >= 0 else -1
            phys_dir  = direction * h
            gx = _x(phys_dir * BOUND)
            c.create_oval(gx-22, cy-22, gx+22, cy+22,
                        fill="#00e676", outline="#00c853", width=3)

        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-18, cy-18, rx+18, cy+18,
                    fill="#ff1744", outline="#d50000", width=3)

        colors = {"go": "#00e676", "return": "#ffeb3b", "rest": "#888888"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                    fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    # def _draw_bf(self, mode, wrist):
    #     c = self._ball_canvas
    #     c.delete("all")
    #     W = c.winfo_width()  or 560
    #     H = c.winfo_height() or 200
    #     left  = float(mode.params.get("left_deg",  -30.0))
    #     right = float(mode.params.get("right_deg",  30.0))
    #     span   = max(abs(right - left) * 1.2, 20.0)
    #     center = (left + right) / 2.0
    #     r_min  = center - span / 2
    #     margin = 40; cy = H // 2
    #     def _x(deg):
    #         return int(margin + (deg - r_min) / span * (W - 2 * margin))
    #     c.create_line(margin, cy, W - margin, cy, fill="#333", width=2)
    #     lx = _x(left)
    #     c.create_line(lx, cy-60, lx, cy+60, fill="#00bcd4", width=2, dash=(6, 4))
    #     c.create_text(lx, cy-72, text=f"L={left:.1f}°", fill="#00bcd4", font=("Segoe UI", 10))
    #     rx = _x(right)
    #     c.create_line(rx, cy-60, rx, cy+60, fill="#ff9800", width=2, dash=(6, 4))
    #     c.create_text(rx, cy-72, text=f"R={right:.1f}°", fill="#ff9800", font=("Segoe UI", 10))
    #     if mode.drive_pos is not None:
    #         sx = _x(max(r_min, min(r_min + span, mode.drive_pos)))
    #         c.create_oval(sx-14, cy-14, sx+14, cy+14, fill="#ffd700", outline="#f9a825", width=2)
    #     wx = _x(max(r_min, min(r_min + span, wrist)))
    #     c.create_oval(wx-18, cy-18, wx+18, cy+18, fill="#ff1744", outline="#d50000", width=3)
    #     total = int(round(mode.params.get("total_reps", 0)))
    #     c.create_text(W//2, 18, text=f"{mode.reps} / {total} reps",
    #                   fill="#ffd700", font=("Segoe UI", 16, "bold"))
    #     self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_rah(self, lc_sample, t_elapsed, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        channel   = p.rah_channel_var.get()
        limit     = max(0.01, float(p.rah_limit_var.get()) if p.rah_limit_var.get() else 10.0)
        direction = p._get_direction()
        h         = 1 if p.handedness_var.get() == "Right" else -1
        phys_dir  = direction * h
        upward    = phys_dir > 0
        ML, MR, MT, MB = 50, 10, 15, 30
        PW, PH = W - ML - MR, H - MT - MB
        WIN_S   = 15.0
        t_left  = t_elapsed - WIN_S / 2
        t_right = t_elapsed + WIN_S / 2
        def _xpx(t_abs):
            return int(ML + (t_abs - t_left) / WIN_S * PW)
        y_zero = MT + PH if upward else MT
        def _ypx(norm_val):
            frac = max(0.0, min(1.4, norm_val))
            return int(y_zero - frac / 1.4 * PH) if upward else int(y_zero + frac / 1.4 * PH)
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill="#0d1117", outline="#1e2a3a")
        c.create_line(ML, y_zero, ML + PW, y_zero, fill="#2a3a4a", width=2)
        unit = "Nm" if channel.startswith("T") else "N"
        for tick in [0.25, 0.5, 0.75, 1.0]:
            yg = _ypx(tick)
            c.create_line(ML, yg, ML + PW, yg, fill="#1a2535", width=1)
            c.create_text(ML - 4, yg, text=f"{tick * limit:.1f}", anchor="e",
                          fill="#445566", font=("Segoe UI", 7))
        c.create_text(12, MT + PH // 2, text=f"{channel}\n{unit}",
                      fill="#445566", font=("Segoe UI", 8), angle=90)
        tick_start = int(t_left // 5) * 5
        t_tick = tick_start
        while t_tick <= t_right + 0.01:
            x = _xpx(t_tick)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill="#445566")
                c.create_text(x, MT + PH + 14, text=f"{int(t_tick)}s",
                              fill="#445566", font=("Segoe UI", 7))
            t_tick += 5
        CYCLE = 45.0
        RAH_PROFILE = [
            (0,0),(5,0),(10,1),(15,1),(20,0),(25,0),(30,1),(35,1),(40,0),(45,0),
        ]
        def _profile_y(t_abs):
            phase = t_abs % CYCLE
            for i in range(len(RAH_PROFILE) - 1):
                t0, y0 = RAH_PROFILE[i]; t1, y1 = RAH_PROFILE[i+1]
                if t0 <= phase <= t1:
                    return y0 if t1 == t0 else y0 + (y1-y0)*(phase-t0)/(t1-t0)
            return RAH_PROFILE[-1][1]
        prof_coords = []
        t_s = t_left
        while t_s <= t_right + 0.01:
            prof_coords.extend([_xpx(t_s), _ypx(_profile_y(t_s))])
            t_s += 0.25
        if len(prof_coords) >= 4:
            c.create_line(*prof_coords, fill="#1565c0", width=2, dash=(8, 4))
        cycle_n = int(t_left // CYCLE)
        while True:
            t_boundary = (cycle_n + 1) * CYCLE
            if t_boundary > t_right + 0.01:
                break
            if t_left <= t_boundary <= t_right:
                xb = _xpx(t_boundary)
                c.create_line(xb, MT, xb, MT + PH, fill="#1e4a8a", width=1, dash=(4, 6))
            cycle_n += 1
        y_lim = _ypx(1.0)
        c.create_line(ML, y_lim, ML + PW, y_lim, fill="#ffd700", width=1, dash=(6, 4))
        c.create_text(ML + PW - 2, y_lim - 7, text=f"{limit:.1f}{unit}", anchor="e",
                      fill="#ffd700", font=("Segoe UI", 8, "bold"))
        x_now = _xpx(t_elapsed)
        c.create_line(x_now, MT, x_now, MT + PH, fill="#aaa", width=2)
        if lc_sample and len(lc_sample) >= 6:
            raw_val = p._lc_rah_value(lc_sample, channel)
            norm_val = abs(raw_val) / limit if channel in ("Fnorm", "Tnorm") \
                       else max(0.0, raw_val * phys_dir / limit)
            p._rah_history.append((t_elapsed, norm_val))
            if len(p._rah_history) > 1000:
                p._rah_history.pop(0)
        if len(p._rah_history) >= 2:
            coords = []
            for t_s, nv in p._rah_history:
                x = _xpx(t_s); y = _ypx(nv)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_nv = p._rah_history[-1][1]
                lc_col  = "#ff4444" if last_nv > 1.0 else "#00e676"
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2]-5, coords[-1]-5, coords[-2]+5, coords[-1]+5,
                              fill=lc_col, outline="white", width=1)
        phase_s  = t_elapsed % CYCLE
        cycle_n2 = int(t_elapsed // CYCLE) + 1
        c.create_text(W // 2, 8,
                      text=f"t={t_elapsed:.1f}s  |  cycle {cycle_n2}  |  phase {phase_s:.1f}s/{int(CYCLE)}s",
                      fill="#aaa", font=("Segoe UI", 9, "bold"))
        self._info_lbl.config(
            text=f"hold={p.motion_runner.active_mode.hold_pos:.1f}°  |  {channel}  limit={limit:.1f}{unit}")
        
    def _draw_cali(self, wrist, mode, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-18, cy-18, rx+18, cy+18,
                    fill="#ff1744", outline="#d50000", width=3)
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                    fill="#aaa", font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def countdown(self, callback):
        self._counting_down = True
        self._instr_lbl.config(text="Get ready", fg="#ffd700")
        self._do_countdown(3, callback)

    def _do_countdown(self, n, callback):
        if n <= 0:
            self._timer_lbl.config(text="GO!", fg="#ff1744")
            self._beep()
            self.after(500, lambda: (
                setattr(self, "_counting_down", False),
                self._instr_lbl.config(text="—", fg="#888888"),
                self._timer_lbl.config(text=""),
                callback()
            ))
            return
        self._timer_lbl.config(text=str(n))
        self._beep()
        self.after(1000, lambda: self._do_countdown(n - 1, callback))


    def _check_force_alert(self, p):
        try:
            channel  = p.lc_channel_var.get()
            baseline = float(p.fx_max_var.get())
            if baseline <= 0:
                baseline = 10.0
            lc_sample, _ = p.lc_worker.ring.get_latest()
            if not lc_sample or len(lc_sample) < 6:
                return
            val = self._lc_value(lc_sample, channel)
            _, signed, _ = self._LC_CHANNELS.get(channel, (0, False, "N"))
            over = abs(val) > baseline if signed else val > baseline
            self._force_alert_lbl.config(
                text="" if over else "⬆ Increase force!")
        except Exception:
            pass
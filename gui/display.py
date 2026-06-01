"""
gui/display.py — Subject Display Window
"""

import math as _math
import time
import threading
import collections

import tkinter as tk
from config import WRIST_LIMIT_DEG, GEAR_RATIO


class DisplayWindow(tk.Toplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Wrist Rig — Subject Display")
        self.configure(bg="#0d1117")
        self.resizable(True, True)
        self.geometry("1920x1080")

        self._fx_history      = collections.deque(maxlen=1500)
        self._fx_last_channel = None
        self._last_beep_sec   = -1
        self._counting_down   = False

        self._build_ui()
        self._poll()

    # ══════════════════════════════════════════════════════════════════════════
    #  UI
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        # ── Top row: wrist angle + instruction ───────────────────────────────
        top = tk.Frame(self, bg="#0d1117")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
        top.columnconfigure(0, minsize=300)
        top.columnconfigure(1, weight=1)

        angle_frm = tk.Frame(top, bg="#0d1117")
        angle_frm.grid(row=0, column=0, sticky="w", padx=(0, 18))
        tk.Label(angle_frm, text="Rep / Time",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 11)).pack(anchor="w")
        self._angle_lbl = tk.Label(angle_frm, text="Rep --\nT  0.0s",
                                   bg="#0d1117", fg="#ffffff",
                                   font=("Consolas", 24, "bold"),
                                   width=8, anchor="w",
                                   justify="left")
        self._angle_lbl.pack(anchor="w")

        instr_frm = tk.Frame(top, bg="#0d1117")
        instr_frm.grid(row=0, column=1, sticky="nsew")
        tk.Label(instr_frm, text="Instruction",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 12)).pack(anchor="w")
        self._instr_lbl = tk.Label(instr_frm, text="—",
                                   bg="#0d1117", fg="#00e676",
                                   font=("Segoe UI", 26, "bold"),
                                   wraplength=2000, justify="left",
                                   anchor="nw")
        self._instr_lbl.pack(anchor="w", fill="x")
        self._timer_lbl = tk.Label(instr_frm, text="",
                                   bg="#0d1117", fg="#ffd700",
                                   font=("Segoe UI", 14, "bold"))
        self._timer_lbl.pack(anchor="w")
        self._force_alert_lbl = tk.Label(instr_frm, text="",
                                         bg="#0d1117", fg="#ff1744",
                                         font=("Segoe UI", 14, "bold"))
        self._force_alert_lbl.pack(side="right", anchor="ne", padx=20)

        # ── Bottom row: force bar (left) + motion guide (right) ──────────────
        bottom_frm = tk.Frame(self, bg="#0d1117")
        bottom_frm.grid(row=1, column=0, sticky="nsew", padx=20, pady=(8, 20))
        bottom_frm.columnconfigure(0, minsize=140, weight=0)  # bar: fixed width
        bottom_frm.columnconfigure(1, weight=1)              # ball: rest
        bottom_frm.rowconfigure(1, weight=1)

        # ── Force bar ─────────────────────────────────────────────────────────
        tk.Label(bottom_frm, text="Force",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w")
        self._bar_canvas = tk.Canvas(bottom_frm, bg="#0d1117", width=140,
                                     highlightthickness=1,
                                     highlightbackground="#1e2a3a")
        self._bar_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 14))

        # ── Motion guide ──────────────────────────────────────────────────────
        tk.Label(bottom_frm, text="Motion Guide",
                 bg="#0d1117", fg="#888888",
                 font=("Segoe UI", 12)).grid(row=0, column=1, sticky="w")
        self._ball_canvas = tk.Canvas(bottom_frm, bg="#1a1a2e",
                                      highlightthickness=1,
                                      highlightbackground="#444")
        self._ball_canvas.grid(row=1, column=1, sticky="nsew")
        self._info_lbl = tk.Label(bottom_frm, text="",
                                  bg="#0d1117", fg="#555555",
                                  font=("Segoe UI", 10))
        self._info_lbl.grid(row=2, column=1, sticky="w", pady=(4, 0))

        self._draw_ball_idle()
        self._draw_bar_empty()

    # ══════════════════════════════════════════════════════════════════════════
    #  Poll loop
    # ══════════════════════════════════════════════════════════════════════════
    def _poll(self, interval_ms=100):
        if not self.winfo_exists():
            return
        try:
            self._update_angle()
            self._update_ball()
            self._update_bar()
            self._update_instruction()
        except Exception:
            pass
        self.after(interval_ms, self._poll)

    def _update_angle(self):
        p = self.parent
        mode = p.motion_runner.active_mode
        elapsed = max(0.0, time.perf_counter() - p.recorder.t0) \
            if p.recorder.recording else 0.0

        rep_text = "Rep --"
        if mode is not None:
            try:
                total = int(round(mode.params.get("total_reps", 1)))
                rep = int(getattr(mode, "rep", 0)) + 1
                if getattr(mode, "done", False):
                    rep = min(rep, total)
                rep_text = f"Rep {rep:02d}/{total:02d}"
            except Exception:
                rep_text = "Rep --"

        self._angle_lbl.config(text=f"{rep_text}\nT {elapsed:5.1f}s")

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
        instr = ""
        timer = ""
        color = "#00e676"
        countdown_remaining = None

        # ── AAN ──────────────────────────────────────────────────────────────
        if name == "aan":
            if phase == "active":
                ae      = abs(mode.params.get("active_end", 30.0))
                timeout = float(mode.params.get("active_timeout_s", 5.0))
                remain  = max(0.0, timeout - mode.active_t)
                instr   = "Move your wrist toward the target"
                color   = "#00e676"
                if remain <= 10.0:
                    timer = f"{remain:.1f}s"
                    countdown_remaining = remain
            elif phase == "pre_passive":
                pause_dur = float(mode.params.get("pre_passive_pause_s", 0.0))
                remain    = max(0.0, pause_dur - mode.pause_t)
                instr     = "Hold still" if mode.timed_out else "Stay still — servo engaging"
                color     = "#ffd700"
                if pause_dur > 0:
                    timer = f"{remain:.1f}s"
                    countdown_remaining = remain
            elif phase == "passive":
                instr = "Keep pushing"
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
                instr      = "Return your wrist to neutral"
                color      = "#ffeb3b"
                timer      = f"{remain:.1f}s"
                countdown_remaining = remain
            elif phase == "servo_back":
                instr = "Relax"
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
                instr = "Relax"
                color = "#90caf9"
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_time_s", 3.0))
                remain   = max(0.0, hold_dur - mode.hold_t)
                instr    = "Hold still"
                color    = "#90caf9"
                timer    = f"{remain:.1f}s"
                countdown_remaining = remain
            elif phase == "servo_back":
                instr = "Relax"
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

        # ── Calibration ───────────────────────────────────────────────────────
        elif name == "calibration":
            if phase == "moving":
                target = abs(mode.params.get("start_angle_deg", 0.0))
                instr  = "Relax"
                color  = "#90caf9"
            elif phase == "hold":
                instr = "Hold still"
                color = "#90caf9"
            elif phase == "return":
                instr = "Return your wrist to neutral"
                color = "#ffeb3b"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        # ── Passive Movement ──────────────────────────────────────────────────
        elif name == "passive_movement":
            if phase == "go":
                instr = "Relax"
                color = "#90caf9"
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_duration_s", 2.0))
                remain   = max(0.0, hold_dur - mode.hold_t)
                instr    = "Hold still"
                color    = "#90caf9"
                timer    = f"{remain:.1f}s"
            elif phase == "return":
                instr = "Relax"
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

        # ── Active Movement ───────────────────────────────────────────────────
        elif name == "active_movement":
            if phase == "go":
                d       = int(round(mode.params.get("direction", 1)))
                dir_str = "extension" if d >= 0 else "flexion"
                instr   = f"Move your wrist in {dir_str}"
                color   = "#00e676"
            elif phase == "return":
                instr = "Return your wrist to neutral"
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

        elif name == "follow":
            target = abs(float(mode.params.get("target_deg", 30.0)))
            if phase == "go":
                instr = "Follow the yellow ball"
                color = "#00e676"
            elif phase == "hold":
                hold_dur = float(mode.params.get("hold_time_s", 2.0))
                remain = max(0.0, hold_dur - mode.hold_t)
                instr = "Hold at the end"
                color = "#90caf9"
                timer = f"{remain:.1f}s"
            elif phase == "return":
                instr = "Follow the yellow ball back to neutral"
                color = "#ffeb3b"
            elif phase == "rest":
                rest_dur = float(mode.params.get("rest_time_s", 2.0))
                remain = max(0.0, rest_dur - mode.rest_t)
                instr = "Rest"
                color = "#888888"
                timer = f"{remain:.1f}s"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        # ── Rest ──────────────────────────────────────────────────────────────
        elif name == "rest":
            if phase in ("moving", "hold"):
                instr = "Relax"
                color = "#90caf9"
            elif phase == "done":
                instr = "Complete"
                color = "#888888"

        if name != "aan" or phase not in ("passive", "hold"):
            self._force_alert_lbl.config(text="")

        self._instr_lbl.config(text=instr, fg=color)
        self._timer_lbl.config(text=timer)

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

    def _update_ball(self):
        p    = self.parent
        mode = p.motion_runner.active_mode
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
        elif name == "follow":
            self._draw_follow(mode, wrist, p)
        elif name == "ramp_and_hold":
            lc_sample, _ = p.lc_worker.ring.get_latest()
            t_elapsed = time.time() - p._rah_t0 if p._rah_t0 else 0.0
            self._draw_rah(lc_sample, t_elapsed, p)
        elif name == "calibration":
            self._draw_cali(wrist, mode, p)
        else:
            self._draw_ball_idle()
            self._info_lbl.config(text=mode.name)

    # ══════════════════════════════════════════════════════════════════════════
    #  LC helpers (still needed for force alert)
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
    #  Force bar
    # ══════════════════════════════════════════════════════════════════════════
    def _update_bar(self):
        p    = self.parent
        mode = p.motion_runner.active_mode
        lc_sample, _ = p.lc_worker.ring.get_latest()
        if not lc_sample or len(lc_sample) < 6:
            self._draw_bar_empty()
            return
        try:
            channel  = p.lc_channel_var.get()
            baseline = float(p.fx_max_var.get())
            if baseline <= 0:
                baseline = 10.0
            val      = self._lc_value(lc_sample, channel)
            _, signed, unit = self._LC_CHANNELS.get(channel, (0, False, "N"))
            is_aan   = mode is not None and mode.name == "aan"
            over     = abs(val) > baseline if signed else val > baseline
            # AAN: green=over(enough force), red=under
            # others: green=under(low force), red=over
            bar_col  = "#00e676" if (over if is_aan else not over) else "#ff4444"
            norm_val = min(1.4, abs(val) / baseline if baseline > 0 else 0.0)
            self._draw_bar(norm_val, bar_col, baseline, val, unit)
        except Exception:
            self._draw_bar_empty()

    def _draw_bar_empty(self):
        c = self._bar_canvas
        c.delete("all")
        W = c.winfo_width()  or 140
        H = c.winfo_height() or 400
        c.create_text(W // 2, H // 2, text="—",
                      fill="#334", font=("Segoe UI", 10))

    def _draw_bar(self, norm_val: float, color: str, baseline: float, val: float, unit: str):
        c = self._bar_canvas
        c.delete("all")
        W  = c.winfo_width()  or 140
        H  = c.winfo_height() or 400
        ML, MR, MT, MB = 8, 8, 20, 40
        BW = W - ML - MR   # bar width
        BH = H - MT - MB   # bar total height

        # background
        c.create_rectangle(ML, MT, ML + BW, MT + BH,
                           fill="#1a1a2e", outline="#333")

        # baseline marker
        by = MT + BH * (1.0 - min(1.0, 1.0 / 1.4))  # baseline at 1.0/1.4 of full
        c.create_line(ML, by, ML + BW, by, fill="#ffd700", width=2, dash=(4, 3))

        # filled bar
        fill_h = min(BH, norm_val / 1.4 * BH)
        top_y  = MT + BH - fill_h
        if fill_h > 0:
            c.create_rectangle(ML, top_y, ML + BW, MT + BH,
                               fill=color, outline="")

        # value label
        c.create_text(W // 2, H - MB // 2,
                      text=f"{val:.1f}", fill="#aaa",
                      font=("Segoe UI", 8, "bold"))
        # baseline label
        c.create_text(W // 2, MT - 8,
                      text=f"{baseline:.0f}{unit}", fill="#ffd700",
                      font=("Segoe UI", 7))

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
        c.create_line(margin, cy, W - margin, cy, fill="#333", width=5)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-16, x, cy+16, fill="#444", width=3)
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
        guide_r = 28
        wrist_r = 24
        phys = direction * h
        gx = _x(phys * BOUND)
        c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r, fill="#00e676", outline="#00c853", width=4)
        if active_rom is not None:
            ax = _x(max(-BOUND, min(BOUND, phys * active_rom)))
            c.create_line(ax, cy-64, ax, cy+64, fill="#00bcd4", width=3, dash=(8, 5))
            c.create_text(ax, cy-62, text=f"A={active_rom:.1f}°", fill="#00bcd4", font=("Segoe UI", 10))
        if passive_rom is not None:
            px = _x(max(-BOUND, min(BOUND, phys * passive_rom)))
            c.create_line(px, cy-64, px, cy+64, fill="#ff9800", width=3, dash=(8, 5))
            c.create_text(px, cy-62, text=f"P={passive_rom:.1f}°", fill="#ff9800", font=("Segoe UI", 10))
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r, fill="#ff1744", outline="#d50000", width=4)
        colors = {"active": "#00e676", "passive": "#90caf9", "servo_back": "#ffeb3b", "done": "#aaa"}
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
        ae   = abs(mode.params.get("active_end", 30.0))
        pe   = abs(mode.params.get("passive_end", 50.0))
        show_guides = bool(getattr(p, "aan_display_guides_var", None) and p.aan_display_guides_var.get())
        guide_r = 28
        wrist_r = 24
        def _clamp_x(x):
            return max(margin, min(W - margin, x))
        active_center_x = _x(max(-BOUND, min(BOUND, phys * ae)))
        # AAN starts when the wrist center reaches active_end. Draw the start
        # line one ball-radius beyond that point, so the ball just touches it.
        active_touch_x = _clamp_x(active_center_x + (wrist_r if phys >= 0 else -wrist_r))

        if show_guides:
            c.create_line(active_touch_x, cy-82, active_touch_x, cy+82, fill="#00bcd4", width=7, dash=(10, 5))
            c.create_text(active_touch_x, cy-72, text="START", fill="#00bcd4", font=("Segoe UI", 10, "bold"))
            px = _x(max(-BOUND, min(BOUND, phys * pe)))
            c.create_line(px, cy-82, px, cy+82, fill="#ff9800", width=7, dash=(10, 5))
            c.create_text(px, cy-72, text="SERVO", fill="#ff9800", font=("Segoe UI", 10))

        # green ball guidance per phase
        if mode.sub_phase in ("active", "pre_passive"):
            # guide toward active end direction (boundary)
            gx = _x(phys * BOUND)
            c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r,
                          fill="#00e676", outline="#00c853", width=4)
        elif mode.sub_phase in ("passive", "hold"):
            # guide at passive end
            gx = _x(max(-BOUND, min(BOUND, phys * pe)))
            c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r,
                          fill="#00e676", outline="#00c853", width=4)
        elif mode.sub_phase in ("return", "servo_back", "rest"):
            # guide back to origin
            ox = _x(0)
            c.create_oval(ox-guide_r, cy-guide_r, ox+guide_r, cy+guide_r,
                          fill="#00e676", outline="#00c853", width=4)

        # red ball = actual wrist
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r, fill="#ff1744", outline="#d50000", width=4)

        colors = {"active": "#00e676", "pre_passive": "#ffd700", "passive": "#ffd700",
                  "hold": "#90caf9", "return": "#ffeb3b", "servo_back": "#ff9800", "rest": "#888888"}
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
        guide_r = 20
        wrist_r = 24
        tx = _x(max(-BOUND, min(BOUND, phys * target)))
        c.create_line(tx, cy-76, tx, cy+76, fill="#ffd700", width=3, dash=(8, 5))
        c.create_text(tx, cy-72, text=f"T={target:.1f}°", fill="#ffd700", font=("Segoe UI", 10))
        if mode.sub_phase in ("go", "hold", "return") and mode.drive_pos is not None:
            sx = _x(max(-BOUND, min(BOUND, mode.drive_pos)))
            c.create_oval(sx-guide_r, cy-guide_r, sx+guide_r, cy+guide_r, fill="#ffd700", outline="#f9a825", width=3)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r, fill="#ff1744", outline="#d50000", width=4)
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
            guide_r = 28
            gx = _x(phys_dir * BOUND)
            c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r,
                          fill="#00e676", outline="#00c853", width=4)
        elif mode.sub_phase == "return":
            guide_r = 28
            ox = _x(0)
            c.create_oval(ox-guide_r, cy-guide_r, ox+guide_r, cy+guide_r,
                          fill="#00e676", outline="#00c853", width=4)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-24, cy-24, rx+24, cy+24,
                      fill="#ff1744", outline="#d50000", width=4)
        colors = {"go": "#00e676", "return": "#ffeb3b", "rest": "#888888"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                      fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_follow(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        direction = 1 if int(round(mode.params.get("direction", 1))) >= 0 else -1
        phys = direction * h
        target = abs(mode.params.get("target_deg", 30.0))
        guide_r = 24
        wrist_r = 24
        tx = _x(max(-BOUND, min(BOUND, phys * target)))
        c.create_line(tx, cy-76, tx, cy+76, fill="#ffd700", width=3, dash=(8, 5))
        c.create_text(tx, cy-72, text=f"T={target:.1f}°", fill="#ffd700", font=("Segoe UI", 10))
        gx = _x(max(-BOUND, min(BOUND, mode.green_pos)))
        c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r,
                      fill="#ffd700", outline="#f9a825", width=4)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r,
                      fill="#ff1744", outline="#d50000", width=4)
        colors = {"go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b", "rest": "#888888", "done": "#888888"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                      fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_cali(self, wrist, mode, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        cy, margin, _x, h, BOUND, _ = self._axis(c, W, H, p)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-24, cy-24, rx+24, cy+24,
                      fill="#ff1744", outline="#d50000", width=4)
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                      fill="#aaa", font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_rah(self, lc_sample, t_elapsed, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        channel   = p.rah_channel_var.get()
        limit     = max(0.01, float(p.rah_limit_var.get()) if p.rah_limit_var.get() else 10.0)
        direction = p._get_direction()
        hand_sign = 1 if p.handedness_var.get() == "Right" else -1
        # Fx follows the anatomical direction: right extension = +Fx, left
        # extension = -Fx. The plot itself always grows upward from zero.
        desired_sign = hand_sign * (1 if direction >= 0 else -1) if channel in ("Fx", "Tz") \
                       else (1 if direction >= 0 else -1)
        ML, MR, MT, MB = 50, 10, 15, 30
        PW, PH = W - ML - MR, H - MT - MB
        WIN_S   = 15.0
        now_frac = 1.0 / 3.0
        t_left  = t_elapsed - WIN_S * now_frac
        t_right = t_left + WIN_S
        def _xpx(t_abs):
            return int(ML + (t_abs - t_left) / WIN_S * PW)
        y_zero = MT + PH
        def _ypx(norm_val):
            frac = max(0.0, min(1.4, norm_val))
            return int(y_zero - frac / 1.4 * PH)
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
        RAH_PROFILE = [(0,0),(5,0),(10,1),(15,1),(20,0),(25,0),(30,1),(35,1),(40,0),(45,0)]
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
            c.create_line(*prof_coords, fill="#40c4ff", width=5, dash=(8, 4))
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
            raw_val  = p._lc_rah_value(lc_sample, channel)
            norm_val = abs(raw_val) / limit if channel in ("Fnorm", "Tnorm") \
                       else max(0.0, raw_val * desired_sign / limit)
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
        mode = p.motion_runner.active_mode
        hold_pos = mode.hold_pos if mode and hasattr(mode, "hold_pos") else 0.0
        self._info_lbl.config(
            text=f"hold={hold_pos:.1f}°  |  {channel}  limit={limit:.1f}{unit}")

    # ══════════════════════════════════════════════════════════════════════════
    #  Countdown
    # ══════════════════════════════════════════════════════════════════════════
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

    # ══════════════════════════════════════════════════════════════════════════
    #  Force alert
    # ══════════════════════════════════════════════════════════════════════════
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
                text="" if over else "Increase force!")
        except Exception:
            pass

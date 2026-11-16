"""
gui/display.py — Subject Display Window
"""

import math as _math
import time
import collections

import tkinter as tk
from PIL import ImageTk
from gui import sprites, themes
from gui.rah_game import RAHCoinGame
from leaderboard import render_leaderboard
from config import WRIST_LIMIT_DEG, GEAR_RATIO, WRIST_ENCODER_SIGN
from config import DISPLAY_SMOOTHING_MS


class DisplayWindow(tk.Toplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title('Wrist Rig — Subject Display')
        self.configure(bg='#0d1117')
        self.resizable(True, True)
        self.geometry('1920x1320')
        self.minsize(1280, 900)
        self._lc_monitor_history = collections.deque(maxlen=1500)
        self._last_monitor_context = None
        self._lc_monitor_scale_max = 20.0
        self._lc_monitor_trial_key = None
        self._counting_down = False
        # Leaderboard overlay (shown after a completed RAH run until the next start).
        self._leaderboard = None
        self._leaderboard_photo = None
        self._leaderboard_size = None
        self._leaderboard_drawn = False
        self._leaderboard_resize_after_id = None
        self._build_ui()
        self._rah_game = RAHCoinGame(self._ball_canvas)
        self._ball_canvas.bind('<Configure>', self._on_ball_canvas_configure)
        self.bind('<Escape>', lambda _event: self.hide_leaderboard())
        # Closing this window only hides it (the GUI's "Show" button brings it back): destroying
        # it would break every later countdown and leaderboard call from the operator GUI.
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self._poll()

    # ==========================================================================
    #  UI
    # ==========================================================================
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        # -- Top row: wrist angle + instruction -------------------------------
        top = tk.Frame(self, bg="#0d1117")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
        self._top_frm = top
        self._top_row_visible = True
        self._countdown_text = ''
        top.columnconfigure(0, minsize=520)
        top.columnconfigure(1, weight=1)

        angle_frm = tk.Frame(top, bg="#0d1117")
        angle_frm.grid(row=0, column=0, sticky="nw", padx=(0, 18))
        self._angle_heading_lbl = tk.Label(angle_frm, text="Rep / Time",
                                            bg="#0d1117", fg="#888888",
                                            font=("Segoe UI", 12))
        self._angle_heading_lbl.pack(anchor="w")
        self._angle_lbl = tk.Label(angle_frm, text="Rep --/--    T  0.0s",
                                   bg="#0d1117", fg="#ffffff",
                                   font=("Segoe UI", 26, "bold"),
                                   width=19, anchor="w",
                                   justify="left")
        self._angle_lbl.pack(anchor="w", pady=(4, 0))

        instr_frm = tk.Frame(top, bg="#0d1117")
        instr_frm.grid(row=0, column=1, sticky="nw")
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

        # -- Bottom row: force bar (left) + motion guide (right) --------------
        bottom_frm = tk.Frame(self, bg="#0d1117")
        bottom_frm.grid(row=1, column=0, sticky="nsew", padx=20, pady=(8, 20))
        self._bottom_frm = bottom_frm
        self._force_bar_visible = True
        bottom_frm.columnconfigure(0, minsize=140, weight=0)  # bar: fixed width
        bottom_frm.columnconfigure(1, weight=1)              # ball: rest
        bottom_frm.rowconfigure(1, weight=1)

        # -- Force bar ---------------------------------------------------------
        self._force_label = tk.Label(bottom_frm, text="Force",
                                     bg="#0d1117", fg="#888888",
                                     font=("Segoe UI", 10))
        self._force_label.grid(row=0, column=0, sticky="w")
        self._bar_canvas = tk.Canvas(bottom_frm, bg="#0d1117", width=140,
                                     highlightthickness=1,
                                     highlightbackground="#1e2a3a")
        self._bar_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 14))

        # -- Experiment monitor / motion guide --------------------------------
        self._monitor_title_var = tk.StringVar(value="Motion Guide")
        self._monitor_title_lbl = tk.Label(bottom_frm, textvariable=self._monitor_title_var,
                                           bg="#0d1117", fg="#888888",
                                           font=("Segoe UI", 12))
        self._monitor_title_lbl.grid(row=0, column=1, sticky="w")
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

    # ==========================================================================
    #  Poll loop
    # ==========================================================================
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
        if not self._top_row_visible:
            return
        p = self.parent
        mode = p.motion_runner.active_mode
        elapsed = max(0.0, time.perf_counter() - p.recorder.t0) \
            if p.recorder.recording else 0.0

        if mode is not None and mode.name == "ramp_and_hold":
            elapsed = max(0.0, time.time() - p._rah_t0) if p._rah_t0 else 0.0
        if mode is not None and mode.name == "rah_practice":
            elapsed = max(0.0, time.time() - p._practice_t0) if getattr(p, "_practice_t0", None) else 0.0

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

        if mode is not None and mode.name == "rah_practice":
            rep_text = "Practice"
        is_mvc = self._display_context(mode) == "mvc_lc"
        self._angle_heading_lbl.config(text="Rep" if is_mvc else "Rep / Time")
        self._angle_lbl.config(text=rep_text if is_mvc else f"{rep_text}    T {elapsed:5.1f}s")

    def _update_instruction(self):
        if not self._top_row_visible or self._counting_down:
            return
        p = self.parent
        if getattr(p, '_mvc_running', False):
            self._instr_lbl.config(text='Use your max force', fg='#00e676')
            self._timer_lbl.config(text='')
            self._force_alert_lbl.config(text='')
            return
        mode = p.motion_runner.active_mode
        if self._leaderboard is not None and (mode is None or getattr(mode, 'done', False)):
            finished = self._leaderboard.get('last_run') is not None
            self._instr_lbl.config(text='Run complete!' if finished else 'Leaderboard', fg='#ffd700')
            self._timer_lbl.config(text='')
            self._force_alert_lbl.config(text='')
            return
        if mode is None:
            self._instr_lbl.config(text='—', fg='#888888')
            self._timer_lbl.config(text='')
            return
        name = mode.name
        phase = mode.sub_phase
        instr = ''
        timer = ''
        color = '#00e676'
        countdown_remaining = None
        if name == 'passive_movement':
            if phase == 'positioning':
                instr = 'Relax - moving to the start point'
                color = '#40c4ff'
            elif phase == 'go':
                instr = 'Relax'
                color = '#90caf9'
            elif phase == 'hold':
                hold_dur = float(mode.params.get('hold_duration_s', 2.0))
                remain = max(0.0, hold_dur - mode.hold_t)
                instr = 'Hold still'
                color = '#90caf9'
                timer = f'{remain:.1f}s'
            elif phase == 'return':
                instr = 'Relax'
                color = '#90caf9'
            elif phase == 'rest':
                rest_dur = float(mode.params.get('rest_time_s', 2.0))
                remain = max(0.0, rest_dur - mode.rest_t)
                instr = 'Rest'
                color = '#888888'
                timer = f'{remain:.1f}s'
            elif phase == 'done':
                instr = 'Complete'
                color = '#888888'
        elif name == 'follow':
            if phase == 'positioning':
                instr = 'Relax - moving to the start point'
                color = '#40c4ff'
            elif phase == 'go':
                instr = 'Track the yellow ball'
                color = '#00e676'
            elif phase == 'hold':
                hold_dur = float(mode.params.get('hold_time_s', 2.0))
                remain = max(0.0, hold_dur - mode.hold_t)
                instr = 'Hold at the end'
                color = '#90caf9'
                timer = f'{remain:.1f}s'
            elif phase == 'return':
                instr = 'Track the yellow ball back to the start point'
                color = '#ffeb3b'
            elif phase == 'rest':
                rest_dur = float(mode.params.get('rest_time_s', 2.0))
                remain = max(0.0, rest_dur - mode.rest_t)
                instr = 'Rest'
                color = '#888888'
                timer = f'{remain:.1f}s'
            elif phase == 'done':
                instr = 'Complete'
                color = '#888888'
        elif name == 'ramp_and_hold':
            instr = themes.active()['instruction']
            color = '#40c4ff'
        elif name == 'rah_practice':
            instr = 'Practice: reach the top'
            color = '#40c4ff'
        elif name == 'rest':
            instr = 'Rest'
            color = '#888888'
        self._force_alert_lbl.config(text='')
        self._instr_lbl.config(text=instr, fg=color)
        self._timer_lbl.config(text=timer)


    def _update_ball(self):
        p = self.parent
        mode = p.motion_runner.active_mode
        context = self._display_context(mode)
        self._set_monitor_context(context)
        # The force bar and the rep/instruction row distract from the game: both are hidden
        # for RAH (game, practice and idle); the countdown is drawn on the game area instead.
        # The EMG-only maximal effort shows only its cues: no force bar, no rep row (no force feedback).
        self._set_force_bar_visible(context not in ('rah_lc', 'mvc_emg'))
        self._set_top_row_visible(context not in ('rah_lc', 'mvc_emg'))
        self._apply_rah_theme(context == 'rah_lc')
        if context == 'mvc_emg':
            self._draw_mvc_emg(p)
            return
        if self._counting_down and context == 'rah_lc':
            self._draw_countdown()
            return
        if self._leaderboard is not None:
            self._monitor_title_var.set('Leaderboard')
            self._draw_leaderboard()
            return
        if context == 'mvc_lc':
            (lc_sample, _) = p.lc_worker.ring.get_latest()
            self._draw_lc_monitor(lc_sample, p, role='mvc_lc')
            return
        if context == 'rest':
            self._draw_rest_blank()
            return
        self._ball_canvas.configure(bg='#1a1a2e')
        if context in ('rah_lc',):
            if mode is not None and getattr(mode, 'name', '') == 'rah_practice':
                self._monitor_title_var.set('Practice (not recorded)')
                self._draw_practice(p)
                return
            if mode is not None and (mode.name == 'ramp_and_hold' or hasattr(mode, 'profile_fraction')):
                (lc_sample, _) = p.lc_worker.ring.get_latest()
                t_elapsed = time.time() - p._rah_t0 if p._rah_t0 else 0.0
                self._draw_rah(lc_sample, t_elapsed, p)
            else:
                self._draw_monitor_idle('')
            return
        if mode is None:
            self._draw_ball_idle()
            self._info_lbl.config(text='')
            return
        (sample, _) = p.serial_worker.ring.get_latest()
        wrist = WRIST_ENCODER_SIGN * (sample[6] - p.wrist_zero) / GEAR_RATIO if sample and len(sample) >= 7 else 0.0
        name = mode.name
        if name == 'passive_movement':
            self._draw_pm(mode, wrist, p)
        elif name == 'follow':
            self._draw_follow(mode, wrist, p)
        elif name == 'direction_reversal':
            if hasattr(mode, 'dynamics_enabled') and (not mode.dynamics_enabled()):
                (lc_sample, _) = p.lc_worker.ring.get_latest()
                t_elapsed = time.time() - p._dr_t0 if p._dr_t0 else 0.0
                self._draw_rah(lc_sample, t_elapsed, p)
            else:
                self._draw_direction_reversal(mode, wrist, p)
        elif name == 'ramp_and_hold':
            (lc_sample, _) = p.lc_worker.ring.get_latest()
            t_elapsed = time.time() - p._rah_t0 if p._rah_t0 else 0.0
            self._draw_rah(lc_sample, t_elapsed, p)
        else:
            self._draw_ball_idle()
            self._info_lbl.config(text=mode.name)

    def _display_context(self, mode):
        p = self.parent
        tab_text = ''
        try:
            tab_text = p.notebook.tab(p.notebook.select(), 'text').strip()
        except Exception:
            pass
        mode_name = mode.name if mode is not None else ''
        # A running task decides the context; the selected tab only matters while nothing runs,
        # so switching tabs in the operator GUI during a run never swaps the participant's view.
        if mode_name in ('ramp_and_hold', 'rah_practice'):
            return 'rah_lc'
        if mode_name in ('passive_movement', 'follow', 'direction_reversal'):
            return 'motion'
        if getattr(p, '_mvc_emg_running', False) or getattr(p, '_mvc_emg_countdown_active', False) or tab_text == 'MVC_EMG':
            return 'mvc_emg'
        if getattr(p, '_mvc_running', False) or tab_text == 'MVC_LC':
            return 'mvc_lc'
        if tab_text in ('RAH_LC',):
            return 'rah_lc'
        if getattr(p, '_rest_running', False) or tab_text == 'Rest':
            return 'rest'
        return 'motion'

    def _set_monitor_context(self, context: str):
        if context != self._last_monitor_context:
            self._lc_monitor_history.clear()
            self._lc_monitor_scale_max = 20.0
            self._lc_monitor_trial_key = None
            self._last_monitor_context = context
        titles = {'rest': '', 'mvc_lc': 'LC Monitor', 'rah_lc': '', 'mvc_emg': ''}
        self._monitor_title_var.set(titles.get(context, 'Motion Guide'))

    # ==========================================================================
    #  LC helpers (still needed for force alert)
    # ==========================================================================
    # (index, signed, unit); Fxy = sqrt(Fx^2 + Fy^2), unsigned like Fnorm and Tnorm.
    _LC_CHANNELS = {
        "Fx":    (0, True,  "N"),  "Fy":    (1, True,  "N"),  "Fz":    (2, True,  "N"),
        "Tx":    (3, True,  "Nmm"), "Ty":    (4, True,  "Nmm"), "Tz":    (5, True,  "Nmm"),
        "Fnorm": (None, False, "N"), "Tnorm": (None, False, "Nmm"), "Fxy": (None, False, "N"),
    }

    def _lc_value(self, sample, channel: str) -> float:
        if channel == "Fnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3)))
        if channel == "Fxy":
            return _math.hypot(sample[0], sample[1])
        if channel == "Tnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3, 6)))
        return sample[self._LC_CHANNELS[channel][0]]

    # ==========================================================================
    #  Force bar
    # ==========================================================================
    def on_theme_changed(self):
        """The game theme was switched in the operator GUI: drop cached chrome and images."""
        self._theme_bg = None
        self._leaderboard_photo = None
        self._leaderboard_drawn = False
        try:
            self._rah_game.prewarm(self._ball_canvas.winfo_width() or 560, self._ball_canvas.winfo_height() or 200)
        except Exception:
            pass

    def _draw_backdrop(self, c, W, H, ML, MT, PW, PH):
        """Theme backdrop on a canvas that is cleared every frame (countdown, practice): the
        background picture, if the theme has one, and the static star field, if it has stars."""
        backdrop = sprites.background_image(W, H)
        if backdrop is not None:
            c.create_image(0, 0, anchor='nw', image=sprites.photo(backdrop, c))
        for (sx, sy, r, colour) in sprites.star_field(PW, PH):
            x, y = ML + sx, MT + sy
            c.create_oval(x - r, y - r, x + r, y + r, fill=colour, outline='')

    def _apply_rah_theme(self, active: bool):
        """Window chrome in the game theme's palette during RAH, the original dark grey otherwise."""
        colour = sprites.BACKGROUND if active else '#0d1117'
        if getattr(self, '_theme_bg', None) == colour:
            return
        self._theme_bg = colour
        for widget in (self, self._bottom_frm, self._info_lbl, self._monitor_title_lbl):
            widget.configure(bg=colour)
        self._monitor_title_lbl.configure(fg=sprites.hex_colour(sprites.THEME['text_dim']) if active else '#888888')
        self._info_lbl.configure(fg=sprites.hex_colour(sprites.THEME['text_dim']) if active else '#555555')
        self._ball_canvas.configure(highlightbackground=sprites.FRAME if active else '#444')

    def _set_top_row_visible(self, visible: bool):
        if self._top_row_visible == bool(visible):
            return
        self._top_row_visible = bool(visible)
        if visible:
            self._top_frm.grid()
        else:
            self._top_frm.grid_remove()

    def _draw_countdown(self):
        """'Get ready 3 2 1 GO!' on the game area while the top row is hidden."""
        c = self._ball_canvas
        c.configure(bg=sprites.BACKGROUND)
        c.delete('all')
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        text = self._countdown_text
        self._draw_backdrop(c, W, H, *RAHCoinGame.plot_box(W, H))
        c.create_text(W // 2, H // 2 - 90, text='GET READY', fill=sprites.hex_colour(sprites.THEME['countdown']),
                      font=sprites.FONT_COUNTDOWN_LABEL)
        if text:
            colour = sprites.THEME['go'] if text == 'GO!' else sprites.THEME['text']
            c.create_text(W // 2, H // 2 + 30, text=text, fill=sprites.hex_colour(colour), font=sprites.FONT_COUNTDOWN)
        self._info_lbl.config(text='')

    def _draw_mvc_emg(self, p):
        """Cues of the EMG-only maximal-effort trial: REST / MAX EFFORT! / RELAX with the seconds left,
        drawn large on the game area (the top row and the force bar are hidden)."""
        if self._counting_down:
            self._draw_countdown()
            return
        c = self._ball_canvas
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        phase = getattr(p, '_mvc_emg_phase', '')
        end = getattr(p, '_mvc_emg_phase_end_perf', None)
        remaining = max(0.0, end - time.perf_counter()) if end else 0.0
        secs = int(_math.ceil(remaining))
        params = getattr(p, '_mvc_emg_params', {}) or {}
        n = int(params.get('n', 0) or 0)
        i = int(getattr(p, '_mvc_emg_effort_index', 0)) + 1
        running = bool(getattr(p, '_mvc_emg_running', False))
        text_colour = sprites.hex_colour(sprites.THEME['text'])
        if running and phase == 'max':
            bg, big, colour, sub = '#3a0d0d', 'MAX EFFORT!', '#ff5252', f'{secs} s'
        elif running and phase == 'rest':
            bg, big, colour, sub = sprites.BACKGROUND, 'REST', '#9e9e9e', f'max effort in {secs} s'
        elif running and phase == 'recover':
            bg, big, colour, sub = sprites.BACKGROUND, 'RELAX', '#4fc3f7', f'next effort in {secs} s'
        else:
            bg, big, colour, sub = sprites.BACKGROUND, 'MVC (EMG)', '#888888', 'waiting for start'
        c.configure(bg=bg)
        c.delete('all')
        c.create_text(W // 2, H // 2 - 40, text=big, fill=colour, font=sprites.FONT_COUNTDOWN)
        c.create_text(W // 2, H // 2 + 60, text=sub, fill=text_colour, font=sprites.FONT_COUNTDOWN_LABEL)
        if running and n:
            c.create_text(W // 2, H - 30, text=f'Effort {i} of {n}', fill='#bbbbbb', font=sprites.FONT_HUD)
        self._info_lbl.config(text='')

    def _set_force_bar_visible(self, visible: bool):
        if self._force_bar_visible == bool(visible):
            return
        self._force_bar_visible = bool(visible)
        if visible:
            self._force_label.grid()
            self._bar_canvas.grid()
            self._bottom_frm.columnconfigure(0, minsize=140)
        else:
            self._force_label.grid_remove()
            self._bar_canvas.grid_remove()
            self._bottom_frm.columnconfigure(0, minsize=0)

    def _update_bar(self):
        if not self._force_bar_visible:
            return
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
            over     = abs(val) > baseline if signed else val > baseline
            bar_col  = "#00e676" if not over else "#ff4444"
            norm_val = min(1.4, abs(val) / baseline if baseline > 0 else 0.0)
            mode_name = getattr(mode, "name", "") if mode is not None else ""
            hide_mvc_reference = mode_name in ("ramp_and_hold", "rah_practice", "direction_reversal")
            self._draw_bar(
                norm_val,
                bar_col,
                baseline,
                val,
                unit,
                show_baseline=not hide_mvc_reference,
                show_value=(mode_name not in ("ramp_and_hold", "rah_practice")),
            )
        except Exception:
            self._draw_bar_empty()

    def _draw_bar_empty(self):
        c = self._bar_canvas
        c.delete("all")
        W = c.winfo_width()  or 140
        H = c.winfo_height() or 400
        c.create_text(W // 2, H // 2, text="—",
                      fill="#334", font=("Segoe UI", 10))

    def _draw_bar(self, norm_val: float, color: str, baseline: float, val: float,
                  unit: str, show_baseline: bool = True, show_value: bool = True):
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

        if show_baseline:
            by = MT + BH * (1.0 - min(1.0, 1.0 / 1.4))
            c.create_line(ML, by, ML + BW, by, fill="#ffd700", width=2, dash=(4, 3))

        # filled bar
        fill_h = min(BH, norm_val / 1.4 * BH)
        top_y  = MT + BH - fill_h
        if fill_h > 0:
            c.create_rectangle(ML, top_y, ML + BW, MT + BH,
                               fill=color, outline="")

        if show_value:
            c.create_text(W // 2, H - MB // 2,
                          text=f"{val:.1f}", fill="#aaa",
                          font=("Segoe UI", 8, "bold"))
        if show_baseline:
            c.create_text(W // 2, MT - 8,
                          text=f"{baseline:.0f}{unit}", fill="#ffd700",
                          font=("Segoe UI", 7))

    # ==========================================================================
    #  Ball canvas
    # ==========================================================================
    def _draw_ball_idle(self):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        c.create_text(W // 2, H // 2, text="Waiting for experiment…",
                      fill="#334", font=("Segoe UI", 12))

    def _draw_rest_blank(self):
        c = self._ball_canvas
        c.configure(bg="#0d1117")
        c.delete("all")
        self._info_lbl.config(text="")

    def _draw_monitor_idle(self, title: str):
        c = self._ball_canvas
        c.configure(bg="#1a1a2e")
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        c.create_text(W // 2, H // 2, text=title,
                      fill="#334", font=("Segoe UI", 12, "bold"))
        self._info_lbl.config(text="Waiting for experiment")

    def _smooth_history_for_display(self, history):
        mode = getattr(self.parent.motion_runner, 'active_mode', None)
        if (mode is not None and getattr(mode, 'name', '') == 'ramp_and_hold'
                and hasattr(self.parent, '_smooth_history_for_display')):
            return self.parent._smooth_history_for_display(history)
        try:
            if hasattr(self.parent, "_rah_display_lowpass_enabled") and not self.parent._rah_display_lowpass_enabled():
                return history
        except Exception:
            pass
        tau_s = max(0.0, float(DISPLAY_SMOOTHING_MS) / 1000.0)
        if len(history) < 3:
            return history
        smoothed = []
        prev_t = float(history[0][0])
        y = float(history[0][1])
        smoothed.append((history[0][0], y))
        mode = getattr(self.parent.motion_runner, "active_mode", None)
        for t_s, value in history[1:]:
            t_now = float(t_s)
            dt_s = max(0.0, t_now - prev_t)
            try:
                cutoff_hz = max(0.0, float(self.parent._rah_display_lowpass_cutoff_hz(t_now, mode)))
                tau_s = 1.0 / (2.0 * _math.pi * cutoff_hz) if cutoff_hz > 0.0 else 0.0
            except Exception:
                tau_s = max(0.0, float(DISPLAY_SMOOTHING_MS) / 1000.0)
            alpha = 1.0 if dt_s <= 0.0 or tau_s <= 0.0 else 1.0 - _math.exp(-dt_s / tau_s)
            y += alpha * (float(value) - y)
            smoothed.append((t_s, y))
            prev_t = t_now
        return smoothed


    def _draw_lc_monitor(self, lc_sample, p, role: str='mvc_lc'):
        channel = getattr(p, '_mvc_lc_channel', None) or p.mvc_lc_channel_var.get()
        title = 'MVC_LC live load cell'
        running = bool(getattr(p, '_mvc_running', False))
        duration_attr = '_mvc_duration_s'
        (_, signed, unit) = self._LC_CHANNELS.get(channel, (None, False, 'N'))
        trial_key = getattr(p, '_trigger_timing_t0', None) if running else self._lc_monitor_trial_key
        if running and trial_key != self._lc_monitor_trial_key:
            self._lc_monitor_history.clear()
            self._lc_monitor_scale_max = 20.0
            self._lc_monitor_trial_key = trial_key
        value = None
        if lc_sample and len(lc_sample) >= 6:
            value = self._lc_value(lc_sample, channel)
            if running:
                t_rel = max(0.0, time.perf_counter() - trial_key) if trial_key is not None else 0.0
                self._lc_monitor_history.append((t_rel, value))
                self._lc_monitor_scale_max = max(20.0, float(self._lc_monitor_scale_max), abs(float(value)))
        value_text = f'{channel}: ---' if value is None else f'{channel}: {value:.2f} {unit}'
        c = self._ball_canvas
        c.configure(bg='#1a1a2e')
        c.delete('all')
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        (ML, MR, MT, MB) = (60, 18, 26, 36)
        (PW, PH) = (W - ML - MR, H - MT - MB)
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill='#0d1117', outline='#1e2a3a')
        c.create_text(ML, 10, text=f'{title} {channel}', anchor='w', fill='#aaa', font=('Segoe UI', 10, 'bold'))
        c.create_text(ML + PW, 10, text=value_text, anchor='e', fill='#00e676', font=('Segoe UI', 10, 'bold'))
        if not self._lc_monitor_history:
            c.create_text(ML + PW // 2, MT + PH // 2, text=f"Waiting for {'MVC_LC'} data", fill='#334', font=('Segoe UI', 11))
            self._info_lbl.config(text=value_text)
            return
        try:
            duration = max(1.0, float(getattr(p, duration_attr, 0.0) or 0.0))
        except Exception:
            duration = 1.0
        latest_t = max((t for (t, _) in self._lc_monitor_history))
        x_max = max(duration, latest_t, 1.0)
        y_scale = max(20.0, float(self._lc_monitor_scale_max))
        y_min = -y_scale if signed else 0.0
        y_max = y_scale

        def _xpx(t_abs):
            return int(ML + max(0.0, min(x_max, t_abs)) / x_max * PW)

        def _ypx(val):
            val = max(y_min, min(y_max, float(val)))
            return int(MT + (y_max - val) / (y_max - y_min) * PH)
        for frac in (0.25, 0.5, 0.75, 1.0):
            if signed:
                tick = y_min + (y_max - y_min) * frac
            else:
                tick = y_max * frac
            y = _ypx(tick)
            c.create_line(ML, y, ML + PW, y, fill='#1a2535')
            c.create_text(ML - 5, y, text=f'{tick:.1f}', anchor='e', fill='#445566', font=('Segoe UI', 7))
        zero_y = _ypx(0.0)
        c.create_line(ML, zero_y, ML + PW, zero_y, fill='#2a3a4a', width=2)
        c.create_text(14, MT + PH // 2, text=f'{unit}', angle=90, fill='#445566', font=('Segoe UI', 8, 'bold'))
        coords = []
        for (t_abs, val) in self._lc_monitor_history:
            coords.extend([_xpx(t_abs), _ypx(val)])
        if len(coords) >= 4:
            c.create_line(*coords, fill='#00e676', width=3, smooth=True, splinesteps=12)
        elif len(coords) == 2:
            c.create_oval(coords[0] - 4, coords[1] - 4, coords[0] + 4, coords[1] + 4, fill='#00e676', outline='white', width=1)
        if len(coords) >= 2:
            c.create_oval(coords[-2] - 5, coords[-1] - 5, coords[-2] + 5, coords[-1] + 5, fill='#00e676', outline='white', width=1)
        self._info_lbl.config(text=f"{channel} live={value_text.split(': ', 1)[-1]} | Y axis {('±' if signed else '0–')}{y_scale:.1f} {unit}")


    def _movement_axis(
        self,
        c,
        W,
        H,
        p,
        start: float,
        target: float,
        pad_deg: float = 10.0,
        view_min=None,
        view_max=None,
    ):
        if view_min is None or view_max is None:
            view_min = max(-WRIST_LIMIT_DEG, min(start, target) - pad_deg)
            view_max = min(WRIST_LIMIT_DEG, max(start, target) + pad_deg)
            if view_max <= view_min:
                mid = (start + target) / 2.0
                view_min = max(-WRIST_LIMIT_DEG, mid - pad_deg)
                view_max = min(WRIST_LIMIT_DEG, mid + pad_deg)
        else:
            view_min = float(view_min)
            view_max = float(view_max)
        if view_max <= view_min:
            view_min, view_max = -WRIST_LIMIT_DEG, WRIST_LIMIT_DEG

        cy = H // 2
        margin = 40
        span = max(1.0, view_max - view_min)
        h = 1 if p.handedness_var.get() == "Right" else -1

        def _x(deg):
            return margin + (deg - view_min) / span * (W - 2 * margin)

        c.create_line(margin, cy, W - margin, cy, fill="#333", width=5)
        ticks = [(view_min, f"{view_min:+.0f}°")]
        if view_min < 0 < view_max:
            ticks.append((0.0, "0°"))
        ticks.append((view_max, f"{view_max:+.0f}°"))
        for deg, label in ticks:
            x = _x(deg)
            c.create_line(x, cy-16, x, cy+16, fill="#444", width=3)
            c.create_text(x, cy+24, text=label, fill="#444", font=("Segoe UI", 10))

        ext_x = W - margin if h >= 0 else margin
        flex_x = margin if h >= 0 else W - margin
        c.create_text(ext_x, cy-26, text="EXT", fill="#00e676", font=("Segoe UI", 10, "bold"))
        c.create_text(flex_x, cy-26, text="FLEX", fill="#ff9800", font=("Segoe UI", 10, "bold"))

        def _clamp(deg):
            return max(view_min, min(view_max, deg))

        return cy, margin, _x, h, (view_min, view_max), _clamp


    def _draw_direction_reversal(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        home = float(getattr(mode, "hold_pos", mode.params.get("hold_angle_deg", 0.0)) or 0.0)
        try:
            target = float(mode.target_angle_for_current_instruction())
        except Exception:
            target = home
        phase = getattr(mode, "sub_phase", "")
        cue = getattr(mode, "current_instruction", "rest")
        guide = target if phase == "instruction" else home
        try:
            dynamics_angle = max(0.0, float(mode.dynamics_angle()))
        except Exception:
            dynamics_angle = max(
                0.0, float(mode.params.get("dynamics_angle_deg", 10.0) or 0.0))
        flex_limit = max(-WRIST_LIMIT_DEG, home - dynamics_angle)
        ext_limit = min(WRIST_LIMIT_DEG, home + dynamics_angle)
        view_min = max(-WRIST_LIMIT_DEG, min(flex_limit, ext_limit) - 15.0)
        view_max = min(WRIST_LIMIT_DEG, max(flex_limit, ext_limit) + 15.0)
        cy, margin, _x, _h, _view_bounds, _clamp = self._movement_axis(
            c,
            W,
            H,
            p,
            home,
            target,
            view_min=view_min,
            view_max=view_max,
        )
        home_x = _x(_clamp(home))
        c.create_line(home_x, cy - 72, home_x, cy + 72, fill="#40c4ff", width=3, dash=(6, 4))
        c.create_text(home_x, cy + 82, text=f"HOME {home:+.1f} deg", fill="#40c4ff", font=("Segoe UI", 10))
        if cue in ("extension", "flexion"):
            target_x = _x(_clamp(target))
            c.create_line(target_x, cy - 76, target_x, cy + 76, fill="#ffd700", width=3, dash=(8, 5))
            c.create_text(target_x, cy - 72, text=f"{cue.upper()} {target:+.1f} deg", fill="#ffd700", font=("Segoe UI", 10, "bold"))
        guide_x = _x(_clamp(guide))
        c.create_oval(guide_x - 24, cy - 24, guide_x + 24, cy + 24, fill="#ffd700", outline="#f9a825", width=4)
        wrist_x = _x(_clamp(wrist))
        c.create_oval(wrist_x - 24, cy - 24, wrist_x + 24, cy + 24, fill="#ff1744", outline="#d50000", width=4)
        colors = {"rest": "#888888", "final_rest": "#888888", "instruction": "#00e676", "return": "#ffeb3b", "done": "#888888"}
        title = cue.upper() if phase == "instruction" else phase.upper()
        c.create_text(W // 2, 18, text=title, fill=colors.get(phase, "#aaa"), font=("Segoe UI", 14, "bold"))
        try:
            remaining = float(mode.phase_remaining())
            rep = int(getattr(mode, "rep", 0)) + 1
            total = int(mode.total_reps()) if hasattr(mode, "total_reps") else int(mode.params.get("total_reps", 1))
            self._info_lbl.config(text=f"wrist = {wrist:.1f} deg | rep {rep}/{total} | {remaining:.1f}s")
        except Exception:
            self._info_lbl.config(text=f"wrist = {wrist:.1f} deg")
    def _draw_pm(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width()  or 560
        H = c.winfo_height() or 200
        start = float(mode.params.get("start_deg", 0.0))
        target = float(mode.params.get("target_deg", 40.0))
        cy, margin, _x, h, _view_bounds, _clamp = self._movement_axis(c, W, H, p, start, target)
        guide_r = 20
        wrist_r = 24
        sx0 = _x(_clamp(start))
        c.create_line(sx0, cy-66, sx0, cy+66, fill="#40c4ff", width=2, dash=(5, 4))
        c.create_text(sx0, cy+80, text=f"S={start:+.1f} deg", fill="#40c4ff", font=("Segoe UI", 10))
        tx = _x(_clamp(target))
        c.create_line(tx, cy-76, tx, cy+76, fill="#ffd700", width=3, dash=(8, 5))
        c.create_text(tx, cy-72, text=f"T={target:.1f}°", fill="#ffd700", font=("Segoe UI", 10))
        if mode.sub_phase in ("positioning", "go", "hold", "return") and mode.drive_pos is not None:
            sx = _x(_clamp(mode.drive_pos))
            c.create_oval(sx-guide_r, cy-guide_r, sx+guide_r, cy+guide_r, fill="#ffd700", outline="#f9a825", width=3)
        rx = _x(_clamp(wrist))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r, fill="#ff1744", outline="#d50000", width=4)
        colors = {"positioning": "#40c4ff", "go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_follow(self, mode, wrist, p):
        c = self._ball_canvas
        c.delete("all")
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        start = mode.current_start() if hasattr(mode, "current_start") else float(mode.params.get("start_deg", 0.0))
        target = mode.current_target() if hasattr(mode, "current_target") else float(mode.params.get("target_deg", 30.0))
        cy, margin, _x, h, _view_bounds, _clamp = self._movement_axis(
            c,
            W,
            H,
            p,
            start,
            target,
            view_min=mode.params.get("view_min_deg"),
            view_max=mode.params.get("view_max_deg"),
        )
        guide_r = 24
        wrist_r = 24
        direction = 1 if target >= start else -1
        arrow_len = min(160, max(72, (W - 2 * margin) * 0.18))
        arrow_cx = W // 2
        arrow_y = cy - 58
        arrow_x0 = arrow_cx - direction * arrow_len * 0.5
        arrow_x1 = arrow_cx + direction * arrow_len * 0.5
        arrow_fill = "#ffd700" if mode.sub_phase in ("go", "return") else "#777777"
        c.create_line(
            arrow_x0, arrow_y, arrow_x1, arrow_y,
            fill=arrow_fill, width=6, arrow=tk.LAST, arrowshape=(24, 30, 11),
        )
        gx = _x(_clamp(mode.green_pos))
        c.create_oval(gx-guide_r, cy-guide_r, gx+guide_r, cy+guide_r,
                      fill="#ffd700", outline="#f9a825", width=4)
        rx = _x(_clamp(wrist))
        c.create_oval(rx-wrist_r, cy-wrist_r, rx+wrist_r, cy+wrist_r,
                      fill="#ff1744", outline="#d50000", width=4)
        colors = {"positioning": "#40c4ff", "go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b", "rest": "#888888", "done": "#888888"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 18, text=mode.sub_phase.upper(),
                      fill=col, font=("Segoe UI", 14, "bold"))
        self._info_lbl.config(text=f"wrist = {wrist:.1f}°")

    def _draw_rah(self, lc_sample, t_elapsed, p):
        c = self._ball_canvas
        mode = p.motion_runner.active_mode
        if mode is not None and getattr(mode, 'name', '') == 'ramp_and_hold':
            self._draw_game(lc_sample, t_elapsed, p, mode)
            return
        c.delete('all')
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        is_dr_force = (mode is not None and getattr(mode, 'name', '') == 'direction_reversal'
                       and hasattr(mode, 'dynamics_enabled') and (not mode.dynamics_enabled()))
        if is_dr_force:
            channel = str(mode.params.get('feedback_channel', 'Fx') or 'Fx')
            mvc_value = max(0.01, float(mode.target_force_value()))
        else:
            channel = p.rah_channel_var.get()
            mvc_value = max(0.01, p._parse_float(p.rah_limit_var, 10.0))
        custom_profile = bool(mode is not None and hasattr(mode, 'has_custom_profile') and mode.has_custom_profile())
        if is_dr_force:
            target_value = float(mode.profile_value(t_elapsed)) if hasattr(mode, 'profile_value') else 0.0
            target_norm = target_value / mvc_value
        else:
            target_pct = p._parse_float(p.rah_target_pct_var, 10.0)
            if mode is not None and hasattr(mode, 'target_pct_at'):
                target_pct = float(mode.target_pct_at(t_elapsed))
            target_pct = max(0.0 if custom_profile else 0.01, target_pct)
            target_value = mvc_value * target_pct / 100.0
            target_norm = max(0.0 if custom_profile else 0.01, target_value / mvc_value)
        (ML, MR, MT, MB) = (50, 10, 15, 30)
        (PW, PH) = (max(1, W - ML - MR), max(1, H - MT - MB))
        WIN_S = 15.0
        now_frac = 1.0 / 3.0
        t_left = t_elapsed - WIN_S * now_frac
        t_right = t_left + WIN_S
        history = getattr(p, '_rah_history', []) or []
        visible_history = [(t_s, nv) for (t_s, nv) in history if t_left <= t_s <= t_right]
        if is_dr_force:
            (y_axis_min, y_axis_max) = (-1.0, 1.0)
        else:
            max_target_norm = target_norm
            if mode is not None and hasattr(mode, 'target_pct_values'):
                max_target_norm = max(max_target_norm, max(mode.target_pct_values(), default=0.0) / 100.0)
            (y_axis_min, y_axis_max) = (0.0, max(0.01, max_target_norm * 1.25))
        y_axis_span = y_axis_max - y_axis_min

        def _xpx(t_abs):
            return int(ML + (t_abs - t_left) / WIN_S * PW)

        def _ypx(norm_val):
            value = max(y_axis_min, min(y_axis_max, norm_val))
            return int(MT + (y_axis_max - value) / y_axis_span * PH)
        y_zero = _ypx(0.0)
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill='#0d1117', outline='#1e2a3a')
        c.create_line(ML, y_zero, ML + PW, y_zero, fill='#2a3a4a', width=2)
        unit = '% MVC' if is_dr_force else 'Nmm' if channel.startswith('T') else 'N'
        ticks = [-1.0, -0.5, 0.0, 0.5, 1.0] if is_dr_force else [y_axis_max * i / 4.0 for i in range(1, 5)]
        for tick in ticks:
            yg = _ypx(tick)
            c.create_line(ML, yg, ML + PW, yg, fill='#1a2535', width=1)
            tick_label = '0%' if is_dr_force and abs(tick) < 1e-09 else f'{tick * 100:+.0f}%' if is_dr_force else f'{tick * mvc_value:.1f}'
            c.create_text(ML - 4, yg, text=tick_label, anchor='e', fill='#445566', font=('Segoe UI', 7))
        c.create_text(12, MT + PH // 2, text=f'{channel}\n{unit}', fill='#445566', font=('Segoe UI', 8), angle=90)
        tick_start = int(t_left // 5) * 5
        t_tick = tick_start
        while t_tick <= t_right + 0.01:
            x = _xpx(t_tick)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill='#445566')
                c.create_text(x, MT + PH + 14, text=f'{int(t_tick)}s', fill='#445566', font=('Segoe UI', 7))
            t_tick += 5
        CYCLE = mode.cycle_duration() if mode is not None and hasattr(mode, 'cycle_duration') else 20.0
        total_reps = int(round(float(mode.params.get('total_reps', 3)))) if mode is not None else 3
        profile_duration = mode.total_duration() if (custom_profile or is_dr_force) and hasattr(mode, 'total_duration') else CYCLE * total_reps

        def _profile_y(t_abs):
            if t_abs < 0 or t_abs > profile_duration:
                return 0.0
            if (custom_profile or is_dr_force) and hasattr(mode, 'profile_value'):
                profile_norm = float(mode.profile_value(t_abs)) / mvc_value
                return profile_norm if is_dr_force else max(0.0, profile_norm)
            frac = mode.profile_fraction(t_abs) if mode is not None and hasattr(mode, 'profile_fraction') else 0.0
            return frac * target_norm
        prof_coords = []
        t_s = t_left
        while t_s <= t_right + 0.01:
            prof_coords.extend([_xpx(t_s), _ypx(_profile_y(t_s))])
            t_s += 0.25
        if len(prof_coords) >= 4:
            c.create_line(*prof_coords, fill='#40c4ff', width=5, dash=(8, 4))
        if not custom_profile:
            cycle_n = int(t_left // CYCLE)
            while True:
                t_boundary = (cycle_n + 1) * CYCLE
                if t_boundary > t_right + 0.01:
                    break
                if t_left <= t_boundary <= t_right:
                    xb = _xpx(t_boundary)
                    c.create_line(xb, MT, xb, MT + PH, fill='#1e4a8a', width=1, dash=(4, 6))
                cycle_n += 1
        x_now = _xpx(t_elapsed)
        c.create_line(x_now, MT, x_now, MT + PH, fill='#aaa', width=2)
        draw_history = self._smooth_history_for_display(visible_history)
        if len(draw_history) >= 2:
            coords = []
            for (t_s, nv) in draw_history:
                x = _xpx(t_s)
                y = _ypx(nv)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_nv = draw_history[-1][1]
                if is_dr_force:
                    lc_col = '#ff4444' if abs(last_nv) > 1.0 else '#00e676'
                else:
                    lc_col = '#ff4444' if last_nv > target_norm else '#00e676'
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2] - 5, coords[-1] - 5, coords[-2] + 5, coords[-1] + 5, fill=lc_col, outline='white', width=1)
        if custom_profile or is_dr_force:
            title = f't={t_elapsed:.1f}s/{profile_duration:.1f}s  |  {mode.sub_phase}'
        else:
            phase_s = t_elapsed % CYCLE
            cycle_n2 = int(t_elapsed // CYCLE) + 1
            title = f't={t_elapsed:.1f}s  |  trial {min(cycle_n2, total_reps)}/{total_reps}  |  phase {phase_s:.1f}s/{CYCLE:.1f}s'
        c.create_text(W // 2, 8, text=title, fill='#aaa', font=('Segoe UI', 9, 'bold'))
        self._info_lbl.config(text='')

    def _draw_game(self, lc_sample, t_elapsed, p, mode):
        """The bean game of a ramp-and-hold run; RAHCoinGame draws everything on the canvas.

        The plot spans NEGATIVE_AXIS_FRACTION of the peak below zero so that rest sits
        visibly on the baseline. A bean is collected when any part of the character touches
        it (see RAHCoinGame); ``pickup_tolerance`` says what that is in force units here.
        """
        c = self._ball_canvas
        c.configure(bg=sprites.BACKGROUND)
        c.delete('!rah_game')
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        mvc_value = max(0.01, p._parse_float(p.rah_limit_var, 10.0))
        peak_norm = max(mode.target_pct_values(), default=0.0) / 100.0 if hasattr(mode, 'target_pct_values') else 0.1
        (axis_min, axis_max) = RAHCoinGame.axis_range(peak_norm)
        (ML, MT, PW, PH) = RAHCoinGame.plot_box(W, H)
        WIN_S = RAHCoinGame.WINDOW_S
        t_left = t_elapsed - WIN_S / 3.0
        t_right = t_left + WIN_S
        sprite_pad = RAHCoinGame.sprite_pad(PH)

        def _xpx(t_abs):
            return ML + (t_abs - t_left) / WIN_S * PW

        def _ypx(norm_val):
            value = max(axis_min, min(axis_max, norm_val))
            return MT + sprite_pad + (axis_max - value) / (axis_max - axis_min) * (PH - 2 * sprite_pad)
        profile_duration = float(mode.total_duration()) if hasattr(mode, 'total_duration') else 0.0

        def _profile_y(t_abs):
            if t_abs < 0 or t_abs > profile_duration:
                return 0.0
            return max(0.0, float(mode.profile_value(t_abs)) / mvc_value)
        update_history = getattr(p, '_update_rah_history', None)
        if update_history is not None:
            # Every LC sample up to this frame (each at its own trial time), so the character
            # and the bean verdicts never use a sample that is up to one GUI tick old.
            update_history()
        history = getattr(p, '_rah_history', []) or []
        visible_history = [(t_s, nv) for (t_s, nv) in history if t_left <= t_s <= t_right]
        draw_history = self._smooth_history_for_display(visible_history)
        current_norm = float(draw_history[-1][1]) if draw_history else None
        _latest, sample_ts = p.lc_worker.ring.get_latest()
        live_data = bool(
            lc_sample is not None and len(lc_sample) >= 6
            and sample_ts is not None and 0.0 <= time.perf_counter() - sample_ts <= 0.5
            and draw_history and 0.0 <= t_elapsed - draw_history[-1][0] <= 0.5
            and current_norm is not None and _math.isfinite(current_norm)
        )
        self._rah_game.draw(
            trial_key=id(mode), elapsed=t_elapsed, duration=profile_duration,
            t_left=t_left, t_right=t_right, x_px=_xpx, y_px=_ypx, profile_y=_profile_y,
            history=draw_history, live_data=live_data, plot_box=(ML, MT, PW, PH),
        )
        self._info_lbl.config(text='')

    def pickup_tolerance(self, mvc_value, peak_norm, ramp_slopes=()):
        """How far the character may be above and below the line and still collect a bean, at
        the current canvas size: (above, below, robot_px, coin_px, ramp), ``above``/``below``
        for a hold (flat bean) in the feedback channel's units (N for forces, Nmm for torques)
        and ``ramp`` = (above, below) for the steepest of ``ramp_slopes`` (fractions of MVC per
        second; None when no ramps are given). It is the sprites' vertical reach
        (sprites.touch_range for a bean lying along the line) converted with this canvas'
        force scale for a run whose highest target is ``peak_norm`` (fraction of MVC) at
        ``mvc_value``."""
        c = self._ball_canvas
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        (axis_min, axis_max) = RAHCoinGame.axis_range(peak_norm)
        plot_box = RAHCoinGame.plot_box(W, H)
        (_ML, _MT, PW, PH) = plot_box
        px_per_norm = (PH - 2 * RAHCoinGame.sprite_pad(PH)) / (axis_max - axis_min)
        px_per_s = PW / RAHCoinGame.WINDOW_S
        # The character is sized for this plot so that its touch reach is the configured zone
        # (set_schedule must have been called for the run, as _begin_rah_score_run does).
        robot_px = self._rah_game.robot_px_for(plot_box)
        coin_px = RAHCoinGame.coin_px(robot_px, PW)
        scale = float(mvc_value) / px_per_norm
        above_px, below_px = RAHCoinGame.pickup_reach_px(robot_px, coin_px, angle=0.0)
        ramp = None
        for slope in ramp_slopes:
            angle = RAHCoinGame.line_angle(slope, px_per_norm, px_per_s)
            a, b = RAHCoinGame.pickup_reach_px(robot_px, coin_px, angle=angle)
            ramp = (a * scale, b * scale) if ramp is None else (max(ramp[0], a * scale), max(ramp[1], b * scale))
        return above_px * scale, below_px * scale, robot_px, coin_px, ramp

    # ==========================================================================
    #  RAH practice (unrecorded warm-up before a run)
    # ==========================================================================
    def _draw_practice(self, p):
        """Unrecorded warm-up: the robot climbs with the live force; the top band is the practice % MVC.

        Reads only p.practice_display_state(); nothing here records or commands anything.
        """
        c = self._ball_canvas
        c.configure(bg=sprites.BACKGROUND)
        c.delete('all')
        W = c.winfo_width() or 560
        H = c.winfo_height() or 200
        state = p.practice_display_state() if hasattr(p, 'practice_display_state') else None
        if state is None:
            self._draw_monitor_idle('')
            return
        (ML, MT, PW, PH) = RAHCoinGame.plot_box(W, H)
        WIN_S = 15.0
        t_now = float(state['t_now'])
        t_left = t_now - WIN_S / 3.0
        t_right = t_left + WIN_S
        top = max(0.01, float(state['top_pct']) / 100.0)
        axis_min = -sprites.NEGATIVE_AXIS_FRACTION * top
        sprite_pad = min(44, PH * 0.18)

        def _xpx(t_abs):
            return ML + (t_abs - t_left) / WIN_S * PW

        def _ypx(norm_val):
            value = max(axis_min, min(top, norm_val))
            return MT + sprite_pad + (top - value) / (top - axis_min) * (PH - 2 * sprite_pad)
        y_top = _ypx(top)
        y_zero = _ypx(0.0)
        self._draw_backdrop(c, W, H, ML, MT, PW, PH)
        c.create_rectangle(ML, MT, ML + PW, y_top, fill=sprites.TOP_BAND_FILL, outline='')
        c.create_rectangle(ML, y_zero, ML + PW, MT + PH, fill=sprites.NEGATIVE_FILL, outline='')
        c.create_line(ML, y_zero, ML + PW, y_zero, fill=sprites.hex_colour(sprites.THEME['baseline']), width=2)
        top_colour = sprites.hex_colour(sprites.THEME['top_line'])
        c.create_line(ML, y_top, ML + PW, y_top, fill=top_colour, width=3, dash=(12, 8))
        c.create_text(ML + PW - 12, (MT + y_top) / 2, text=f'TOP  {top * 100:g} %', anchor='e',
                      fill=top_colour, font=sprites.FONT_TOP)
        history = [(t_s, nv) for (t_s, nv) in state['history'] if t_left <= t_s <= t_right]
        coords = []
        for (t_s, nv) in history:
            coords.extend([_xpx(t_s), _ypx(nv)])
        if len(coords) >= 4:
            c.create_line(*coords, fill=sprites.hex_colour(sprites.THEME['trail']), width=2, smooth=True, splinesteps=8)
        current = history[-1][1] if history else None
        robot_px = RAHCoinGame.robot_height(PH)
        # Lowest mood until the top has been reached once, then the brightest.
        mood = 4 if state.get('top_hits', 0) else 1
        robot = sprites.photo(sprites.robot_image(robot_px, mood), c)
        if current is not None:
            dx, dy = sprites.robot_anchor(robot_px, mood)   # body centre on the force value
            c.create_image(_xpx(t_now) - dx, _ypx(current) - dy, image=robot)
        else:
            c.create_text(_xpx(t_now), MT + 18, text='Waiting for live LC data',
                          fill=sprites.hex_colour(sprites.THEME['text_dim']), font=('Segoe UI', 10))
        c.create_text(ML + 4, 17, text='PRACTICE', anchor='w', fill=sprites.hex_colour(sprites.THEME['text_dim']),
                      font=sprites.FONT_LABEL)
        c.create_text(ML + PW - 6, 17, text=f'TOP REACHED  {int(state.get("top_hits", 0))}', anchor='e',
                      fill=sprites.hex_colour(sprites.THEME['text']), font=sprites.FONT_HUD)
        self._info_lbl.config(text='')

    # ==========================================================================
    #  Leaderboard overlay
    # ==========================================================================
    def show_leaderboard(self, records, highlight=None, last_run=None):
        """Overlay the standings on the game area until the next run starts or it is hidden.

        ``records`` are RAHScore personal bests, ``highlight`` the Player ID to mark as
        YOU and ``last_run`` the dictionary about the run that just finished (or None
        when the operator asks for the board outside a run).
        """
        self._leaderboard = {'records': list(records), 'highlight': highlight, 'last_run': last_run}
        self._leaderboard_photo = None
        self._leaderboard_size = None
        self._leaderboard_drawn = False
        self._draw_leaderboard()

    def hide_leaderboard(self):
        if self._leaderboard is None:
            return
        self._leaderboard = None
        self._leaderboard_photo = None
        self._leaderboard_drawn = False
        self._ball_canvas.delete('all')
        self._info_lbl.config(text='')

    @property
    def leaderboard_visible(self) -> bool:
        return self._leaderboard is not None

    def _on_ball_canvas_configure(self, _event=None):
        if self._leaderboard is None:
            return
        if self._leaderboard_resize_after_id is not None:
            self.after_cancel(self._leaderboard_resize_after_id)
        self._leaderboard_resize_after_id = self.after(150, self._redraw_leaderboard_after_resize)

    def _redraw_leaderboard_after_resize(self):
        self._leaderboard_resize_after_id = None
        if self._leaderboard is not None:
            self._leaderboard_drawn = False
            self._draw_leaderboard()

    def _draw_leaderboard(self):
        info = self._leaderboard
        if info is None:
            return
        c = self._ball_canvas
        W, H = int(c.winfo_width()), int(c.winfo_height())
        if W <= 1 or H <= 1:
            return  # not mapped yet; the poll loop tries again
        if self._leaderboard_drawn and self._leaderboard_size == (W, H) and c.type('leaderboard'):
            return
        if self._leaderboard_photo is None or self._leaderboard_size != (W, H):
            image = render_leaderboard(info['records'], limit=10, highlight=info['highlight'],
                                       last_run=info['last_run'], size=(W, H))
            self._leaderboard_photo = ImageTk.PhotoImage(image, master=c)
            self._leaderboard_size = (W, H)
        c.configure(bg=themes.active()['leaderboard']['background'])
        c.delete('all')
        c.create_image(0, 0, anchor='nw', image=self._leaderboard_photo, tags=('leaderboard',))
        self._leaderboard_drawn = True
        self._info_lbl.config(text='The leaderboard stays until the next run starts (Esc on this window hides it)')

    # ==========================================================================
    #  Countdown
    # ==========================================================================
    def countdown(self, callback):
        self.hide_leaderboard()
        # Sprites for the coming run are rendered now, not on its first frame.
        try:
            self._rah_game.prewarm(self._ball_canvas.winfo_width() or 560, self._ball_canvas.winfo_height() or 200)
        except Exception:
            pass
        self._countdown_text = ''
        self._counting_down = True
        self._instr_lbl.config(text="Get ready", fg="#ffd700")
        self._do_countdown(3, callback)

    def _do_countdown(self, n, callback):
        self._countdown_text = "GO!" if n <= 0 else str(n)
        if n <= 0:
            self._timer_lbl.config(text="GO!", fg="#ff1744")
            self.after(500, lambda: (
                setattr(self, "_counting_down", False),
                self._instr_lbl.config(text="—", fg="#888888"),
                self._timer_lbl.config(text=""),
                callback()
            ))
            return
        self._timer_lbl.config(text=str(n))
        self.after(1000, lambda: self._do_countdown(n - 1, callback))

    # ==========================================================================
    #  Force alert
    # ==========================================================================

"""The RAH collecting game on the subject display: the coffee robot collects coffee beans (the
"alien" theme in gui/themes.py swaps the artwork; "bean"/"robot" in the names cover both).

The character sits at "now" and rises with the live force while the target path scrolls past
it, carrying collectibles at equal distance along the path (so ramps are as dense as holds). A
bean is collected when the character's pickup part (its round body shrunk to
config.RAH_PICKUP_BODY_FRACTION) touches the bean at the moment the bean reaches the character,
i.e. when the two sprites overlap on screen (``sprites.touch_range`` gives the vertical reach).
The zone is defined in force: config.RAH_PICKUP_TOLERANCE_PCT_TARGET % of the run's highest
target above and below the line, and the character is sized for every plot size so that the
on-screen touch happens exactly there (``robot_px_for``); so the zone is the same on any
monitor, a window resized during a run changes nothing, and ``display.pickup_tolerance``
reports exactly what is judged. The verdict uses the force at the instant the
bean's centre reaches the character's pickup column (interpolated between the force samples
around that instant), never the frame's own moment, so it does not depend on when the display
happens to redraw. A collected bean vanishes with a small sparkle; a missed one stays exactly as
it was and scrolls away, so the participant sees it was missed. The character's mood follows
each rep of the profile (``rep_starts``: a rep = the rest, ramp up, hold and ramp down of one
cycle): it starts a rep asleep and brightens one step each time a quarter
(config.RAH_ROBOT_WAKE_FRACTIONS) of that rep's collectibles has been collected, falls asleep
again when the next rep's rest begins, and keeps its last face through the extra rest at the end
of the run. The pickup body is the same for every mood (``sprites.PICKUP_REFERENCE_STATE``), so
the face never changes the zone. Nothing else: no corridor, trail or streak effects.

The bean positions are computed once per run from the profile in a nominal plot (100 px per
second, 1000 px for the whole force axis), so they do not depend on the window size and the
total is fixed for a given diagram. Cost per frame is kept small on purpose (the Tk main thread
is shared with the operator GUI): every canvas item is created once and then only moved, the
sprites are small cached images that ``prewarm`` renders before a run starts, and the background
is flat canvas items. Display only: nothing here records or commands anything.
"Coin" in names = one bean.
"""

import bisect
import math

from gui import sprites, themes

try:
    from config import RAH_COIN_INTERVAL_S, RAH_ROBOT_WAKE_FRACTIONS
except ImportError:  # importable without the serial_manager config
    RAH_COIN_INTERVAL_S = 0.4
    RAH_ROBOT_WAKE_FRACTIONS = (0.25, 0.5, 0.75)
try:
    from config import RAH_PICKUP_TOLERANCE_PCT_TARGET
except ImportError:
    RAH_PICKUP_TOLERANCE_PCT_TARGET = 6.0


class RAHCoinGame:
    """Keep the beans' states across canvas redraws, with a fresh state for each trial."""

    COIN_INTERVAL_S = max(0.05, float(RAH_COIN_INTERVAL_S))   # bean spacing on a flat segment, in seconds
    COIN_GRACE_S = 0.8         # a bean not judged within this time of being reached (no live data) counts as missed
    SAMPLE_REACH_S = 0.25      # a verdict needs a force sample within this time of the bean's instant
    END_MARGIN_S = 0.15        # no bean in the last moments of the run: the run ends before it could be judged
    PICKUP_SECONDS = 0.25      # small sparkle after a pickup
    PATH_STEP_S = 0.1
    MARGINS = (24, 24, 34, 18)  # left, right, top, bottom margins of the plot inside the canvas
    WINDOW_S = 15.0            # seconds of trial time across the plot; "now" sits at one third
    ROBOT_FRACTION = 0.135     # practice view: character height as a fraction of the plot height
    PICKUP_PCT_TARGET = max(0.1, float(RAH_PICKUP_TOLERANCE_PCT_TARGET))   # collect zone, % of the run's highest target
    ROBOT_PX_MIN = 24          # the game's character is sized to the zone, between these limits
    ROBOT_PX_MAX_FRACTION = 0.6   # ... at most this fraction of the plot height
    ANGLE_STEP_DEG = 5.0       # bean orientations are rounded to this step (few pictures and touch ranges)
    SLOPE_DT_S = 0.05          # half-width of the central difference that gives the line's slope at a bean
    NOMINAL_PX_PER_S = 100.0   # nominal plot used to space the beans along the path
    NOMINAL_PLOT_H = 1000.0    # ... height of the whole force axis in that plot
    WAKE_FRACTIONS = tuple(sorted(float(f) for f in RAH_ROBOT_WAKE_FRACTIONS))

    def __init__(self, canvas):
        self.canvas = canvas
        self.trial_key = None
        self.collected = set()
        self.missed = set()
        self._coin_times = None
        self._profile_y = None      # set_schedule keeps the profile, peak and axis range of the run
        self._peak_norm = None
        self._axis_range = None
        self._rep_starts = None     # trial times at which the reps of the scheduled run begin
        self._bean_rep = None       # bean index - 1 -> rep index (0-based)
        self._rep_totals = None     # beans per rep
        self._rep_collected = None  # beans collected per rep in the current trial
        self._angles = {}
        self._robot_px_cache = {}   # plot size -> character height that gives the collect zone
        self._pickups = {}
        self._last_elapsed = None
        # Per-bean record of the run, written next to the recording by the operator GUI:
        # index -> (bean time, displayed force, target, offset px, lo px, hi px, collected, judged at).
        self.verdicts = {}
        self.px_per_norm = None     # the plot's px per fraction of MVC at the last frame
        self._items = {}
        self._item_options = {}
        self._item_coords = {}
        self._frame_keys = set()
        self._created = False

    # ---- canvas bookkeeping: move existing items, create or drop only what changed ----
    def _begin_frame(self):
        # Other display modes can clear the shared canvas between frames.
        counter = self._items.get("counter")
        if counter is not None and not self.canvas.type(counter):
            self._items.clear()
            self._item_options.clear()
            self._item_coords.clear()
        self._frame_keys.clear()
        self._created = False

    def _draw_item(self, key, kind, *coords, **options):
        self._frame_keys.add(key)
        options["tags"] = ("rah_game",) + tuple(options.get("tags", ()))
        if key not in self._items:
            item = getattr(self.canvas, "create_" + kind)(*coords, **options)
            self._items[key] = item
            self._created = True
        else:
            item = self._items[key]
            if self._item_coords[key] != coords:
                self.canvas.coords(item, *coords)
            previous = self._item_options[key]
            changed = {name: value for name, value in options.items() if previous.get(name) != value}
            if changed:
                self.canvas.itemconfigure(item, **changed)
        self._item_coords[key] = coords
        self._item_options[key] = options
        return item

    def _drop_item(self, key):
        self.canvas.delete(self._items.pop(key))
        self._item_coords.pop(key)
        self._item_options.pop(key)
        self._frame_keys.discard(key)

    def _finish_frame(self):
        for key in list(self._items):
            if key not in self._frame_keys:
                self._drop_item(key)
        if self._created:  # stacking only changes when something new was created
            for tag in ("rah_backdrop", "rah_path", "rah_coin", "rah_sparkle", "rah_robot", "rah_hud"):
                self.canvas.tag_raise(tag)

    def _photo(self, image):
        return sprites.photo(image, self.canvas)

    # ---- geometry and timing ----
    @classmethod
    def plot_box(cls, width, height):
        """(left, top, width, height) of the plot inside a canvas of the given size."""
        (ML, MR, MT, MB) = cls.MARGINS
        return (ML, MT, max(1, int(width) - ML - MR), max(1, int(height) - MT - MB))

    @staticmethod
    def sprite_pad(plot_height):
        """Vertical padding inside the plot so that sprites at the axis limits stay whole."""
        return min(44, plot_height * 0.18)

    @staticmethod
    def axis_range(peak_norm):
        """(axis_min, axis_max) in fractions of MVC for a run whose highest target is ``peak_norm``."""
        axis_max = max(0.01, float(peak_norm) * 1.25)
        return -sprites.NEGATIVE_AXIS_FRACTION * axis_max, axis_max

    @classmethod
    def robot_height(cls, plot_height):
        """Character height in px for views without a collect zone (the practice): ROBOT_FRACTION
        of the plot, within limits that depend on the theme."""
        lo, hi = themes.active().get("robot_px", (48, 108))
        return max(lo, min(hi, round(plot_height * cls.ROBOT_FRACTION)))

    @classmethod
    def zone_norm(cls, peak_norm):
        """The collect zone above and below the line as a fraction of MVC: PICKUP_PCT_TARGET %
        of the run's highest target."""
        return cls.PICKUP_PCT_TARGET / 100.0 * float(peak_norm)

    def robot_px_for(self, plot_box):
        """Character height for this plot such that its pickup part touches a hold bean exactly
        at the collect zone (zone_norm of the scheduled run, in this plot's pixels), so the rule
        seen on screen and the force rule are the same at every window size. Monotonic in the
        size, so a binary search over the sprites' touch ranges; cached per plot size. Falls
        back to robot_height before a run is scheduled."""
        (_ML, _MT, PW, PH) = plot_box
        if not self._peak_norm:
            return self.robot_height(PH)
        key = (themes.active_name(), int(PW), int(PH), round(self._peak_norm, 5), sprites.PICKUP_BODY_FRACTION,
               self.PICKUP_PCT_TARGET)
        robot_px = self._robot_px_cache.get(key)
        if robot_px is None:
            px_per_norm, _px_per_s = self._scales(plot_box)
            wanted = self.zone_norm(self._peak_norm) * px_per_norm

            def reach(height):
                above, below = self.pickup_reach_px(height, self.coin_px(height, PW), angle=0.0)
                return (above + below) / 2.0

            low, high = self.ROBOT_PX_MIN, max(self.ROBOT_PX_MIN + 1, int(PH * self.ROBOT_PX_MAX_FRACTION))
            while high - low > 1:
                mid = (low + high) // 2
                if reach(mid) < wanted:
                    low = mid
                else:
                    high = mid
            robot_px = min((low, high), key=lambda height: abs(reach(height) - wanted))
            self._robot_px_cache[key] = robot_px
        return robot_px

    @staticmethod
    def coin_size(robot_px):
        return max(28, min(60, round(robot_px * 0.6)))

    @classmethod
    def coin_px(cls, robot_px, plot_width):
        """Bean size in px for a plot of ``plot_width``: 0.6 x the character, never more than
        the bean spacing on a flat segment (so a flat chain of beans touches without overlap)."""
        size = cls.coin_size(robot_px)
        spacing_px = plot_width / cls.WINDOW_S * cls.COIN_INTERVAL_S
        if spacing_px > 0:
            size = max(12, min(size, int(spacing_px)))
        return size

    @staticmethod
    def pickup_reach_px(robot_px, coin_px, state=4, angle=0.0):
        """(above, below) in px: how far the character's pickup point may sit above or below
        the centre of a bean lying along a line of direction ``angle`` (degrees on screen, 0 =
        a hold) while the character's pickup part still touches the bean (the bean horizontally
        centred on the pickup point, as when it reaches the character)."""
        lo, hi = sprites.touch_range(robot_px, state, coin_px, angle=angle)
        return hi, -lo

    @classmethod
    def line_angle(cls, slope_norm_per_s, px_per_norm, px_per_s):
        """Direction of the target line on screen, in degrees rounded to ANGLE_STEP_DEG: 0 =
        flat, positive = rising to the right, for a slope in fractions of MVC per second."""
        raw = math.degrees(math.atan2(float(slope_norm_per_s) * px_per_norm, px_per_s))
        return round(raw / cls.ANGLE_STEP_DEG) * cls.ANGLE_STEP_DEG

    def _scales(self, plot_box):
        """(px per fraction of MVC, px per second) of the plot, as the display maps them."""
        (_ML, _MT, PW, PH) = plot_box
        axis_min, axis_max = self._axis_range if self._axis_range else self.axis_range(0.1)
        px_per_norm = (PH - 2 * self.sprite_pad(PH)) / max(1e-9, float(axis_max) - float(axis_min))
        return px_per_norm, PW / self.WINDOW_S

    def bean_angle(self, index, plot_box, profile_y=None):
        """On-screen direction of the target line at bean ``index`` (degrees, see line_angle):
        the bean is drawn along it and judged with a bean of that orientation. Cached per bean
        and plot scale."""
        px_per_norm, px_per_s = self._scales(plot_box)
        key = (index, int(round(px_per_norm)), int(round(px_per_s)))
        angle = self._angles.get(key)
        if angle is None:
            profile_y = profile_y or self._profile_y or (lambda _t: 0.0)
            t = self.coin_time(index)
            d = self.SLOPE_DT_S
            slope = (float(profile_y(t + d)) - float(profile_y(t - d))) / (2.0 * d)
            angle = self.line_angle(slope, px_per_norm, px_per_s)
            self._angles[key] = angle
        return angle

    def prewarm(self, width, height):
        """Render and upload the sprites (and the background picture, if the theme has one) for
        this canvas size, and compute the bean orientations of the scheduled run and their touch
        ranges, so the first frames of a run are cheap."""
        plot_box = self.plot_box(width, height)
        (_left, _top, PW, PH) = plot_box
        robot_px = self.robot_px_for(plot_box)
        coin_px = self.coin_px(robot_px, PW)
        angles = {0.0}
        if self._coin_times is not None and self._profile_y is not None:
            angles.update(self.bean_angle(index, plot_box) for index in range(1, len(self._coin_times) + 1))
        for state in range(1, sprites.ROBOT_STATES + 1):
            self._photo(sprites.robot_image(robot_px, state))
            for angle in angles:
                sprites.touch_range(robot_px, state, coin_px, angle=angle)
        for angle in angles:
            self._photo(sprites.bean_image(coin_px, angle))
        self._photo(sprites.bean_image(20))
        backdrop = sprites.background_image(width, height)
        if backdrop is not None:
            self._photo(backdrop)

    @classmethod
    def bean_times(cls, profile_y, duration, axis_min, axis_max):
        """Trial times of the beans: equal distance along the path in the nominal plot.

        ``profile_y(t)`` is the target as a fraction of MVC. The first bean sits one spacing
        from the start; a flat second of path is NOMINAL_PX_PER_S long, the whole force axis
        NOMINAL_PLOT_H high, so ramps get more beans per second than holds.
        """
        spacing = cls.COIN_INTERVAL_S * cls.NOMINAL_PX_PER_S
        y_scale = cls.NOMINAL_PLOT_H / max(1e-9, float(axis_max) - float(axis_min))
        duration = float(duration)
        times, t, dt = [], 0.0, 0.01
        travelled = 0.0
        y_prev = float(profile_y(0.0)) * y_scale
        next_mark = spacing
        while t < duration - 1e-9:
            t2 = min(duration, t + dt)
            y2 = float(profile_y(t2)) * y_scale
            seg = math.hypot((t2 - t) * cls.NOMINAL_PX_PER_S, y2 - y_prev)
            while seg > 0 and travelled + seg >= next_mark - 1e-9:
                frac = (next_mark - travelled) / seg
                times.append(t + frac * (t2 - t))
                next_mark += spacing
            travelled += seg
            t, y_prev = t2, y2
        return [t for t in times if t <= duration - cls.END_MARGIN_S]

    @classmethod
    def rep_starts(cls, profile_y, duration):
        """Trial times at which the reps of the profile begin: 0, then the start of every rest
        (target back at zero after being positive) that is followed by force again. A rep is
        the rest, ramp up, hold and ramp down of one cycle, so the rest between two cycles opens
        the next rep; the extra rest after the last ramp down belongs to the last rep. Scanned
        at 10 ms like the bean positions."""
        duration = float(duration)
        steps = int(math.ceil(duration / 0.01))
        starts, candidates = [0.0], []
        active_prev = float(profile_y(0.0)) > 1e-9
        last_active = 0.0 if active_prev else None
        for i in range(1, steps + 1):
            t = min(duration, round(i * 0.01, 3))
            active = float(profile_y(t)) > 1e-9
            if active:
                last_active = t
            elif active_prev:
                candidates.append(t)
            active_prev = active
        if last_active is not None:
            starts.extend(t for t in candidates if t < last_active)
        return starts

    def set_schedule(self, profile_y, duration, peak_norm):
        """Fix the bean positions for the coming run (call once per run, before it starts) and
        keep the profile, its highest target (``peak_norm``, fraction of MVC: the axis and the
        collect zone follow it) and the axis range for the beans' orientations. Also assigns
        every bean to its rep (``rep_starts``) for the character's mood."""
        axis_min, axis_max = self.axis_range(peak_norm)
        self._coin_times = self.bean_times(profile_y, duration, axis_min, axis_max)
        self._profile_y = profile_y
        self._peak_norm = float(peak_norm)
        self._axis_range = (float(axis_min), float(axis_max))
        self._angles.clear()
        self._robot_px_cache.clear()
        self._rep_starts = self.rep_starts(profile_y, duration)
        self._bean_rep = [max(0, bisect.bisect_right(self._rep_starts, t) - 1) for t in self._coin_times]
        self._rep_totals = [0] * len(self._rep_starts)
        for rep in self._bean_rep:
            self._rep_totals[rep] += 1
        self._rep_collected = [0] * len(self._rep_starts)

    def total_coins(self, duration=None):
        """Number of beans in the run (scheduled; or one per flat spacing of ``duration`` as a fallback)."""
        if self._coin_times is not None:
            return len(self._coin_times)
        return max(0, math.floor(float(duration or 0.0) / self.COIN_INTERVAL_S + 1e-9))

    def coin_time(self, index):
        """Trial time of bean number ``index`` (1-based)."""
        if self._coin_times is not None:
            return self._coin_times[index - 1]
        return index * self.COIN_INTERVAL_S

    def _visible_range(self, t_left, t_right, duration):
        """Indices (1-based, inclusive) of the beans inside the scrolling window."""
        if self._coin_times is not None:
            return bisect.bisect_left(self._coin_times, t_left) + 1, bisect.bisect_right(self._coin_times, t_right)
        step = self.COIN_INTERVAL_S
        return max(1, math.ceil(t_left / step - 1e-9)), min(self.total_coins(duration), math.floor(t_right / step + 1e-9))

    def rep_at(self, elapsed):
        """0-based index of the rep that trial time ``elapsed`` belongs to (the last rep also
        covers the extra rest at the end of the run and anything after it)."""
        if not self._rep_starts:
            return 0
        return max(0, min(len(self._rep_starts) - 1, bisect.bisect_right(self._rep_starts, float(elapsed)) - 1))

    def robot_state(self, total, elapsed=None):
        """1 (asleep) .. 4 (wide awake): one step per wake fraction of the beans of the current
        rep (``rep_at(elapsed)``) collected, so the character falls asleep again when a rep's
        rest begins and keeps its last face through the extra rest at the end. Without a
        scheduled run (no rep information) the fraction is taken over the whole run."""
        if self._rep_totals and elapsed is not None:
            rep = self.rep_at(elapsed)
            rep_total = self._rep_totals[rep]
            fraction = self._rep_collected[rep] / float(rep_total) if rep_total > 0 else 0.0
        else:
            if total <= 0:
                return 1
            fraction = len(self.collected) / float(total)
        return 1 + sum(1 for f in self.WAKE_FRACTIONS if fraction >= f - 1e-9)

    def begin_trial(self, trial_key):
        self.trial_key = trial_key
        self.collected.clear()
        self.missed.clear()
        self.verdicts.clear()
        self._pickups.clear()
        self._last_elapsed = None
        if self._rep_collected is not None:
            self._rep_collected = [0] * len(self._rep_collected)

    def _sync_trial(self, trial_key, elapsed):
        if trial_key != self.trial_key:
            self.begin_trial(trial_key)
        self._last_elapsed = elapsed

    @classmethod
    def _force_at(cls, history, times, t):
        """The displayed force at trial time ``t``: interpolated between the two samples of
        ``history`` (time-ordered (trial time, fraction of MVC) pairs; ``times`` = their times)
        around ``t``, the nearer one alone when only one side exists, None when no sample lies
        within SAMPLE_REACH_S of ``t`` (no live data at that moment)."""
        k = bisect.bisect_left(times, t)
        before = history[k - 1] if k > 0 else None
        after = history[k] if k < len(history) else None
        if before is not None and t - before[0] > cls.SAMPLE_REACH_S:
            before = None
        if after is not None and after[0] - t > cls.SAMPLE_REACH_S:
            after = None
        if before is None and after is None:
            return None
        if before is None:
            return float(after[1])
        if after is None:
            return float(before[1])
        span = after[0] - before[0]
        if span <= 0.0:
            return float(after[1])
        weight = (t - before[0]) / span
        return float(before[1]) + weight * (float(after[1]) - float(before[1]))

    def _judge_coins(self, first, last, elapsed, history, profile_y, y_px, touch_of):
        """Judge each bean once, as soon as the force at the bean's instant is known: collected
        when the character's pickup part touches the bean at that instant, i.e. when the bean's
        centre lies within ``touch_of(index)`` = (lo, hi) canvas px of the character's pickup
        point (sprites.touch_range for that bean's orientation), the pickup point sitting on
        the displayed force at the moment the bean reaches it (interpolated between the force
        samples around that moment) and the bean on the target at its own time; otherwise
        missed (the bean stays on the path). Force and target are taken at the same instant and
        mapped with the frame's own y scale, so the verdict is exactly the overlap the
        participant sees when the bean reaches the character, whenever the display happens to
        redraw. The profile itself is never touched."""
        if not history:
            return
        times = [t for t, _value in history]
        latest = times[-1]
        for index in range(first, last + 1):
            if index in self.collected or index in self.missed:
                continue
            t_coin = self.coin_time(index)
            target = profile_y(t_coin)
            if latest < t_coin:
                if elapsed > t_coin + self.COIN_GRACE_S:
                    self.missed.add(index)  # passed while no live data was available
                    self.verdicts[index] = (t_coin, None, target, None, None, None, False, elapsed)
                    continue
                break  # not reached yet (beans are in time order)
            force = self._force_at(history, times, t_coin)
            if force is not None and math.isfinite(force):
                lo, hi = touch_of(index)
                offset = y_px(target) - y_px(force)   # bean centre below the pickup point by this many px
                collected = lo <= offset <= hi
                self.verdicts[index] = (t_coin, force, target, offset, lo, hi, collected, elapsed)
                if collected:
                    self.collected.add(index)
                    if self._bean_rep is not None:
                        self._rep_collected[self._bean_rep[index - 1]] += 1
                    self._pickups[index] = elapsed
                    continue
            else:
                self.verdicts[index] = (t_coin, None, target, None, None, None, False, elapsed)
            self.missed.add(index)

    def draw(self, *, trial_key, elapsed, duration, t_left, t_right, x_px, y_px, profile_y,
             history, live_data, plot_box):
        """Draw one frame: baseline and negative zone, path, beans, robot and the bean counter.

        ``x_px``/``y_px`` map trial time and fraction of MVC to canvas pixels, ``profile_y``
        gives the target fraction at a time, ``history`` is the displayed force inside the
        window as time-ordered (trial time, fraction of MVC) pairs, its last pair being the
        character's position, and ``plot_box`` is (left, top, width, height) of the plot area.
        """
        self._sync_trial(trial_key, elapsed)
        self._begin_frame()
        c = self.canvas
        (ML, MT, PW, PH) = plot_box
        T = sprites.THEME

        # Background picture of the theme (if any): one canvas image item, created once.
        backdrop = sprites.background_image(ML + PW + self.MARGINS[1], MT + PH + self.MARGINS[3])
        if backdrop is not None:
            self._draw_item("background", "image", 0, 0, anchor="nw", image=self._photo(backdrop),
                            tags=("rah_backdrop",))

        # Static star field (themes with stars only; [] otherwise), created once and never moved.
        for index, (sx, sy, r, colour) in enumerate(sprites.star_field(PW, PH)):
            x, y = ML + sx, MT + sy
            self._draw_item(("star", index), "oval", x - r, y - r, x + r, y + r, fill=colour, outline="",
                            tags=("rah_backdrop",))

        y_zero = y_px(0.0)
        self._draw_item("negative", "rectangle", ML, y_zero, ML + PW, MT + PH, fill=sprites.NEGATIVE_FILL,
                        outline="", tags=("rah_backdrop",))
        self._draw_item("baseline", "line", ML, y_zero, ML + PW, y_zero, fill=sprites.hex_colour(T["baseline"]),
                        width=2, tags=("rah_backdrop",))

        centre = []
        t = t_left
        while t <= t_right + 1e-9:
            centre.extend([x_px(t), y_px(profile_y(t))])
            t += self.PATH_STEP_S
        self._draw_item("path_glow", "line", *centre, fill=sprites.hex_colour(T["path_glow"]), width=11,
                        tags=("rah_path",))
        self._draw_item("path", "line", *centre, fill=sprites.hex_colour(T["path"]), width=4, tags=("rah_path",))

        total = self.total_coins(duration)
        first, last = self._visible_range(t_left, t_right, duration)
        last = min(last, total)
        robot_px = self.robot_px_for(plot_box)
        coin_px = self.coin_px(robot_px, PW)
        self.px_per_norm = self._scales(plot_box)[0]
        sample_t, current_norm = (float(history[-1][0]), float(history[-1][1])) if history else (None, None)
        if (current_norm is not None and math.isfinite(current_norm) and live_data
                and 0.0 <= elapsed <= duration + self.COIN_GRACE_S):
            mood = self.robot_state(total, elapsed)

            def touch_of(index):
                return sprites.touch_range(robot_px, mood, coin_px,
                                           angle=self.bean_angle(index, plot_box, profile_y))
            self._judge_coins(first, last, elapsed, history, profile_y, y_px, touch_of)

        for index in range(first, last + 1):
            if index in self.collected:
                continue  # collected beans vanish; missed ones keep their look and scroll away
            t_coin = self.coin_time(index)
            # Each bean lies along the line (flat on a hold, tilted with a ramp).
            bean = self._photo(sprites.bean_image(coin_px, self.bean_angle(index, plot_box, profile_y)))
            self._draw_item(("coin", index), "image", x_px(t_coin), y_px(profile_y(t_coin)),
                            image=bean, tags=("rah_coin",))

        for index, picked_at in list(self._pickups.items()):
            age = elapsed - picked_at
            if not 0.0 <= age < self.PICKUP_SECONDS:
                del self._pickups[index]
                continue
            t_coin = self.coin_time(index)
            cx, cy = x_px(t_coin), y_px(profile_y(t_coin))
            spread = coin_px * 0.5 + age / self.PICKUP_SECONDS * coin_px
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                x, y = cx + dx * spread, cy + dy * spread
                self._draw_item(("sparkle", index, dx, dy), "oval", x - 3, y - 3, x + 3, y + 3,
                                fill=sprites.hex_colour(T["sparkle"]), outline="", tags=("rah_sparkle",))

        state = self.robot_state(total, elapsed)
        robot = self._photo(sprites.robot_image(robot_px, state))
        if current_norm is not None and math.isfinite(current_norm):
            # The character's pickup point (its body centre) sits on its latest force sample: at
            # that sample's time (a few ms behind "now" with live data) and value, so it is
            # always a point of the drawn trajectory, never a stale value at a newer time.
            robot_t = sample_t if (live_data and sample_t is not None) else elapsed
            dx, dy = sprites.robot_anchor(robot_px, state)
            self._draw_item("robot", "image", x_px(robot_t) - dx, y_px(current_norm) - dy, image=robot,
                            tags=("rah_robot",))

        hud_y = 17
        counter = self._draw_item("counter", "text", ML + PW - 6, hud_y, anchor="e",
                                  text=f"{len(self.collected)} / {total}", fill=sprites.hex_colour(T["text"]),
                                  font=sprites.FONT_HUD, tags=("rah_hud",))
        icon_x = c.bbox(counter)[0] - 16
        self._draw_item("counter_icon", "image", icon_x, hud_y, image=self._photo(sprites.bean_image(20)),
                        tags=("rah_hud",))
        if not live_data:
            self._draw_item("waiting", "text", x_px(elapsed), MT + 18, text="WAITING FOR LIVE LC DATA",
                            fill=sprites.hex_colour(T["text_dim"]), font=sprites.FONT_LABEL, tags=("rah_hud",))
        self._finish_frame()

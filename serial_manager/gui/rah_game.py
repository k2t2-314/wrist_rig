"""Pixel sprites and display-only coin collection for the RAH subject view."""

import math
from pathlib import Path

from PIL import Image, ImageTk


class RAHCoinGame:
    """Keep pickups across canvas redraws, with a fresh state for each trial."""

    ASSET_DIR = Path(__file__).resolve().parent / "assets" / "rah"
    PICKUP_SECONDS = 0.3
    TEXTURE_HEIGHTS = {"robot": 32, "coin": 16}

    def __init__(self, canvas):
        self.canvas = canvas
        self.trial_key = None
        self.collected = set()
        self._pickups = {}
        self._last_elapsed = None
        self._sources = {}
        self._source_sizes = {}
        self._photos = {}
        self._sprite_height = None
        self._items = {}
        self._item_options = {}
        self._item_coords = {}
        self._frame_keys = set()

    def _begin_frame(self):
        # Other display modes can clear the shared canvas between RAH frames.
        score = self._items.get("score")
        if score is not None and not self.canvas.type(score):
            self._items.clear()
            self._item_options.clear()
            self._item_coords.clear()
        self._frame_keys.clear()

    def _draw_item(self, key, kind, *coords, **options):
        """Move existing sprites and update only properties that changed."""
        self._frame_keys.add(key)
        options["tags"] = ("rah_game",) + tuple(options.get("tags", ()))
        if key not in self._items:
            item = getattr(self.canvas, "create_" + kind)(*coords, **options)
            self._items[key] = item
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
        self.canvas.tag_raise("rah_game")
        self.canvas.tag_raise("rah_robot")

    @staticmethod
    def boxes_touch(first, second):
        """Axis-aligned bounding boxes collide even when their edges just touch."""
        return (
            first[0] <= second[2] and first[2] >= second[0]
            and first[1] <= second[3] and first[3] >= second[1]
        )

    @staticmethod
    def total_coins(duration):
        return max(0, math.floor(duration + 1e-9))

    def begin_trial(self, trial_key):
        self.trial_key = trial_key
        self.collected.clear()
        self._pickups.clear()
        self._last_elapsed = None

    def _sync_trial(self, trial_key, elapsed):
        rewound = self._last_elapsed is not None and elapsed < self._last_elapsed - 1e-6
        if trial_key != self.trial_key or rewound:
            self.begin_trial(trial_key)
        self._last_elapsed = elapsed

    def _load_sprites(self, robot_height):
        if robot_height == self._sprite_height:
            return
        sizes = {"robot": robot_height, "coin": max(18, min(28, round(robot_height * 0.44)))}
        for name, height in sizes.items():
            if name not in self._sources:
                with Image.open(self.ASSET_DIR / (name + ".png")) as image:
                    source = image.convert("RGBA")
                # Crop transparent margins so image bounds match the visible sprite.
                bounds = source.getchannel("A").getbbox()
                source = source.crop(bounds) if bounds else source
                self._source_sizes[name] = source.size
                texture_height = min(source.height, self.TEXTURE_HEIGHTS[name])
                texture_width = max(1, round(source.width * texture_height / source.height))
                # Small textures soften fine detail and keep the resident cache small.
                self._sources[name] = source.resize((texture_width, texture_height), Image.Resampling.BOX)
            source = self._sources[name]
            original_width, original_height = self._source_sizes[name]
            width = max(1, round(original_width * height / original_height))
            sprite = source.resize((width, height), Image.Resampling.BILINEAR)
            self._photos[name] = ImageTk.PhotoImage(sprite, master=self.canvas)
        self._sprite_height = robot_height

    def draw(self, *, trial_key, elapsed, duration, t_left, t_right,
             x_px, y_px, profile_y, current_norm, live_data, running,
             plot_height, right_edge):
        """Draw one coin per whole second and collect only touching live sprites.

        The robot stays at the current time while the target path scrolls left.
        Coin times are 1, 2, ..., floor(duration), including rest segments.
        No mode parameters, motor commands, or recorded samples are changed here.
        """
        self._sync_trial(trial_key, elapsed)
        self._load_sprites(max(36, min(64, round(plot_height * 0.12))))
        self._begin_frame()
        c = self.canvas
        total = self.total_coins(duration)
        first = max(1, math.ceil(t_left - 1e-9))
        last = min(total, math.floor(t_right + 1e-9))
        coin_items = []
        for second in range(first, last + 1):
            if second in self.collected:
                continue
            item = self._draw_item(("coin", second), "image", x_px(second), y_px(profile_y(second)),
                                   image=self._photos["coin"], tags=("rah_coin", f"rah_coin_{second}"))
            coin_items.append((second, item, c.bbox(item)))

        robot_box = None
        if current_norm is not None and math.isfinite(current_norm):
            robot = self._draw_item("robot", "image", x_px(elapsed), y_px(current_norm),
                                    image=self._photos["robot"], tags=("rah_robot",))
            robot_box = c.bbox(robot)
        if robot_box is not None and live_data and running and 0.0 <= elapsed <= duration:
            for second, item, bounds in coin_items:
                if self.boxes_touch(robot_box, bounds):
                    self.collected.add(second)
                    self._pickups[second] = elapsed
                    self._drop_item(("coin", second))

        for second, picked_at in list(self._pickups.items()):
            age = elapsed - picked_at
            if not 0.0 <= age < self.PICKUP_SECONDS:
                del self._pickups[second]
                continue
            cx, cy = x_px(second), y_px(profile_y(second))
            spread = 12 + age * 44
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                x, y = cx + dx * spread, cy + dy * spread
                self._draw_item(("pickup", second, dx, dy), "rectangle", x - 2, y - 2, x + 2, y + 2,
                                fill="#ffe8a3", outline="", tags=("rah_pickup",))

        score = self._draw_item("score", "text", right_edge - 2, 17,
                               anchor="e", text=f"COINS {len(self.collected)} / {total}",
                               fill="#ffe39a", font=("Consolas", 11, "bold"), tags=("rah_score",))
        icon_x = c.bbox(score)[0] - self._photos["coin"].width() / 2 - 8
        self._draw_item("score_icon", "image", icon_x, 17, image=self._photos["coin"], tags=("rah_score_icon",))
        if not live_data:
            self._draw_item("waiting", "text", x_px(elapsed), 52, text="Waiting for live LC data",
                            fill="#a6b4c4", font=("Segoe UI", 10), tags=("rah_waiting",))
        self._finish_frame()

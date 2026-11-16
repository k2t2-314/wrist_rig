"""Themes of the RAH game on the subject display.

A theme is a plain dictionary: the colours of the drawn elements ("palette"), the leaderboard
palette, the character sprites (gui/assets/rah/<robot_prefix>_<1..4>.png, one per mood), the
collectible ("bean" = coffee bean drawn in code, otherwise gui/assets/rah/<collectible>.png),
an optional full-canvas background picture, the sticker shown on the leaderboard for a sticker
run (config.RAH_STICKER_FRACTION of the collectibles taken), the nouns used in texts and a
star-field switch. "coffee" is the final game; "alien" is kept as an option.
``active()`` returns the theme chosen in config.RAH_THEME; ``set_active(name)`` switches at run
time (the RAH tab has a selector). Everything that draws reads the theme at draw time, so a
switch takes effect at once. Scores, markers, triggers and timing are the same in every theme.
"""

try:
    from config import RAH_THEME as _DEFAULT_THEME
except ImportError:  # importable without the serial_manager config
    _DEFAULT_THEME = "alien"

FONT_FAMILY = "Bahnschrift"   # DIN-style typeface that ships with Windows 10+

THEMES = {
    "coffee": {
        "title": "Coffee Run",
        "subtitle": "Wrist Rig  ·  Coffee Run  ·  all complete runs add up",
        "noun": ("bean", "beans"),
        "instruction": "Collect the beans",
        "robot_prefix": "coffee_robot",
        "collectible": "bean",            # drawn in code (sprites.bean_image)
        "background_image": None,
        "perfect_sprite": "coffee_robot_winner",  # gui/assets/rah/coffee_robot_winner.png: the robot with its cup (sticker)
        "stars": False,
        "robot_px": (44, 96),                     # body height limits (13.5 % of the plot in between; the sprite has no feet)
        "palette": {
            # Light, sporty: light grey background, charcoal trajectory and text, burnt-orange accents.
            "bg": (235, 237, 240),
            "negative": (165, 70, 60),
            "baseline": (85, 90, 100),
            "path": (38, 42, 50),
            "path_glow": (196, 200, 206),
            "bean": (150, 86, 40),
            "bean_light": (212, 142, 82),
            "bean_dark": (58, 30, 12),
            "sparkle": (230, 130, 40),
            "top_band": (235, 150, 50),
            "top_line": (215, 110, 25),
            "trail": (150, 155, 165),
            "countdown": (38, 42, 50),
            "go": (225, 95, 30),
            "text": (35, 39, 46),
            "text_dim": (118, 124, 134),
            "star": (0, 0, 0),
            "star_bright": (0, 0, 0),
        },
        "blend": {"frame": 70, "negative": 40, "top_band": 60},
        "leaderboard": {
            "background": "#e9ebee", "panel": "#f5f6f8", "row_a": "#dfe3e8", "row_b": "#eef0f3",
            "header": "#cfd4da", "line": "#aeb4bc", "text": "#23272e", "muted": "#646b75",
            "teal": "#c8641a", "gold": "#c98a0c", "silver": "#7f858e", "bronze": "#a9623a",
            "you_fill": "#f3e1d0", "green": "#2a8a5a", "chip_text": "#ffffff",
        },
    },
    "alien": {
        # The user's artwork: a green alien collects coffee beans in front of a window on the night sky.
        "title": "Alien Coffee Run",
        "subtitle": "Wrist Rig  ·  Alien Coffee Run  ·  all complete runs add up",
        "noun": ("bean", "beans"),
        "instruction": "Collect the coffee beans",
        "robot_prefix": "alien",                  # gui/assets/rah/alien_1..4.png: eyes shut, narrow, half open, wide open
        "collectible": "coffee_bean",             # gui/assets/rah/coffee_bean.png
        "background_image": "alien_background",   # gui/assets/rah/alien_background.png, scaled to cover the canvas
        # During the game (and the countdown and practice) the picture is washed out: blended
        # this much toward a light slate so that the moon, stars and window frame stay faint
        # behind the path and the beans (the user found the full picture distracting). 0 = the
        # plain picture, 1 = a flat colour. The leaderboard keeps its own dimmed version.
        "background_fade": ((152, 162, 180), 0.60),
        "perfect_sprite": "alien_perfect",        # gui/assets/rah/alien_perfect.png: the alien with its coffee
        "stars": False,                           # the picture has its own stars and moon
        "robot_px": (32, 64),                     # compact: about 1.7 x the bean, so the pickup point is obvious
        "anchor_x": 0.84,                         # the mouth (fraction of the sprite width) sits on "now": beans vanish there
        "palette": {
            # Drawn over the washed-out picture: moonlight-cream path with a warm glow, light
            # text, a slightly darker band below the baseline. "bg" is the window chrome around
            # the canvas and the tone the negative band is blended from (the faded sky's tone).
            "bg": (104, 116, 138),
            "negative": (10, 16, 40),
            "baseline": (210, 218, 232),
            "path": (250, 240, 222),
            "path_glow": (255, 226, 170),
            "bean": (150, 86, 40),
            "bean_light": (212, 142, 82),
            "bean_dark": (58, 30, 12),
            "sparkle": (255, 226, 170),
            "top_band": (255, 200, 120),
            "top_line": (255, 214, 140),
            "trail": (170, 180, 200),
            "countdown": (250, 240, 222),
            "go": (255, 170, 80),
            "text": (248, 244, 236),
            "text_dim": (222, 228, 238),
            "star": (0, 0, 0),
            "star_bright": (0, 0, 0),
        },
        "blend": {"frame": 90, "negative": 90, "top_band": 60},
        "leaderboard": {
            # Over the dimmed window picture: slate-blue cards, cyan accents (the antennae), gold points.
            "background": "#1b2a44", "panel": "#223656", "row_a": "#26395a", "row_b": "#1f3150",
            "header": "#2b4168", "line": "#3f5680", "text": "#eef2f8", "muted": "#a4b4cf",
            "teal": "#5ee3ff", "gold": "#ffd27a", "silver": "#c3cbd8", "bronze": "#d39a6a",
            "you_fill": "#2a4470", "green": "#8ff0b4", "chip_text": "#0d1526",
        },
    },
}

_active_name = _DEFAULT_THEME if _DEFAULT_THEME in THEMES else "alien"
_listeners = []


def names():
    return list(THEMES)


def active_name():
    return _active_name


def active():
    return THEMES[_active_name]


def on_change(callback):
    """Register a callback(theme_name) run after every switch (sprite caches use it)."""
    _listeners.append(callback)


def set_active(name):
    global _active_name
    name = str(name)
    if name not in THEMES:
        raise ValueError(f"Unknown theme {name!r}; choose one of {', '.join(THEMES)}")
    if name == _active_name:
        return
    _active_name = name
    for callback in list(_listeners):
        try:
            callback(name)
        except Exception:
            pass

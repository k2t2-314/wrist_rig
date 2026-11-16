"""Sprites and colours of the subject display, for the active theme (gui/themes.py).

The character comes from gui/assets/rah/<robot_prefix>_<1..4>.png of the theme (four mood
states, same size, body centred). The collectible is the coffee bean drawn with Pillow at 4x and
downsampled, or the theme's sprite file (gui/assets/rah/<collectible>.png). A theme may have a
full-canvas background picture (``background_image``, scaled once per canvas size and kept as one
canvas image item) and a sprite for a perfect run (``perfect_image``). Images are cached per
theme and size; ``photo(image, canvas)`` returns (and keeps) the Tk PhotoImage for a canvas.
``THEME`` and the derived colours below follow the active theme: they are refreshed in place on
every switch, so callers read them at draw time.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

from gui import themes

try:
    from config import RAH_PICKUP_BODY_FRACTION
except ImportError:  # importable without the serial_manager config
    RAH_PICKUP_BODY_FRACTION = 1.0

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "rah"
ROBOT_STATES = 4
# The pickup body and the pickup point of every mood are taken from this one face (the wide
# awake one, which the character is sized for), so the collect zone is identical whatever face
# the character shows; the four faces differ only in eyes and mouth.
PICKUP_REFERENCE_STATE = ROBOT_STATES
# The part of the character that collects a bean: its body shrunk about the pickup point to
# this fraction (1.0 = the whole sprite; 0.9 = the outer 10 % of the body does not count).
PICKUP_BODY_FRACTION = max(0.05, min(1.0, float(RAH_PICKUP_BODY_FRACTION)))

THEME = {}   # palette of the active theme (see _refresh)

# One typeface everywhere on the subject display: Bahnschrift (DIN-style, ships with Windows 10+).
FONT_HUD = ("Bahnschrift", 16, "bold")
FONT_LABEL = ("Bahnschrift", 12)
FONT_TOP = ("Bahnschrift", 16, "bold")
FONT_COUNTDOWN = ("Bahnschrift", 96, "bold")
FONT_COUNTDOWN_LABEL = ("Bahnschrift", 30, "bold")

NEGATIVE_AXIS_FRACTION = 0.12   # plots extend this fraction of their positive range below zero
SCALE = 4
_IMAGES = {}
_PHOTOS = {}
_SOURCES = {}

BACKGROUND = FRAME = NEGATIVE_FILL = TOP_BAND_FILL = "#000000"   # set by _refresh()


def hex_colour(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb[:3])


def blend(base, tint, alpha):
    """``tint`` laid over ``base`` with ``alpha`` (0-255), as an RGB tuple."""
    a = alpha / 255.0
    return tuple(int(round(b * (1.0 - a) + t * a)) for b, t in zip(base[:3], tint[:3]))


def _refresh(_name=None):
    """Load the active theme's palette into THEME and the derived colours."""
    global BACKGROUND, FRAME, NEGATIVE_FILL, TOP_BAND_FILL
    theme = themes.active()
    THEME.clear()
    THEME.update(theme["palette"])
    alphas = theme.get("blend", {})
    BACKGROUND = hex_colour(THEME["bg"])
    FRAME = hex_colour(blend(THEME["bg"], THEME["baseline"], alphas.get("frame", 70)))
    NEGATIVE_FILL = hex_colour(blend(THEME["bg"], THEME["negative"], alphas.get("negative", 40)))
    TOP_BAND_FILL = hex_colour(blend(THEME["bg"], THEME["top_band"], alphas.get("top_band", 60)))


_refresh()
themes.on_change(_refresh)


def _rgba(colour, alpha=255):
    return tuple(int(v) for v in colour[:3]) + (int(alpha),)


def _cached(key, build):
    key = (themes.active_name(),) + tuple(key)
    if key not in _IMAGES:
        if len(_IMAGES) > 96:
            _IMAGES.clear()
            _PHOTOS.clear()
        _IMAGES[key] = build()
    return _IMAGES[key]


def photo(image, master):
    """Tk PhotoImage of a cached PIL image, kept alive for the given canvas."""
    key = (id(image), str(master))
    if key not in _PHOTOS:
        _PHOTOS[key] = ImageTk.PhotoImage(image, master=master)
    return _PHOTOS[key]


def _source(name):
    """The RGBA source image gui/assets/rah/<name>.png, loaded once."""
    if name not in _SOURCES:
        with Image.open(ASSET_DIR / f"{name}.png") as source:
            _SOURCES[name] = source.convert("RGBA")
    return _SOURCES[name]


_CENTROIDS = {}


def _body_centroid(name):
    """(x, y) of the opaque-pixel centroid of a sprite as fractions of its width and height.

    The character's "middle": for the alien the antennae stick up above the body, so its body
    centre is below the middle of the picture; the game places this point on the force value."""
    if name not in _CENTROIDS:
        source = _source(name)
        alpha = source.getchannel("A").resize((96, 96), Image.Resampling.BOX)
        total = sx = sy = 0.0
        for index, value in enumerate(alpha.getdata()):
            if value:
                total += value
                sx += value * (index % 96 + 0.5)
                sy += value * (index // 96 + 0.5)
        _CENTROIDS[name] = (sx / total / 96.0, sy / total / 96.0) if total else (0.5, 0.5)
    return _CENTROIDS[name]


def robot_anchor(height, state=1):
    """(dx, dy) in px from the centre of ``robot_image(height, state)`` to the character's pickup
    point: vertically its body centre, horizontally its mouth when the theme gives ``anchor_x``
    (a fraction of the sprite width), else the body centre. Draw the image centred at
    (x - dx, y - dy) to put that point on (x, y). The body centre is that of the reference
    face (PICKUP_REFERENCE_STATE) for every mood, so all faces are drawn and judged alike."""
    theme = themes.active()
    image = robot_image(height, state)
    cx, cy = _body_centroid(f"{theme['robot_prefix']}_{PICKUP_REFERENCE_STATE}")
    anchor_x = theme.get("anchor_x")
    if anchor_x is not None:
        cx = float(anchor_x)
    return (cx - 0.5) * image.width, (cy - 0.5) * image.height


def robot_image(height, state=1):
    """The character, ``height`` px tall, in mood ``state`` (1 .. 4; asleep to wide awake for the
    coffee robot, angry to delighted for the alien)."""
    H = max(16, int(round(height)))
    state = max(1, min(ROBOT_STATES, int(state)))
    prefix = themes.active()["robot_prefix"]

    def build():
        source = _source(f"{prefix}_{state}")
        W = max(1, round(source.width * H / source.height))
        return source.resize((W, H), Image.Resampling.LANCZOS)

    return _cached(("robot", prefix, H, state), build)


ICON_BEAN_TILT = -28.0   # rotation of the drawn bean when no line direction is given (icons)


def bean_image(size, angle=None):
    """The collectible, fitted into ``size`` x ``size`` px: the coffee bean (brown ellipse with
    a dark crease and a soft highlight, drawn in code) or the theme's sprite file. ``angle`` is
    the direction of the target line on screen in degrees (0 = flat, positive = rising to the
    right): the drawn bean then lies along the line, as the beans on the path do; None gives
    the classic tilt used for icons. A sprite file is never rotated."""
    D = max(8, int(round(size)))
    collectible = themes.active()["collectible"]
    rotation = ICON_BEAN_TILT if angle is None else 90.0 + float(angle)
    if collectible != "bean":
        def build_sprite():
            source = _source(collectible)
            scale = D / float(max(source.width, source.height))
            W = max(1, round(source.width * scale))
            H = max(1, round(source.height * scale))
            return source.resize((W, H), Image.Resampling.LANCZOS)

        return _cached(("collectible", collectible, D), build_sprite)

    def build():
        s = D * SCALE
        body = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(body)
        T = THEME
        box = (0.22 * s, 0.08 * s, 0.78 * s, 0.92 * s)
        d.ellipse(box, fill=_rgba(T["bean"]), outline=_rgba(T["bean_dark"]), width=max(1, int(0.03 * s)))
        highlight = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(highlight).ellipse((0.28 * s, 0.14 * s, 0.58 * s, 0.6 * s), fill=_rgba(T["bean_light"], 170))
        highlight = highlight.filter(ImageFilter.GaussianBlur(0.05 * s))
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).ellipse((box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2), fill=255)
        body.paste(highlight, (0, 0), ImageChops.multiply(mask, highlight.getchannel("A")))
        d = ImageDraw.Draw(body)
        points = [(0.5 * s + 0.05 * s * math.sin(k / 10.0 * 2.0 * math.pi - 0.5),
                   box[1] + 0.08 * s + (box[3] - box[1] - 0.16 * s) * k / 10.0) for k in range(11)]
        d.line(points, fill=_rgba(T["bean_dark"]), width=max(1, int(0.07 * s)), joint="curve")
        # The bean is drawn upright; rotated by 90 deg it lies flat, by 90 + angle along the line.
        bean = body.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=False)
        return bean.resize((D, D), Image.Resampling.LANCZOS)

    return _cached(("bean", D, round(rotation, 2)), build)


_TOUCH = {}


def _solid_mask(image, threshold=96):
    """1-bit style mask (L, 0/255) of the pixels of an RGBA sprite that count as solid (alpha
    above ``threshold``), so that feathered edges do not count as touching."""
    return image.getchannel("A").point(lambda a: 255 if a > threshold else 0, mode="L")


def pickup_mask(robot_height, state, fraction=None):
    """Mask (L, 0/255, the size of ``robot_image``) of the part of the character that collects
    a bean: its solid pixels shrunk about the pickup point to ``fraction`` of their size
    (PICKUP_BODY_FRACTION by default; 1.0 = the whole character). The body is always that of
    the reference face (PICKUP_REFERENCE_STATE); ``state`` is accepted for compatibility."""
    fraction = PICKUP_BODY_FRACTION if fraction is None else max(0.05, min(1.0, float(fraction)))
    robot = robot_image(robot_height, PICKUP_REFERENCE_STATE)
    mask = _solid_mask(robot)
    if fraction >= 0.999:
        return mask
    dx, dy = robot_anchor(robot_height, PICKUP_REFERENCE_STATE)
    ax = robot.width / 2.0 + dx
    ay = robot.height / 2.0 + dy
    small = mask.resize((max(1, round(robot.width * fraction)), max(1, round(robot.height * fraction))),
                        Image.Resampling.LANCZOS).point(lambda v: 255 if v >= 128 else 0)
    shrunk = Image.new("L", robot.size, 0)
    shrunk.paste(small, (int(round(ax - ax * fraction)), int(round(ay - ay * fraction))))
    return shrunk


def touch_range(robot_height, state, coin_px, fraction=None, angle=None):
    """(lo, hi) in px: the vertical offsets of a bean's centre from the character's pickup point
    (positive = the bean lower on the canvas) at which the bean touches the character's pickup
    part (``pickup_mask``: the body shrunk to ``fraction``, PICKUP_BODY_FRACTION by default)
    when the bean is horizontally centred on the pickup point, i.e. the moment the bean reaches
    the character. ``angle`` is the bean's orientation (the line direction, see ``bean_image``).
    Every offset inside [lo, hi] collects the bean; so with the pickup point ON the force
    value, hi is how far the character may be above the target and -lo how far below it and
    still collect it. Solid pixels only (alpha above 96). The same for every mood: the pickup
    body is the reference face's (PICKUP_REFERENCE_STATE), ``state`` is accepted for
    compatibility. Cached per theme, character size, bean size, fraction and angle."""
    fraction = PICKUP_BODY_FRACTION if fraction is None else max(0.05, min(1.0, float(fraction)))
    key = (themes.active_name(), int(robot_height), int(coin_px), round(fraction, 3),
           None if angle is None else round(float(angle), 2))
    if key not in _TOUCH:
        robot = robot_image(robot_height, PICKUP_REFERENCE_STATE)
        bean = bean_image(coin_px, angle)
        dx, dy = robot_anchor(robot_height, PICKUP_REFERENCE_STATE)
        ax = robot.width / 2.0 + dx
        ay = robot.height / 2.0 + dy
        robot_mask = pickup_mask(robot_height, PICKUP_REFERENCE_STATE, fraction)
        bean_mask = _solid_mask(bean)
        left = int(round(ax - bean.width / 2.0))
        span = robot.height + bean.height
        hits = []
        for d in range(-span, span + 1):
            top = int(round(ay + d - bean.height / 2.0))
            if top >= robot.height or top + bean.height <= 0:
                continue
            canvas = Image.new("L", robot.size, 0)
            canvas.paste(bean_mask, (left, top))
            if ImageChops.multiply(canvas, robot_mask).getbbox() is not None:
                hits.append(d)
        _TOUCH[key] = (min(hits), max(hits)) if hits else (0, 0)
    return _TOUCH[key]


def background_image(width, height, faded=True):
    """The theme's full-canvas background picture, scaled to cover ``width`` x ``height`` px
    (centre crop) as an RGB image; None for a theme without one. Cached per size.

    With ``faded`` (the game, countdown and practice) the picture is washed out as the theme's
    ``background_fade`` = (colour, amount 0..1) says, so that it stays behind the path and the
    beans instead of competing with them; the leaderboard asks for the plain picture and dims
    it in its own way."""
    theme = themes.active()
    name = theme.get("background_image")
    if not name:
        return None
    width, height = max(1, int(width)), max(1, int(height))
    fade = theme.get("background_fade") if faded else None
    if fade:
        colour, amount = tuple(int(v) for v in fade[0][:3]), max(0.0, min(1.0, float(fade[1])))
        fade = (colour, round(amount, 3)) if amount > 0 else None

    def build():
        source = _source(name)
        scale = max(width / float(source.width), height / float(source.height))
        scaled = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))),
                               Image.Resampling.LANCZOS)
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        picture = scaled.crop((left, top, left + width, top + height)).convert("RGB")
        if fade:
            picture = Image.blend(picture, Image.new("RGB", picture.size, fade[0]), fade[1])
        return picture

    return _cached(("background", name, width, height, fade), build)


def perfect_image(height):
    """The theme's sprite for a perfect run (every collectible taken), ``height`` px tall; None
    for a theme without one."""
    name = themes.active().get("perfect_sprite")
    if not name:
        return None
    H = max(16, int(round(height)))

    def build():
        source = _source(name)
        W = max(1, round(source.width * H / source.height))
        return source.resize((W, H), Image.Resampling.LANCZOS)

    return _cached(("perfect", name, H), build)


def star_field(width, height):
    """A sparse, static star field for themes that have one: [(x, y, radius, colour)] inside a
    ``width`` x ``height`` area, identical for every call with the same size (fixed seed); []
    for the other themes. Kept faint and few so it never competes with the path."""
    if not themes.active().get("stars"):
        return []
    width, height = max(1, int(width)), max(1, int(height))

    def build():
        rng = random.Random(20261027)
        count = max(24, min(90, int(width * height / 24000)))   # about 60 at 1600 x 900
        stars = []
        for _ in range(count):
            x = rng.uniform(0.02, 0.98) * width
            y = rng.uniform(0.03, 0.97) * height
            radius = rng.choice((1.0, 1.0, 1.0, 1.5, 2.0))
            colour = hex_colour(THEME["star_bright"] if rng.random() < 0.25 else THEME["star"])
            stars.append((x, y, radius, colour))
        return stars

    return _cached(("stars", width, height), build)

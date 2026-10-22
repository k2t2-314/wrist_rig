"""Render one static RAH leaderboard snapshot, then optionally display it.

Run ``python leaderboard.py`` next to run.py. The score TXT is read once;
the saved PNG and its standalone window never refresh in the background.
Only Pillow and the existing score reader are needed; no devices are opened.
"""

import argparse
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from recording.rah_score_store import RAHScoreStore


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "gui" / "assets" / "rah"
PALETTE = {
    "background": "#0b1526", "shadow": "#050b15", "frame": "#24455c",
    "edge": "#5dd9c9", "panel": "#101f32", "alternate": "#14263b",
    "muted": "#91a9bd", "white": "#e7f4f4", "gold": "#ffd16c",
    "silver": "#bbd6e9", "bronze": "#dd9c74", "winner": "#243338",
}

# Original 5 x 7 bitmap glyphs keep the artwork portable and avoid font installs.
# Lowercase is distinct so Player IDs retain their original letter case.
GLYPHS = {
    " ": "00000/00000/00000/00000/00000/00000/00000",
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01111/10000/10000/10111/10001/10001/01111",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "11111/00100/00100/00100/00100/00100/11111",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/11001/10101/10011/10001/10001/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/10101/01010",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
    "a": "00000/00000/01110/00001/01111/10001/01111",
    "b": "10000/10000/10110/11001/10001/10001/11110",
    "c": "00000/00000/01111/10000/10000/10000/01111",
    "d": "00001/00001/01101/10011/10001/10001/01111",
    "e": "00000/00000/01110/10001/11111/10000/01111",
    "f": "00110/01001/01000/11100/01000/01000/01000",
    "g": "00000/01111/10001/10001/01111/00001/01110",
    "h": "10000/10000/10110/11001/10001/10001/10001",
    "i": "00100/00000/01100/00100/00100/00100/01110",
    "j": "00010/00000/00110/00010/00010/10010/01100",
    "k": "10000/10000/10010/10100/11000/10100/10010",
    "l": "01100/00100/00100/00100/00100/00100/01110",
    "m": "00000/00000/11010/10101/10101/10101/10101",
    "n": "00000/00000/10110/11001/10001/10001/10001",
    "o": "00000/00000/01110/10001/10001/10001/01110",
    "p": "00000/00000/11110/10001/11110/10000/10000",
    "q": "00000/00000/01111/10001/01111/00001/00001",
    "r": "00000/00000/10110/11001/10000/10000/10000",
    "s": "00000/00000/01111/10000/01110/00001/11110",
    "t": "01000/01000/11100/01000/01000/01001/00110",
    "u": "00000/00000/10001/10001/10001/10011/01101",
    "v": "00000/00000/10001/10001/10001/01010/00100",
    "w": "00000/00000/10001/10001/10101/10101/01010",
    "x": "00000/00000/10001/01010/00100/01010/10001",
    "y": "00000/00000/10001/10001/01111/00001/01110",
    "z": "00000/00000/11111/00010/00100/01000/11111",
    "0": "01110/10001/10011/10101/11001/10001/01110",
    "1": "00100/01100/00100/00100/00100/00100/01110",
    "2": "01110/10001/00001/00010/00100/01000/11111",
    "3": "11110/00001/00001/01110/00001/00001/11110",
    "4": "00010/00110/01010/10010/11111/00010/00010",
    "5": "11111/10000/10000/11110/00001/00001/11110",
    "6": "01110/10000/10000/11110/10001/10001/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "8": "01110/10001/10001/01110/10001/10001/01110",
    "9": "01110/10001/10001/01111/00001/00001/01110",
    ".": "00000/00000/00000/00000/00000/00110/00110",
    ",": "00000/00000/00000/00000/00110/00100/01000",
    ":": "00000/00110/00110/00000/00110/00110/00000",
    ";": "00000/00110/00110/00000/00110/00100/01000",
    "-": "00000/00000/00000/11111/00000/00000/00000",
    "_": "00000/00000/00000/00000/00000/00000/11111",
    "/": "00001/00001/00010/00100/01000/10000/10000",
    "\\": "10000/10000/01000/00100/00010/00001/00001",
    "%": "11001/11010/00100/00100/01000/10110/00110",
    "!": "00100/00100/00100/00100/00100/00000/00100",
    "?": "01110/10001/00001/00010/00100/00000/00100",
    "=": "00000/00000/11111/00000/11111/00000/00000",
    "+": "00000/00100/00100/11111/00100/00100/00000",
    "|": "00100/00100/00100/00100/00100/00100/00100",
    "#": "01010/11111/01010/01010/11111/01010/00000",
    "@": "01110/10001/10111/10101/10111/10000/01111",
    "(": "00010/00100/01000/01000/01000/00100/00010",
    ")": "01000/00100/00010/00010/00010/00100/01000",
    "[": "01110/01000/01000/01000/01000/01000/01110",
    "]": "01110/00010/00010/00010/00010/00010/01110",
    "<": "00010/00100/01000/10000/01000/00100/00010",
    ">": "01000/00100/00010/00001/00010/00100/01000",
    "'": "00100/00100/00000/00000/00000/00000/00000",
    '"': "01010/01010/00000/00000/00000/00000/00000",
    "*": "00000/10101/01110/11111/01110/10101/00000",
    "&": "01100/10010/10100/01000/10101/10010/01101",
    "$": "00100/01111/10100/01110/00101/11110/00100",
    "^": "00100/01010/10001/00000/00000/00000/00000",
    "`": "01000/00100/00000/00000/00000/00000/00000",
    "~": "00000/00000/01001/10110/00000/00000/00000",
    "{": "00010/00100/00100/01000/00100/00100/00010",
    "}": "01000/00100/00100/00010/00100/00100/01000",
}


class PixelText:
    def __init__(self, image):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.fallback = None
        self.fallback_masks = {}

    @staticmethod
    def advance(char):
        return 12 if unicodedata.east_asian_width(char) in ("W", "F") else 6

    def width(self, text, scale=1):
        return max(0, sum(self.advance(char) for char in str(text)) - 1) * scale

    def _fallback_mask(self, char):
        if char in self.fallback_masks:
            return self.fallback_masks[char]
        if self.fallback is None:
            for name in ("msyh.ttc", "DejaVuSans.ttf", "arial.ttf"):
                try:
                    self.fallback = ImageFont.truetype(name, 14)
                    break
                except OSError:
                    pass
            else:
                self.fallback = ImageFont.load_default()
        box = self.fallback.getbbox(char)
        mask = Image.new("L", (max(1, box[2]-box[0]), max(1, box[3]-box[1])))
        ImageDraw.Draw(mask).text((-box[0], -box[1]), char, font=self.fallback, fill=255)
        mask.thumbnail((self.advance(char)-1, 7), Image.Resampling.NEAREST)
        mask = mask.point(lambda value: 255 if value >= 96 else 0)
        self.fallback_masks[char] = mask
        return mask

    def text(self, xy, text, color, scale=1, anchor="left", max_width=None):
        text = str(text)
        if max_width is not None:
            while scale > 1 and self.width(text, scale) > max_width:
                scale -= 1
            if self.width(text, scale) > max_width:
                while text and self.width(text + "...", scale) > max_width:
                    text = text[:-1]
                text += "..."
        x, y = xy
        if anchor == "right":
            x -= self.width(text, scale)
        elif anchor == "center":
            x -= self.width(text, scale) // 2
        for char in text:
            if char in GLYPHS:
                for row, bits in enumerate(GLYPHS[char].split("/")):
                    for col, bit in enumerate(bits):
                        if bit == "1":
                            self.draw.rectangle((x+col*scale, y+row*scale,
                                                 x+(col+1)*scale-1, y+(row+1)*scale-1), fill=color)
            else:
                mask = self._fallback_mask(char)
                mask = mask.resize((mask.width*scale, mask.height*scale), Image.Resampling.NEAREST)
                self.image.paste(color, (x, y), mask)
            x += self.advance(char) * scale


def accuracy(record):
    return Fraction(record.collected, record.total) if record.total else Fraction(0)


def rank_players(records):
    """Use one complete run per player: points, accuracy, then latest TXT row."""
    best = {}
    for index, record in enumerate(records):
        key = (record.score, accuracy(record), index)
        if record.player_id not in best or key > best[record.player_id][0]:
            best[record.player_id] = (key, record)
    return [record for key, record in sorted(best.values(), key=lambda item: item[0], reverse=True)]


def stepped_box(draw, box, fill, outline):
    x0, y0, x1, y1 = box
    points = [(x0+4, y0), (x1-4, y0), (x1-4, y0+4), (x1, y0+4),
              (x1, y1-4), (x1-4, y1-4), (x1-4, y1), (x0+4, y1),
              (x0+4, y1-4), (x0, y1-4), (x0, y0+4), (x0+4, y0+4)]
    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=outline, width=1)


def paste_sprite(image, name, xy, height):
    with Image.open(ASSETS / (name + ".png")) as source:
        sprite = source.convert("RGBA")
    bounds = sprite.getchannel("A").getbbox()
    if bounds:
        sprite = sprite.crop(bounds)
    width = max(1, round(sprite.width * height / sprite.height))
    sprite = sprite.resize((width, height), Image.Resampling.BOX)
    image.paste(sprite, xy, sprite)


def render_leaderboard(records, limit=10, rendered_at=None):
    """Build a pixel-aligned RGB image without touching scores or opening Tk."""
    if not 1 <= limit <= 50:
        raise ValueError("Row limit must be between 1 and 50.")
    records = list(records)
    ranking = rank_players(records)
    visible = ranking[:limit]
    row_count = max(5, len(visible))
    table_top, row_height = 200, 28
    bottom = table_top + row_count * row_height
    width, height = 640, bottom + 62
    c = PALETTE
    image = Image.new("RGB", (width, height), c["background"])
    draw = ImageDraw.Draw(image)
    font = PixelText(image)

    # Deliberately sparse background: the table and characters get the attention.
    for x, y in ((20, 32), (610, 57), (22, height-62), (608, height-38)):
        draw.rectangle((x, y, x+2, y+2), fill=c["frame"])
    stepped_box(draw, (10, 10, 630, height-10), c["panel"], c["frame"])
    draw.line((28, 14, 192, 14), fill=c["edge"], width=2)
    draw.line((450, height-14, 612, height-14), fill=c["edge"], width=2)
    for x, y in ((18, 18), (618, 18), (18, height-20), (618, height-20)):
        draw.rectangle((x, y, x+2, y+2), fill=c["edge"])

    font.text((32, 30), "WRIST RIG  /  RAH ARCADE", c["edge"])
    font.text((34, 49), "LEADERBOARD", c["shadow"], scale=4)
    font.text((32, 47), "LEADERBOARD", c["white"], scale=4)
    font.text((33, 85), "SMALL ROBOT. BIG EFFORT.", c["muted"])
    draw.line((456, 84, 591, 84), fill=c["frame"], width=1)
    paste_sprite(image, "robot", (470, 26), 65)
    paste_sprite(image, "coin", (559, 46), 26)
    font.text((581, 32), "+1", c["gold"])

    # Three fixed-width summary cards share the same baseline.
    stats = [(32, "PLAYERS", len(ranking), c["edge"]),
             (228, "SAVED BESTS", len(records), c["white"]),
             (424, "BEST SCORE", ranking[0].score if ranking else 0, c["gold"])]
    for x, label, value, color in stats:
        stepped_box(draw, (x, 105, x+184, 143), c["background"], c["frame"])
        font.text((x+10, 113), label, c["muted"])
        font.text((x+172, 123), value, color, scale=2, anchor="right", max_width=163)

    font.text((32, 161), "PERSONAL BESTS", c["white"])
    font.text((608, 161), "1 COIN = 1 POINT", c["gold"], anchor="right")
    draw.rectangle((32, 178, 607, 196), fill=c["frame"])
    for xy, label, anchor in (((45, 184), "RANK", "left"),
                               ((96, 184), "PLAYER ID", "left"),
                               ((398, 184), "COINS", "right"),
                               ((504, 184), "HIT RATE", "right"),
                               ((594, 184), "POINTS", "right")):
        font.text(xy, label, c["white"], anchor=anchor)

    medals = (c["gold"], c["silver"], c["bronze"])
    for index in range(row_count):
        y = table_top + index * row_height
        fill = c["winner"] if index == 0 and visible else c["alternate"] if index % 2 == 0 else c["panel"]
        draw.rectangle((32, y, 607, y+row_height-3), fill=fill)
        if index >= len(visible):
            if visible:
                font.text((60, y+9), "--", c["frame"], anchor="center")
            continue
        record = visible[index]
        color = medals[index] if index < 3 else c["muted"]
        if index < 3:
            stepped_box(draw, (39, y+3, 80, y+21), c["background"], color)
        font.text((60, y+6), f"{index+1:02}", color, scale=2, anchor="center")
        font.text((96, y+6), record.player_id, c["white"], scale=2, max_width=200)
        font.text((398, y+6), f"{record.collected}/{record.total}", c["muted"],
                  scale=2, anchor="right", max_width=90)
        font.text((504, y+3), f"{record.percent:.1f}%", c["edge"],
                  anchor="right", max_width=94)
        draw.rectangle((422, y+16, 503, y+19), fill=c["background"])
        filled = round(82 * accuracy(record))
        if filled:
            draw.rectangle((422, y+16, 421+filled, y+19), fill=c["edge"])
        font.text((594, y+6), record.score, c["gold"], scale=2, anchor="right", max_width=76)
    if not ranking:
        font.text((320, table_top+44), "NO SCORES YET", c["gold"], scale=2, anchor="center")
        font.text((320, table_top+69), "FINISH A RAH RUN WITH A PLAYER ID", c["muted"], anchor="center")
        font.text((320, table_top+85), "YOUR FIRST COIN STARTS THE STORY.", c["muted"], anchor="center")

    draw.line((32, bottom+7, 607, bottom+7), fill=c["frame"])
    font.text((32, bottom+18), "BEST RUN / PLAYER. TIES: HIT RATE, THEN LATEST RUN.", c["muted"])
    font.text((32, bottom+35), (rendered_at or datetime.now()).strftime("SNAPSHOT  %Y-%m-%d %H:%M"), c["edge"])
    font.text((608, bottom+35), f"TOP {len(visible)} / {len(ranking)}", c["muted"], anchor="right")
    return image.resize((width*2, height*2), Image.Resampling.NEAREST)


def show_snapshot(image):
    """Display the already-rendered image; no polling or redraw timer is used."""
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.withdraw()
    root.title("RAH Arcade - Leaderboard")
    scale = min(1.0, (root.winfo_screenwidth()-100) / image.width,
                (root.winfo_screenheight()-140) / image.height)
    display = image
    if scale < 1.0:
        display = image.resize((max(1, int(image.width*scale)), max(1, int(image.height*scale))),
                               Image.Resampling.NEAREST)
    photo = ImageTk.PhotoImage(display, master=root)
    root.configure(bg=PALETTE["background"])
    label = tk.Label(root, image=photo, borderwidth=0, highlightthickness=0)
    label.pack()
    root.resizable(False, False)
    root.bind("<Escape>", lambda _event: root.destroy())
    x = max(0, (root.winfo_screenwidth()-display.width)//2)
    y = max(0, (root.winfo_screenheight()-display.height)//2-20)
    root.geometry(f"{display.width}x{display.height}+{x}+{y}")
    root.deiconify()
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=ROOT / "rah_scores.txt",
                        help="Input TXT; defaults to rah_scores.txt beside this script.")
    parser.add_argument("--output", type=Path, default=ROOT / "leaderboard.png",
                        help="Output PNG; defaults to leaderboard.png beside this script.")
    parser.add_argument("--limit", type=int, default=10, help="Show the top 1-50 players (default: 10).")
    parser.add_argument("--no-show", action="store_true", help="Save the image without opening its window.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    if args.output.suffix.lower() != ".png":
        parser.error("--output must have a .png extension")
    if args.output.resolve() == args.scores.resolve():
        parser.error("The PNG output must not overwrite the score input.")
    try:
        records = RAHScoreStore(args.scores).read()
        image = render_leaderboard(records, limit=args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.output, format="PNG")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, f"Could not render leaderboard: {exc}\n")
    print(f"Saved {args.output.resolve()}")
    if not args.no_show:
        show_snapshot(image)
    return 0


if __name__ == "__main__":
    sys.exit(main())

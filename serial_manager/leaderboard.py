"""Render one RAH leaderboard snapshot as an image.

Run ``python leaderboard.py`` next to run.py to save leaderboard.png and open it in a
window. The score TXT is read once; the PNG and the window never refresh on their own.
The subject display imports ``render_leaderboard`` to show the standings after every
completed RAH run, sized to its own canvas. Only Pillow and the score reader are
needed; no device is opened and the score file is never written here.
"""

import argparse
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import sys

from PIL import Image, ImageColor, ImageDraw, ImageFont

from gui import sprites, themes
from gui.sprites import bean_image, robot_image
from recording.rah_score_store import RAHScoreStore, STICKER_FRACTION, earns_sticker


ROOT = Path(__file__).resolve().parent
BASE_WIDTH = 1600          # design width in px; every length below scales with the rendered width
MAX_STICKERS = 6           # stickers drawn one by one; more are shown as one sticker "x n"
STICKER_PCT = int(round(STICKER_FRACTION * 100))   # a run with at least this share of its beans earns a sticker


def palette():
    """Colours of the active game theme's leaderboard (gui/themes.py)."""
    return themes.active()["leaderboard"]


def nouns():
    """(singular, plural) name of the collectible in the active theme."""
    return tuple(themes.active()["noun"])
# (file, named variation or None): Bahnschrift (DIN-style, ships with Windows 10+) first.
FONT_FILES = {
    "bold": (("bahnschrift.ttf", "Bold"), ("segoeuib.ttf", None), ("arialbd.ttf", None), ("DejaVuSans-Bold.ttf", None)),
    "regular": (("bahnschrift.ttf", "Regular"), ("segoeui.ttf", None), ("arial.ttf", None), ("DejaVuSans.ttf", None)),
    "mono": (("bahnschrift.ttf", "SemiBold"), ("consolab.ttf", None), ("consola.ttf", None), ("DejaVuSansMono-Bold.ttf", None)),
}
_FONTS = {}


def font(size, weight="bold"):
    """A TrueType font of ``size`` px (Bahnschrift, else Segoe UI / Consolas); Pillow's default as a last resort."""
    size = max(8, int(round(size)))
    key = (weight, size)
    if key not in _FONTS:
        loaded = None
        for name, variation in FONT_FILES[weight]:
            try:
                loaded = ImageFont.truetype(name, size)
                if variation:
                    loaded.set_variation_by_name(variation)
                break
            except Exception:
                loaded = None
                continue
        if loaded is None:
            try:
                loaded = ImageFont.load_default(size=size)
            except TypeError:
                loaded = ImageFont.load_default()
        _FONTS[key] = loaded
    return _FONTS[key]


def fit_text(draw, text, fnt, max_width):
    """Shorten ``text`` with an ellipsis so that it fits in ``max_width`` px."""
    text = str(text)
    if draw.textlength(text, font=fnt) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=fnt) > max_width:
        text = text[:-1]
    return text + "..."


def accuracy(record):
    return Fraction(record.collected, record.total) if record.total else Fraction(0)


def rank_players(records):
    """One running total per player (all complete runs added up): points, hit rate, then the latest TXT row."""
    best = {}
    for index, record in enumerate(records):
        key = (record.score, accuracy(record), index)
        if record.player_id not in best or key > best[record.player_id][0]:
            best[record.player_id] = (key, record)
    return [record for key, record in sorted(best.values(), key=lambda item: item[0], reverse=True)]


def is_sticker_run(last_run):
    """True when the run that just finished collected at least STICKER_FRACTION of its beans."""
    if not last_run or last_run.get("collected") is None:
        return False
    return earns_sticker(int(last_run["collected"]), int(last_run.get("total") or 0))


def run_summary(last_run):
    """Banner text and colour for the run that just finished (``last_run`` comes from the GUI)."""
    c = palette()
    singular, plural = nouns()
    player = last_run.get("player_id") or "No Player ID"
    collected = last_run.get("collected")
    total = last_run.get("total") or 0
    note = last_run.get("note") or ""
    if collected is None:
        return ("Run complete.  " + note).strip(), c["muted"]
    percent = 100.0 * collected / total if total else 0.0
    base = f"{player}:  {collected} / {total} {plural} this run  ({percent:.0f} %)"
    sticker = is_sticker_run(last_run)
    if sticker:
        base += "   ·   WINNER RUN: +1 sticker!"
    if last_run.get("saved"):
        record = last_run.get("record") or last_run.get("best")   # the player's running total
        if record is not None:
            base += f"   ·   total {record.collected} / {record.total}"
            stickers = int(getattr(record, "perfect_runs", 0) or 0)
            if stickers:
                base += f"   ·   {stickers} sticker{'s' if stickers != 1 else ''}"
        if last_run.get("first"):
            base += "   ·   first run on the board!"
        return base, c["green"] if (sticker or last_run.get("first")) else c["gold"]
    return base + "   ·   " + (note or "not saved"), c["green"] if sticker else c["muted"]


def render_leaderboard(records, limit=10, rendered_at=None, highlight=None, last_run=None, size=None):
    """Build the leaderboard image without touching the score file or opening Tk.

    ``records`` are the players' running totals (collected / possible beans over all complete
    runs, plus their sticker count). ``size=None`` gives a 1600 px wide PNG whose height
    follows the number of rows. ``size=(W, H)`` fills exactly that many pixels (the subject
    display passes its canvas size) and shows fewer rows when they would not fit.
    ``highlight`` marks one Player ID with a YOU tag; a highlighted player outside the top rows
    is added below them with their true rank. ``last_run`` adds a banner about the finished
    run (its own collected/total, and the new running total under "record").
    """
    if not 1 <= limit <= 50:
        raise ValueError("Row limit must be between 1 and 50.")
    records = list(records)
    ranking = rank_players(records)
    highlight = (highlight or "").strip() or None
    rank_of = {record.player_id: index for index, record in enumerate(ranking)}

    if size is None:
        W, H = BASE_WIDTH, None
    else:
        W, H = max(320, int(size[0])), max(240, int(size[1]))
    s = W / BASE_WIDTH
    m = 44 * s
    table_top = 150 * s + (84 * s if last_run else 0)
    rows_top = table_top + 48 * s
    footer_h = 58 * s
    row_min, row_max = 34 * s, 80 * s

    def layout(rows_limit):
        visible = ranking[:rows_limit]
        extra = 2 if highlight in rank_of and rank_of[highlight] >= rows_limit else 0
        return visible, extra, max(5, len(visible) + extra)

    visible, extra, n_rows = layout(limit)
    if H is None:
        row_h = 72 * s
        H = int(round(rows_top + n_rows * row_h + footer_h))
    else:
        while rows_top + n_rows * row_min + footer_h > H and limit > 3:
            limit -= 1
            visible, extra, n_rows = layout(limit)
        row_h = max(row_min * 0.6, min(row_max, (H - rows_top - footer_h) / n_rows))

    c = palette()
    theme = themes.active()
    singular, plural = nouns()
    image = Image.new("RGB", (W, H), c["background"])
    backdrop = sprites.background_image(W, H, faded=False)
    if backdrop is not None:
        # The theme's picture, dimmed so that the cards and the text stay readable.
        dim = Image.new("RGBA", (W, H), ImageColor.getrgb(c["background"]) + (150,))
        image = Image.alpha_composite(backdrop.convert("RGBA"), dim).convert("RGB")
    draw = ImageDraw.Draw(image)

    # Title block with the game's character (after a sticker run: the theme's sticker, e.g.
    # the coffee robot with its cup) and the scoring rule.
    draw.text((m, 22 * s), "LEADERBOARD", font=font(64 * s, "bold"), fill=c["text"], anchor="la")
    draw.text((m + 4 * s, 100 * s), theme["subtitle"],
              font=font(24 * s, "regular"), fill=c["muted"], anchor="la")
    hero = sprites.perfect_image(132 * s) if is_sticker_run(last_run) else None
    if hero is None:
        hero = robot_image(112 * s, 4)
    image.paste(hero, (int(W - m - hero.width), int(8 * s)), hero)
    coin = bean_image(36 * s)
    rule_font = font(24 * s, "bold")
    rule_x = W - m - hero.width - 28 * s
    rule_text = f"1 {singular} = 1 point   ·   {STICKER_PCT} % of the {plural} = 1 sticker"
    draw.text((rule_x, 46 * s), rule_text, font=rule_font, fill=c["gold"], anchor="rm")
    rule_w = draw.textlength(rule_text, font=rule_font)
    image.paste(coin, (int(rule_x - rule_w - 12 * s - coin.width), int(46 * s - coin.height / 2)), coin)

    if last_run:
        text, colour = run_summary(last_run)
        top, bottom = 136 * s, 204 * s
        draw.rounded_rectangle((m, top, W - m, bottom), radius=12 * s, fill=c["panel"],
                               outline=colour, width=max(1, int(round(2 * s))))
        max_w = W - 2 * m - 44 * s
        for size in (30, 27, 24, 22):   # a long banner shrinks a little before it is shortened
            banner_font = font(size * s, "bold")
            if draw.textlength(text, font=banner_font) <= max_w:
                break
        draw.text((m + 22 * s, (top + bottom) / 2), fit_text(draw, text, banner_font, max_w),
                  font=banner_font, fill=colour, anchor="lm")

    # Column anchors shared by the header and the rows.
    x_rank = m + 48 * s
    x_player = m + 116 * s
    x_coins = W * 0.555
    x_bar0, x_bar1 = W * 0.60, W * 0.75
    x_points = W - m - 14 * s
    header_font = font(22 * s, "bold")
    draw.rounded_rectangle((m, table_top, W - m, table_top + 40 * s), radius=8 * s, fill=c["header"])
    header_y = table_top + 20 * s
    for x, label, anchor in ((x_rank, "RANK", "mm"), (x_player, "PLAYER", "lm"), (x_coins, plural.upper(), "rm"),
                             (x_bar0, "HIT RATE", "lm"), (x_points, "POINTS", "rm")):
        draw.text((x, header_y), label, font=header_font, fill=c["muted"], anchor=anchor)

    entries = [(index, record) for index, record in enumerate(visible)]
    if extra:
        entries.append(("gap", None))
        entries.append((rank_of[highlight], ranking[rank_of[highlight]]))
    medals = (c["gold"], c["silver"], c["bronze"])
    outline_w = max(1, int(round(3 * s)))
    for row in range(n_rows):
        y0 = rows_top + row * row_h
        y1 = y0 + row_h - 4 * s
        rh = y1 - y0
        cy = (y0 + y1) / 2
        if row >= len(entries):
            draw.rounded_rectangle((m, y0, W - m, y1), radius=8 * s, fill=c["row_b"] if row % 2 else c["row_a"])
            continue
        index, record = entries[row]
        if index == "gap":
            draw.text((W / 2, cy), "· · ·", font=font(rh * 0.5, "bold"), fill=c["line"], anchor="mm")
            continue
        is_you = highlight is not None and record.player_id == highlight
        fill = c["you_fill"] if is_you else (c["row_b"] if row % 2 else c["row_a"])
        if is_you:
            draw.rounded_rectangle((m, y0, W - m, y1), radius=8 * s, fill=fill, outline=c["teal"], width=outline_w)
        else:
            draw.rounded_rectangle((m, y0, W - m, y1), radius=8 * s, fill=fill)
        rank_font = font(rh * 0.5, "mono")
        if index < 3:
            bw, bh = 64 * s, rh * 0.7
            draw.rounded_rectangle((x_rank - bw / 2, cy - bh / 2, x_rank + bw / 2, cy + bh / 2),
                                   radius=8 * s, fill=medals[index])
            draw.text((x_rank, cy), str(index + 1), font=rank_font, fill=c.get("chip_text", c["background"]), anchor="mm")
        else:
            draw.text((x_rank, cy), str(index + 1), font=rank_font, fill=c["muted"], anchor="mm")
        name_font = font(rh * 0.46, "bold")
        # One sticker (the robot with its cup) per complete run that collected at least
        # STICKER_PCT % of its beans; beyond MAX_STICKERS a single sticker with "x n" stands
        # for all of them.
        stickers = int(getattr(record, "perfect_runs", 0) or 0)
        sticker = sprites.perfect_image(rh * 0.95) if stickers else None
        count_font = font(rh * 0.36, "bold")
        if sticker is None:
            stickers_w = 0
        elif stickers <= MAX_STICKERS:
            stickers_w = stickers * (sticker.width + 4 * s) + 6 * s
        else:
            stickers_w = sticker.width + 8 * s + draw.textlength(f"x {stickers}", font=count_font) + 6 * s
        name_max = x_coins - 160 * s - x_player - (100 * s if is_you else 0) - stickers_w
        name = fit_text(draw, record.player_id, name_font, name_max)
        draw.text((x_player, cy), name, font=name_font, fill=c["text"], anchor="lm")
        cursor = x_player + draw.textlength(name, font=name_font) + 16 * s
        if is_you:
            chip_font = font(rh * 0.3, "bold")
            chip_w = draw.textlength("YOU", font=chip_font) + 22 * s
            chip_h = rh * 0.48
            draw.rounded_rectangle((cursor, cy - chip_h / 2, cursor + chip_w, cy + chip_h / 2),
                                   radius=chip_h / 2, fill=c["teal"])
            draw.text((cursor + chip_w / 2, cy), "YOU", font=chip_font, fill=c.get("chip_text", c["background"]), anchor="mm")
            cursor += chip_w + 12 * s
        if sticker is not None:
            shown = stickers if stickers <= MAX_STICKERS else 1
            for _ in range(shown):
                image.paste(sticker, (int(cursor), int(cy - sticker.height / 2)), sticker)
                cursor += sticker.width + 4 * s
            if stickers > MAX_STICKERS:
                draw.text((cursor + 4 * s, cy), f"x {stickers}", font=count_font, fill=c["gold"], anchor="lm")
        draw.text((x_coins, cy), f"{record.collected} / {record.total}", font=font(rh * 0.44, "mono"),
                  fill=c["text"], anchor="rm")
        bar_h = max(4.0, rh * 0.26)
        draw.rounded_rectangle((x_bar0, cy - bar_h / 2, x_bar1, cy + bar_h / 2), radius=bar_h / 2, fill=c["line"])
        filled = x_bar0 + (x_bar1 - x_bar0) * float(accuracy(record))
        if filled >= x_bar0 + bar_h:
            draw.rounded_rectangle((x_bar0, cy - bar_h / 2, filled, cy + bar_h / 2), radius=bar_h / 2, fill=c["teal"])
        draw.text((x_bar1 + 14 * s, cy), f"{record.percent:.1f} %", font=font(rh * 0.4, "mono"),
                  fill=c["teal"], anchor="lm")
        draw.text((x_points, cy), str(record.score), font=font(rh * 0.58, "mono"), fill=c["gold"], anchor="rm")

    if not ranking:
        mid = rows_top + n_rows * row_h / 2
        draw.text((W / 2, mid - 26 * s), "No scores yet", font=font(44 * s, "bold"), fill=c["gold"], anchor="mm")
        draw.text((W / 2, mid + 24 * s), "Finish a ramp-and-hold run with a Player ID to enter the board.",
                  font=font(24 * s, "regular"), fill=c["muted"], anchor="mm")

    footer_font = font(20 * s, "regular")
    line_y = H - footer_h + 8 * s
    draw.line((m, line_y, W - m, line_y), fill=c["line"], width=max(1, int(round(s))))
    footer_y = H - footer_h / 2 + 6 * s
    draw.text((m, footer_y), f"All complete runs add up.  Ranking: points ({plural} collected), then hit rate, then the latest run",
              font=footer_font, fill=c["muted"], anchor="lm")
    stamp = (rendered_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    draw.text((W - m, footer_y), f"{stamp}   ·   showing {len(visible)} of {len(ranking)} players",
              font=footer_font, fill=c["muted"], anchor="rm")
    return image


def show_snapshot(image):
    """Display the already-rendered image; no polling or redraw timer is used."""
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.withdraw()
    root.title(f"{themes.active()['title']} - Leaderboard")
    scale = min(1.0, (root.winfo_screenwidth()-100) / image.width,
                (root.winfo_screenheight()-140) / image.height)
    display = image
    if scale < 1.0:
        display = image.resize((max(1, int(image.width*scale)), max(1, int(image.height*scale))),
                               Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(display, master=root)
    root.configure(bg=palette()["background"])
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
    parser.add_argument("--player", default=None, help="Optional Player ID to mark with a YOU tag.")
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
        image = render_leaderboard(records, limit=args.limit, highlight=args.player)
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

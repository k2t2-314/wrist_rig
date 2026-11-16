"""Rig trial files for the MVC-from-EMG analysis (standard library only).

A rig trial `<stem>` (for example `01_RAH_R_flex_20261028_124850`) produces `<stem>_log.txt`,
`<stem>_lc_raw.csv`, `<stem>_trigger_timing.csv` and `<stem>_openrb_raw.csv`, copied into
`recordings\<subdir>` at the end of the trial. The TMSi recorder saves `<prefix>_<ts>_<tag>-<ts>.poly5`,
where the operator types the rig stem (or the stem without its time) as the prefix.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import re
from datetime import datetime

STAMP = re.compile(r"_(\d{8})_(\d{6})")
SCHEDULE_RE = re.compile(r"\(([^,()]+),([^,()]+)\)")
FORCE_COLUMNS = {"Fx": "fx_N", "Fy": "fy_N", "Fz": "fz_N", "Tx": "tx_Nmm", "Ty": "ty_Nmm", "Tz": "tz_Nmm"}
FORCE_QUANTITIES = ("Fx", "Fy", "Fz", "Fxy", "Fnorm")


class RigFileError(Exception):
    pass


# ----------------------------------------------------------------------------- trials and logs
def list_trials(recordings_root, subdir):
    """[(stem, log path)] of the trials in <recordings_root>/<subdir>, newest first."""
    folder = os.path.join(recordings_root, subdir)
    logs = glob.glob(os.path.join(folder, "*_log.txt"))
    logs.sort(key=os.path.getmtime, reverse=True)
    return [(os.path.basename(p)[:-len("_log.txt")], p) for p in logs]


def prefix_of(log_path):
    if not log_path.endswith("_log.txt"):
        raise RigFileError("not a rig log: %s" % log_path)
    return log_path[:-len("_log.txt")]


def parse_log(path):
    """`key: value` lines of a rig log as a dict (first occurrence wins; summary blocks are skipped)."""
    out = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line or line.startswith("=") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            if k and k not in out:
                out[k] = v.strip()
    return out


def schedule_from_log(log):
    """[(time_s, pct)] of the ramp-and-hold profile in the log."""
    raw = log.get("profile_schedule_time_pct_mvc", "")
    pts = [(float(a), float(b)) for a, b in SCHEDULE_RE.findall(raw)]
    if len(pts) < 2:
        raise RigFileError("the log has no ramp-and-hold profile (profile_schedule_time_pct_mvc)")
    return pts


def segments_of(schedule):
    """rest / ramp-up / hold / ramp-down windows of a profile (seconds), holds numbered from 1."""
    segs, rep = [], 0
    for (t0, v0), (t1, v1) in zip(schedule[:-1], schedule[1:]):
        if v0 == 0 and v1 == 0:
            lab = "rest"
        elif v0 < v1:
            rep += 1
            lab = "ramp-up"
        elif v0 == v1:
            lab = "hold"
        else:
            lab = "ramp-down"
        segs.append({"label": lab, "rep": rep if lab != "rest" else 0, "a": float(t0), "b": float(t1)})
    return segs, rep


def stamp_of(stem):
    """datetime of the trailing _YYYYMMDD_HHMMSS of a stem (None if absent)."""
    m = None
    for m in STAMP.finditer(stem):
        pass
    if m is None:
        return None
    try:
        return datetime.strptime(m.group(1) + "_" + m.group(2), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def stem_through_date(stem):
    """`01_RAH_R_flex_20261028_124850` -> `01_RAH_R_flex_20261028` (what the operator may have typed)."""
    return re.sub(r"_(\d{8})_\d{6}$", r"_\1", stem)


# ----------------------------------------------------------------------------- CSVs
def read_trigger_timing(path):
    """[(code, t_s)] of the acknowledged triggers, on the rig clock (experiment t0 = 0), stop code 15 dropped."""
    out = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            try:
                code = int(float(row.get("trigger_code", "")))
                t = float(row.get("estimated_event_rel_ns", "")) / 1e9
            except (TypeError, ValueError):
                continue
            if code == 15:
                continue
            out.append((code, t))
    return out


def read_lc_raw(path):
    """Rig time (s) and the six components (N, N·mm) of the load-cell file; rows without a valid sample skipped."""
    t, cols = [], {k: [] for k in FORCE_COLUMNS.values()}
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.DictReader(fh)
        tcol = "host_rx_rel_ns" if "host_rx_rel_ns" in (rd.fieldnames or []) else "ati_sample_est_rel_ns"
        for row in rd:
            try:
                ts = float(row[tcol]) / 1e9
                vals = [float(row[c]) for c in FORCE_COLUMNS.values()]
            except (TypeError, ValueError, KeyError):
                continue
            t.append(ts)
            for c, v in zip(FORCE_COLUMNS.values(), vals):
                cols[c].append(v)
    if not t:
        raise RigFileError("no valid load-cell samples in %s" % path)
    return t, cols


def direction_sign(channel, handedness, direction_label):
    """Sign that makes the task direction positive, as the GUI's _rah_lc_direction_sign does:
    extension is +1, flexion -1; Fx -> hand x direction, torques -> -hand x direction, others -> direction."""
    d = str(direction_label).strip().lower()
    direction = -1 if d.startswith("flex") else 1
    hand = 1 if str(handedness).strip().lower().startswith("r") else -1
    if channel == "Fx":
        return hand * direction
    if channel in ("Tx", "Ty", "Tz"):
        return -hand * direction
    return direction


def force_quantity(cols, quantity, sign=1):
    """One force quantity per sample: Fx/Fy/Fz signed, Fxy and Fnorm unsigned magnitudes."""
    fx, fy, fz = cols["fx_N"], cols["fy_N"], cols["fz_N"]
    if quantity == "Fxy":
        return [math.hypot(a, b) for a, b in zip(fx, fy)]
    if quantity == "Fnorm":
        return [math.sqrt(a * a + b * b + c * c) for a, b, c in zip(fx, fy, fz)]
    if quantity in ("Fx", "Fy", "Fz"):
        src = {"Fx": fx, "Fy": fy, "Fz": fz}[quantity]
        return [sign * v for v in src]
    raise RigFileError("unknown force quantity %s" % quantity)


# ----------------------------------------------------------------------------- TMSi files
def find_poly5(tmsi_dir, stem, tag):
    """Poly5 of the flexor SAGA for a rig stem: `<stem or stem-through-date>*_<tag>-*.poly5` in tmsi_dir.
    Returns (path, note). Several matches: the one whose recorder timestamp is closest to the rig stamp."""
    if not os.path.isdir(tmsi_dir):
        raise RigFileError("TMSi recordings folder not found: %s" % tmsi_dir)
    base = stem_through_date(stem)
    pattern = os.path.join(tmsi_dir, "%s*_%s-*.poly5" % (base, tag))
    matches = glob.glob(pattern)
    if not matches:
        raise RigFileError("no TMSi file matches %s\n(type the rig trial name as the TMSi filename prefix and %s as the file tag)"
                           % (pattern, tag))
    rig_t = stamp_of(stem)
    if len(matches) == 1 or rig_t is None:
        return matches[0], ""

    def gap(p):
        m = re.search(r"_(\d{8}_\d{6})_%s-" % re.escape(tag), os.path.basename(p))
        if not m:
            return float("inf")
        return abs((datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") - rig_t).total_seconds())

    best = min(matches, key=gap)
    note = "%d files match; took %s (recorder time %.0f s from the rig stamp)" % (len(matches), os.path.basename(best), gap(best))
    if gap(best) > 600:
        note += " - more than 10 min apart, check the pairing"
    return best, note


POLY5_NAME = re.compile(r"^(?P<prefix>.+?)_(?P<date>\d{8})_(?P<time>\d{6})_(?P<tag>[^-]+)-(\d{8})_(\d{6})\.poly5$", re.I)


def parse_poly5_name(path):
    """`02_RAH_R_ext_20261028_154139_FLX-20261028_154139.poly5` -> dict(prefix, stamp (datetime), tag), or None."""
    m = POLY5_NAME.match(os.path.basename(path))
    if not m:
        return None
    try:
        stamp = datetime.strptime(m.group("date") + "_" + m.group("time"), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return {"prefix": m.group("prefix"), "stamp": stamp, "tag": m.group("tag")}


def find_rig_log(recordings_root, subdir, poly5_path, max_gap_s=900):
    """The rig trial that goes with a Poly5 file: a log in <recordings_root>/<subdir> whose stem starts with the
    prefix typed in the TMSi app (and the same date), else the trial closest in time to the TMSi recording
    (within max_gap_s). Returns (log path or None, note)."""
    info = parse_poly5_name(poly5_path)
    trials = list_trials(recordings_root, subdir)
    if not trials:
        return None, "no rig trials in %s" % os.path.join(recordings_root, subdir)
    if info is None:
        return None, "cannot read the TMSi file name %s" % os.path.basename(poly5_path)
    stamp = info["stamp"]
    want = "%s_%s" % (info["prefix"], stamp.strftime("%Y%m%d"))

    def gap(item):
        t = stamp_of(item[0])
        return abs((t - stamp).total_seconds()) if t else float("inf")

    by_name = [(s, p) for s, p in trials if s.startswith(want)]
    if by_name:
        best = min(by_name, key=gap)
        return best[1], "rig trial %s (by name, %.0f s from the TMSi recording)" % (best[0], gap(best))
    best = min(trials, key=gap)
    g = gap(best)
    if g <= max_gap_s:
        return best[1], ("rig trial %s matched by time (%.0f s from the TMSi recording; its name differs from the TMSi prefix %s): "
                         "check it is the right trial" % (best[0], g, info["prefix"]))
    return None, "no rig trial named %s* and none within %d min of the TMSi recording" % (want, max_gap_s // 60)


# ----------------------------------------------------------------------------- square-wave profile
def square_wave_points(levels_N, hold_s, rest_s, ramp_s, mvc_value=100.0):
    """[(t, pct)] for rest, then for each level: ramp up, hold, ramp down, rest. pct = N / mvc_value x 100."""
    if not levels_N:
        raise RigFileError("give at least one level")
    if ramp_s < 0.001:
        raise RigFileError("the ramp must be at least 0.001 s (the RAH profile needs strictly increasing times)")
    pts = [(0.0, 0.0)]
    t = float(rest_s)
    pts.append((t, 0.0))
    for lv in levels_N:
        pct = 100.0 * float(lv) / float(mvc_value)
        if not 0 < pct <= 100:
            raise RigFileError("level %.3g N is outside 0-%.0f N for an MVC value of %.0f" % (lv, mvc_value, mvc_value))
        t += ramp_s
        pts.append((t, pct))
        t += hold_s
        pts.append((t, pct))
        t += ramp_s
        pts.append((t, 0.0))
        t += rest_s
        pts.append((t, 0.0))
    return [(round(a, 3), round(b, 3)) for a, b in pts]


def compact_number(value, decimals=3):
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return ("%%.%df" % decimals % number).rstrip("0").rstrip(".")


def format_points(points):
    return ",".join("(%s,%s)" % (compact_number(t), compact_number(p)) for t, p in points)


# ----------------------------------------------------------------------------- settings
def load_settings(path, defaults):
    out = dict(defaults)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in defaults})
    except (OSError, ValueError):
        pass
    return out


def save_settings(path, values):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(values, fh, indent=1)
    except OSError:
        pass

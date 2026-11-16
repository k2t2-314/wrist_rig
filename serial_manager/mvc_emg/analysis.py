"""MVC from EMG: square-wave calibration holds (rig force + TMSi EMG) and EMG-only maximal efforts ->
fitted force-EMG curves -> approximate MVC force.

Steps (see the MVC_EMG tab of the operator GUI and README):
  1. the calibration Poly5 is aligned to its rig trial with the TTL pulses (TRIGGERS channel <-> _trigger_timing.csv)
  2. every hold of the rig profile: middle part (default 3 s of 5 s) and the rest before it (noise floor)
  3. each grid channel: band-pass 20-500 Hz, RMS in 0.5 s epochs, sqrt(RMS^2 - rest^2) noise correction
  4. channel quality: no signal, noisy at rest, clipped or not responding -> excluded; every remaining channel
     is kept (default) and ranked by the hold/rest signal-to-noise ratio averaged over the levels; n_best > 0
     keeps only the best N, explicit_channels replaces the ranking. Per hold the EMG is the median over the kept
     channels of the noise-corrected epoch RMS.
  5. maximal efforts: the Poly5 of the MVC_EMG trial cut by its own triggers (rest code 6, max code 7):
     floor from the 5 s rest, peak 0.5 s epoch of the median over the kept channels within the 5 s of effort
  6. straight line, power law and quadratic fitted to (force, EMG) of the hold epochs, each inverted at the
     largest effort EMG; bootstrap over holds; verdict with the spread between the models.
Imports numpy and scipy.signal at import time: import this module lazily (the GUI does so in a worker thread).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from dataclasses import dataclass, field, asdict, replace

try:
    import numpy as np
    from scipy import signal
except ImportError as exc:  # pragma: no cover
    raise ImportError("The MVC_EMG analysis needs numpy, scipy and matplotlib. In wrist_rig-main run: "
                      "python -m pip install -r requirements.txt (%s)" % exc) from exc

from . import rigfiles as rf
from . import poly5 as p5


class MvcEmgError(Exception):
    pass


# ----------------------------------------------------------------------------- parameters and result
@dataclass
class Params:
    band_hz: tuple = (20.0, 500.0)
    epoch_s: float = 0.5
    hold_seconds: float = 3.0             # analysed middle part of each hold
    rest_trim_s: tuple = (2.0, 1.0)       # trimmed from the start / end of the rest before each hold
    n_best: int = 0                       # 0 = every channel that passes the quality rules; N = only the best N by SNR
    explicit_channels: tuple = ()         # channel labels that replace the ranking
    noisy_rest_factor: float = 2.0        # rest RMS above factor x median over channels -> excluded
    dead_uv: float = 0.5                  # RMS below this at rest and during the holds -> channel not connected
    clip_uv: float = 5000.0
    clip_fraction: float = 1e-3
    unresponsive_ratio: float = 1.1       # hold/rest RMS at the top level below this ...
    unresponsive_median_ratio: float = 1.3  # ... while the median channel is above this -> excluded
    mvc_rest_code: int = 6
    mvc_max_code: int = 7
    mvc_max_s: float = 5.0
    mvc_rest_trim_s: float = 1.0
    n_boot: int = 2000
    spread_limit_pct: float = 25.0
    force_quantity: str = "Fx"
    accept_rah_codes_for_mvc: bool = False  # testing only: RAH holds (rest 1 / hold 3) stand in for efforts


@dataclass
class Result:
    calibration_stem: str = ""
    calibration_poly5: str = ""
    mvc_stems: list = field(default_factory=list)
    mvc_poly5: list = field(default_factory=list)
    tag: str = ""
    force_quantity: str = ""
    force_sign: int = 1
    alignment: dict = field(default_factory=dict)
    holds: list = field(default_factory=list)          # per hold: rep, target_N, force_N, emg_uV, baseline_uV, win, rest_win, n_epochs
    epochs: list = field(default_factory=list)         # per epoch: hold, t, force_N, emg_uV
    ranking: list = field(default_factory=list)        # (label, score, rest_rms_uV)
    excluded: dict = field(default_factory=dict)       # label -> reason
    chosen: list = field(default_factory=list)
    efforts: list = field(default_factory=list)        # per effort: file, index, t_rest, t_max, peak_uV, peak_t, best_1s_uV, floor_uV, snr
    emg_mvc_uV: float = float("nan")
    effort_spread_pct: float = float("nan")
    fits: dict = field(default_factory=dict)           # model -> params, r2, rmse
    estimates_N: dict = field(default_factory=dict)
    bootstrap: dict = field(default_factory=dict)      # model -> p2.5, p50, p97.5
    leverage: float = float("nan")
    verdict: str = ""
    mvc_estimate_N: float = float("nan")
    warnings: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    traces: dict = field(default_factory=dict)         # small arrays for the figure
    out_dir: str = ""

    def to_json(self):
        d = asdict(self)
        return json.dumps(d, indent=1, default=_json_default)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return str(o)


# ----------------------------------------------------------------------------- signal helpers
def _sos(band, fs):
    return signal.butter(4, [band[0] / (fs / 2.0), band[1] / (fs / 2.0)], btype="band", output="sos")


def _bandpass(x, sos):
    x = np.asarray(x, dtype=np.float64)
    return signal.sosfiltfilt(sos, x - x.mean(axis=0), axis=0)


def _epoch_rms(sig, n):
    n_ep = sig.shape[0] // n
    if n_ep < 1:
        return np.empty((0, sig.shape[1]))
    x = sig[:n_ep * n].reshape(n_ep, n, sig.shape[1])
    return np.sqrt(np.mean(x * x, axis=1))


def _window_rms(pf, chans, t, ta, tb, sos, n, clip_uv, pad_s=1.0):
    """Epoch RMS (epochs x channels) between rig times ta..tb of the given file channels, the epoch start
    times (rig s) and the clipped fraction per channel. The slice is filtered with pad_s of context."""
    i0, i1 = int(np.searchsorted(t, ta)), int(np.searchsorted(t, tb))
    pad = int(pad_s * pf.fs)
    j0, j1 = max(0, i0 - pad), min(pf.n_samples, i1 + pad)
    raw = pf.window(j0, j1, chans)
    if raw.shape[0] < 2 * n:
        return np.empty((0, len(chans))), np.empty(0), np.zeros(len(chans))
    y = _bandpass(raw, sos)[i0 - j0:i0 - j0 + (i1 - i0)]
    clipped = np.mean(np.abs(y) > clip_uv, axis=0)
    rms = _epoch_rms(y, n)
    starts = t[i0 + np.arange(rms.shape[0]) * n] if rms.shape[0] else np.empty(0)
    return rms, starts, clipped


def _window_rms_detail(pf, chans, t, ta, tb, sos, n, clip_uv, pad_s=1.0):
    """As _window_rms, plus the clipped fraction per epoch and channel (epochs x channels)."""
    i0, i1 = int(np.searchsorted(t, ta)), int(np.searchsorted(t, tb))
    pad = int(pad_s * pf.fs)
    j0, j1 = max(0, i0 - pad), min(pf.n_samples, i1 + pad)
    raw = pf.window(j0, j1, chans)
    if raw.shape[0] < 2 * n:
        return np.empty((0, len(chans))), np.empty(0), np.zeros(len(chans)), np.empty((0, len(chans)))
    y = _bandpass(raw, sos)[i0 - j0:i0 - j0 + (i1 - i0)]
    clipped = np.mean(np.abs(y) > clip_uv, axis=0)
    n_ep = y.shape[0] // n
    x = y[:n_ep * n].reshape(n_ep, n, y.shape[1])
    rms = np.sqrt(np.mean(x * x, axis=1))
    clip_ep = np.mean(np.abs(x) > clip_uv, axis=1)
    starts = t[i0 + np.arange(n_ep) * n] if n_ep else np.empty(0)
    return rms, starts, clipped, clip_ep


def _match_codes(codes_file, codes_rig):
    """Indices into codes_file that reproduce codes_rig: exact equality, or the rig sequence found once as a
    contiguous run inside the file's sequence (the TMSi recording spanned more than the rig trial)."""
    a, b = [int(c) for c in codes_file], [int(c) for c in codes_rig]
    if a == b:
        return list(range(len(a)))
    hits = [i for i in range(len(a) - len(b) + 1) if a[i:i + len(b)] == b]
    if len(hits) == 1:
        return list(range(hits[0], hits[0] + len(b)))
    raise MvcEmgError("trigger codes differ between the TMSi file (%d pulses: %s) and the rig trial (%d: %s)"
                      % (len(a), " ".join(map(str, a[:40])), len(b), " ".join(map(str, b[:40]))))


# ----------------------------------------------------------------------------- calibration
def load_calibration(rig_prefix, poly5_path, params, progress):
    progress("reading rig files of %s" % os.path.basename(rig_prefix))
    log = rf.parse_log(rig_prefix + "_log.txt")
    schedule = rf.schedule_from_log(log)
    segs, _ = rf.segments_of(schedule)
    mvc_value = float(log.get("mvc_value", "nan"))
    trig = rf.read_trigger_timing(rig_prefix + "_trigger_timing.csv")
    if len(trig) < 2:
        raise MvcEmgError("fewer than 2 acknowledged triggers in %s_trigger_timing.csv" % rig_prefix)
    codes_r = np.array([c for c, _ in trig]); t_r = np.array([t for _, t in trig])
    t_lc, cols = rf.read_lc_raw(rig_prefix + "_lc_raw.csv")
    q = params.force_quantity
    sign = rf.direction_sign(q, log.get("handedness", "Right"), log.get("direction", "flexion")) if q in ("Fx", "Fy", "Fz") else 1
    force = np.array(rf.force_quantity(cols, q, sign)); t_lc = np.array(t_lc)

    progress("reading %s" % os.path.basename(poly5_path))
    pf = p5.Poly5File(poly5_path)
    grid = p5.grid_channels(pf.names)
    if not grid:
        raise MvcEmgError("no R<row>C<col> channels in %s" % poly5_path)
    ti = p5.trigger_index(pf.names, pf.units)
    if ti is None:
        raise MvcEmgError("no TRIGGERS channel in %s" % poly5_path)
    t_p, codes_p, info = p5.trigger_pulses(pf.channel(ti), pf.fs)
    if info["flat"]:
        raise MvcEmgError("the TRIGGERS channel of %s is flat (value %d): the SAGA was not streaming or not wired to the trigger box"
                          % (os.path.basename(poly5_path), info["idle"]))
    sel = _match_codes(codes_p, codes_r)
    scale, offset = np.polyfit(t_p[sel], t_r, 1)      # rig time = scale * poly5 time + offset
    resid = np.abs(t_r - (scale * t_p[sel] + offset))
    t = scale * np.arange(pf.n_samples) / pf.fs + offset
    align = {"n_triggers": int(len(sel)), "max_residual_ms": float(resid.max() * 1000), "drift_ppm": float((scale - 1) * 1e6),
             "poly5_pulses": int(len(codes_p)), "idle_value": info["idle"]}
    return {"log": log, "segs": segs, "mvc_value": mvc_value, "t_lc": t_lc, "force": force, "sign": sign, "pf": pf, "grid": grid,
            "t": t, "align": align, "prefix": rig_prefix}


def analyse_calibration(cal, params, progress):
    pf, t, segs = cal["pf"], cal["t"], cal["segs"]
    labels = ["R%dC%d" % (r, c) for _, r, c in cal["grid"]]
    chans = [i for i, _, _ in cal["grid"]]
    sos = _sos(params.band_hz, pf.fs)
    n = int(round(params.epoch_s * pf.fs))
    holds = [s for s in segs if s["label"] == "hold"]
    rests = [s for s in segs if s["label"] == "rest"]
    if not holds:
        raise MvcEmgError("the rig profile has no holds")
    t_lc, force = cal["t_lc"], cal["force"]
    out_holds, epochs, rest_rows, hold_rms_rows, clipped = [], [], [], [], np.zeros(len(chans))
    for k, h in enumerate(holds):
        progress("hold %d of %d" % (k + 1, len(holds)))
        dur = h["b"] - h["a"]
        trim = max(0.0, (dur - params.hold_seconds) / 2)
        ta, tb = h["a"] + trim, h["b"] - trim
        before = [r for r in rests if r["b"] <= h["a"]]
        if not before:
            raise MvcEmgError("hold %d has no rest before it" % h["rep"])
        r = before[-1]
        ra, rb = r["a"] + params.rest_trim_s[0], r["b"] - params.rest_trim_s[1]
        if rb - ra < 2 * params.epoch_s:
            ra, rb = r["a"], r["b"]
        rest_rms_ep, _, clip_r = _window_rms(pf, chans, t, ra, rb, sos, n, params.clip_uv)
        if rest_rms_ep.shape[0] < 1:
            raise MvcEmgError("the rest before hold %d is shorter than one epoch" % h["rep"])
        rest_rms = np.median(rest_rms_ep, axis=0)
        rms, starts, clip_h = _window_rms(pf, chans, t, ta, tb, sos, n, params.clip_uv)
        if rms.shape[0] < 1:
            raise MvcEmgError("hold %d is shorter than one epoch" % h["rep"])
        clipped = np.maximum(clipped, np.maximum(clip_r, clip_h))
        corr = np.sqrt(np.clip(rms ** 2 - rest_rms[None, :] ** 2, 0, None))
        f_ep = np.array([force[(t_lc >= s) & (t_lc < s + params.epoch_s)].mean() if np.any((t_lc >= s) & (t_lc < s + params.epoch_s)) else np.nan for s in starts])
        target_pct = float(np.interp((ta + tb) / 2, [p for p, _ in rf.schedule_from_log(cal["log"])], [v for _, v in rf.schedule_from_log(cal["log"])]))
        m = (t_lc >= ta) & (t_lc < tb)
        out_holds.append({"rep": h["rep"], "target_N": target_pct * cal["mvc_value"] / 100.0, "force_N": float(force[m].mean()) if m.any() else float("nan"),
                          "win": (float(ta), float(tb)), "rest_win": (float(ra), float(rb)), "n_epochs": int(rms.shape[0]),
                          "rest_rms": rest_rms, "rms": rms, "corr": corr, "f_ep": f_ep, "starts": starts})
    # ---- channel quality and ranking
    rest_all = np.median(np.array([h["rest_rms"] for h in out_holds]), axis=0)
    hold_all = np.median(np.array([h["rms"].mean(axis=0) for h in out_holds]), axis=0)
    dead = (rest_all < params.dead_uv) & (hold_all < params.dead_uv)      # flat channels (not connected)
    live = ~dead
    med_rest = float(np.median(rest_all[live])) if live.any() else float("nan")
    top = max(out_holds, key=lambda h: h["force_N"] if np.isfinite(h["force_N"]) else -1)
    top_ratio = top["rms"].mean(axis=0) / np.maximum(top["rest_rms"], 1e-9)
    med_top_ratio = float(np.median(top_ratio[live])) if live.any() else float("nan")
    excluded = {}
    for k, lab in enumerate(labels):
        reasons = []
        if dead[k]:
            excluded[lab] = "no signal (RMS below %.1f uV at rest and during the holds)" % params.dead_uv
            continue
        if rest_all[k] > params.noisy_rest_factor * med_rest:
            reasons.append("rest RMS %.1f uV > %.1f x median %.1f (noisy)" % (rest_all[k], params.noisy_rest_factor, med_rest))
        if clipped[k] > params.clip_fraction:
            reasons.append("%.2f %% of samples beyond %.0f uV (clipped)" % (100 * clipped[k], params.clip_uv))
        if med_top_ratio > params.unresponsive_median_ratio and top_ratio[k] < params.unresponsive_ratio:
            reasons.append("hold/rest %.2f at the top level while the median channel is %.2f (not responding)" % (top_ratio[k], med_top_ratio))
        if reasons:
            excluded[lab] = "; ".join(reasons)
    score = np.mean([h["rms"].mean(axis=0) / np.maximum(h["rest_rms"], 1e-9) for h in out_holds], axis=0)
    ranking = sorted([(lab, float(score[k]), float(rest_all[k])) for k, lab in enumerate(labels) if lab not in excluded], key=lambda x: -x[1])
    n_live = int(live.sum())
    if n_live < len(labels):
        quality_note = "%d of %d channels carry no signal (not connected?)" % (len(labels) - n_live, len(labels))
    else:
        quality_note = ""
    n_clipped = int(sum(1 for lab, why in excluded.items() if "clipped" in why))
    if n_clipped:
        quality_note += ("; " if quality_note else "") + "%d channels clipped beyond %.0f uV (saturated amplifier or missing reference?)" % (n_clipped, params.clip_uv)
    if params.explicit_channels:
        unknown = [c for c in params.explicit_channels if c not in labels]
        if unknown:
            raise MvcEmgError("unknown channels: %s" % ", ".join(unknown))
        chosen = list(params.explicit_channels)
    else:
        chosen = [lab for lab, _, _ in ranking[:params.n_best]] if params.n_best > 0 else [lab for lab, _, _ in ranking]
    if not chosen:
        n_dead = int(dead.sum())
        n_noisy = sum(1 for why in excluded.values() if "noisy" in why)
        n_clip = sum(1 for why in excluded.values() if "clipped" in why)
        raise MvcEmgError("no usable channel: %d of %d carry no signal, %d are noisy at rest, %d are clipped beyond %.0f uV "
                          "(median rest RMS of the live channels %.0f uV). The EMG is dominated by interference or missing: check the "
                          "patient ground and reference electrode, the grid cables, and the rig's motor noise (compare rest RMS with torque off and on)."
                          % (n_dead, len(labels), n_noisy, n_clip, params.clip_uv, med_rest if np.isfinite(med_rest) else 0.0))
    idx = [labels.index(c) for c in chosen]
    # ---- per hold and per epoch values on the chosen channels
    holds_out, epochs_out = [], []
    for h in out_holds:
        e_ep = np.median(h["corr"][:, idx], axis=1)      # median over the kept channels, as for the efforts
        holds_out.append({"rep": h["rep"], "target_N": round(h["target_N"], 4), "force_N": round(h["force_N"], 4),
                          "emg_uV": float(e_ep.mean()), "baseline_uV": float(np.median(h["rest_rms"][idx])),
                          "win": h["win"], "rest_win": h["rest_win"], "n_epochs": h["n_epochs"]})
        for s, f_, e_ in zip(h["starts"], h["f_ep"], e_ep):
            if np.isfinite(f_):
                epochs_out.append({"hold": h["rep"], "t": float(s), "force_N": float(f_), "emg_uV": float(e_)})
    rest_chosen = rest_all[idx]
    return {"labels": labels, "chosen": chosen, "idx": idx, "ranking": ranking, "excluded": excluded, "holds": holds_out,
            "epochs": epochs_out, "rest_chosen": rest_chosen, "chans_chosen": [chans[k] for k in idx], "quality_note": quality_note}


# ----------------------------------------------------------------------------- maximal efforts
def analyse_mvc(poly5_path, chosen, params, progress, fallback_floor):
    pf = p5.Poly5File(poly5_path)
    grid = {("R%dC%d" % (r, c)): i for i, r, c in p5.grid_channels(pf.names)}
    missing = [c for c in chosen if c not in grid]
    if missing:
        raise MvcEmgError("%s lacks channels %s" % (os.path.basename(poly5_path), ", ".join(missing)))
    chans = [grid[c] for c in chosen]
    ti = p5.trigger_index(pf.names, pf.units)
    if ti is None:
        raise MvcEmgError("no TRIGGERS channel in %s" % poly5_path)
    t_p, codes, info = p5.trigger_pulses(pf.channel(ti), pf.fs)
    rest_code, max_code = params.mvc_rest_code, params.mvc_max_code
    max_s = params.mvc_max_s
    windows = []
    if info["flat"] or not np.any(codes == max_code):
        if params.accept_rah_codes_for_mvc and not info["flat"] and np.any(codes == 3):
            for k in np.flatnonzero(codes == 3):
                t7 = t_p[k]
                nxt = t_p[k + 1] if k + 1 < len(t_p) else t7 + max_s
                prev_rest = [t_p[j] for j in range(k) if codes[j] == 1]
                t6 = prev_rest[-1] if prev_rest else max(0.0, t7 - 10.0)
                windows.append((t6, t7, min(nxt, t7 + max_s)))
        elif info["flat"]:
            raise MvcEmgError("the TRIGGERS channel of %s is flat: the SAGA was not streaming or not wired" % os.path.basename(poly5_path))
        else:
            raise MvcEmgError("no maximal-effort trigger (code %d) in %s; codes found: %s" % (max_code, os.path.basename(poly5_path), " ".join(map(str, codes[:40]))))
    else:
        for k in np.flatnonzero(codes == max_code):
            t7 = t_p[k]
            prev_rest = [t_p[j] for j in range(k) if codes[j] == rest_code]
            t6 = prev_rest[-1] if prev_rest else max(0.0, t7 - 5.0)
            windows.append((t6, t7, t7 + max_s))
    sos = _sos(params.band_hz, pf.fs)
    n = int(round(params.epoch_s * pf.fs))
    t_file = np.arange(pf.n_samples) / float(pf.fs)
    efforts, traces, warnings = [], [], []
    for i, (t6, t7, t_end) in enumerate(windows, start=1):
        progress("maximal effort %d of %d" % (i, len(windows)))
        ra, rb = t6 + params.mvc_rest_trim_s, t7 - params.mvc_rest_trim_s
        if rb - ra < 2 * params.epoch_s:
            ra, rb = t6, t7
        rest_ep, _, _ = _window_rms(pf, chans, t_file, ra, rb, sos, n, params.clip_uv)
        floor = np.median(rest_ep, axis=0) if rest_ep.shape[0] >= 1 else np.asarray(fallback_floor)
        floor_src = "own rest" if rest_ep.shape[0] >= 1 else "calibration rest"
        rms, starts, clip_ch, clip_ep = _window_rms_detail(pf, chans, t_file, t7, t_end, sos, n, params.clip_uv)
        if rms.shape[0] < 1:
            raise MvcEmgError("effort %d of %s is shorter than one epoch" % (i, os.path.basename(poly5_path)))
        # Artefacts during a maximal effort (electrode pops, cable tugs) are far larger than EMG: a channel that
        # clips beyond clip_uv or sits above 10x the median channel is left out of this effort; epochs in which
        # most remaining channels clip are not eligible for the peak. The amplitude is the median over channels.
        ch_med = np.median(rms, axis=0)
        bad = (clip_ch > params.clip_fraction) | (ch_med > 10.0 * max(float(np.median(ch_med)), 1e-9))
        keep = ~bad if int((~bad).sum()) >= 2 else np.ones(len(chans), bool)
        if bad.any() and int((~bad).sum()) >= 2:
            warnings.append("effort %d: channel(s) %s left out (artefact: clipped or >10x the other channels)"
                            % (i, ", ".join(c for c, b in zip(chosen, bad) if b)))
        corr = np.sqrt(np.clip(rms ** 2 - floor[None, :] ** 2, 0, None))
        emg = np.median(corr[:, keep], axis=1)
        ok_ep = (clip_ep[:, keep] > 0).mean(axis=1) < 0.5
        if not ok_ep.any():
            ok_ep = np.ones(len(emg), bool)
        k = int(np.flatnonzero(ok_ep)[np.argmax(emg[ok_ep])])
        pairs = [(emg[j] + emg[j + 1]) / 2 for j in range(len(emg) - 1) if ok_ep[j] and ok_ep[j + 1]]
        best1 = max(pairs) if pairs else float(emg[k])
        efforts.append({"file": os.path.basename(poly5_path), "index": i, "t_rest": float(t6), "t_max": float(t7), "peak_uV": float(emg[k]),
                        "peak_t": float(starts[k]), "best_1s_uV": float(best1), "floor_uV": float(np.median(floor[keep])), "floor_source": floor_src,
                        "snr": float(np.median(rms[k, keep]) / max(float(np.median(floor[keep])), 1e-9)),
                        "channels_used": [c for c, kp in zip(chosen, keep) if kp], "epochs_skipped": int((~ok_ep).sum())})
        # trace from rest start to effort end for the figure
        span_rms, span_t, _ = _window_rms(pf, chans, t_file, t6, t_end, sos, n, params.clip_uv)
        span = np.median(np.sqrt(np.clip(span_rms ** 2 - floor[None, :] ** 2, 0, None))[:, keep], axis=1) if span_rms.shape[0] else np.empty(0)
        traces.append({"t": (span_t - t7).tolist(), "emg": span.tolist(), "label": "%s effort %d" % (os.path.basename(poly5_path)[:28], i)})
    pf.close()
    return efforts, traces, warnings


# ----------------------------------------------------------------------------- fits
def _fit_linear(F, E):
    b, a = np.polyfit(F, E, 1); return {"a": float(a), "b": float(b)}


def _eval_linear(p, F):
    return p["a"] + p["b"] * F


def _inv_linear(p, E, fmax):
    return (E - p["a"]) / p["b"] if p["b"] > 0 else float("nan")


def _fit_power(F, E):
    m = (F > 0) & (E > 0)
    if m.sum() < 2:
        raise ValueError("power fit needs positive values")
    b, logk = np.polyfit(np.log(F[m]), np.log(E[m]), 1); return {"k": float(np.exp(logk)), "b": float(b)}


def _eval_power(p, F):
    return p["k"] * np.power(np.clip(F, 1e-9, None), p["b"])


def _inv_power(p, E, fmax):
    return float(np.exp((np.log(E) - np.log(p["k"])) / p["b"])) if (p["b"] > 0 and E > 0) else float("nan")


def _fit_quadratic(F, E):
    c, b, a = np.polyfit(F, E, 2); return {"a": float(a), "b": float(b), "c": float(c)}


def _eval_quadratic(p, F):
    return p["a"] + p["b"] * F + p["c"] * F * F


def _inv_quadratic(p, E, fmax):
    roots = np.roots([p["c"], p["b"], p["a"] - E])
    roots = roots[np.isreal(roots)].real
    ok = [r for r in roots if r > fmax and (p["b"] + 2 * p["c"] * r) > 0]
    return float(min(ok)) if ok else float("nan")


MODELS = {"linear": (_fit_linear, _eval_linear, _inv_linear), "power": (_fit_power, _eval_power, _inv_power),
          "quadratic": (_fit_quadratic, _eval_quadratic, _inv_quadratic)}


def fit_all(F, E):
    out = {}
    for name, (fit, ev, _) in MODELS.items():
        try:
            p = fit(F, E)
            resid = E - ev(p, F)
            ss = float(np.sum((E - E.mean()) ** 2))
            out[name] = {"params": p, "rmse_uV": float(np.sqrt(np.mean(resid ** 2))), "r2": float(1 - np.sum(resid ** 2) / ss) if ss > 0 else float("nan")}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"params": None, "rmse_uV": float("nan"), "r2": float("nan"), "error": str(exc)}
    return out


def invert_all(fits, E_mvc, fmax):
    return {name: (float(inv(fits[name]["params"], E_mvc, fmax)) if fits[name]["params"] else float("nan")) for name, (_, _, inv) in MODELS.items()}


def bootstrap(F, E, T, mvc_values, n_boot, fmax, seed=0):
    rng = np.random.default_rng(seed)
    groups = np.unique(T)
    est = {k: [] for k in MODELS}
    for _ in range(int(n_boot)):
        if len(groups) > 1:
            pick = rng.choice(groups, size=len(groups), replace=True)
            idx = np.concatenate([np.flatnonzero(T == g) for g in pick])
        else:
            idx = rng.integers(0, len(F), len(F))
        mv = rng.choice(mvc_values, size=len(mvc_values), replace=True)
        inv = invert_all(fit_all(F[idx], E[idx]), float(np.max(mv)), fmax)
        for k, v in inv.items():
            est[k].append(v)
    out = {}
    for k, v in est.items():
        a = np.array(v); a = a[np.isfinite(a)]
        out[k] = {"p2.5": float(np.percentile(a, 2.5)) if a.size else float("nan"), "p50": float(np.percentile(a, 50)) if a.size else float("nan"),
                  "p97.5": float(np.percentile(a, 97.5)) if a.size else float("nan")}
    return out


def leverage(F, f_hat):
    F = np.asarray(F, float); n = len(F); sxx = float(np.sum((F - F.mean()) ** 2))
    return float(np.sqrt(1 + 1 / n + (f_hat - F.mean()) ** 2 / sxx)) if sxx > 0 else float("nan")


# ----------------------------------------------------------------------------- the whole run
def run(rah_log, mvc_logs, tmsi_dir=None, tag="", params=None, progress=None, out_dir=None, mvc_poly5_override=None, cal_poly5_override=None):
    """Full analysis. rah_log: path of the calibration trial's _log.txt. The Poly5 files are either given
    directly (cal_poly5_override, mvc_poly5_override) or found by name in tmsi_dir with the file tag.
    mvc_logs: the MVC_EMG trial logs (entries may be None when the Poly5 is given directly)."""
    params = params or Params()
    progress = progress or (lambda s: None)
    res = Result(tag=tag, force_quantity=params.force_quantity, params=asdict(params))
    prefix = rf.prefix_of(rah_log)
    res.calibration_stem = os.path.basename(prefix)
    if cal_poly5_override:
        cal_poly5 = cal_poly5_override
    else:
        cal_poly5, note = rf.find_poly5(tmsi_dir, res.calibration_stem, tag)
        if note:
            res.warnings.append("calibration: " + note)
    res.calibration_poly5 = cal_poly5
    try:
        cal = load_calibration(prefix, cal_poly5, params, progress)
    except MvcEmgError as exc:
        if "trigger codes differ" not in str(exc):
            raise
        # The chosen rig log is not the trial inside this Poly5 (one TMSi recording often spans a stopped
        # attempt and the real trial): try the other trials of the same folder, nearest in time first.
        folder = os.path.dirname(os.path.abspath(rah_log))
        info = rf.parse_poly5_name(cal_poly5)
        stamp = info["stamp"] if info else rf.stamp_of(res.calibration_stem)
        others = [(s, p) for s, p in rf.list_trials(os.path.dirname(folder), os.path.basename(folder)) if p != os.path.abspath(rah_log) and s != res.calibration_stem]
        if stamp is not None:
            others.sort(key=lambda sp: abs(((rf.stamp_of(sp[0]) or stamp) - stamp).total_seconds()))
        cal = None
        for s, p in others[:12]:
            try:
                progress("trying rig trial %s" % s)
                cal = load_calibration(rf.prefix_of(p), cal_poly5, params, progress)
            except MvcEmgError:
                continue
            res.warnings.append("the chosen rig log %s does not match the triggers in the TMSi file; used %s instead" % (res.calibration_stem, s))
            if out_dir and os.path.basename(os.path.normpath(out_dir)) == res.calibration_stem + "__mvc_from_emg":
                out_dir = os.path.join(os.path.dirname(os.path.normpath(out_dir)), s + "__mvc_from_emg")   # name the outputs after the trial used
            res.calibration_stem = s
            prefix = rf.prefix_of(p)
            break
        if cal is None:
            raise MvcEmgError("%s\nNo other trial in %s matches either: choose the rig log of the trial that was running while this TMSi file was recorded." % (exc, folder))
    res.alignment = cal["align"]; res.force_sign = int(cal["sign"])
    if cal["align"]["max_residual_ms"] > 20:
        res.warnings.append("alignment residual %.1f ms is large: check the trigger pairing" % cal["align"]["max_residual_ms"])
    progress("EMG of the holds")
    c = analyse_calibration(cal, params, progress)
    res.holds, res.epochs, res.ranking, res.excluded, res.chosen = c["holds"], c["epochs"], c["ranking"], c["excluded"], c["chosen"]
    if c.get("quality_note"):
        res.warnings.append("signal quality: " + c["quality_note"])
    # traces for the figure: force (downsampled) and target
    t_lc, force = cal["t_lc"], cal["force"]
    step = max(1, len(t_lc) // 3000)
    sched = rf.schedule_from_log(cal["log"])
    res.traces["force_t"] = t_lc[::step].tolist(); res.traces["force"] = force[::step].tolist()
    res.traces["target_t"] = [p for p, _ in sched]; res.traces["target"] = [v * cal["mvc_value"] / 100.0 for _, v in sched]
    cal["pf"].close()

    mvc_files = []
    mvc_logs = list(mvc_logs or [])
    if mvc_poly5_override and len(mvc_logs) < len(mvc_poly5_override):
        mvc_logs += [None] * (len(mvc_poly5_override) - len(mvc_logs))
    for i, ml in enumerate(mvc_logs):
        if mvc_poly5_override and i < len(mvc_poly5_override) and mvc_poly5_override[i]:
            path = mvc_poly5_override[i]
            stem = os.path.basename(rf.prefix_of(ml)) if ml else os.path.splitext(os.path.basename(path))[0]
        else:
            if not ml:
                raise MvcEmgError("no MVC Poly5 file and no MVC_EMG trial given")
            stem = os.path.basename(rf.prefix_of(ml))
            path, note = rf.find_poly5(tmsi_dir, stem, tag)
            if note:
                res.warnings.append("%s: %s" % (stem, note))
        res.mvc_stems.append(stem)
        mvc_files.append(path)
    res.mvc_poly5 = mvc_files
    efforts, traces = [], []
    for ml, path in zip(mvc_logs, mvc_files):
        p_eff = params
        if ml and os.path.isfile(ml):   # the MVC_EMG log carries the effort length and the codes used
            mlog = rf.parse_log(ml)
            try:
                p_eff = replace(params, mvc_max_s=float(mlog.get("max_s", params.mvc_max_s)))
            except ValueError:
                pass
        e, tr, w = analyse_mvc(path, c["chosen"], p_eff, progress, c["rest_chosen"])
        efforts += e; traces += tr; res.warnings += w
    res.efforts = efforts
    res.traces["efforts"] = traces
    peaks = np.array([e["peak_uV"] for e in efforts]) if efforts else np.empty(0)
    top_hold_emg = max((h["emg_uV"] for h in res.holds), default=float("nan"))
    if peaks.size and np.isfinite(top_hold_emg) and top_hold_emg > 0 and peaks.max() > 5.0 * top_hold_emg:
        res.warnings.append("EMG_MVC (%.0f uV) is %.1f x the EMG at the top calibration level (%.0f uV): an artefact in the effort is likely; check the effort traces"
                            % (peaks.max(), peaks.max() / top_hold_emg, top_hold_emg))
    F = np.array([e["force_N"] for e in res.epochs]); E = np.array([e["emg_uV"] for e in res.epochs]); T = np.array([str(e["hold"]) for e in res.epochs])
    levels = sorted({round(h["force_N"], 1) for h in res.holds})
    if peaks.size:
        res.emg_mvc_uV = float(peaks.max())
        res.effort_spread_pct = float(100 * (peaks.max() - peaks.min()) / peaks.max()) if peaks.size > 1 else 0.0
    if peaks.size == 0:
        res.verdict = "No maximal effort analysed: calibration only."
    elif len(levels) < 3:
        res.verdict = "Only %d distinct hold level(s) (%s N): at least 3 are needed for the fit." % (len(levels), ", ".join("%.1f" % v for v in levels))
    else:
        progress("fitting and bootstrap")
        fmax = float(F.max())
        res.fits = fit_all(F, E)
        res.estimates_N = invert_all(res.fits, res.emg_mvc_uV, fmax)
        res.bootstrap = bootstrap(F, E, T, peaks.tolist(), params.n_boot, fmax)
        vals = np.array([v for v in res.estimates_N.values() if np.isfinite(v)])
        if np.isfinite(res.estimates_N.get("linear", float("nan"))):
            res.leverage = leverage(F, res.estimates_N["linear"])
        if vals.size >= 2:
            spread = 100 * (vals.max() - vals.min()) / vals.min()
            res.mvc_estimate_N = float(np.median(vals))
            if spread <= params.spread_limit_pct:
                res.verdict = ("Approximate MVC force: %.0f N (median of %d models, range %.0f-%.0f N, spread %.0f %%). Calibration reached %.1f N "
                               "(extrapolation factor %.1f)." % (res.mvc_estimate_N, vals.size, vals.min(), vals.max(), spread, fmax, res.mvc_estimate_N / fmax))
            else:
                res.verdict = ("The models disagree (%.0f-%.0f N, spread %.0f %% > %.0f %%): the EMG does not pin the maximum down. "
                               "Report it as above %.1f N, the top calibration level." % (vals.min(), vals.max(), spread, params.spread_limit_pct, fmax))
                res.mvc_estimate_N = float("nan")
        else:
            res.verdict = "The fits failed; check the hold epochs."
    if out_dir:
        res.out_dir = out_dir
        write_outputs(res, out_dir, progress)
    return res


# ----------------------------------------------------------------------------- text, figure, files
def summary_text(res):
    L = []
    L.append(res.verdict)
    L.append("")
    L.append("Calibration %s  <->  %s" % (res.calibration_stem, os.path.basename(res.calibration_poly5)))
    a = res.alignment
    if a:
        L.append("  alignment: %d triggers, largest residual %.1f ms, drift %.0f ppm" % (a.get("n_triggers", 0), a.get("max_residual_ms", float("nan")), a.get("drift_ppm", 0)))
    L.append("  force quantity %s (sign %+d), %d holds:" % (res.force_quantity, res.force_sign, len(res.holds)))
    for h in res.holds:
        L.append("    hold %d: target %.2f N, measured %.2f N, EMG %.1f uV, baseline %.1f uV (%d epochs)" % (h["rep"], h["target_N"], h["force_N"], h["emg_uV"], h["baseline_uV"], h["n_epochs"]))
    n_good, n_all = len(res.ranking), len(res.ranking) + len(res.excluded)
    if res.params.get("explicit_channels"):
        how = "fixed by hand"
    elif int(res.params.get("n_best", 0) or 0) > 0:
        how = "best %d by SNR" % int(res.params["n_best"])
    else:
        how = "all channels that pass the quality rules"
    L.append("Channels kept: %d of %d good channels (%d in the file; %s): %s" % (len(res.chosen), n_good, n_all, how, ", ".join(res.chosen)))
    L.append("  EMG per hold and per effort = median over the kept channels of the noise-corrected 0.5 s RMS")
    if res.excluded:
        dead = [k for k, v in res.excluded.items() if v.startswith("no signal")]
        other = [(k, v) for k, v in res.excluded.items() if not v.startswith("no signal")]
        if dead:
            L.append("  no signal (%d channels, not connected?): %s" % (len(dead), ", ".join(dead)))
        if other:
            L.append("  excluded: " + "; ".join("%s (%s)" % (k, v) for k, v in other))
    L.append("  SNR ranking (score, rest RMS), top %d of %d: " % (min(10, n_good), n_good) + ", ".join("%s %.2f (%.1f uV)" % (l, s, r) for l, s, r in res.ranking[:10]))
    if res.efforts:
        L.append("Maximal efforts:")
        for e in res.efforts:
            extra = ""
            if e.get("channels_used") is not None and len(e["channels_used"]) < len(res.chosen):
                extra += ", channels %s" % "+".join(e["channels_used"])
            if e.get("epochs_skipped"):
                extra += ", %d clipped epoch(s) skipped" % e["epochs_skipped"]
            L.append("    %s #%d: peak %.1f uV at %.1f s after the cue (best 1 s %.1f uV, floor %.1f uV from %s, SNR %.1f%s)"
                     % (e["file"][:40], e["index"], e["peak_uV"], e["peak_t"] - e["t_max"], e["best_1s_uV"], e["floor_uV"], e["floor_source"], e["snr"], extra))
        L.append("  EMG_MVC = %.1f uV, spread between efforts %.1f %%" % (res.emg_mvc_uV, res.effort_spread_pct))
    if res.fits:
        L.append("Fits (force -> EMG) and inversions at EMG_MVC:")
        for name, f in res.fits.items():
            p = f.get("params")
            ptxt = ", ".join("%s=%.4g" % (k, v) for k, v in p.items()) if p else "failed"
            b = res.bootstrap.get(name, {})
            L.append("    %-9s %-32s R2 %.3f  F_MVC %.1f N  (bootstrap %.1f-%.1f)" % (name, ptxt, f["r2"], res.estimates_N.get(name, float("nan")), b.get("p2.5", float("nan")), b.get("p97.5", float("nan"))))
        if np.isfinite(res.leverage):
            L.append("  leverage of the linear inverse prediction: %.1f" % res.leverage)
    for w in res.warnings:
        L.append("note: " + w)
    return "\n".join(L)


def render_png(res, width=900, height=620, dpi=100):
    """Overview figure as PNG bytes, drawn with the Agg canvas (safe in a worker thread)."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    FigureCanvasAgg(fig)
    ax1 = fig.add_subplot(2, 2, 1); ax2 = fig.add_subplot(2, 2, 2); ax3 = fig.add_subplot(2, 2, 3); ax4 = fig.add_subplot(2, 2, 4)
    tr = res.traces
    if tr.get("force_t"):
        ax1.plot(tr["force_t"], tr["force"], color="0.3", lw=0.7, label="measured %s (N)" % res.force_quantity)
        ax1.plot(tr["target_t"], tr["target"], color="tab:orange", lw=1.2, label="target (N)")
        for h in res.holds:
            ax1.axvspan(h["win"][0], h["win"][1], color="tab:green", alpha=0.2)
            ax1.axvspan(h["rest_win"][0], h["rest_win"][1], color="0.5", alpha=0.12)
        ax1.set_title("calibration trial (green = analysed, grey = rest floor)", fontsize=9, loc="left")
        ax1.set_xlabel("rig time (s)"); ax1.set_ylabel("force (N)"); ax1.legend(fontsize=7)
    for e in tr.get("efforts", []):
        ax2.plot(e["t"], e["emg"], lw=1, label=e["label"])
    ax2.axvline(0, color="k", lw=0.8, ls="--")
    ax2.set_title("maximal efforts, median of the kept channels (0 = MAX cue)", fontsize=9, loc="left")
    ax2.set_xlabel("time from the cue (s)"); ax2.set_ylabel("EMG RMS (uV)")
    if tr.get("efforts"):
        ax2.legend(fontsize=6)
    F = np.array([e["force_N"] for e in res.epochs]); E = np.array([e["emg_uV"] for e in res.epochs])
    for ax, xmax, title in ((ax3, (F.max() * 1.15 if F.size else 1), "calibration range"), (ax4, None, "extrapolated to EMG_MVC")):
        if F.size:
            ax.plot(F, E, "o", color="0.25", ms=3, label="hold epochs")
        if res.fits:
            if xmax is None:
                vals = [v for v in res.estimates_N.values() if np.isfinite(v)]
                xmax = (max(vals) * 1.15) if vals else F.max() * 3
            xx = np.linspace(0, xmax, 300)
            for name, (fit, ev, _) in MODELS.items():
                p = res.fits.get(name, {}).get("params")
                if p:
                    est = res.estimates_N.get(name, float("nan"))
                    ax.plot(xx, ev(p, xx), lw=1.2, label=("%s: %.0f N" % (name, est)) if np.isfinite(est) else ("%s: no solution (stays below EMG_MVC)" % name))
            ax.axhline(res.emg_mvc_uV, color="k", ls="--", lw=0.8, label="EMG_MVC")
            ax.set_xlim(0, max(xmax, 1e-3))
            ytop = max(res.emg_mvc_uV * 1.15, (E.max() * 1.3 if E.size else 1)) if ax is ax4 else (E.max() * 1.3 if E.size else 1)
            ax.set_ylim(0, max(float(ytop) if np.isfinite(ytop) else 1.0, 1.0))
        ax.set_title(title, fontsize=9, loc="left"); ax.set_xlabel("force (N)"); ax.set_ylabel("EMG RMS (uV)"); ax.legend(fontsize=7)
    fig.suptitle(res.verdict[:110], fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    fig.clear()
    return buf.getvalue()


def write_outputs(res, out_dir, progress=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(summary_text(res) + "\n")
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        fh.write(res.to_json())
    with open(os.path.join(out_dir, "hold_epochs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["hold", "t_s", "force_N", "emg_uV"])
        for e in res.epochs:
            w.writerow([e["hold"], "%.2f" % e["t"], "%.4f" % e["force_N"], "%.3f" % e["emg_uV"]])
    try:
        png = render_png(res)
        with open(os.path.join(out_dir, "overview.png"), "wb") as fh:
            fh.write(png)
    except Exception as exc:  # noqa: BLE001
        res.warnings.append("figure not written: %s" % exc)


# ----------------------------------------------------------------------------- CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="MVC from EMG: calibration holds + maximal efforts -> approximate MVC force")
    ap.add_argument("--rah-log", required=True, help="<stem>_log.txt of the square-wave calibration trial")
    ap.add_argument("--rah-poly5", default=None, help="Poly5 of the calibration trial, given directly instead of by name")
    ap.add_argument("--mvc-log", nargs="*", default=[], help="<stem>_log.txt of the MVC_EMG trial(s)")
    ap.add_argument("--mvc-poly5", nargs="*", default=[], help="Poly5 of the maximal efforts, given directly instead of by name")
    ap.add_argument("--tmsi-dir", default=None, help="TMSi recordings folder (needed when a Poly5 is not given directly)")
    ap.add_argument("--tag", default="FLX")
    ap.add_argument("--force", default="Fx", choices=list(rf.FORCE_QUANTITIES))
    ap.add_argument("--n-best", type=int, default=0, help="0 = every channel that passes the quality rules (default); N = only the best N by SNR")
    ap.add_argument("--channels", default="", help="comma-separated labels replacing the ranking")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--accept-rah-codes", action="store_true", help="testing: RAH holds stand in for maximal efforts")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    params = Params(force_quantity=a.force, n_best=a.n_best, explicit_channels=tuple(s.strip() for s in a.channels.split(",") if s.strip()),
                    n_boot=a.boot, accept_rah_codes_for_mvc=a.accept_rah_codes)
    mvc_logs = list(a.mvc_log)
    override = list(a.mvc_poly5)
    if not override and not mvc_logs:
        ap.error("give --mvc-poly5 or --mvc-log")
    if not a.rah_poly5 and not a.tmsi_dir:
        ap.error("give --rah-poly5 or --tmsi-dir")
    t0 = time.perf_counter()
    res = run(a.rah_log, mvc_logs, a.tmsi_dir, a.tag, params, progress=lambda s: print("  ..", s), out_dir=a.out,
              mvc_poly5_override=override or None, cal_poly5_override=a.rah_poly5)
    print(summary_text(res))
    print("(%.1f s)" % (time.perf_counter() - t0))
    if a.out:
        print("outputs in", res.out_dir or a.out)
    return res

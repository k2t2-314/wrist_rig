"""Self-test without hardware: the fit on synthetic four-level data, the square-wave profile builder and,
when a Poly5 path is given, the reader and the trigger decoding.
Run from serial_manager:  python -m mvc_emg.selftest [some_file.poly5]"""
import sys

import numpy as np

from . import rigfiles as rf
from . import analysis as an


def test_fit():
    rng = np.random.default_rng(0)
    levels = [1, 5, 20, 30]
    F, E, T = [], [], []
    for rep, lv in enumerate(levels, start=1):
        for _ in range(6):
            f = lv * (1 + rng.normal(0, 0.03)); e = (5 + 6 * f ** 1.1) * (1 + rng.normal(0, 0.08))
            F.append(f); E.append(e); T.append(str(rep))
    F, E, T = np.array(F), np.array(E), np.array(T)
    true_max = 60.0
    emg_max = 5 + 6 * true_max ** 1.1
    fits = an.fit_all(F, E)
    est = an.invert_all(fits, emg_max, float(F.max()))
    boot = an.bootstrap(F, E, T, [emg_max, emg_max * 0.95], 200, float(F.max()))
    print("synthetic fit (true maximum %.0f N):" % true_max)
    for k, v in est.items():
        print("  %-9s F_MVC %.1f N  bootstrap %.1f-%.1f" % (k, v, boot[k]["p2.5"], boot[k]["p97.5"]))
    assert all(np.isfinite(v) for v in est.values()), "an inversion failed"
    assert 40 < est["linear"] < 80, "linear estimate off"


def test_profile():
    pts = rf.square_wave_points([1, 5, 20, 30], 5, 10, 0.5)
    txt = rf.format_points(pts)
    print("square-wave profile:", txt)
    assert txt.startswith("(0,0),(10,0),(10.5,1),(15.5,1),(16,0),(26,0)")
    times = [t for t, _ in pts]
    assert all(b > a for a, b in zip(times, times[1:])), "times must be strictly increasing"
    assert rf.direction_sign("Fx", "Right", "flexion") == -1 and rf.direction_sign("Fx", "Right", "extension") == 1
    assert rf.stem_through_date("01_RAH_R_flex_20261028_124850") == "01_RAH_R_flex_20261028"


def test_poly5(path):
    from . import poly5 as p5
    pf = p5.Poly5File(path)
    grid = p5.grid_channels(pf.names)
    ti = p5.trigger_index(pf.names, pf.units)
    print("%s: %d channels at %d Hz, %.1f s, %d grid channels, TRIGGERS index %s" % (path, len(pf.names), pf.fs, pf.duration_s, len(grid), ti))
    if ti is not None:
        t, codes, info = p5.trigger_pulses(pf.channel(ti), pf.fs)
        print("  trigger idle %d, flat %s, %d pulses: %s" % (info["idle"], info["flat"], len(codes), " ".join("%d@%.2f" % (c, s) for c, s in zip(codes[:12], t[:12]))))
    w = pf.window(0, min(pf.n_samples, 4000), [i for i, _, _ in grid[:3]])
    print("  first window shape %s, RMS of the first grid channel %.1f uV" % (w.shape, float(np.sqrt(np.mean(w[:, 0] ** 2)))))
    pf.close()


if __name__ == "__main__":
    test_fit()
    test_profile()
    if len(sys.argv) > 1:
        test_poly5(sys.argv[1])
    print("selftest OK")

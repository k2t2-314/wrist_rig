"""
review_poly5.py — interactive viewer for TMSi SAGA .poly5 recordings

Usage:
    python review_poly5.py                  # file-picker dialog
    python review_poly5.py path/to/file.poly5

Displays (no files saved):
    1. Channel count and list
    2. Grid type detected from channel names
    3. Trigger channel(s) plotted over time
"""

import sys
import os
import re

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ── locate Poly5Reader ────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                               # find config.py
import config                                           # inserts TMSI_SDK_PATH
from TMSiFileFormats.file_readers.poly5reader import Poly5Reader


# ── helpers ───────────────────────────────────────────────────────────────────

def _detect_grid(ch_names):
    """Return a human-readable grid-type string."""
    rc = []
    for name in ch_names:
        m = re.fullmatch(r"R(\d+)C(\d+)", name, re.IGNORECASE)
        if m:
            rc.append((int(m.group(1)), int(m.group(2))))

    if not rc:
        # Check for BIP-only layout
        bip = [n for n in ch_names if n.upper().startswith("BIP")]
        if bip:
            return f"Bipolar ({len(bip)} BIP channels, no grid)"
        return "No grid (free channels)"

    rows = max(r for r, _ in rc)
    cols = max(c for _, c in rc)
    return f"Textile grid  {rows} × {cols}  ({len(rc)} electrode channels)"


def _trigger_channels(ch_names, ch_units):
    """Return indices of channels that carry trigger/digital data."""
    indices = []
    for i, (name, unit) in enumerate(zip(ch_names, ch_units)):
        name_up = name.upper()
        unit_up = unit.upper()
        if (unit_up in ("BIT", "BITS")
                or "TRIG" in name_up
                or "DIGI" in name_up
                or "STATUS" in name_up
                or name_up in ("TRIGGERS", "TRIGGER")):
            indices.append(i)
    return indices


def _bip_channels(ch_names, wanted=("BIP 01", "BIP 02", "BIP01", "BIP02")):
    """Return list of (index, ch_name) for BIP 01 / BIP 02 channels."""
    result = []
    for i, name in enumerate(ch_names):
        if name.upper().replace(" ", "") in [w.upper().replace(" ", "") for w in wanted]:
            result.append((i, name))
    return result


def _expand_status_bits(samples, ch_names, trig_idx, n_bits=16):
    """
    For any STATUS channel, return per-bit signals as a list of
    (label, signal_array) tuples. Other trigger channels are returned as-is.
    """
    expanded = []
    for idx in trig_idx:
        name = ch_names[idx].upper()
        if "STATUS" in name:
            raw = samples[idx, :].astype(np.int32)
            for bit in range(n_bits):
                bit_sig = ((raw >> bit) & 1).astype(np.float32)
                if bit_sig.any():           # skip always-zero bits
                    expanded.append((f"STATUS bit {bit}  (IN{bit})", bit_sig))
            # also keep raw STATUS for reference
            expanded.insert(0, (f"{ch_names[idx]} (raw)", samples[idx, :]))
        else:
            expanded.append((ch_names[idx], samples[idx, :]))
    return expanded


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    # ── load file ─────────────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        data = Poly5Reader(filepath)
    else:
        data = Poly5Reader()          # shows file-picker dialog

    ch_names  = data.ch_names
    ch_units  = data.ch_unit_names
    samples   = data.samples          # shape: [channels, samples]
    fs        = data.sample_rate
    n_ch      = data.num_channels
    t_start   = data.start_time

    # ── 1. channel summary ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  File     : {data.filename}")
    print(f"  Recorded : {t_start}")
    print(f"  Channels : {n_ch}   |   Sample rate : {fs} Hz")
    print(f"  Duration : {samples.shape[1] / fs:.1f} s  "
          f"({samples.shape[1]} samples)")
    print("-" * 60)
    print(f"  {'#':<5} {'Name':<20} {'Unit'}")
    print(f"  {'-'*5} {'-'*20} {'-'*10}")
    for i, (name, unit) in enumerate(zip(ch_names, ch_units)):
        print(f"  {i:<5} {name:<20} {unit}")

    # ── 2. grid type ──────────────────────────────────────────────────────────
    grid_str = _detect_grid(ch_names)
    print("-" * 60)
    print(f"  Grid type: {grid_str}")
    print("=" * 60 + "\n")

    # ── 3. trigger plot ───────────────────────────────────────────────────────
    trig_idx = _trigger_channels(ch_names, ch_units)

    if not trig_idx:
        print("No trigger/digital channels found - nothing to plot.")
        return

    if samples.shape[1] == 0:
        print("WARNING: file contains 0 samples - no data to plot.")
        print("         Try a longer recording, or check that Stop Recording")
        print("         was called before closing the app.")
        return

    t = np.arange(samples.shape[1]) / fs          # time axis (seconds)
    n_trig = len(trig_idx)

    # expand STATUS bits → individual bit traces
    trig_traces = _expand_status_bits(samples, ch_names, trig_idx)

    fig = plt.figure(figsize=(14, 2.5 * len(trig_traces) + 1))
    fig.suptitle(
        f"Trigger channels — {os.path.basename(data.filename)}\n"
        f"Start: {t_start}  |  {fs} Hz  |  {samples.shape[1]/fs:.1f} s",
        fontsize=10,
    )
    gs = gridspec.GridSpec(len(trig_traces), 1, hspace=0.45)

    for row, (label, sig) in enumerate(trig_traces):
        ax = fig.add_subplot(gs[row])

        # step-plot is clearest for digital/trigger data
        ax.step(t, sig, where="post", linewidth=0.8, color="tab:blue")
        ax.fill_between(t, sig, step="post", alpha=0.15, color="tab:blue")

        ax.set_ylabel(label, fontsize=8)
        ax.set_xlim(t[0], t[-1])

        # annotate rising edges (value changes upward)
        diff = np.diff(sig.astype(float), prepend=sig[0])
        edges = np.where(diff > 0)[0]
        for e in edges:
            ax.axvline(t[e], color="tab:red", linewidth=0.6, alpha=0.6)
            ax.text(t[e], ax.get_ylim()[1], f"{sig[e]:.0f}",
                    fontsize=6, color="tab:red", ha="left", va="top")

        if row == len(trig_traces) - 1:
            ax.set_xlabel("Time (s)", fontsize=9)

    plt.tight_layout()
    plt.show()

    # ── 4. BIP 01 / BIP 02 plot ───────────────────────────────────────────────
    bip_info = _bip_channels(ch_names)

    if not bip_info:
        print("No BIP 01 / BIP 02 channels found - skipping BIP plot.")
    else:
        fig_bip, axes_bip = plt.subplots(
            len(bip_info), 1,
            figsize=(14, 3 * len(bip_info) + 1),
            sharex=True,
            squeeze=False,
        )
        fig_bip.suptitle(
            f"BIP channels — {os.path.basename(data.filename)}\n"
            f"Start: {t_start}  |  {fs} Hz  |  {samples.shape[1]/fs:.1f} s",
            fontsize=10,
        )

        for row, (idx, name) in enumerate(bip_info):
            ax = axes_bip[row, 0]
            unit = ch_units[idx]
            sig_bip = samples[idx, :]

            ax.plot(t, sig_bip, linewidth=0.7, color="tab:green")
            ax.set_ylabel(f"{name}\n({unit})", fontsize=8)
            ax.set_xlim(t[0], t[-1])
            ax.grid(True, linewidth=0.4, alpha=0.5)

        axes_bip[-1, 0].set_xlabel("Time (s)", fontsize=9)
        fig_bip.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()

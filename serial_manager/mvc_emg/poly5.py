"""Minimal TMSi Poly5 (v2) reader and trigger decoding for the MVC-from-EMG analysis.

Needs numpy only (imported here, so import this module lazily). The channel order of the file is kept as
recorded (hardware order); grid channels are found by their names R<row>C<col>, the trigger channel by the
name TRIGGERS. Layout from the SDK's poly5reader: 217-byte header, then two 136-byte descriptors per channel,
then blocks of 86-byte header + samples_per_block x n_channels float32, sample-major.
"""
from __future__ import annotations

import os
import re
import struct

import numpy as np

HEADER_BYTES = 217
DESCRIPTOR_BYTES = 136
BLOCK_HEADER_BYTES = 86
GRID_NAME = re.compile(r"R(\d+)C(\d+)")


class Poly5Error(Exception):
    pass


def read_header(path):
    with open(path, "rb") as f:
        head = f.read(HEADER_BYTES)
        if len(head) < HEADER_BYTES:
            raise Poly5Error("%s is too short to be a Poly5 file" % path)
        fs = struct.unpack_from("<h", head, 114)[0]
        n_ch = struct.unpack_from("<h", head, 119)[0] // 2
        n_samples = struct.unpack_from("<i", head, 121)[0]
        n_blocks, period = struct.unpack_from("<iH", head, 143)
        descriptors = [f.read(DESCRIPTOR_BYTES) for _ in range(2 * n_ch)]
        offset = f.tell()
    names = [d[1:1 + d[0]].decode("latin1")[5:] for d in descriptors[::2]]
    units = [d[46:46 + d[46]].decode("latin1", "replace") if len(d) > 46 else "" for d in descriptors[::2]]
    block_bytes = BLOCK_HEADER_BYTES + 4 * period * n_ch
    size = os.path.getsize(path)
    blocks_on_disk = (size - offset) // block_bytes if block_bytes > 0 else 0
    if n_blocks <= 0 or n_samples <= 0 or n_blocks > blocks_on_disk:
        # file not closed properly: the writer updates the counts on close; trust the file size
        n_blocks = int(blocks_on_disk)
        n_samples = n_blocks * period
    return {"fs": int(fs), "n_ch": int(n_ch), "n_samples": int(n_samples), "n_blocks": int(n_blocks),
            "period": int(period), "offset": int(offset), "names": names, "units": units, "path": path}


def read_poly5(path, channels=None):
    """names, fs, data (samples x channels, float32) and the file indices of the returned channels.
    channels: list of file indices to keep (None = all). Only the kept channels are copied into memory."""
    h = read_header(path)
    if h["n_blocks"] <= 0:
        raise Poly5Error("%s holds no data blocks" % path)
    block = np.dtype([("header", "V%d" % BLOCK_HEADER_BYTES), ("data", "<f4", (h["period"], h["n_ch"]))])
    mm = np.memmap(path, block, "r", h["offset"], h["n_blocks"])["data"]
    if channels is None:
        channels = list(range(h["n_ch"]))
        arr = np.asarray(mm).reshape(-1, h["n_ch"])[:h["n_samples"]]
    else:
        channels = [int(c) for c in channels]
        arr = np.asarray(mm[:, :, channels]).reshape(-1, len(channels))[:h["n_samples"]]
    del mm
    names = [h["names"][c] for c in channels]
    return names, h["fs"], np.ascontiguousarray(arr), channels


class Poly5File:
    """Memory-mapped Poly5: whole single channels or sample windows of a few channels are copied on demand,
    so a long recording never has to be loaded completely."""

    def __init__(self, path):
        self.h = read_header(path)
        if self.h["n_blocks"] <= 0:
            raise Poly5Error("%s holds no data blocks" % path)
        self.path = path
        self.names = self.h["names"]
        self.units = self.h["units"]
        self.fs = self.h["fs"]
        self.n_samples = self.h["n_samples"]
        self.period = self.h["period"]
        block = np.dtype([("header", "V%d" % BLOCK_HEADER_BYTES), ("data", "<f4", (self.period, self.h["n_ch"]))])
        self._mm = np.memmap(path, block, "r", self.h["offset"], self.h["n_blocks"])["data"]

    @property
    def duration_s(self):
        return self.n_samples / float(self.fs)

    def channel(self, idx):
        """One whole channel as float32 (samples,)."""
        return np.ascontiguousarray(np.asarray(self._mm[:, :, int(idx)]).reshape(-1)[:self.n_samples])

    def window(self, i0, i1, channels):
        """Samples i0:i1 of the given file channels as float32 (samples x channels)."""
        i0, i1 = max(0, int(i0)), min(self.n_samples, int(i1))
        if i1 <= i0:
            return np.empty((0, len(channels)), dtype=np.float32)
        b0, b1 = i0 // self.period, (i1 + self.period - 1) // self.period
        arr = np.asarray(self._mm[b0:b1][:, :, [int(c) for c in channels]]).reshape(-1, len(channels))
        return np.ascontiguousarray(arr[i0 - b0 * self.period:i1 - b0 * self.period])

    def close(self):
        self._mm = None


def grid_channels(names):
    """[(index in `names`, row, col)] of the EMG channels R<row>C<col>, row-major."""
    out = []
    for i, n in enumerate(names):
        m = GRID_NAME.fullmatch(n.strip())
        if m:
            out.append((i, int(m.group(1)), int(m.group(2))))
    return sorted(out, key=lambda x: (x[1], x[2]))


def trigger_index(names, units=None):
    """Index of the TRIGGERS channel (by name, then by unit BIT/BITS or a name containing TRIG)."""
    for i, n in enumerate(names):
        if n.strip().upper() == "TRIGGERS":
            return i
    if units:
        for i, u in enumerate(units):
            if u.strip().upper() in ("BIT", "BITS"):
                return i
    for i, n in enumerate(names):
        if "TRIG" in n.upper():
            return i
    return None


def trigger_pulses(trig, fs):
    """Onset times (s) and codes of the TTL pulses in a TRIGGERS channel.
    The channel idles at one value (15 on the rig: inputs are active low) and a pulse of code c reads
    idle XOR c. Pulses with code 15 (the rig's stop marker) and 0 are dropped. Returns (t, codes, info)."""
    v = np.asarray(np.rint(trig), dtype=np.int64)
    v = np.clip(v, 0, 15)
    counts = np.bincount(v, minlength=16)
    idle = int(np.argmax(counts))
    info = {"idle": idle, "flat": bool(counts[idle] == v.size)}
    if info["flat"]:
        return np.empty(0), np.empty(0, dtype=int), info
    active = np.r_[0, (v != idle).astype(np.int8), 0]
    starts = np.flatnonzero(np.diff(active) == 1)
    ends = np.flatnonzero(np.diff(active) == -1)
    codes = np.array([int(np.bincount(v[a:b] ^ idle, minlength=16)[1:].argmax() + 1) for a, b in zip(starts, ends)], dtype=int)
    widths = (ends - starts) / float(fs)
    keep = (codes != 15) & (codes != 0) & (widths >= 0.003)   # the firmware pulses last 20 ms; drop glitches
    info["n_raw"] = int(len(starts))
    return starts[keep] / float(fs), codes[keep], info

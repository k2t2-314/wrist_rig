"""Streaming One Euro smoothing for display feedback, using real timestamps.

Algorithm: Casiez, Roussel and Vogel (CHI 2012), https://gery.casiez.net/1euro/
This module has no acquisition, recording, device or GUI dependencies.
"""

import math


class OneEuroFilter:
    """Adaptive first-order low-pass filter with a filtered signal derivative.

    beta depends on input units. RAH supplies fractions of MVC (0.1 = 10% MVC),
    rather than force in N/Nmm or pixel coordinates. Timestamps are monotonic
    host seconds from the LC ring buffer. A long gap resets the filter so that
    a reconnect does not create a velocity estimate across missing data.
    """

    def __init__(self, min_cutoff_hz=1.0, beta=0.0, derivative_cutoff_hz=1.0,
                 max_gap_s=0.5):
        self.min_cutoff_hz = float(min_cutoff_hz)
        self.beta = float(beta)
        self.derivative_cutoff_hz = float(derivative_cutoff_hz)
        self.max_gap_s = float(max_gap_s)
        parameters = (self.min_cutoff_hz, self.beta, self.derivative_cutoff_hz, self.max_gap_s)
        if (not all(math.isfinite(value) for value in parameters)
                or self.min_cutoff_hz <= 0 or self.beta < 0
                or self.derivative_cutoff_hz <= 0 or self.max_gap_s <= 0):
            raise ValueError("Cutoffs and max gap must be positive; beta must be nonnegative.")
        self.reset()

    def reset(self):
        self._timestamp = None
        self._raw = None
        self._value = None
        self._derivative = 0.0
        self.cutoff_hz = self.min_cutoff_hz

    @staticmethod
    def _alpha(cutoff_hz, dt_s):
        return 1.0 / (1.0 + 1.0 / (2.0 * math.pi * cutoff_hz * dt_s))

    def _seed(self, value, timestamp):
        self._timestamp = timestamp
        self._raw = self._value = value
        self._derivative = 0.0
        self.cutoff_hz = self.min_cutoff_hz
        return value

    def update(self, value, timestamp):
        value, timestamp = float(value), float(timestamp)
        if not math.isfinite(value) or not math.isfinite(timestamp):
            raise ValueError("Display value and timestamp must be finite.")
        if self._timestamp is None:
            return self._seed(value, timestamp)
        dt_s = timestamp - self._timestamp
        if dt_s == 0.0:
            # A GUI redraw can see the same LC sample; do not filter it twice.
            return self._value
        if dt_s < 0.0 or dt_s > self.max_gap_s:
            return self._seed(value, timestamp)
        derivative = (value - self._raw) / dt_s
        if not math.isfinite(derivative):
            return self._seed(value, timestamp)
        alpha_d = self._alpha(self.derivative_cutoff_hz, dt_s)
        self._derivative += alpha_d * (derivative - self._derivative)
        self.cutoff_hz = self.min_cutoff_hz + self.beta * abs(self._derivative)
        alpha = self._alpha(self.cutoff_hz, dt_s)
        self._value += alpha * (value - self._value)
        self._raw, self._timestamp = value, timestamp
        return self._value

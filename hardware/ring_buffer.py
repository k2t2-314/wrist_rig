import bisect
import collections
import threading


class InterpRingBuffer:
    """
    Thread-safe ring buffer storing (timestamp, sample) pairs.

    Methods:
        append(ts, sample)              — add a new sample
        get_latest()                    — most recent (sample, ts)
        get_interpolated(query_ts)      — linearly interpolated sample at query_ts
        get_since(last_ts)              — all samples after last_ts
        clear()                         — reset buffer
    """

    def __init__(self, maxlen: int = 4000):
        self._lock = threading.Lock()
        self._ts   = collections.deque(maxlen=maxlen)
        self._data = collections.deque(maxlen=maxlen)

    def append(self, ts: float, sample):
        with self._lock:
            self._ts.append(ts)
            self._data.append(sample)

    def get_latest(self):
        """Return (sample, ts) for the most recent entry, or (None, None)."""
        with self._lock:
            if not self._ts:
                return None, None
            return list(self._data[-1]), self._ts[-1]

    def get_interpolated(self, query_ts: float, max_dt: float = 0.05):
        """
        Return (sample, ts) linearly interpolated at query_ts.
        Returns (None, None) if no samples are within max_dt.
        """
        with self._lock:
            n = len(self._ts)
            if n == 0:
                return None, None
            ts_list   = list(self._ts)
            data_list = list(self._data)

        idx = bisect.bisect_left(ts_list, query_ts)
        if idx == 0:
            return (list(data_list[0]), ts_list[0]) \
                if abs(ts_list[0] - query_ts) <= max_dt else (None, None)
        if idx >= n:
            return (list(data_list[-1]), ts_list[-1]) \
                if abs(ts_list[-1] - query_ts) <= max_dt else (None, None)

        t0, t1 = ts_list[idx - 1], ts_list[idx]
        if abs(t0 - query_ts) > max_dt and abs(t1 - query_ts) > max_dt:
            return None, None
        dt = t1 - t0
        if dt <= 0:
            return list(data_list[idx]), t1
        alpha = (query_ts - t0) / dt
        s0, s1 = data_list[idx - 1], data_list[idx]
        nch = min(len(s0), len(s1))
        return [s0[i] + alpha * (s1[i] - s0[i]) for i in range(nch)], query_ts

    def get_since(self, last_ts: float) -> list:
        """Return all (ts, sample) pairs with ts > last_ts."""
        with self._lock:
            if not self._ts:
                return []
            ts_list   = list(self._ts)
            data_list = list(self._data)
        idx = bisect.bisect_right(ts_list, last_ts)
        return list(zip(ts_list[idx:], data_list[idx:]))

    def clear(self):
        with self._lock:
            self._ts.clear()
            self._data.clear()
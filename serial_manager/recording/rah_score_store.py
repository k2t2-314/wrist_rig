"""Keep one running total per Player ID in a local UTF-8 text file.

One line per player: ``<id> <collected>/<total> <points>`` and, when the player has sticker
runs, `` perfect=<n>`` (n = number of complete runs that collected at least STICKER_FRACTION of
their beans, 90 %; each one is a sticker on the leaderboard; the field keeps its historical
name). collected and total ADD UP over all of the player's complete runs: 45/93 after one run,
then 65/200 after a second run of 107 beans in which 20 were collected. points = collected. A
run stopped early is never added, and the operator GUI may leave a run off the board.
"""

from dataclasses import dataclass, replace
import os
from pathlib import Path
import tempfile
import threading
import time

try:
    from config import RAH_STICKER_FRACTION as STICKER_FRACTION
except ImportError:  # importable without the serial_manager config
    STICKER_FRACTION = 0.9

STICKER_FRACTION = max(0.0, min(1.0, float(STICKER_FRACTION)))
_WRITE_LOCK = threading.Lock()


def earns_sticker(collected, total):
    """True when a complete run collected at least STICKER_FRACTION of its beans (90 %: 84 of 93)."""
    collected, total = int(collected), int(total)
    return total > 0 and collected * 1000 >= int(round(STICKER_FRACTION * 1000)) * total


@dataclass(frozen=True)
class RAHScore:
    """A player's running total: beans collected and beans possible over all complete runs."""
    player_id: str
    collected: int
    total: int
    perfect_runs: int = 0   # sticker runs (at least STICKER_FRACTION of the beans): one sticker each

    def __post_init__(self):
        if not self.player_id or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                                     for char in self.player_id):
            raise ValueError("Player ID must be nonempty and contain no spaces or control characters.")
        if not 0 <= self.collected <= self.total:
            raise ValueError("Collected coins must be between zero and the total coin count.")
        if self.perfect_runs < 0:
            raise ValueError("The number of sticker runs cannot be negative.")

    @property
    def score(self):
        return self.collected

    @property
    def percent(self):
        return 100.0 * self.collected / self.total if self.total else 0.0

    @property
    def stickers(self):
        return self.perfect_runs

    def line(self):
        text = f"{self.player_id} {self.collected}/{self.total} {self.score}"
        if self.perfect_runs:
            text += f" perfect={self.perfect_runs}"
        return text


class RAHScoreStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / "rah_scores.txt"
        self._lock = _WRITE_LOCK

    @staticmethod
    def _accumulate(records):
        """One record per player: collected, total and sticker runs summed over the records."""
        totals = {}
        for record in records:
            previous = totals.get(record.player_id)
            if previous is None:
                totals[record.player_id] = record
            else:
                totals[record.player_id] = replace(
                    previous,
                    collected=previous.collected + record.collected,
                    total=previous.total + record.total,
                    perfect_runs=previous.perfect_runs + record.perfect_runs,
                )
        return list(totals.values())

    def _write(self, records, extra_lines=()):
        """Rewrite the file atomically: the running totals, then any line that is not a score
        (a comment or a damaged row) so that nothing typed into the file is ever lost."""
        payload = ''.join(record.line() + '\n' for record in records)
        payload += ''.join(line + '\n' for line in extra_lines)
        payload = payload.encode('utf-8')
        try:
            if self.path.read_bytes() == payload:
                return
        except FileNotFoundError:
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f'.{self.path.name}.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    # On Windows a virus scanner or an editor can hold the file for a moment.
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def append(self, player_id, collected, total):
        """Add a completed run to the player's running total (collected and total beans) and,
        when it collected at least STICKER_FRACTION of them, one to the sticker count. Returns
        the stored record with the new totals."""
        record = RAHScore(player_id, collected, total, perfect_runs=1 if earns_sticker(collected, total) else 0)
        with self._lock:
            records, extra_lines = self._read_all()
            records = self._accumulate(records + [record])
            self._write(records, extra_lines)
        return next(item for item in records if item.player_id == player_id)

    def compact(self):
        """Merge duplicate lines of a player (a hand-edited or legacy file) into one running total."""
        with self._lock:
            if not self.path.exists():
                return []
            records, extra_lines = self._read_all()
            records = self._accumulate(records)
            self._write(records, extra_lines)
        return records

    def _read_all(self):
        """(score records, other non-empty lines) of the file; [] and [] when it is missing."""
        try:
            # utf-8-sig also accepts a file saved with a BOM by Notepad.
            lines = self.path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            return [], []
        records = []
        extra_lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                tokens = line.split()
                if len(tokens) not in (3, 4):
                    raise ValueError("a score line has 3 or 4 fields")
                identifier, accuracy, points = tokens[:3]
                perfect_runs = 0
                if len(tokens) == 4:
                    if not tokens[3].startswith("perfect="):
                        raise ValueError("unknown fourth field")
                    perfect_runs = int(tokens[3][len("perfect="):])
                collected, total = (int(value) for value in accuracy.split("/"))
                record = RAHScore(identifier, collected, total, perfect_runs)
                if int(points) != record.score:
                    raise ValueError("points do not match the collected count")
            except (ValueError, TypeError):
                extra_lines.append(line.rstrip())
                continue
            records.append(record)
        return records, extra_lines

    def read(self, player_id=None):
        """Running totals, one record per player (duplicate lines of a player are summed)."""
        records, _extra_lines = self._read_all()
        records = self._accumulate(records)
        return [record for record in records if player_id is None or record.player_id == player_id]

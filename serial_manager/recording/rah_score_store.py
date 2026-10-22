"""Keep one personal best per Player ID in a local UTF-8 text file."""

from dataclasses import dataclass
from fractions import Fraction
import os
from pathlib import Path
import tempfile
import threading


_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class RAHScore:
    player_id: str
    collected: int
    total: int

    def __post_init__(self):
        if not self.player_id or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                                     for char in self.player_id):
            raise ValueError("Player ID must be nonempty and contain no spaces or control characters.")
        if not 0 <= self.collected <= self.total:
            raise ValueError("Collected coins must be between zero and the total coin count.")

    @property
    def score(self):
        return self.collected

    @property
    def percent(self):
        return 100.0 * self.collected / self.total if self.total else 0.0

    def line(self):
        return f"{self.player_id} {self.collected}/{self.total} {self.score}"


class RAHScoreStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / "rah_scores.txt"
        self._lock = _WRITE_LOCK

    @staticmethod
    def _personal_bests(records):
        best = {}
        for record in records:
            previous = best.get(record.player_id)
            quality = (record.score, Fraction(record.collected, record.total) if record.total else Fraction(0))
            if previous is not None:
                previous_quality = (previous.score, Fraction(previous.collected, previous.total) if previous.total else Fraction(0))
                if quality < previous_quality:
                    continue
                # A tie keeps the newer row, matching the leaderboard's policy.
                del best[record.player_id]
            best[record.player_id] = record
        return list(best.values())

    def _write(self, records):
        payload = ''.join(record.line() + '\n' for record in records).encode('utf-8')
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
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def append(self, player_id, collected, total):
        """Save a completed run if it improves the best; return the stored best.

        The method name is retained for existing GUI callers. A lower score
        never replaces a higher one. Same-score ties prefer greater accuracy.
        """
        record = RAHScore(player_id, collected, total)
        with self._lock:
            records = self._personal_bests(self.read() + [record])
            self._write(records)
        return next(item for item in records if item.player_id == player_id)

    def compact(self):
        """Consolidate a legacy history file without creating a missing TXT."""
        with self._lock:
            if not self.path.exists():
                return []
            records = self.read()
            self._write(records)
        return records

    def read(self, player_id=None):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records = []
        for line in lines:
            try:
                identifier, accuracy, points = line.split()
                collected, total = (int(value) for value in accuracy.split("/"))
                record = RAHScore(identifier, collected, total)
                if int(points) != record.score:
                    continue
            except (ValueError, TypeError):
                continue
            records.append(record)
        records = self._personal_bests(records)
        return [record for record in records if player_id is None or record.player_id == player_id]

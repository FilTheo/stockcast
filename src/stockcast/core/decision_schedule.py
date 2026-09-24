"""Decision opportunities, indexed by zero-based demand period within a run."""

from dataclasses import dataclass
from typing import Optional


def _period(value, name="period"):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0")
    return value


class DecisionSchedule:
    """Extend with deterministic eligibility, next opportunity, and provenance.

    ``next_decision_period(t)`` returns an opportunity strictly after ``t``,
    or None. Schedules see no future demand and must not mutate during queries.
    """

    def should_decide(self, period: int) -> bool:
        raise NotImplementedError

    def next_decision_period(self, period: int) -> Optional[int]:
        raise NotImplementedError

    def to_manifest(self) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class PeriodicSchedule(DecisionSchedule):
    every: int
    start: int = 0

    def __post_init__(self):
        if _period(self.every, "every") == 0:
            raise ValueError("every must be an integer >= 1")
        _period(self.start, "start")

    def should_decide(self, period):
        _period(period)
        return period >= self.start and (period - self.start) % self.every == 0

    def next_decision_period(self, period):
        _period(period)
        if period < self.start:
            return self.start
        return self.start + ((period - self.start) // self.every + 1) * self.every

    def to_manifest(self):
        return {"type": "periodic", "every": self.every, "start": self.start}


@dataclass(frozen=True)
class OneTimeSchedule(DecisionSchedule):
    period: int = 0

    def __post_init__(self):
        _period(self.period)

    def should_decide(self, period):
        return _period(period) == self.period

    def next_decision_period(self, period):
        return self.period if _period(period) < self.period else None

    def to_manifest(self):
        return {"type": "one_time", "period": self.period}


@dataclass(frozen=True)
class ExplicitSchedule(DecisionSchedule):
    periods: tuple[int, ...]

    def __post_init__(self):
        values = tuple(self.periods)
        for value in values:
            _period(value)
        if len(set(values)) != len(values):
            raise ValueError("periods must be unique")
        object.__setattr__(self, "periods", tuple(sorted(values)))

    def should_decide(self, period):
        return _period(period) in self.periods

    def next_decision_period(self, period):
        _period(period)
        return next((p for p in self.periods if p > period), None)

    def to_manifest(self):
        return {"type": "explicit", "periods": list(self.periods)}

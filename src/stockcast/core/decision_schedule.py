"""Decision opportunities, indexed by zero-based demand period within a run."""

from dataclasses import dataclass
from typing import Optional


def _period(value, name="period"):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0")
    return value


class DecisionSchedule:
    """Base class for a calendar of ordering opportunities.

    A schedule answers *when* a policy may order; the policy answers *how much*.
    Periods are zero-based demand periods of the run. Subclasses implement the
    three methods below; they see only period numbers (never demand) and must not
    change while being queried, so the engine can check every decision before a
    run starts.

    Example:
        ```python
        class EveryOtherDay(DecisionSchedule):
            def should_decide(self, period):
                return period % 2 == 0

            def next_decision_period(self, period):
                return period + 2 - period % 2

            def to_manifest(self):
                return {"type": "every_other_day"}
        ```
    """

    def should_decide(self, period: int) -> bool:
        """Whether the policy may order in ``period``.

        Args:
            period: Zero-based demand period.

        Returns:
            ``True`` for an ordering opportunity.
        """
        raise NotImplementedError

    def next_decision_period(self, period: int) -> Optional[int]:
        """The next opportunity strictly after ``period``.

        Used to size the protection window of irregular schedules,
        ``H = (next - period) + lead_time``.

        Args:
            period: Zero-based demand period.

        Returns:
            The next decision period, or ``None`` if there is none.
        """
        raise NotImplementedError

    def to_manifest(self) -> dict:
        """Describe the schedule for the run manifest.

        Returns:
            A JSON-serialisable dict.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class PeriodicSchedule(DecisionSchedule):
    """Decide every ``every`` periods, starting at ``start``.

    Decision periods are ``start, start + every, start + 2 * every, ...``.
    ``review_period=R`` on a policy is shorthand for ``PeriodicSchedule(R)``.

    Args:
        every: Review period ``R``, an integer >= 1.
        start: First decision period, an integer >= 0 (default 0).

    Example:
        ```python
        PeriodicSchedule(every=7, start=3)   # periods 3, 10, 17, ...
        ```
    """
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
    """Decide exactly once, in ``period``.

    Used by ``SingleOrderPolicy`` for a single seasonal purchase.

    Args:
        period: The decision period, an integer >= 0 (default 0).
    """
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
    """Decide in an explicit list of periods.

    Useful for supplier calendars and irregular ordering days. Each decision then
    covers its own window, ``(next decision - decision) + lead_time``.

    Args:
        periods: Unique integers >= 0, in any order (stored sorted).

    Example:
        ```python
        ExplicitSchedule(periods=(0, 3, 10))
        ```
    """
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

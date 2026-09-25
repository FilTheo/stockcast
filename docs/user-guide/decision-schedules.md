# Decision schedules

A decision schedule says **when** a policy may order. The policy says **how
much**. Keeping the two apart lets you change the ordering calendar without
touching the ordering rule, and the other way round.

## Built-in schedules

| Schedule | Decides in periods | Example |
|---|---|---|
| `PeriodicSchedule(every=R, start=0)` | $\text{start}, \text{start} + R, \text{start} + 2R, \dots$ | Order every Monday |
| `OneTimeSchedule(period=0)` | exactly one period | A seasonal buy |
| `ExplicitSchedule(periods=(...))` | the listed periods | A supplier's delivery calendar |

Periods are **demand periods**, counted from 0 at the first demand row.

```python
from stockcast.core import ExplicitSchedule, OneTimeSchedule, PeriodicSchedule

schedules = {
    "every 4, from 0": PeriodicSchedule(every=4),
    "every 4, from 2": PeriodicSchedule(every=4, start=2),
    "once, at 5": OneTimeSchedule(period=5),
    "explicit": ExplicitSchedule(periods=(0, 3, 10)),
}
{name: [t for t in range(12) if s.should_decide(t)] for name, s in schedules.items()}
```

```text
{'every 4, from 0': [0, 4, 8], 'every 4, from 2': [2, 6, 10], 'once, at 5': [5], 'explicit': [0, 3, 10]}
```

### `review_period` is shorthand

Most policies take `review_period=R`. That is the same as
`schedule=PeriodicSchedule(every=R)`: decisions at $0, R, 2R, \dots$ Use the
`schedule=` argument when you need a different start, a one-off decision, or an
irregular calendar:

```python
from stockcast.policies import OrderUpToPolicy

weekly = OrderUpToPolicy(lead_time=2, review_period=7, allow_backorders=False)
weekly_from_day_3 = OrderUpToPolicy(
    lead_time=2, schedule=PeriodicSchedule(every=7, start=3), allow_backorders=False,
)
```

`review_period=1` means the policy may order every period.

## An opportunity, not an obligation

A schedule grants an **opportunity** to order. Whether an order is placed, and
how large it is, is up to the policy: an order-up-to policy orders nothing if
the position is already at $S$. Rules that depend on the state ("order only
when stock is low") belong in the policy, not the schedule. Schedules only
see period numbers, so they are deterministic and can be checked before the
run starts.

## Irregular schedules and their targets

With a periodic schedule, every decision covers the same window, $H = L + R$,
so one target can serve every review. With an irregular schedule, each
decision covers a different window:

$$
H_t = (u_t - t) + L ,
$$

where $u_t$ is the next decision after $t$ (see
[Timing](concepts/timing.md#irregular-schedules)). Each decision therefore
gets its own fitted target. Pass the first one as `policy` and the rest in
`policy_schedule`, keyed by decision period:

```python
import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.utils import DemandGenerator

sku, opening_date, lead_time = "tea_250g", pd.Timestamp("2026-01-05"), 2
calendar = ExplicitSchedule(periods=(0, 3, 10))
rng = np.random.default_rng(42)


def fit_for(decision_period, horizon):
    """Fit the policy used at one decision, for its own coverage window."""
    origin = opening_date + pd.Timedelta(days=decision_period)   # last observed day
    total = rng.poisson(6.0, size=(10_000, horizon)).sum(axis=1)
    target = pd.DataFrame({
        "unique_id": [sku],
        "target": [np.quantile(total, 0.95)],
        "end": [origin + pd.Timedelta(days=horizon)],
    })
    return OrderUpToPolicy(
        lead_time=lead_time, schedule=calendar, service_level=0.95,
        allow_backorders=False,
    ).fit(
        target, target_column="target", target_probability=0.95,
        protection_horizon=horizon, target_source="external_direct",
        forecast_origin=origin, forecast_frequency="D", target_end_date_column="end",
    )


first = fit_for(0, horizon=(3 - 0) + lead_time)             # covers periods 0..4
later = {
    3: fit_for(3, horizon=(10 - 3) + lead_time),             # covers periods 3..11
    10: fit_for(10, horizon=4),                              # last decision: covers to the end
}

demand = DemandGenerator([sku], start_date=opening_date + pd.Timedelta(days=1),
                         period_frequency="D", seed=3).constant(n_periods=14, value=6.0)
inventory = InventoryStateDataFrame([sku], max_lead_time=lead_time, allow_backorders=False)
inventory.initialize_from_observed(pd.DataFrame({"unique_id": [sku], "on_hand": [20.0]}),
                                   on_hand_column="on_hand", start_date=opening_date)

result = SimulationEngine().run(
    policy=first, policy_schedule=later,
    demand_source=demand, inventory=inventory, n_periods=14, period_frequency="D",
    warmup_periods=0, scoring_periods=14, settlement_periods=0,
    order_during_settlement=False, demand_source_name="constant_six", random_seed=3,
)
events = result.to_event_frame()
events.loc[events["decision_flag"], ["demand_period", "date", "decision_inventory_position",
                                      "target_level", "order_quantity"]]
```

```text
    demand_period       date  decision_inventory_position  target_level  order_quantity
0               0 2026-01-06                         20.0          39.0            19.0
3               3 2026-01-09                         21.0          66.0            45.0
10             10 2026-01-16                         24.0          32.0             8.0
```

Before the run starts, the engine checks every decision: that its target's
horizon equals $(u_t - t) + L$, that its forecast origin is the last date
observed before that decision, and that its end date matches. The last
decision has no next opportunity, so its horizon is whatever window you
declare. [Notebook 04f](../notebooks/04f_scheduled_forecast_simulation.ipynb)
builds this workflow with real smooth forecasts.

## `policy_schedule`: refitting over time

`policy_schedule` is also how you refresh a periodic policy with new
forecasts: `{decision_period: fitted_policy}`. Each snapshot must be the same
policy class with the same lead time, schedule, service level, and shortage
rule; only the fitted targets change. Its forecast origin must equal the
decision's information date, $\text{opening date} + t\,\Delta$. The manifest
logs every update. See
[Refresh targets as forecasts roll](../how-to/rolling-targets.md).

## Write your own schedule

Subclass `DecisionSchedule` and implement three methods:

```python
from stockcast.core import DecisionSchedule


class Weekdays(DecisionSchedule):
    """Decide on chosen weekdays; period 0 falls on `first_weekday` (Mon = 0)."""

    def __init__(self, weekdays, first_weekday):
        self.weekdays, self.first_weekday = tuple(weekdays), first_weekday

    def should_decide(self, period):
        return (self.first_weekday + period) % 7 in self.weekdays

    def next_decision_period(self, period):
        return next(p for p in range(period + 1, period + 8) if self.should_decide(p))

    def to_manifest(self):
        return {"type": "weekdays", "weekdays": list(self.weekdays),
                "first_weekday": self.first_weekday}


# Period 0 is Tuesday 6 January; order on Mondays and Thursdays.
mon_thu = Weekdays(weekdays=(0, 3), first_weekday=1)
[t for t in range(10) if mon_thu.should_decide(t)]
```

```text
[2, 6, 9]
```

- `should_decide(period)` returns a `bool`.
- `next_decision_period(period)` returns the next opportunity **strictly
  after** `period`, or `None` if there is none.
- `to_manifest()` returns a JSON-serialisable description, stored in the run
  manifest.

**Go deeper:** [Timing](concepts/timing.md) ·
[Notebook 02b: decision schedules](../notebooks/02b_decision_schedules.ipynb) ·
[Notebook 05c: extension points](../notebooks/05c_extension_points.ipynb)

# Demand and calendars

Demand is what the simulation plays forward. It can be your real sales
history (a backtest), synthetic demand (a stress test), or scenarios drawn
from a forecast model. Stockcast reads all of them in the same simple format.

## The demand table

| Column | Type | Meaning |
|---|---|---|
| `unique_id` | any hashable | SKU identifier, matching the inventory state |
| `period` | int | Demand period, `0 … n_periods − 1` |
| `date` | timestamp | Calendar date of the period |
| `y` | float $\ge 0$ | Units demanded |

The table is a complete grid: every SKU in every period, exactly once.

### The calendar

Periods and dates are tied to the opening state:

$$
\text{date}_p = \text{opening date} + (p + 1)\,\Delta ,
$$

where $\Delta$ is `period_frequency`, any forward pandas frequency: `"D"`,
`"W-MON"`, `"MS"`, `"h"`, `"15min"`, and so on. The engine checks each
period's date against this formula before running.

### What the engine checks

Before the first period is simulated, the whole table is validated:

- the columns `unique_id`, `period`, `date`, `y` are present;
- periods are exactly $0, \dots, n - 1$;
- every period contains every SKU of the state, once;
- each period's date follows the calendar formula above;
- every `y` is finite and non-negative.

A run therefore never starts on a table with gaps. Days without demand are
rows with `y = 0`.

## Demand from your data

Most sales data needs a reshape and a period counter:

```python
import pandas as pd

opening_date = pd.Timestamp("2026-01-05")
sales = pd.DataFrame({
    "date": pd.date_range("2026-01-06", periods=5, freq="D").repeat(2),
    "unique_id": ["tea_250g", "coffee_1kg"] * 5,
    "y": [10, 3, 2, 4, 9, 2, 6, 5, 4, 1],
})

sales["period"] = (sales["date"] - opening_date).dt.days - 1   # daily data
sales.head(4)
```

```text
        date   unique_id   y  period
0 2026-01-06    tea_250g  10       0
1 2026-01-06  coffee_1kg   3       0
2 2026-01-07    tea_250g   2       1
3 2026-01-07  coffee_1kg   4       1
```

If some SKUs have no sales row on a day, complete the grid with zeros first,
for example with `pivot_table(..., fill_value=0)` and `melt`.

## Synthetic demand: `DemandGenerator`

`DemandGenerator` produces reproducible demand tables in the right format.
Every parameter can be a single value for all SKUs or a dict with one value
per SKU.

```python
from stockcast.utils import DemandGenerator

generator = DemandGenerator(
    ["tea_250g", "coffee_1kg"],
    start_date=opening_date + pd.Timedelta(days=1),   # date of period 0
    period_frequency="D",
    random_seed=7,
    negative_demand_handling="clip_zero",
)
weekly_pattern = generator.seasonal(
    n_periods=28,
    base={"tea_250g": 6.0, "coffee_1kg": 3.0},
    amplitude=2.0,
    season_length=7,
    std=1.0,
)
weekly_pattern.groupby("unique_id")["y"].mean().round(2)
```

```text
unique_id
coffee_1kg    2.95
tea_250g      5.55
Name: y, dtype: float64
```

| Method | Demand in period $t$ |
|---|---|
| `constant(n_periods, value)` | $c$ |
| `normal(n_periods, mean, std)` | $\mu + \varepsilon_t$ |
| `seasonal(n_periods, base, amplitude, season_length, std)` | $b + a \sin(2\pi t / m) + \varepsilon_t$ |
| `trend(n_periods, initial, growth_rate, std)` | $y_0 + g\,t + \varepsilon_t$ |
| `from_historical(historical_df, n_periods, sampling_method="normal_moments")` | $\hat\mu + \varepsilon_t$ with $\hat\mu, \hat\sigma$ from your history |
| `sample(n_periods, sampler)` | whatever your sampler draws: any distribution, any pattern |

with $\varepsilon_t \sim \mathcal{N}(0, \sigma^2)$ drawn from the generator's
seeded random stream.

**Negative draws.** A normal draw can be negative. The default
`negative_demand_handling="raise"` stops with an error;
`"clip_zero"` sets such values to zero and warns, and the clipping count is
stored in the run manifest.

### Any distribution: samplers

For everything else, write a **sampler**: a small function
`sampler(rng, periods)` that returns one demand value per period. `rng` is the
generator's seeded random stream: draw from it, and the same `random_seed` gives the
same demand every time. `periods` holds the period indices, so demand can
change over time.

One sampler generates the whole panel, with independent draws for each SKU:

```python
def poisson(rng, periods):
    return rng.poisson(6.0, periods.size)

panel = generator.sample(n_periods=28, sampler=poisson)
panel.head(4)
```

```text
    unique_id    y  period       date
0    tea_250g  8.0       0 2026-01-06
1  coffee_1kg  4.0       0 2026-01-06
2    tea_250g  5.0       1 2026-01-07
3  coffee_1kg  3.0       1 2026-01-07
```

A dict gives each SKU its own sampler, just like a dict of parameters:

```python
def busy_weekends(rng, periods):
    rate = 6.0 + 3.0 * (periods % 7 >= 5)          # Saturdays and Sundays
    return rng.poisson(rate)

def intermittent(rng, periods):
    sells = rng.binomial(1, 0.3, periods.size)     # sells on about 30% of days
    return sells * rng.gamma(2.0, 2.0, periods.size).round()

mixed = generator.sample(
    n_periods=28,
    sampler={"tea_250g": busy_weekends, "coffee_1kg": intermittent},
)
```

Anything that draws from `rng` works, including resampling your own history
(`rng.choice(history, periods.size)`) or a SciPy distribution:

```py
from scipy import stats

def negative_binomial(rng, periods):
    return stats.nbinom(n=5, p=0.4).rvs(periods.size, random_state=rng)
```

Because `sample` passes all periods at once, a sampler can also carry state
from one period to the next, such as autocorrelated demand:

```python
def autocorrelated(rng, periods):
    demand, level = [], 6.0
    for _ in periods:
        level = 6.0 + 0.7 * (level - 6.0) + rng.normal(0.0, 1.5)
        demand.append(max(level, 0.0))
    return demand

smooth_swings = generator.sample(n_periods=28, sampler=autocorrelated)
```

The generator checks every sampler's output (numbers, finite, one per period)
and applies the same negative-draw handling as the built-in methods.

## Demand as a function

Each method has an `_fn` twin (`normal_fn`, `seasonal_fn`, `sample_fn`, …)
that returns a function `period -> DataFrame`. `sample_fn` calls your sampler
with one period at a time, so it suits samplers that draw each period on its
own; use `sample` for samplers that carry state across periods. You can also
write your own:

```python
import numpy as np

rng = np.random.default_rng(0)

def promo_demand(period):
    boost = 3.0 if period % 14 == 0 else 1.0     # a promotion every two weeks
    return pd.DataFrame({
        "unique_id": ["tea_250g"],
        "period": [period],
        "date": [opening_date + pd.Timedelta(days=period + 1)],
        "y": [float(rng.poisson(6.0 * boost))],
    })
```

Pass it as `demand_source=promo_demand`. The engine calls it once per period
**before** the run, builds the complete table, validates it, and then runs.
Every run, and every branch of a comparison, therefore uses one fixed demand
path.

## Recording where demand came from

`SimulationEngine.run` asks for `demand_source_name` and `random_seed`. They,
a fingerprint of the demand table, and the generator's negative-demand
handling (with the number of clipped values) are stored in the run manifest,
so a result always says which demand produced it. The generator's own seed,
model, and parameters are not recorded; pass its seed as `random_seed` and
keep the call that built the demand with your experiment.

**Go deeper:** [Learn step 2](../learn/02-demand-and-time.md) ·
[API: utilities](../reference/utils.md)

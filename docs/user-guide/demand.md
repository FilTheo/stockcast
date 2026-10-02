# Demand and calendars

Demand is what the simulation plays forward. It can be your real sales
history (a backtest), synthetic demand (a stress test), or scenarios drawn
from a forecast model. Stockcast reads all of them in the same format.

## The demand table

| Column | Type | Meaning |
|---|---|---|
| `unique_id` | any hashable | SKU identifier, matching the inventory state |
| `date` | timestamp | Calendar date of the period |
| `period` | int | Demand period, counted from the opening date: `0` is the first period after it |
| `y` | float $\ge 0$ | Units demanded |

Give `date`, `period`, or both: each follows from the other through the
calendar below, so the engine adds the missing one. When the table has dates,
the dates decide each row's period. The table is a complete grid: every SKU
in every period, exactly once.

### Column names

These are default names, not fixed ones. If your table uses other names, say
so where the table goes in, with the same four arguments everywhere:
`sku_column`, `date_column`, `period_column` and `demand_column`.

```python
from stockcast.core import SimulationEngine

engine = SimulationEngine(sku_column="item", date_column="ds", demand_column="sales")
```

`DemandGenerator`, the scheduled callbacks, `ShelfLife` and every policy take
the names they need in their constructor in the same way;
`InventoryStateDataFrame.from_observed` takes them in the call. For the demand table, a callback schedule
or `ShelfLife`'s opening lots, a name you set must be a column of the table,
and the table must not also hold the default name, so no column is read by
mistake. Results and event tables always use the default names.

### The calendar

Periods and dates are tied to the opening state:

$$
\text{date}_p = \text{opening date} + (p + 1)\,\Delta ,
$$

where $\Delta$ is `freq`, any forward pandas frequency: `"D"`,
`"W-MON"`, `"MS"`, `"h"`, `"15min"`, and so on (by default the policy's
`freq`). The engine checks each period's date against this
formula before running; a date off this grid is an error, never rounded.
The opening date itself must be a period date of `freq` too: a Monday for
`"W-MON"`, the first of a month for `"MS"`. The same holds for a forecast
origin and for `DemandGenerator(first_date=...)`.

Period 0 is the first period after the opening date, the day you counted the
stock. When you count the stock on the forecast origin, as you usually will,
period 0 is the forecast's first step (`fh = 1`), period 1 is `fh = 2`, and
so on. Decision schedules, the warm-up, scoring and settlement windows, and
`policy_schedule` keys all count in these same periods.

### What the engine checks

Before the first period is simulated, the whole table is validated:

- the columns `unique_id`, `y`, and `date` or `period` are present;
- periods are exactly $0, \dots, n - 1$: demand starts one period after the
  opening date and has no gaps;
- every period contains every SKU of the state, once;
- each period's date follows the calendar formula above;
- a `period` column next to dates advances one per period, the same way for
  every SKU;
- every `y` is finite and non-negative.

Days without demand are rows with `y = 0`.

## Demand from your data

Most sales data only needs a reshape into one row per SKU and date:

```python
import pandas as pd

opening_date = pd.Timestamp("2026-01-05")
sales = pd.DataFrame({
    "date": pd.date_range("2026-01-06", periods=5, freq="D").repeat(2),
    "unique_id": ["tea_250g", "coffee_1kg"] * 5,
    "y": [10, 3, 2, 4, 9, 2, 6, 5, 4, 1],
})
sales.head(4)
```

```text
        date   unique_id   y
0 2026-01-06    tea_250g  10
1 2026-01-06  coffee_1kg   3
2 2026-01-07    tea_250g   2
3 2026-01-07  coffee_1kg   4
```

Pass it as it is: the first day after the opening date is period 0.

If some SKUs have no sales row on a day, complete the grid with zeros first,
for example with `pivot_table(..., fill_value=0)` and `melt`. Sales equal
demand on days that had stock left; [Sales and demand](#sales-and-demand)
covers the days that ran out.

## Sales and demand

**Demand** is what customers wanted; **sales** are what the shop served.
Under lost sales, a period's sales are its demand capped by the stock on the
shelf:

$$
\text{sales}_t = \min(\text{demand}_t,\ \text{stock}_t) .
$$

On a day with stock left over, sales equal demand. On a day that ran out,
sales equal the stock, and demand was *at least* that much: the rest was never
seen (Nahmias, 1994). With backorders, the unmet part waits as a backorder, so
demand stays on record.

The demand table holds demand, and the engine works out the sales: the
[event table](concepts/accounting.md) reports `demand`, `fulfilled_units`
(sales) and `lost_sales_units` for every period. When you build the table
from a sales history, the days that ran out hold a floor for demand rather
than demand itself; [What to forecast](#what-to-forecast) shows how to
estimate it.

A week with eight units on the shelf each morning shows the difference:

```python
week = pd.DataFrame({
    "date": pd.date_range("2026-01-06", periods=7, freq="D"),
    "demand": [6.0, 9.0, 4.0, 11.0, 7.0, 12.0, 5.0],
    "stock": 8.0,                      # on the shelf each morning
})
week["sales"] = week[["demand", "stock"]].min(axis=1)
week["stockout"] = week["demand"] > week["stock"]
week
```

```text
        date  demand  stock  sales  stockout
0 2026-01-06     6.0    8.0    6.0     False
1 2026-01-07     9.0    8.0    8.0      True
2 2026-01-08     4.0    8.0    4.0     False
3 2026-01-09    11.0    8.0    8.0      True
4 2026-01-10     7.0    8.0    7.0     False
5 2026-01-11    12.0    8.0    8.0      True
6 2026-01-12     5.0    8.0    5.0     False
```

Customers wanted 7.71 units a day. Two quick estimates from the sales both
come out lower:

```python
pd.Series({
    "mean demand": week["demand"].mean(),
    "mean sales": week["sales"].mean(),
    "mean sales, stockout days dropped": week.loc[~week["stockout"], "sales"].mean(),
}).round(2)
```

```text
mean demand                          7.71
mean sales                           6.57
mean sales, stockout days dropped    5.50
dtype: float64
```

Raw sales cap the busy days at the stock. Dropping the stockout days removes
exactly the busy days, because a day runs out precisely when its demand is
above the stock.

### What to forecast

A Stockcast target is a quantile of **demand** over the protection period
([Forecast targets](forecast-targets.md)), so the forecast should describe
demand:

- **When demand is on record**, forecast it directly: sales from periods that
  never ran out, sales plus new backorders, or logged requests the shop could
  not serve.
- **When some periods ran out**, give the forecaster what those periods really
  say: demand of at least the sales. Methods built for such censored data
  estimate demand from it: maximum likelihood for normal demand (Nahmias,
  1994), negative binomial demand for retail (Agrawal and Smith, 1996), a
  Tobit Kalman filter for state-space forecasting models (Trapero et al.,
  2024), and the Kaplan–Meier estimator, which assumes no distribution (Huh
  et al., 2011). If you record when the shelf emptied, use it: for Poisson
  and normal demand, that one time carries everything the timing of sales
  reveals about demand (Jain, Rudi and Wang, 2015).
- **When customers switch** to another product on an empty shelf, that
  product's sales include some of the missing demand. Anupindi, Dada and
  Gupta (1998) estimate demand and substitution together.

A forecast fitted to raw sales comes out low. How far depends on how often
stockouts happen, how variable demand is, and how strongly it is correlated
over time (Wecker, 1978). Inside a run, a low forecast can feed itself: it
means less stock, more stockouts, and lower sales to learn from next time.
[Notebook 04a](../notebooks/04a_forecasting_from_sales.ipynb) follows a shop
through that loop, and
[When the forecast learns from the run](../how-to/rolling-targets.md#when-the-forecast-learns-from-the-run)
shows how to refit a forecast during a run from what the run records.

## Synthetic demand: `DemandGenerator`

`DemandGenerator` produces reproducible demand tables in the right format.
Every parameter can be a single value for all SKUs or a dict with one value
per SKU.

```python
from stockcast.utils import DemandGenerator

generator = DemandGenerator(
    ["tea_250g", "coffee_1kg"],
    first_date=opening_date + pd.Timedelta(days=1),   # date of period 0
    freq="D",
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
| `normal_from_history(historical_df, n_periods)` | $\hat\mu + \varepsilon_t$ with $\hat\mu, \hat\sigma$ from your history |
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

## Split a history into past and future

A backtest splits a history in two: the past to fit your forecast, the future
to play forward. The future keeps its own period numbers. `panel` numbers its
days from 0, so the rows after 19 January are periods 14 to 27. Pass them as
they are, with the stock counted on 19 January. The dates tell the engine that
20 January is period 0 of the run; it renumbers the table and records the
shift:

```python
from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.policies import ReorderPointPolicy

past = panel[panel["date"] <= "2026-01-19"]       # periods 0 to 13
future = panel[panel["date"] > "2026-01-19"]      # periods 14 to 27

stock = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"], "on_hand": [20.0, 20.0]}),
    opening_date=pd.Timestamp("2026-01-19"),        # counted at the end of the past
)
policy = ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=False).fit(
    reorder_point=10.0, order_up_to_level=25.0,
)
result = SimulationEngine().run(policy, future, stock, freq="D")
result.run_settings["demand_period_offset"]
```

```text
14
```

Any numbering works the same way, such as a week number from your sales
system, as long as it advances one per period for every SKU. A table with only
a `period` column has no dates to go by, so its periods start at 0.

## Demand as a function

Instead of a table, `demand_source` can be a function `period -> DataFrame`
that returns one row per SKU for that period. It need not repeat the period
or the date: the engine knows which period it asked for.

```python
import numpy as np

rng = np.random.default_rng(0)

def promo_demand(period):
    boost = 3.0 if period % 14 == 0 else 1.0     # a promotion every two weeks
    return pd.DataFrame({
        "unique_id": ["tea_250g"],
        "y": [float(rng.poisson(6.0 * boost))],
    })
```

Pass it as `demand_source=promo_demand`, with `n_periods`: a function has no
length of its own. The engine calls it once per period
**before** the run, builds the complete table, validates it, and then runs.
Every run, and every branch of a comparison, therefore uses one fixed demand
path.

## Provenance

The run manifest stores a fingerprint of the demand table and the
generator's negative-demand handling (with the number of clipped values), so
a result always says which demand produced it. `SimulationEngine.run` also
takes an optional `demand_source_name` and `random_seed` for the manifest.
The generator's own seed, model, and parameters are not recorded; pass its
seed as `random_seed` and keep the call that built the demand with your
experiment.

## References

- Agrawal, N., and Smith, S. A. (1996). Estimating negative binomial demand
  for retail inventory management with unobservable lost sales. *Naval
  Research Logistics*, 43(6), 839–861.
  [doi:10.1002/(SICI)1520-6750(199609)43:6&lt;839::AID-NAV4&gt;3.0.CO;2-5](https://doi.org/10.1002/%28SICI%291520-6750%28199609%2943%3A6%3C839%3A%3AAID-NAV4%3E3.0.CO%3B2-5)
- Anupindi, R., Dada, M., and Gupta, S. (1998). Estimation of consumer demand
  with stock-out based substitution: An application to vending machine
  products. *Marketing Science*, 17(4), 406–423.
  [doi:10.1287/mksc.17.4.406](https://doi.org/10.1287/mksc.17.4.406)
- Huh, W. T., Levi, R., Rusmevichientong, P., and Orlin, J. B. (2011).
  Adaptive data-driven inventory control with censored demand based on
  Kaplan-Meier estimator. *Operations Research*, 59(4), 929–941.
  [doi:10.1287/opre.1100.0906](https://doi.org/10.1287/opre.1100.0906)
- Jain, A., Rudi, N., and Wang, T. (2015). Demand estimation and ordering
  under censoring: Stock-out timing is (almost) all you need. *Operations
  Research*, 63(1), 134–150.
  [doi:10.1287/opre.2014.1326](https://doi.org/10.1287/opre.2014.1326)
- Nahmias, S. (1994). Demand estimation in lost sales inventory systems.
  *Naval Research Logistics*, 41(6), 739–757.
  [doi:10.1002/1520-6750(199410)41:6&lt;739::AID-NAV3220410605&gt;3.0.CO;2-A](https://doi.org/10.1002/1520-6750%28199410%2941%3A6%3C739%3A%3AAID-NAV3220410605%3E3.0.CO%3B2-A)
- Trapero, J. R., Holgado de Frutos, E., and Pedregal, D. J. (2024). Demand
  forecasting under lost sales stock policies. *International Journal of
  Forecasting*, 40(3), 1055–1068.
  [doi:10.1016/j.ijforecast.2023.09.004](https://doi.org/10.1016/j.ijforecast.2023.09.004)
- Wecker, W. E. (1978). Predicting demand from sales data in the presence of
  stockouts. *Management Science*, 24(10), 1043–1054.
  [doi:10.1287/mnsc.24.10.1043](https://doi.org/10.1287/mnsc.24.10.1043)

**See also:** [Walkthrough step 2](../learn/02-demand-and-time.md) ·
[Notebook 04a: forecasting from sales](../notebooks/04a_forecasting_from_sales.ipynb) ·
[API: utilities](../reference/utils.md)

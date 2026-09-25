# Build a target from sample paths

Sample paths are the most flexible way to get a cumulative target: they work
for any demand distribution, any horizon, and any dependence between periods.
This recipe builds paths by bootstrapping a simple model's residuals, then
turns them into a target.

## The recipe

$$
T^{(m)} = \sum_{h=1}^{H} d^{(m)}_{h},
\qquad
S = Q_\alpha\!\left(T^{(1)}, \dots, T^{(M)}\right)
$$

1. Make a point forecast $\hat y_{h}$ for the next $H$ periods.
2. Draw $M$ error paths $e^{(m)}_1, \dots, e^{(m)}_H$ from past forecast errors.
3. Form demand paths $d^{(m)}_h = \max\bigl(0, \hat y_h + e^{(m)}_h\bigr)$.
4. Sum each path over the window and take the $\alpha$-quantile of the sums.

## Step by step

A history of 16 weeks of daily sales with a weekly pattern:

```python
import numpy as np
import pandas as pd

from stockcast.utils import DemandGenerator

origin = pd.Timestamp("2026-01-05")          # last observed day
history = DemandGenerator(
    ["tea_250g"], start_date=origin - pd.Timedelta(days=111), period_frequency="D",
    seed=11, negative_demand_handling="clip_zero",
).seasonal(n_periods=112, base=6.0, amplitude=2.0, season_length=7, std=1.5)
y = history["y"].round().to_numpy()
```

**1. Point forecast.** A seasonal naive forecast repeats the same weekday of
last week:

```python
H = 6
point = y[-7:][:H]                            # the next 6 days = same weekdays last week
residuals = y[7:] - y[:-7]                    # past one-week-ahead errors
```

**2. Error paths.** Resample **blocks** of $H$ consecutive residuals, so a run
of high days stays together. That keeps the dependence between days that the
total depends on:

```python
rng = np.random.default_rng(0)
M = 10_000
starts = rng.integers(0, len(residuals) - H + 1, size=M)
error_paths = np.stack([residuals[s:s + H] for s in starts])     # shape (M, H)
```

**3 and 4. Demand paths and the target.**

```python
demand_paths = np.maximum(point + error_paths, 0.0)
totals = demand_paths.sum(axis=1)
target_value = np.quantile(totals, 0.95)
round(point.sum(), 1), round(target_value, 1)
```

```text
(np.float64(37.0), np.float64(46.0))
```

The point forecast expects 37 packs over the six days; covering 95% of the
bootstrapped futures takes 46.

## Hand it to a policy

```python
from stockcast.policies import OrderUpToPolicy

target = pd.DataFrame({
    "unique_id": ["tea_250g"],
    "target": [target_value],
    "target_end_date": [origin + pd.Timedelta(days=H)],
})
policy = OrderUpToPolicy(lead_time=2, review_period=4, service_level=0.95,
                         allow_backorders=False).fit(
    target, target_column="target", target_probability=0.95, protection_horizon=H,
    target_source="external_direct", forecast_origin=origin, forecast_frequency="D",
    target_end_date_column="target_end_date",
)
```

## Good practice

- **Blocks, not single residuals**, whenever errors are correlated in time.
  Independent resampling assumes each day's error is unrelated to the next.
- **Enough paths.** The quantile of a sum settles quickly; a few thousand paths
  are usually plenty. Report the seed and $M$ next to the target.
- **Errors from the right horizon.** Ideally use errors of forecasts made $h$
  steps ahead, for each $h$. The simple one-week errors above are a
  reasonable start for a seasonal naive model.
- **Paths from a model are even better.** State-space and probabilistic models
  can simulate paths directly; smooth, for example, can simulate its
  cumulative forecast (`interval="simulated"`). The summing step is the same.

[Notebook 04e](../notebooks/04e_cumulative_target_methods.ipynb) compares
independent-normal, approximate cumulative, and simulated cumulative targets
on the same replay.

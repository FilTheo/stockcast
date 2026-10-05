# Forecast targets

A forecast target connects a forecast to a policy. Your forecasting model
describes future demand; the target turns that description into the one
number a policy needs, such as an order-up-to level $S$ or a reorder point
$s$. This page covers the theory, the three ways to supply a target, and how
Stockcast keeps each target tied to the decision it was made for.

## Definition

A replenishment decision at period $t$ must cover demand over its protection
window of $H$ periods ($H = L + R$ for periodic review; see
[Timing](concepts/timing.md#the-protection-horizon)). Write the total demand
over that window as

$$
D_{t}^{(H)} = \sum_{h=0}^{H-1} D_{t+h}.
$$

The target at probability $\alpha$ is its quantile, given what is known at
the decision:

$$
S_t = Q_\alpha\!\left(D_t^{(H)} \,\middle|\, \mathcal{I}_{t-1}\right),
$$

where $\mathcal{I}_{t-1}$ is the demand history up to the previous period.
The rest of this page covers how to compute $S_t$ and pass it to Stockcast
with its context.

## Quantiles of sums

The quantile of a sum is not the sum of the quantiles:

$$
Q_\alpha\!\left(\sum_h D_{t+h}\right) \;\ne\; \sum_h Q_\alpha(D_{t+h})
\quad\text{in general.}
$$

Quantiles add in special cases, such as when every day's demand is exactly
the same as the first day's. With six independent days of Poisson(6) demand,
the 95% quantile of the total is 46 while six daily 95% quantiles add up to 60
([Walkthrough step 3](../learn/03-forecast-targets.md) shows the histogram).
For sparse, intermittent demand the sum can be too low: with six independent
days, each having a 4% chance of one unit of demand, every daily 95% quantile
is 0, yet the 95% quantile of the total is 1. Stockcast therefore always works
with the target of the **whole window**.

## Computing the quantile of the total

Any forecasting model can give you $S_t$. Choose the route that matches its
output.

### From sample paths

If the model can simulate future paths (bootstrap, Monte Carlo, a
probabilistic neural network, a Bayesian posterior), the recipe is:

1. draw $M$ paths $d^{(m)}_{t}, \dots, d^{(m)}_{t+H-1}$;
2. sum each path: $T^{(m)} = \sum_h d^{(m)}_{t+h}$;
3. take the empirical quantile: $S_t = Q_\alpha\bigl(T^{(1)}, \dots, T^{(M)}\bigr)$.

```python
import numpy as np
import pandas as pd

paths = np.random.default_rng(0).poisson(6.0, size=(20_000, 6))   # from your model
target_value = np.quantile(paths.sum(axis=1), 0.95)
target_value
```

```text
np.float64(46.0)
```

Paths carry the dependence between periods automatically. When you build paths
by bootstrapping residuals, resample whole multi-period blocks so that
dependence survives. See
[Build a target from sample paths](../how-to/targets-from-sample-paths.md).

### From a cumulative forecast

Some libraries forecast the total directly. In smooth, for example,
`model.predict(h=H, interval="prediction", level=0.95, side="upper",
cumulative=True)` returns the upper bound of the cumulative forecast, which is
exactly $S_t$.

### From means, standard deviations, and correlations

When demand over the window is approximately normal, its total has mean
$\sum_h \mu_h$ and variance

$$
\operatorname{Var}\!\left(D_t^{(H)}\right)
= \sum_{h} \sigma_h^2 \;+\; 2\sum_{h<k} \operatorname{Cov}(D_{t+h}, D_{t+k})
= \mathbf{1}^\top \Sigma\, \mathbf{1},
$$

so

$$
S_t = \sum_h \mu_h + z_\alpha \sqrt{\mathbf{1}^\top \Sigma \mathbf{1}} .
$$

Forecast errors are often positively correlated across horizons (a model that
underestimates today tends to underestimate tomorrow). The covariance terms
then make the window more variable than independent days would suggest:

```python
from statistics import NormalDist

H, z = 6, NormalDist().inv_cdf(0.95)
mu, sigma, rho = np.full(H, 6.0), np.full(H, 2.0), 0.5

lags = np.abs(np.subtract.outer(np.arange(H), np.arange(H)))
cov = np.outer(sigma, sigma) * rho ** lags          # AR(1)-style correlation

target_independent = mu.sum() + z * np.sqrt((sigma ** 2).sum())
target_correlated = mu.sum() + z * np.sqrt(cov.sum())
round(target_independent, 2), round(target_correlated, 2)
```

```text
(np.float64(44.06), np.float64(48.34))
```

When you are comfortable assuming independent normal days, Stockcast can do
this sum for you (the independent-normal mode below). With correlation,
compute $S_t$ as above and pass it as a direct target.

## Three ways to fit a target

Every fitted target records its probability, horizon, forecast origin,
frequency, and end date. The fitting modes differ in what you supply.

The frequency is the policy's `freq`: the length of one period, such as
`"D"` or `"W-MON"`. It sits next to `lead_time` and `review_period` because
they count periods of that length. With per-step forecasts (the
independent-normal mode) you may leave it out: Stockcast reads it from the
step dates when every SKU has at least three of them, evenly spaced on a
daily, weekly, monthly, quarterly or yearly calendar (steps such as `"2D"`
included). Day steps need a Saturday or Sunday among them when weekdays
alone could just as well be business days: Monday, Tuesday, Wednesday could
be daily or business-daily, and Monday, Wednesday, Friday could be `"2D"` or
`"2B"`. A one-row target has a single date per SKU, so it needs `freq`.

The `date` column of a forecast table is the last period each row covers:
the window's last day for a one-row target, the step's own day for a
per-step row. The forecast origin follows from it exactly, so you can leave
out `forecast_origin` (give both and Stockcast checks they agree). A table
with its dates in another column names it on the policy, next to the SKU
column: `OrderUpToPolicy(..., sku_column="item", date_column="end")`. Per-step
rows may also leave out `fh` when you give `forecast_origin`: each date
tells its step.

=== "Direct target (most common)"

    One row per SKU with the target you computed:

    ```python
    from stockcast.policies import OrderUpToPolicy

    opening_date = pd.Timestamp("2026-01-05")
    target = pd.DataFrame({
        "unique_id": ["tea_250g"],
        "S": [target_correlated],
        "date": [opening_date + pd.Timedelta(days=6)],   # the last day it covers
    })
    policy = OrderUpToPolicy(lead_time=2, review_period=4, freq="D",
                             service_level=0.95, allow_backorders=False).fit(
        target, target_column="S",
    )
    ```

=== "Independent normal"

    One row per SKU and horizon step $\mathrm{fh} = 1, \dots, H$, with the
    date of each step, a mean, and a standard deviation. Stockcast computes
    $S = \sum_h \mu_h + z_\alpha \sqrt{\sum_h \sigma_h^2}$, which assumes the
    steps are independent; the target metadata records it as
    `calculation_method="independent_normal"`:

    ```python
    steps = pd.DataFrame({
        "unique_id": "tea_250g",
        "fh": range(1, 7),
        "date": pd.date_range("2026-01-06", periods=6, freq="D"),
        "mean": mu,
        "std": sigma,
    })
    independent = OrderUpToPolicy(lead_time=2, review_period=4, freq="D",
                                  service_level=0.95, allow_backorders=False).fit(
        steps, mean_column="mean", std_column="std",
    )
    independent.get_target_levels()
    ```

    ```text
      unique_id  target_level
    0  tea_250g     44.058104
    ```

=== "Planner target"

    Sometimes $S$ is not a quantile at all: it comes from an optimiser, a
    planner, or a business rule. Leave `service_level=None` and omit the
    probability:

    ```python
    planned = OrderUpToPolicy(lead_time=2, review_period=4, freq="D",
                              service_level=None, allow_backorders=False).fit(
        target, target_column="S",
    )
    planned.get_target_metadata()["representation"]
    ```

    ```text
    'external_inventory_target'
    ```

### The fit arguments

| Argument | Meaning | Default and checks |
|---|---|---|
| `target_column` | Column holding the target | One finite, non-negative value per SKU |
| `forecast_origin` | Last date of demand the forecast used | Optional when the date column dates the rows; must not be after the first decision's information date; the window must end after the opening date |
| `target_probability` | Probability $\alpha$ the target represents | Defaults to the policy's `service_level`; must equal it if given |
| `protection_horizon` | Number of periods $H$ the target covers | Defaults to $L + R$ for periodic schedules (must equal it if given); required for other schedules, checked as $(u - t) + L$ per decision |

The table's columns are named on the policy: `sku_column` (default
`"unique_id"`) and `date_column` (default `"date"`), the date of the last
period each row covers. For a one-row target that is the window's last
period, origin $+ H \Delta$; for a per-step row, its own period, origin
$+ \mathrm{fh}\,\Delta$. The default date column is read only when present;
a named one must exist.

Column names that look like quantiles, such as `q95`, `p90`, `up_95`, or
`q975` (97.5%), are read as probabilities and must agree with the target's
probability. A column called `q90` cannot be passed as a 95% target by
accident.

## Targets over time

A target fitted at the opening date can be reused at every review: a
**static-target** experiment, useful for isolating the effect of a policy
setting. To refresh targets as new forecasts arrive, fit one policy per
forecast origin and pass them as `policy_schedule={decision_period: policy}`.
Each snapshot's origin must be the decision's information date,
$\text{opening date} + t\,\Delta$. See
[Refresh targets as forecasts roll](../how-to/rolling-targets.md) and
[Notebook 04d](../notebooks/04d_rolling_cumulative_targets.ipynb).

## Target probability and realised service

The target probability describes the forecast: "with probability at least
0.95, demand over the window stays at or below $S$". Realised service in a
simulation also depends on things the forecast does not know about: how
accurate it actually is, the opening stock, lost sales, expiry, order minimums, and supplier
reliability. The evaluator measures what the whole system delivers
([Evaluation and metrics](metrics.md)); comparing it with the target
probability shows how well the forecast and the policy work together.

**See also:** [Connect any forecasting model](../how-to/connect-a-forecaster.md) ·
[Notebook 04e: three target methods](../notebooks/04e_cumulative_target_methods.ipynb)

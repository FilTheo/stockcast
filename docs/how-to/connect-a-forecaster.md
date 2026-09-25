# Connect any forecasting model

Stockcast works with any forecasting library: statistical models, machine
learning, deep learning, foundation models, or your own code. The connection
is always the same three steps:

1. **Forecast** the next $H$ periods from data up to the decision.
2. **Summarise** the forecast into one target per SKU for the whole window.
3. **Fit** a policy with that target and its dates.

This page shows the recipe with smooth, which the notebooks use, and with a
generic model that produces sample paths.

## What Stockcast needs from your model

For each SKU and decision, one of:

| Your model gives | Turn it into a target by |
|---|---|
| sample paths (simulations, bootstrap, probabilistic networks) | summing each path over the window, then taking the quantile |
| a quantile of the cumulative forecast | using it directly |
| per-period means and standard deviations | the independent-normal mode, or your own covariance formula |

and three dates: the **forecast origin** (last observed demand), the
**frequency**, and the **end date** $= \text{origin} + H\Delta$.
[Forecast targets](../user-guide/forecast-targets.md) explains the theory.

## With smooth

[smooth](https://openforecast.org/smooth-py/) can forecast the cumulative
demand over a horizon directly. For a daily series `history` ending at
`origin`:

```py
from smooth import ES

H = lead_time + review_period
model = ES(model="ANN")
model.fit(history["y"])
total = model.predict(h=H, interval="prediction", level=0.95,
                      side="upper", cumulative=True)

target = pd.DataFrame({
    "unique_id": [sku],
    "target": [float(total.upper.iloc[-1, 0])],
    "target_end_date": [origin + pd.Timedelta(days=H)],
})
```

Then fit the policy exactly as in the [Quickstart](../get-started/quickstart.md),
with `forecast_origin=origin`. Notebooks
[04](../notebooks/04_forecast_to_inventory_integration.ipynb),
[04c](../notebooks/04c_cumulative_protection_target.ipynb),
[04e](../notebooks/04e_cumulative_target_methods.ipynb), and
[04f](../notebooks/04f_scheduled_forecast_simulation.ipynb) run this end to
end.

## With any model that samples paths

Many libraries can draw future sample paths. The helper below turns a
`(n_paths, H)` array per SKU into a target table; plug in whatever produces
the paths:

```python
import numpy as np
import pandas as pd


def target_from_paths(paths_by_sku, *, probability, origin, horizon, freq="D"):
    """One cumulative target per SKU from arrays of shape (n_paths, horizon)."""
    offset = pd.tseries.frequencies.to_offset(freq)
    return pd.DataFrame({
        "unique_id": list(paths_by_sku),
        "target": [float(np.quantile(p[:, :horizon].sum(axis=1), probability))
                   for p in paths_by_sku.values()],
        "target_end_date": origin + horizon * offset,
    })


# Stand-in for your model: 5,000 simulated futures for two SKUs.
rng = np.random.default_rng(1)
paths = {
    "tea_250g": rng.poisson(6.0, size=(5_000, 6)),
    "coffee_1kg": rng.negative_binomial(3, 0.4, size=(5_000, 6)),
}
origin = pd.Timestamp("2026-01-05")
targets = target_from_paths(paths, probability=0.95, origin=origin, horizon=6)
targets
```

```text
    unique_id  target target_end_date
0    tea_250g    46.0      2026-01-11
1  coffee_1kg    41.0      2026-01-11
```

```python
from stockcast.policies import OrderUpToPolicy

policy = OrderUpToPolicy(lead_time=2, review_period=4, service_level=0.95,
                         allow_backorders=False).fit(
    targets, target_column="target", target_probability=0.95,
    protection_horizon=6, target_source="external_direct",
    forecast_origin=origin, forecast_frequency="D",
    target_end_date_column="target_end_date",
)
policy.get_target_levels()
```

```text
    unique_id  target_level
0    tea_250g          46.0
1  coffee_1kg          41.0
```

## Tips for any library

- **Long format fits.** Most forecasting libraries (Nixtla's, sktime's,
  skforecast's multi-series tools) use the same long `unique_id` / date /
  value layout as Stockcast, so reshaping is usually a `groupby`.
- **Forecast the window you need.** Ask your model for exactly $H$ steps from
  the decision's origin. For irregular schedules, $H$ differs per decision.
- **Keep the origin honest.** Fit on data up to the origin only. For a
  decision at demand period $t$, the origin is the date of period $t - 1$ (the
  opening date for $t = 0$). Stockcast checks the dates you declare.
- **Quantile forecasts per step are not enough on their own.** They are
  marginal: combine them through paths or a cumulative forecast, not by
  adding them ([why](../user-guide/design/cumulative-targets.md)).
- **Record how the target was made.** Keep the model, its settings, and the
  seed next to the target table; the run manifest stores the target itself
  and its metadata.

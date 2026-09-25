# Refresh targets as forecasts roll

In practice you re-forecast before every order, using the latest sales. A
backtest should do the same: at each review, fit the forecast on data up to
that day only, and use the resulting target for that decision. Stockcast's
`policy_schedule` holds one fitted policy per decision and checks that each
one only uses information available at its decision.

## The rule for origins

A decision at demand period $t$ is made before period $t$'s demand. The last
demand it can know is period $t - 1$. So its forecast origin is

$$
\text{origin}_t = \text{opening date} + t\,\Delta ,
$$

and its target covers $H$ periods from there.

## Step by step

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

Eight weeks of sales before the opening give the model a history:

```python
history = DemandGenerator(
    [sku], start_date=opening_date - pd.Timedelta(days=55), period_frequency="D",
    seed=4, negative_demand_handling="clip_zero",
).seasonal(n_periods=56, base=6.0, amplitude=2.0, season_length=7, std=2.0)
history["y"] = history["y"].round()
```

A small forecasting function: the mean of the last 28 days, turned into
Poisson sample paths for the six-day window. Swap in your own model here.

```python
rng = np.random.default_rng(0)


def fit_at(t):
    """Fit the policy used at decision period t, from data up to t - 1."""
    observed = np.concatenate([history["y"].to_numpy(), demand["y"].to_numpy()[:t]])
    rate = observed[-28:].mean()
    totals = rng.poisson(rate, size=(10_000, horizon)).sum(axis=1)
    origin = opening_date + pd.Timedelta(days=t)
    target = pd.DataFrame({
        "unique_id": [sku],
        "target": [np.quantile(totals, 0.95)],
        "target_end_date": [origin + pd.Timedelta(days=horizon)],
    })
    return OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period,
        service_level=0.95, allow_backorders=False,
    ).fit(
        target, target_column="target", target_probability=0.95,
        protection_horizon=horizon, target_source="external_direct",
        forecast_origin=origin, forecast_frequency="D",
        target_end_date_column="target_end_date",
    )
```

Fit one policy per review and run:

```python
reviews = range(0, 56, review_period)                 # 0, 4, 8, ...
snapshots = {t: fit_at(t) for t in reviews}

rolling = SimulationEngine().run(
    policy=snapshots[0],
    policy_schedule={t: p for t, p in snapshots.items() if t > 0},
    demand_source=demand, inventory=inventory, **run_settings,
)
events = rolling.to_event_frame()
events.loc[events["decision_flag"], ["date", "target_level", "order_quantity"]].head(5)
```

```text
         date  target_level  order_quantity
0  2026-01-06          45.0            15.0
4  2026-01-10          46.0            28.0
8  2026-01-14          44.0            12.0
12 2026-01-18          46.0            34.0
16 2026-01-22          44.0            15.0
```

Each review now uses its own target. The run manifest logs every update in
`run_settings["policy_update_log"]`, with the target metadata of each
snapshot.

## What Stockcast checks

Before the run starts, for every snapshot:

- its key is a decision period of the policy's schedule;
- it has the same class, lead time, schedule, service level, and shortage rule
  as the first policy (only the fitted targets may change);
- its forecast origin equals $\text{opening date} + t\,\Delta$ exactly;
- its frequency matches the simulation.

Stockcast checks the dates you declare. Using only data up to the origin when
fitting is part of your forecasting code, as in `fit_at` above.

## Comparing rolling forecasts

To compare two forecasting models under rolling refits, build one snapshot
dictionary per model and pass them to `run_comparison`:

```python
comparison = SimulationEngine().run_comparison(
    policies=[snapshots[0], tea_policy(0.95)],
    policy_schedules=[{t: p for t, p in snapshots.items() if t > 0}, None],
    labels=["rolling 28-day mean", "static target"],
    demand_source=demand, inventory=inventory, **run_settings,
)
comparison.summary()[["fill_rate", "mean_ending_on_hand_per_sku_period"]]
```

```text
                     fill_rate  mean_ending_on_hand_per_sku_period
policy
rolling 28-day mean        1.0                           18.642857
static target              1.0                           18.607143
```

On this stable demand the two agree closely, as they should. Rolling targets
pay off when demand shifts, which you can test by changing the demand path.
[Notebook 04d](../notebooks/04d_rolling_cumulative_targets.ipynb) does this
with smooth forecasts refitted at every review.

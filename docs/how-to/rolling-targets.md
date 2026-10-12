# Refresh targets as forecasts roll

In practice you re-forecast before every order, using everything observed so
far. A backtest should do the same: at each review, fit the forecast on data
up to that day only, and use the resulting target for that decision. Stockcast's
`policy_schedule` holds one fitted policy per decision and checks that each
one only uses information available at its decision.

## Forecast origins

A decision at demand period $t$ is made before period $t$'s demand. The last
demand it can know is period $t - 1$. So its forecast origin is

$$
\text{origin}_t = \text{opening date} + t\,\Delta ,
$$

and its target covers $H$ periods from there.

## Step by step

??? example "Setup: the tea shop from the Walkthrough"

    ```python
    --8<-- "learn-setup.py"
    ```

Eight weeks of sales before the opening give the model a history:

```python
history = DemandGenerator(
    [sku], first_date=opening_date - pd.Timedelta(days=55), freq="D",
    random_seed=4, negative_demand_handling="clip_zero",
).seasonal(n_periods=56, base=6.0, amplitude=2.0, season_length=7, std=2.0)
history["y"] = history["y"].round()
```

A small forecasting function: the mean of the last 28 days, turned into
Poisson sample paths for the six-day window. Swap in your own model here.
`fit_at(t)` gives it the data known before decision `t`.

```python
rng = np.random.default_rng(0)


def policy_from(observed, origin):
    """Forecast from `observed`, known at `origin`, and fit the policy."""
    rate = observed[-28:].mean()
    totals = rng.poisson(rate, size=(10_000, horizon)).sum(axis=1)
    target = pd.DataFrame({
        "unique_id": [sku],
        "target": [np.quantile(totals, 0.95)],
        "target_end_date": [origin + pd.Timedelta(days=horizon)],
    })
    return OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period, freq="D",
        service_level=0.95, allow_backorders=False,
        date_column="target_end_date",
    ).fit(
        target, target_column="target",
        forecast_origin=origin,
    )


def fit_at(t):
    """Fit the policy used at decision period t, from data up to t - 1."""
    observed = np.concatenate([history["y"].to_numpy(), demand["y"].to_numpy()[:t]])
    return policy_from(observed, opening_date + pd.Timedelta(days=t))
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
`run_settings["policy_update_log"]`, with the target metadata, the target
fingerprint, and the fitted levels of each snapshot.

## What Stockcast checks

Before the run starts, for every snapshot:

- its key is a decision period of the policy's schedule;
- it has the same class, lead time, schedule, service level, and shortage rule
  as the first policy (only the fitted targets may change);
- it has a target for every SKU of the inventory;
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

On this stable demand the two agree closely. Rolling targets
pay off when demand shifts, which you can test by changing the demand path.
[Notebook 04d](../notebooks/04d_rolling_cumulative_targets.ipynb) does this
with smooth forecasts refitted at every review.

## From a table of targets

Forecasting pipelines often write every forecast into one long table: one
row per product and forecast origin, with a column per service level.
`rolling_schedule` turns such a table into the schedule a run needs.

Here is a shop with two products and its table. Each row's `cutoff` is the
last day its forecast saw, and `date` is the last day its target covers. The
forecast is the 28-day mean from the example above, at two levels:

```python
from stockcast.policies import rolling_schedule

shop = ["tea_250g", "coffee_1kg"]
daily = DemandGenerator(
    shop, first_date=opening_date - pd.Timedelta(days=55), freq="D",
    random_seed=2, negative_demand_handling="clip_zero",
).seasonal(n_periods=112, base=6.0, amplitude=2.0, season_length=7, std=2.0)
daily["y"] = daily["y"].round()
shop_demand = daily[daily["date"] > opening_date]          # the run's 56 days
shop_inventory = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": shop, "date": opening_date, "on_hand": 30.0}),
)

rows = []
for t in range(0, 56, review_period):
    cutoff = opening_date + pd.Timedelta(days=t)
    for product in shop:
        known = daily[(daily["unique_id"] == product) & (daily["date"] <= cutoff)]
        rate = known["y"].tail(28).mean()
        totals = np.random.default_rng(t).poisson(rate, size=(10_000, horizon)).sum(axis=1)
        rows.append({
            "unique_id": product, "cutoff": cutoff,
            "date": cutoff + pd.Timedelta(days=horizon),
            "q_90": np.quantile(totals, 0.90), "q_95": np.quantile(totals, 0.95),
        })
targets = pd.DataFrame(rows)
targets.head(4)
```

```text
    unique_id     cutoff       date  q_90  q_95
0    tea_250g 2026-01-05 2026-01-11  45.0  48.0
1  coffee_1kg 2026-01-05 2026-01-11  41.0  43.0
2    tea_250g 2026-01-09 2026-01-15  47.0  49.0
3  coffee_1kg 2026-01-09 2026-01-15  41.0  44.0
```

Give `rolling_schedule` the policy's configuration, the table, the opening
state, and the column to fit. It fits one copy of the policy per cutoff and
returns `{decision_period: policy}`:

```python
schedule = rolling_schedule(
    OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period, freq="D",
        service_level=0.95, allow_backorders=False,
    ),
    targets, shop_inventory,
    target_column="q_95",
)
first = min(schedule)
table_run = SimulationEngine().run(
    policy=schedule[first], policy_schedule=schedule,
    demand_source=shop_demand, inventory=shop_inventory,
    allow_target_reuse=False, **run_settings,
)
print(list(schedule))
```

```text
[0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52]
```

Each cutoff belongs to exactly one decision: the decision at period $t$ has
cutoff $\text{opening date} + t\,\Delta$. Along the way `rolling_schedule`
checks that:

- every cutoff is on the `freq` calendar and falls on a decision period of
  the policy's schedule (a cutoff between two reviews is an error, not a
  rounding);
- every product of the opening state has its row at every cutoff;
- every decision between the first and the last cutoff has its rows;
- every snapshot passes `fit`'s own checks: here, `q_95` at a 95% service
  level and a `date` six days after the cutoff.

An error names the product, the cutoff and the decision period. Each
snapshot keeps only the columns it was fitted on, so the other level stays
out of its record. The origin column is called `cutoff` by default; name
another one with `origin_column=`.

### One target per decision

By default a policy keeps deciding until the schedule replaces it. That is
what makes a static-target experiment possible, and it also means a missing
snapshot goes unnoticed: the decision before it simply decides again. Pass
`allow_target_reuse=False` when every decision must use a target fitted for
it. The run then checks before it starts that every decision has its own
snapshot, and stops at the first one that would reuse an older target:

```python
try:
    SimulationEngine().run(
        policy=schedule[first],
        demand_source=shop_demand, inventory=shop_inventory,
        allow_target_reuse=False, **run_settings,
    )
except ValueError as error:
    print(error)
```

```text
allow_target_reuse=False: decision period 4 (information date 2026-01-09) has no policy of its own in policy_schedule, so it would reuse the target of an earlier decision; give policy_schedule a policy for every decision (rolling_schedule builds one from a table of targets), or pass allow_target_reuse=True to reuse a static target on purpose
```

A run with the check records `allow_target_reuse: False` in its
`run_settings`.

### Several service levels

One table can hold several levels. Build one schedule per level and pass them
to `run_comparison`, which runs every branch on the same demand:

```python
by_level = {
    f"{level:.0%}": rolling_schedule(
        OrderUpToPolicy(
            lead_time=lead_time, review_period=review_period, freq="D",
            service_level=level, allow_backorders=False,
        ),
        targets, shop_inventory,
        target_column=column,
    )
    for level, column in [(0.90, "q_90"), (0.95, "q_95")]
}
levels = SimulationEngine().run_comparison(
    policies={label: s[min(s)] for label, s in by_level.items()},
    policy_schedules=by_level,
    demand_source=shop_demand, inventory=shop_inventory,
    allow_target_reuse=False, **run_settings,
)
levels.summary()[["fill_rate", "mean_ending_on_hand_per_sku_period"]]
```

```text
        fill_rate  mean_ending_on_hand_per_sku_period
policy
90%      0.994012                           16.598214
95%      0.997006                           18.750000
```

`allow_target_reuse` applies to every branch, and so does `missing_targets`
below.

### Targets the forecast could not produce

Some forecasts cannot produce a target for every product at every cutoff, for
example when a product's history is still too short. Keep the row and leave
its target columns empty (NaN). By default `rolling_schedule` stops there and
names the product and the cutoff. With `missing_targets="no_order"` it leaves
the product out of that cutoff's policy, and the same option on the run places
no order for it at that decision. Here coffee has no target at the review of
14 January:

```python
gappy = targets.copy()
gap = (gappy["unique_id"] == "coffee_1kg") & (gappy["cutoff"] == opening_date + pd.Timedelta(days=8))
gappy.loc[gap, ["q_90", "q_95"]] = np.nan

schedule = rolling_schedule(
    OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period, freq="D",
        service_level=0.95, allow_backorders=False,
    ),
    gappy, shop_inventory,
    target_column="q_95",
    missing_targets="no_order",
)
gap_run = SimulationEngine().run(
    policy=schedule[first], policy_schedule=schedule,
    demand_source=shop_demand, inventory=shop_inventory,
    allow_target_reuse=False, missing_targets="no_order", **run_settings,
)
coffee = gap_run.to_event_frame().query("unique_id == 'coffee_1kg'")
coffee[["date", "decision_flag", "target_level", "order_quantity", "target_missing_flag"]].iloc[4:13]
```

```text
         date  decision_flag  target_level  order_quantity  target_missing_flag
9  2026-01-10           True          44.0            29.0                False
11 2026-01-11          False          44.0             0.0                False
13 2026-01-12          False          44.0             0.0                False
15 2026-01-13          False          44.0             0.0                False
17 2026-01-14           True           NaN             0.0                 True
19 2026-01-15          False           NaN             0.0                False
21 2026-01-16          False           NaN             0.0                False
23 2026-01-17          False           NaN             0.0                False
25 2026-01-18           True          44.0            44.0                False
```

Only coffee's order at that review changes: its demand is still served from
the shelf, and an order placed before the gap still arrives on time. Its
`target_level` is empty from
the gap until its next decision with a target, and `target_missing_flag`
marks the decision without one. The run's settings count these decisions per
product:

```python
gap_run.run_settings["missing_targets"]
```

```text
{'mode': 'no_order', 'decisions_without_target': 1, 'skus': [{'unique_id': 'coffee_1kg', 'decisions': 1}]}
```

No value is filled in. The zero order is itself a decision, so when you
compare forecasts with different gaps, report each one's count with its
results. A missing row, rather than an empty one, always stops
`rolling_schedule`. A gap product is not part of that decision's order, so a
callback that changes its order there stops the run. `missing_targets` also
works with schedules you build by hand: a snapshot without a level for a
product is a gap for that product.

## When the forecast learns from the run

The engine replays the targets you give it; it never fits a forecasting model
itself. Preparing every snapshot before the run, as above, gives exactly the
targets you would get by forecasting during the run whenever the forecast
learns from demand. Demand comes from the demand table, whatever the shop
orders, so the data known at each decision is the same either way. With
backorders, unmet demand stays on the books, so demand is fully observed too.

Some forecasts learn from what the run itself produces:

- **Sales under lost sales.** On a day that runs out of stock, sales stop at
  the stock on the shelf and the rest of the demand is never seen
  ([Sales and demand](../user-guide/demand.md#sales-and-demand)). How much
  was sold depends on the stock, which depends on earlier orders and their
  forecasts.
- **Stock as a feature.** A model that uses stockouts, stock levels or past
  orders as inputs reads values the run creates.

These forecasts are made during the run. The engine calls a policy's
`predict` before each decision's demand, and `get_history()` on the state it
passes lists every completed period, never the current one:

| History column | Meaning |
|---|---|
| `latest_incoming_demand` | demand that arrived in the period |
| `latest_fulfilled` | units served from stock |
| `latest_shortage` | demand that could not be served |
| `on_hand` | stock at the end of the period |

So a small policy can refit the forecast at every review. This one learns
from sales, reuses `policy_from` from above, and lets the fitted
`OrderUpToPolicy` compute the order:

```python
from stockcast.core import BasePolicy


class RefitFromSales(BasePolicy):
    """Refit the forecast at every review from the sales recorded so far."""

    def __init__(self):
        super().__init__(
            lead_time=lead_time, review_period=review_period,
            service_level=0.95, allow_backorders=False,
        )

    def fit(self, history):
        self.before_run = history["y"].to_numpy()   # sales observed before the run
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        past = inventory_state_df.get_history()      # completed days only
        sales = past["latest_fulfilled"].to_numpy() if len(past) else np.array([])
        today = inventory_state_df.get_dataframe()["date"].iloc[0]
        policy = policy_from(
            np.concatenate([self.before_run, sales]),
            origin=today - pd.Timedelta(days=1),
        )
        return policy.predict(inventory_state_df, current_period=current_period)


rng = np.random.default_rng(0)   # the same draws as the prepared snapshots
in_run = SimulationEngine().run(
    policy=RefitFromSales().fit(history),
    demand_source=demand, inventory=inventory, freq="D", **run_settings,
)
prepared = rolling.to_event_frame().drop(columns="policy")
refitted = in_run.to_event_frame().drop(columns="policy")
prepared.equals(refitted)
```

```text
True
```

The policy records no forecast frequency, so the run gets `freq` directly.
For several SKUs, group the history by `unique_id`. The engine works on its
own copy of the policy, and each branch of a `run_comparison` keeps its own
history, so every branch learns from its own stockouts.

On this demand the shop never runs out, so its sales equal its demand and
the refits inside the run give the same event table as the prepared
snapshots. [Notebook 04a](../notebooks/04a_forecasting_from_sales.ipynb)
follows a shop where stockouts hide demand: there the two forecasts part
after the first stockout, and the forecast that learns from sales loses more
sales. Refitting at every decision does more work than replaying prepared
targets, so it is the route to choose when the forecast needs what the run
produces.

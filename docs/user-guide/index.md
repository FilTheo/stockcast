# Guide

The Guide explains each part of Stockcast in depth: what it does, the ideas
behind it, and every option it takes. New to Stockcast? The
[Walkthrough](../learn/index.md) introduces the same parts in nine short steps.

- **The core of a run**: the parts every simulation uses, in the order a run
  uses them: [inventory state](inventory-state.md),
  [demand](demand.md), [forecast targets](forecast-targets.md),
  [policies](policies/index.md) and their [schedules](decision-schedules.md),
  [the engine](engine.md) and its [timing](concepts/timing.md),
  [the event table](concepts/accounting.md), [metrics](metrics.md), and
  [plots](visualization.md).
- **Real-world operations**: optional rules, each added with one argument:
  [ordering constraints](constraints.md), [callbacks](callbacks.md),
  [suppliers](suppliers.md), [unreliable deliveries](unreliable-deliveries.md),
  and [shelf life](processes.md).
- **Recipes**: [short pages for specific tasks](../how-to/index.md), with
  runnable code.

## I want to…

Find the building block for your job, the page that explains it, and a
notebook that uses it.

| I want to… | Use | Read | See it in |
|---|---|---|---|
| start from my current stock and open orders | `InventoryStateDataFrame`, `with_open_orders` | [Inventory state](inventory-state.md) | [01](../notebooks/01_introduction_to_inventory_flow.ipynb), [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| feed in sales history or simulated demand | demand table, `DemandGenerator` | [Demand and calendars](demand.md) | [02](../notebooks/02_first_engine_simulation.ipynb), [02c](../notebooks/02c_synthetic_demand.ipynb) |
| forecast from sales that ran out of stock | `demand` vs `fulfilled_units` in the event table, `get_history()` in `predict` | [Sales and demand](demand.md#sales-and-demand), [When the forecast learns from the run](../how-to/rolling-targets.md#when-the-forecast-learns-from-the-run) | [04a](../notebooks/04a_forecasting_from_sales.ipynb) |
| turn my forecast into a stock target | target table + `fit` | [Forecast targets](forecast-targets.md), [Connect any forecasting model](../how-to/connect-a-forecaster.md) | [04c](../notebooks/04c_cumulative_protection_target.ipynb), [04e](../notebooks/04e_cumulative_target_methods.ipynb) |
| order up to a level at every review | `OrderUpToPolicy` | [Order-up-to](policies/order-up-to.md) | [04](../notebooks/04_forecast_to_inventory_integration.ipynb), [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) |
| order only when stock falls low | `ReorderPointPolicy` | [Reorder point](policies/reorder-point.md) | [05b](../notebooks/05b_reorder_points_and_review_frequency.ipynb), [05g](../notebooks/05g_reorder_point_sources.ipynb), [08](../notebooks/08_callbacks_and_audit.ipynb) |
| buy once for a season | `SingleOrderPolicy`, `newsvendor_critical_fractile` | [Single order](policies/single-order.md) | [04b](../notebooks/04b_daily_newsvendor.ipynb) |
| use my own ordering rule | subclass `BasePolicy` | [Write your own policy](policies/custom-policies.md) | [05](../notebooks/05_custom_policies.ipynb) |
| order on specific days only | `PeriodicSchedule`, `ExplicitSchedule`, `DecisionSchedule` | [Decision schedules](decision-schedules.md) | [02b](../notebooks/02b_decision_schedules.ipynb), [04f](../notebooks/04f_scheduled_forecast_simulation.ipynb) |
| simulate a policy over time | `SimulationEngine.run` | [The simulation engine](engine.md) | [02](../notebooks/02_first_engine_simulation.ipynb) |
| use a lead time from a paper or another tool | `lead_time`, `PeriodicSchedule(start=...)` | [Work with any timing convention](../how-to/timing-conventions.md) | – |
| read what happened in a run | `to_event_frame`, `validate_event_frame` | [The event table](concepts/accounting.md) | [02](../notebooks/02_first_engine_simulation.ipynb), [08](../notebooks/08_callbacks_and_audit.ipynb) |
| measure service, stock, and cost | `InventoryEvaluator`, metrics | [Evaluation and metrics](metrics.md), [Put costs on a run](../how-to/costs.md) | [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| plot a run or a comparison | `plot_simulation_dashboard`, `plot_comparison_dashboard` | [Plots](visualization.md) | [02](../notebooks/02_first_engine_simulation.ipynb), [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) |
| respect minimums, case sizes, or capacity | `OrderingConstraints` and friends | [Ordering constraints](constraints.md) | [05c](../notebooks/05c_extension_points.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| add holidays, promotions, or stock corrections | `SimulationCallback`, scheduled callbacks | [Callbacks](callbacks.md) | [08](../notebooks/08_callbacks_and_audit.ipynb), [05c](../notebooks/05c_extension_points.ipynb) |
| use several suppliers or random lead times | `SupplyModel`, `Supplier`, `SupplierAllocation` | [Suppliers and open orders](suppliers.md) | [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| model late or short deliveries | `DeliveryOutcome` | [Unreliable deliveries](unreliable-deliveries.md) | [05f](../notebooks/05f_unreliable_supplier.ipynb) |
| model products that expire, inspections, returns | `ShelfLife`, `InventoryProcess` | [Shelf life and processes](processes.md) | [05e](../notebooks/05e_inventory_processes.ipynb), [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb) |
| refresh targets as new forecasts arrive | `policy_schedule` | [Refresh targets as forecasts roll](../how-to/rolling-targets.md) | [04a](../notebooks/04a_forecasting_from_sales.ipynb), [04d](../notebooks/04d_rolling_cumulative_targets.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| compare forecasts or policies fairly | `run_comparison` | [Compare forecasts and policies](../how-to/compare-policies.md) | [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| compute real orders every day | `advance_period` → `predict` → constraints → `fulfill_demand` | [Use Stockcast in a daily job](../how-to/production.md), [Walkthrough step 9](../learn/09-production.md) | [10](../notebooks/10_production_daily_close.ipynb) |

## All together

Every optional part is one argument of `run`. This run adds two ordering
constraints, a holiday callback, a supplier whose deliveries sometimes take a
day longer, and shelf life to the tea shop. Remove any of them and the rest
still works.

??? example "Setup: the tea shop from the Walkthrough"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.core import (
    InventoryStateDataFrame, MinimumOrderQuantity,
    OrderMultiple, ScheduledOrderHold, ShelfLife, SimulationEngine, Supplier,
    SupplyModel,
)

# Orders of at least 12 packs, in cases of 6.
constraints = [
    MinimumOrderQuantity(12, mode="adjust"),
    OrderMultiple(6, mode="adjust"),
]

# The supplier is closed on 18 January: skip that review.
holiday = ScheduledOrderHold(pd.DataFrame({
    "unique_id": [sku],
    "date": [pd.Timestamp("2026-01-18")],
    "reason": ["supplier closed"],
    "source": ["supplier calendar"],
}))

# Deliveries take 2 days, sometimes 3.
supply = SupplyModel(
    [Supplier("tea_wholesaler", lead_time={2: 0.8, 3: 0.2})],
    random_seed=11,
)

# Tea keeps for 21 days; the opening 30 packs arrived three days ago.
shelf_life = ShelfLife(
    shelf_life_days=21,
    opening_lots=pd.DataFrame({
        "unique_id": [sku],
        "received_date": [opening_date - pd.Timedelta(days=3)],
        "quantity": [30.0],
    }),
)

inventory_3 = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}), opening_date=opening_date,
)

result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory_3,
    order_constraints=constraints,
    callbacks=[holiday],
    supply=supply,
    processes=[shelf_life],
    **run_settings,
)

events = result.to_event_frame()
events.loc[events["requested_order_quantity"] > 0,
           ["date", "requested_order_quantity", "callback_adjusted_order_quantity",
            "order_quantity", "binding_constraints"]].head(6)
```

```text
         date  requested_order_quantity  callback_adjusted_order_quantity  order_quantity     binding_constraints
0  2026-01-06                      16.0                              16.0            18.0          order_multiple
4  2026-01-10                      25.0                              25.0            30.0          order_multiple
8  2026-01-14                       9.0                               9.0            12.0  minimum_order_quantity
12 2026-01-18                      29.0                               0.0             0.0
16 2026-01-22                      46.0                              46.0            48.0          order_multiple
20 2026-01-26                       9.0                               9.0            12.0  minimum_order_quantity
```

Read across a row and you see each component at work: the policy asked for 16
packs, the case-pack rule rounded it to 18; on 18 January the holiday callback
set the order to zero. The order frame (`result.to_order_frame()`) shows which
deliveries took three days, and the callback audit
(`result.to_callback_audit_frame()`) records the holiday with its reason.

!!! note "Lead-time warning"

    This run prints a `UserWarning`: the policy's target was sized for a
    2-day lead time, and some deliveries take 3. Stockcast simulates what
    you declared and warns that the two differ; that mismatch is often what
    you want to study. See
    [Suppliers: the lead-time assumption](suppliers.md#lead-time-assumption).

**See also:** [Notebook 05c: extension points](../notebooks/05c_extension_points.ipynb) ·
[Notebook 09: full operational experiment](../notebooks/09_full_operational_experiment.ipynb) ·
[Philosophy](../get-started/philosophy.md): why Stockcast is built this way

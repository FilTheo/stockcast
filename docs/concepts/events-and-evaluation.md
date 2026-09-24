# Events, results, and evaluation

`SimulationResult.to_event_frame()` is the authoritative accounting output of a
run. New runs contain one row per SKU and demand epoch, including the first
scheduled decision. The evaluator can still read historical opening-decision
rows. Use the ledger for metrics, plots, and downstream analysis.

The additive `decision_inventory_position` field records the actual inventory
position presented to a policy before demand. It is missing on epochs without
a decision. Do not recover it by simply subtracting orders from ending position:
current demand and physical callbacks occur between those two snapshots.

## Durable outputs

| Output | Purpose |
|---|---|
| `to_event_frame(window=...)` | Canonical inventory, demand, pipeline, decision, callback, and constraint evidence. |
| `to_callback_audit_frame()` | Accepted typed callback effects and their reason/source information. |
| `run_manifest` | Reproducibility receipt: demand source, package/dependencies, policy, opening state, and run settings. |
| `summary()` | Compact scoring-window convenience summary. |

The event rows are validated against demand fulfillment, physical-stock,
backlog, pipeline, inventory-position, callback-adjustment, and constraint
identities. See the complete [schema reference](../reference/schemas.md).

## Evaluate a run

`InventoryEvaluator` requires an explicit window and grouping when fitting or
evaluating. It can evaluate built-in functions or a custom `BaseInventoryMetric`.

```python
from stockcast.evaluation import InventoryEvaluator, fill_rate, avg_on_hand

report = InventoryEvaluator().fit(result, window="scoring").evaluate(
    metrics=[fill_rate, avg_on_hand],
    groupby=[],
)
```

Cost metrics are intentionally strict: specify active `cost_components` and
every corresponding rate in the evaluation context or event frame. See
[evaluation and metrics](../reference/evaluation.md) for units and denominators.

`fill_rate` measures demand fulfilled immediately; later backlog clearance is
recorded separately. `avg_on_hand`, `avg_on_order`, and
`avg_inventory_position` average SKU-period rows, so a pooled result is a
per-SKU-period mean, not average total portfolio stock. In contrast,
`peak_ending_on_hand` and `ending_on_hand_variance` operate on portfolio totals
by period.

`order_event_count` is a system-wide count stored on one SKU row per event to
preserve additivity. Use it for the complete portfolio; use
`sku_order_line_count` for per-SKU ordering activity.

Purchase cost is charged when orders are placed. Scoring-window costs exclude
orders and holding exposure outside that window. For finite-horizon economic
comparisons, explicitly choose whether to include warmup/opening costs,
settlement effects, terminal backlog/pipeline charges, and salvage credit.

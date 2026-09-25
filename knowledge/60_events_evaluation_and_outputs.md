# 60 — Events, evaluation, and outputs

## 60.1 Canonical event ledger

The event frame is the analytical source of truth. It has one row per SKU and
period, plus a separately typed opening-order event when requested. Columns
cover these groups:

| Group | Examples |
|---|---|
| identity/time | SKU, event type, period, date, window |
| opening state | starting on-hand, backlog, pipeline, inventory position |
| demand/receipt flows | demand, received, current fulfillment, old backlog fulfillment, shortage |
| ending state | ending on-hand, backlog, pipeline, inventory position |
| order flow | raw requested, callback-adjusted, constrained, final quantity, timing |
| policy diagnostics | target level, safety stock, review flag |
| operational audit | order-event/line counts, capacity flags, constraint audit |
| perishability | expired units and explicit stock adjustment |
| inventory processes (optional) | `process_inflow_units`, `process_outflow_units`, present together only when a run's processes declare general flows ([98](98_inventory_processes.md)) |
| supplier delivery outcomes (optional) | `supplier_shortfall_units`, present only when a supplier has a `DeliveryOutcome`; leaves the pipeline balance ([97.7](97_open_orders_and_suppliers.md#977-supplier-delivery-outcomes)) |

`validate_event_frame` enforces required columns, types, finite/nonnegative
values, unique row keys, mutually consistent shortage mode, timing flags, and
all flow identities from [20.8](20_data_and_time_contracts.md#208-balance-equations).

## 60.2 Result object

`SimulationResult` packages:

- period state history;
- final inventory;
- validated events;
- explicit run settings;
- the run manifest.
- a normalized callback-audit frame exposed through
  `to_callback_audit_frame()`;
- an order-level frame, `to_order_frame()` (`ORDER_FRAME_COLUMNS`): one row
  per scheduled delivery of the opening pipeline and of every placed order,
  with supplier, dates, realized lead time and status. Received rows sum to
  `received_units` per SKU-period and open rows to the final `on_order_end`
  ([97](97_open_orders_and_suppliers.md)). Built-in metrics do not use it.
  Runs with a supplier `DeliveryOutcome` add `scheduled_due_period`,
  `received_quantity`, `delayed_quantity` and `undelivered_quantity`
  ([97.7](97_open_orders_and_suppliers.md#977-supplier-delivery-outcomes)).
  No built-in metric reads `supplier_shortfall_units`; supplier fill rate
  and similar are custom callables (Notebook 05f).
- a process flow frame, `to_process_flow_frame()` (`PROCESS_FLOW_COLUMNS`):
  one row per nonzero process flow, SKU and period. Expiry rows sum to
  `expired_units`, general rows to the two process columns. Built-in metrics
  do not use it, and `waste_cost` prices `expired_units` only.

`to_event_frame(window=...)` filters the ledger. Summary behavior uses scoring
events and exact event-based metrics. Consumers should persist both events and
manifest; a summary alone cannot reproduce the run.

## 60.3 Evaluator contract

`InventoryEvaluator.fit` accepts exactly one `SimulationResult` or one event
frame. A result requires an explicit window selection. The event frame is
validated before metrics run.

`evaluate` also requires explicit grouping:

- `groupby=[]` means a pooled calculation;
- named columns produce grouped results.

This prevents an accidental implicit average across SKUs or periods. Metrics
may be built-in objects, compatible metric objects, or callables, but all
operate on the same validated rows.

## 60.4 Metric families

### Units and shortage

Demand, fulfilled units, shortage, lost sales, backlog unit-periods, terminal
backlog, and terminal pipeline are direct event aggregations. The ambiguous
name `backorder_units_end` is not part of the evaluation API. Callers must
choose `backlog_unit_periods` for summed backlog exposure or
`terminal_backlog_units` for final-period backlog.

### Service

- fill rate;
- demand-period service;
- cycle service, with explicit inclusion/exclusion of partial receipt cycles;
- SKU-period stockout rate;
- calendar-period stockout/backorder rate.

These have different populations. A calendar rate first combines SKU rows by
period, while a SKU-period rate counts each SKU-period observation.

### Ordering and capacity

- units ordered;
- SKU order-line count;
- system order-event count;
- positive order-size population variance;
- capacity violation count and rate.

System events are stored once per period rather than repeated on every SKU row,
preserving additivity.

### Inventory

- average on-hand, inventory position, and on-order stock;
- population variance and peak of total ending on-hand by calendar period;
- annualized inventory turns with explicit `periods_per_year`.

### Cost

Holding, shortage, backlog, ordering, purchase, waste, terminal backlog,
terminal pipeline, and salvage calculations require explicit row data or
context rates. Ordering cost supports a fixed SKU-line charge plus a unit cost;
there is no implicit global event cost. `total_cost` requires an explicit list
of included components, and salvage is subtracted.

### Coverage

Forward coverage requires `expected_demand_rate` per row or an explicit
`forward_demand_rate` context. Trailing coverage uses realized per-SKU mean
demand. Multi-SKU aggregation must be chosen explicitly.

## 60.5 Visualization

Plot functions consume event frames (or result/history adapters), return axes,
and do not call `show`. They provide single-run and comparison inventory plots
and dashboards. When aggregating a multi-SKU event frame without a selected
SKU, preparation combines rows by period. Plots are presentation views; they do
not replace canonical validation or metric calculations.

## 60.6 Provenance needed beside outputs

An inspectable experiment artifact should retain:

- canonical event frame;
- run manifest and settings;
- opening-state data/fingerprint;
- exact demand data/fingerprint and generation settings;
- policy target data, metadata, and fingerprint;
- constraint order/configuration;
- package/build provenance;
- evaluation window, grouping, metric definitions, and cost context.

If one of these is missing, document the limitation rather than synthesizing a
replacement value.

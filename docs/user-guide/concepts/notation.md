# Notation and glossary

Every symbol used in these docs, in one place. Formulas elsewhere use exactly
these meanings.

## Indices and time

| Symbol | Meaning | In code |
|---|---|---|
| $i$ | A SKU | `unique_id` |
| $t$ | A demand period, counted from 0 | `demand_period` in the ledger; keys of schedules |
| $\Delta$ | The length of one period, a pandas frequency | `period_frequency`, `forecast_frequency` |
| $L$ | Lead time: periods from order to delivery, $L \ge 0$ | `lead_time` |
| $R$ | Review period: periods between ordering opportunities, $R \ge 1$ | `review_period`, `PeriodicSchedule(every=R)` |
| $u$ | The next decision period after $t$ | `schedule.next_decision_period(t)` |
| $H$ | Protection horizon: periods a decision must cover | `protection_horizon`, `reorder_horizon` |

## Stock

| Symbol | Meaning | Ledger column |
|---|---|---|
| $\mathit{OH}_t$ | On-hand stock | `starting_on_hand`, `ending_on_hand` |
| $P_t$ | Pipeline: units on order, not yet received | `starting_on_order`, `on_order_end` |
| $B_t$ | Backorders: demand owed to customers | `starting_backorders`, `backorders_end` |
| $\mathit{IP}_t$ | Inventory position $= \mathit{OH}_t + P_t - B_t$ | `decision_inventory_position`, `inventory_position_end` |
| $D_t$ | Demand in period $t$ | `demand` |
| $q_t$ | Order quantity placed in period $t$ | `order_quantity` |
| $r_t$ | Units received in period $t$ | `received_units` |

## Policy parameters

| Symbol | Meaning | Used by |
|---|---|---|
| $S$ | Order-up-to level | `OrderUpToPolicy`, `ReorderPointPolicy("sS")`, `PeriodicReviewPolicy` |
| $s$ | Reorder point: order when $\mathit{IP} \le s$ | `ReorderPointPolicy`, `PeriodicReviewPolicy` |
| $Q$ | Fixed order quantity | `ReorderPointPolicy("sQ")` |
| $\alpha$ | Target probability (the quantile level of a target) | `service_level`, `target_probability` |
| $z_\alpha$ | Standard normal quantile, $\Phi^{-1}(\alpha)$ | independent-normal targets |
| $Q_\alpha(X)$ | The $\alpha$-quantile of a random quantity $X$ | forecast targets |
| $p, c, v$ | Selling price, purchase cost, salvage value | `newsvendor_critical_fractile` |

## Glossary

Backorder
:   Demand that cannot be served now but is kept and served from a later
    delivery. Enabled with `allow_backorders=True`.

Lost sale
:   Demand that cannot be served and leaves the system. The default in shops;
    `allow_backorders=False`.

Pipeline (on order)
:   Units that have been ordered and accepted but have not arrived. Held per
    SKU in `in_transit`, with slot $k$ arriving $k + 1$ periods from now.

Inventory position
:   On hand + pipeline − backorders. The quantity ordering rules control.

Lead time
:   The number of periods between placing an order and receiving it. An order
    placed in period $t$ is received at the start of period $t + L$, before
    that period's demand.

Review period
:   The number of periods between two ordering opportunities in a periodic
    schedule.

Decision schedule
:   The object that says in which periods a policy may order.

Protection horizon
:   The number of demand periods a decision must cover before the next order
    can arrive: $H = L + R$ for periodic review.

Forecast target
:   A number derived from a forecast that a policy aims for, together with its
    context: probability, horizon, origin, and end date.

Forecast origin
:   The last date whose demand the forecast used. For a decision in period
    $t$, this is the date of period $t - 1$ (or the opening date for $t = 0$).

Order-up-to level
:   The inventory position a policy restores after ordering.

Reorder point
:   The inventory position at or below which a policy orders.

Event ledger
:   The table with one row per SKU and period that records every flow of units
    in a run. The output of `SimulationResult.to_event_frame()`.

Warm-up, scoring, settlement
:   The three consecutive windows of a run. Metrics describe the scoring
    window by default.

Fill rate
:   Share of demand served from stock in the period it occurred.

Cycle service level
:   Share of replenishment cycles, from one delivery to the next, with no
    shortage.

Run manifest
:   The record attached to every result that describes the inputs, settings,
    and software versions of the run.

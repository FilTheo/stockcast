# Notation

The symbols used throughout the docs, and where each one appears in the code.

## Indices and time

| Symbol | Meaning | In code |
|---|---|---|
| $i$ | A SKU | `unique_id` |
| $t$ | A demand period, counted from 0 | `demand_period` in the event table; keys of schedules |
| $\Delta$ | The length of one period, a pandas frequency | `freq` |
| $L$ | Lead time: periods from order to delivery, $L \ge 0$ | `lead_time` |
| $R$ | Review period: periods between ordering opportunities, $R \ge 1$ | `review_period`, `PeriodicSchedule(every=R)` |
| $u$ | The next decision period after $t$ | `schedule.next_decision_period(t)` |
| $H$ | Protection horizon: periods a decision must cover | `protection_horizon`, `reorder_horizon` |

## Stock

| Symbol | Meaning | Event table column |
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
| $S$ | Order-up-to level | `OrderUpToPolicy`, `ReorderPointPolicy("sS")` |
| $s$ | Reorder point: order when $\mathit{IP} \le s$ | `ReorderPointPolicy` |
| $Q$ | Fixed order quantity | `ReorderPointPolicy("sQ")` |
| $\alpha$ | Target probability (the quantile level of a target) | `service_level`, `target_probability` |
| $z_\alpha$ | Standard normal quantile, $\Phi^{-1}(\alpha)$ | independent-normal targets |
| $Q_\alpha(X)$ | The $\alpha$-quantile of a random quantity $X$ | forecast targets |
| $p, c, v$ | Selling price, purchase cost, salvage value | `newsvendor_critical_fractile` |

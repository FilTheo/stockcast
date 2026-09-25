# Upgrade from demand-first timing

Before the 0.1 release, Stockcast's engine served demand first and decided at
the end of each period. It now [decides before demand](../user-guide/design/decide-before-demand.md),
supports zero lead time, and keeps decision schedules separate from policies.
This page helps you move code and results written for the earlier behaviour.
If you are new to Stockcast, you can skip it.

## What changed

| Topic | Before | Now |
|---|---|---|
| Order of events in a period | demand, then decision | receive, decision, then demand |
| Lead time | $L \ge 1$ | $L \ge 0$; $L = 0$ arrives before today's demand |
| When a policy may order | the review period | a `DecisionSchedule` (`review_period` is shorthand for a periodic one) |
| Opening order | `initial_decision="before_first_demand"` | a schedule that decides in period 0 (the default) |
| Protection window of $(R, S)$ | $L + R - 1$ future periods after the decision | $L + R$ periods, starting with the decision period |
| Rolling-forecast keys | state periods | zero-based demand periods, origin = the previous demand date |
| `on_after_prediction` callbacks | after demand | before demand; `on_after_demand` still after demand |

Results can change for the same inputs, because the decision now happens
earlier and sees different information. The old window count was valid for the
old event order; the new one is valid for the new order.

## Checklist

1. **Opening decisions.** Replace `initial_decision="before_first_demand"`
   with the default schedule, which decides in period 0. `"none"` is still
   accepted and simply means "follow the schedule". To start later, use
   `PeriodicSchedule(R, start=k)`. Orders placed *before* the run belong in the
   opening pipeline (`in_transit` or `with_open_orders`).
2. **Lead times.** Keep lead time as "periods until the goods can serve
   customers". Do not subtract one mechanically; review lead time, opening
   stock, and review phase together.
3. **Targets.** For $(R, S)$, fit `protection_horizon = lead_time +
   review_period`. For reorder points checked every period, use $L + 1$.
4. **Rolling snapshots.** Key `policy_schedule` by zero-based demand period
   $t$, with forecast origin $\text{opening date} + t\,\Delta$.
5. **Callbacks.** Tables for scheduled callbacks keep using state periods (or
   dates). Remember that order callbacks now act before demand.
6. **Manual loops.** Use `advance_period`, then the decision and
   `update_inventory_with_orders`, then `fulfill_demand`
   ([Run your own simulation loop](manual-loop.md)). `process_demand` remains
   available for periods without a decision.
7. **Baselines.** Re-run notebooks and experiments to establish new reference
   numbers.

## New possibilities

- **Same-day replenishment:** `lead_time=0`.
- **One purchase per season:** `SingleOrderPolicy` or `OneTimeSchedule`.
- **Irregular ordering calendars:** `ExplicitSchedule` or your own
  `DecisionSchedule`, with a target per decision.

See [Decision schedules](../user-guide/decision-schedules.md) and
[Timing](../user-guide/concepts/timing.md).
